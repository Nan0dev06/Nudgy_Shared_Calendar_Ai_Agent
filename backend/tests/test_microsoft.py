"""Microsoft/Outlook integration (Phase 1 Branch 4): the Graph-backed provider,
the OAuth token helpers, and the sign-in routes. All HTTP is mocked — no network,
no real Azure calls (the live consent flow needs a human's Microsoft password).
"""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth_routes
from app.api.auth_routes import STATE_COOKIE, router
import app.auth.microsoft as ms_auth
from app.auth.microsoft import (
    build_authorize_url, exchange_code, get_account_email, refresh_token,
)
import app.calendars.microsoft as ms_cal
from app.calendars import provider_for_account
from app.calendars.microsoft import MicrosoftCalendarProvider, _graph_dt, _parse_utc
from app.db import repo
from app.db.models import Base, CalendarAccount
from app.db.session import get_session

LIVE_TOKEN = '{"access_token":"t","refresh_token":"r","expires_at":9999999999}'


def _dt(hour, minute=0):
    return datetime(2026, 1, 5, hour, minute, tzinfo=timezone.utc)


def _node(hour, minute=0):
    # Graph shape: 7 fractional digits, no trailing Z, zone in a sibling field
    return {"dateTime": f"2026-01-05T{hour:02d}:{minute:02d}:00.0000000", "timeZone": "UTC"}


class FakeResp:
    def __init__(self, data=None, status=200):
        self._data = data if data is not None else {}
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("http error", request=None, response=None)


@pytest.fixture
def Session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ============================================================= datetime helpers

def test_graph_dt_has_no_zone_suffix():
    # naive wall-clock string; the zone rides in a separate timeZone field
    assert _graph_dt(_dt(17)) == "2026-01-05T17:00:00"
    assert not _graph_dt(_dt(17)).endswith("Z")


def test_parse_utc_handles_seven_fraction_digits():
    dt = _parse_utc(_node(12, 30))
    assert dt == _dt(12, 30)
    assert dt.tzinfo is not None  # stamped UTC


# =================================================================== provider

def test_get_busy_filters_free_and_merges_overlaps(monkeypatch):
    page = {"value": [
        {"showAs": "busy", "start": _node(9), "end": _node(10)},
        {"showAs": "free", "start": _node(10), "end": _node(11)},        # excluded
        {"showAs": "tentative", "start": _node(9, 30), "end": _node(11)}, # busy
    ]}
    monkeypatch.setattr(ms_cal.httpx, "get", lambda *a, **k: FakeResp(page))
    provider = MicrosoftCalendarProvider(LIVE_TOKEN)
    busy = provider.get_busy(_dt(8), _dt(12))
    # 09:00-10:00 unioned with tentative 09:30-11:00 => 09:00-11:00; free dropped
    assert busy == [(_dt(9), _dt(11))]


def test_get_busy_treats_oof_and_unknown_as_busy_but_working_elsewhere_as_free(monkeypatch):
    page = {"value": [
        {"showAs": "oof", "start": _node(9), "end": _node(10)},
        {"showAs": "workingElsewhere", "start": _node(11), "end": _node(12)},  # free
        {"showAs": "Unknown", "start": _node(13), "end": _node(14)},  # casing-insensitive
        {"start": _node(15), "end": _node(16)},  # showAs ABSENT -> fail-safe busy
    ]}
    monkeypatch.setattr(ms_cal.httpx, "get", lambda *a, **k: FakeResp(page))
    busy = MicrosoftCalendarProvider(LIVE_TOKEN).get_busy(_dt(8), _dt(17))
    assert busy == [(_dt(9), _dt(10)), (_dt(13), _dt(14)), (_dt(15), _dt(16))]


def test_get_busy_follows_paging(monkeypatch):
    pages = [
        {"value": [{"showAs": "busy", "start": _node(9), "end": _node(10)}],
         "@odata.nextLink": "https://graph.microsoft.com/next"},
        {"value": [{"showAs": "busy", "start": _node(11), "end": _node(12)}]},
    ]
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        # page 2 is fetched from the nextLink with no extra params
        if i == 1:
            assert url == "https://graph.microsoft.com/next" and params is None
        return FakeResp(pages[i])

    monkeypatch.setattr(ms_cal.httpx, "get", fake_get)
    busy = MicrosoftCalendarProvider(LIVE_TOKEN).get_busy(_dt(8), _dt(13))
    assert busy == [(_dt(9), _dt(10)), (_dt(11), _dt(12))]
    assert calls["n"] == 2


def test_get_busy_requests_only_privacy_safe_fields(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        if params:
            seen.update(params)
            seen["_headers"] = headers
        return FakeResp({"value": []})

    monkeypatch.setattr(ms_cal.httpx, "get", fake_get)
    MicrosoftCalendarProvider(LIVE_TOKEN).get_busy(_dt(8), _dt(12))
    assert seen["$select"] == "start,end,showAs"  # never subject/attendees
    assert seen["_headers"]["Prefer"] == 'outlook.timezone="UTC"'
    assert seen["_headers"]["Authorization"] == "Bearer t"


def test_get_event_locations_extracts_display_names(monkeypatch):
    page = {"value": [
        {"location": {"displayName": "BHive Cafe"}},
        {"location": {"displayName": ""}},     # skipped
        {"location": None},                      # skipped
        {"location": {"displayName": "  Office  "}},  # trimmed
    ]}
    monkeypatch.setattr(ms_cal.httpx, "get", lambda *a, **k: FakeResp(page))
    locs = MicrosoftCalendarProvider(LIVE_TOKEN).get_event_locations(_dt(17), _dt(19))
    assert locs == ["BHive Cafe", "Office"]


def test_create_event_builds_graph_body(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, headers=headers)
        return FakeResp({"id": "evt1", "webLink": "https://outlook.office.com/evt1"})

    monkeypatch.setattr(ms_cal.httpx, "post", fake_post)
    created = MicrosoftCalendarProvider(LIVE_TOKEN).create_event(
        summary="Coffee", start=_dt(17), end=_dt(19),
        attendee_emails=["a@x.com", "b@x.com"], location="BHive Cafe",
        description="see you there",
    )
    assert created.id == "evt1"
    assert created.link == "https://outlook.office.com/evt1"  # webLink, not htmlLink
    b = captured["body"]
    assert captured["url"].endswith("/me/events")
    assert b["subject"] == "Coffee"
    assert b["start"] == {"dateTime": "2026-01-05T17:00:00", "timeZone": "UTC"}  # no Z
    assert b["end"] == {"dateTime": "2026-01-05T19:00:00", "timeZone": "UTC"}
    assert b["attendees"] == [
        {"emailAddress": {"address": "a@x.com"}, "type": "required"},
        {"emailAddress": {"address": "b@x.com"}, "type": "required"},
    ]
    assert b["location"] == {"displayName": "BHive Cafe"}
    assert b["body"] == {"contentType": "text", "content": "see you there"}


def test_create_event_omits_optional_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr(ms_cal.httpx, "post",
                        lambda url, json=None, **k: captured.update(body=json) or FakeResp({"id": "e"}))
    MicrosoftCalendarProvider(LIVE_TOKEN).create_event(
        summary="Sync", start=_dt(12), end=_dt(13), attendee_emails=[])
    assert "location" not in captured["body"]
    assert "body" not in captured["body"]
    assert captured["body"]["attendees"] == []


def test_update_event_sends_only_given_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr(ms_cal.httpx, "patch",
                        lambda url, json=None, **k: captured.update(url=url, body=json) or FakeResp({"id": "e", "webLink": "L"}))
    MicrosoftCalendarProvider(LIVE_TOKEN).update_event("evt1", location="New venue")
    assert captured["url"].endswith("/me/events/evt1")
    assert captured["body"] == {"location": {"displayName": "New venue"}}


def test_delete_event_hits_the_right_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(ms_cal.httpx, "delete",
                        lambda url, headers=None, timeout=None: captured.update(url=url) or FakeResp({}, status=204))
    MicrosoftCalendarProvider(LIVE_TOKEN).delete_event("evt1")
    assert captured["url"].endswith("/me/events/evt1")


def test_from_account_persists_a_refreshed_token(Session, monkeypatch):
    monkeypatch.setattr(
        ms_cal, "refresh_token",
        lambda tj: ('{"access_token":"NEW","refresh_token":"r","expires_at":9999999999}', True),
    )
    with Session() as s:
        user = repo.login_with_microsoft(s, "a@out.com", '{"access_token":"OLD","refresh_token":"r","expires_at":0}')
        account = repo.get_primary_calendar_account(s, user)
        provider = MicrosoftCalendarProvider.from_account(s, account)
        assert json.loads(account.token_json)["access_token"] == "NEW"  # persisted
        assert provider._token == "NEW"


# ===================================================== repo: login_with_microsoft

def test_login_with_microsoft_creates_verified_identity(Session):
    with Session() as s:
        user = repo.login_with_microsoft(s, "a@out.com", LIVE_TOKEN)
        assert user.email_verified is True  # provider verified the address
        accounts = repo.get_calendar_accounts(s, user)
        assert len(accounts) == 1
        assert accounts[0].provider == "microsoft"
        assert accounts[0].is_primary is True


def test_google_then_microsoft_same_email_is_one_identity(Session):
    with Session() as s:
        u1 = repo.login_with_google(s, "sam@x.com", '{"token":"g"}')
        u2 = repo.login_with_microsoft(s, "sam@x.com", LIVE_TOKEN)
        assert u1.id == u2.id  # unified identity, two calendars
        providers = {a.provider for a in repo.get_calendar_accounts(s, u2)}
        assert providers == {"google", "microsoft"}


def test_provider_factory_dispatches_microsoft():
    account = CalendarAccount(provider="microsoft", external_email="a@out.com", token_json=LIVE_TOKEN)
    assert isinstance(provider_for_account(None, account), MicrosoftCalendarProvider)


# ================================================================= oauth helpers

def test_build_authorize_url_has_all_required_params():
    url = build_authorize_url("STATE-XYZ")
    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    for frag in ["client_id=", "response_type=code", "response_mode=query", "state=STATE-XYZ"]:
        assert frag in url
    assert "offline_access" in url and "Calendars.ReadWrite" in url  # scopes
    assert "redirect_uri=http" in url


def test_build_authorize_url_requires_config(monkeypatch):
    monkeypatch.setattr(ms_auth, "MS_CLIENT_ID", "")
    with pytest.raises(RuntimeError):
        build_authorize_url("s")


def test_refresh_token_is_noop_when_still_valid():
    out, refreshed = refresh_token(LIVE_TOKEN)
    assert refreshed is False
    assert out == LIVE_TOKEN


def test_refresh_token_carries_refresh_token_forward_when_omitted(monkeypatch):
    # Microsoft may omit refresh_token on refresh — the old one must be kept.
    monkeypatch.setattr(ms_auth.httpx, "post",
                        lambda url, data=None, timeout=None: FakeResp({"access_token": "NEW", "expires_in": 3600}))
    expired = '{"access_token":"OLD","refresh_token":"KEEPME","expires_at":0}'
    out, refreshed = refresh_token(expired)
    assert refreshed is True
    data = json.loads(out)
    assert data["access_token"] == "NEW"
    assert data["refresh_token"] == "KEEPME"  # carried forward, account survives
    assert data["expires_at"] > 0


def test_exchange_code_packs_the_token(monkeypatch):
    def fake_post(url, data=None, timeout=None):
        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "code123"
        return FakeResp({"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})

    monkeypatch.setattr(ms_auth.httpx, "post", fake_post)
    d = json.loads(exchange_code("code123"))
    assert d["access_token"] == "AT" and d["refresh_token"] == "RT" and d["expires_at"] > 0


def test_get_account_email_uses_upn_not_the_spoofable_mail(monkeypatch):
    # `mail` is attacker-settable on work tenants (nOAuth); userPrincipalName is
    # the verified identity and must win even when mail is present.
    monkeypatch.setattr(ms_auth.httpx, "get", lambda *a, **k: FakeResp(
        {"mail": "spoofed@victim.com", "userPrincipalName": "Real@Tenant.com"}))
    assert get_account_email(LIVE_TOKEN) == "real@tenant.com"


def test_get_account_email_raises_without_upn(monkeypatch):
    monkeypatch.setattr(ms_auth.httpx, "get",
                        lambda *a, **k: FakeResp({"mail": "x@y.com"}))  # mail alone is not trusted
    with pytest.raises(RuntimeError):
        get_account_email(LIVE_TOKEN)


# ======================================================================= routes

@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    return TestClient(app, follow_redirects=False)


def test_microsoft_login_redirects_with_state_cookie(client):
    r = client.get("/auth/microsoft/login")
    assert r.status_code == 307
    issued = r.cookies.get(STATE_COOKIE)
    assert issued, "login must pin a state cookie for the callback to verify"
    assert r.headers["location"].startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
    assert f"state={issued}" in r.headers["location"]  # same state travels to MS


def test_microsoft_callback_without_state_is_refused(client):
    r = client.get("/auth/microsoft/callback", params={"code": "x"})
    assert r.status_code == 400


def test_microsoft_callback_with_mismatched_state_is_refused(client):
    client.cookies.set(STATE_COOKIE, "real")
    r = client.get("/auth/microsoft/callback", params={"code": "x", "state": "forged"})
    assert r.status_code == 400


def test_microsoft_callback_happy_path_signs_in(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "exchange_code", lambda code: LIVE_TOKEN)
    monkeypatch.setattr(auth_routes, "ms_get_account_email", lambda tj: "person@out.com")
    client.cookies.set(STATE_COOKIE, "S1")
    r = client.get("/auth/microsoft/callback", params={"code": "c", "state": "S1"})
    assert r.status_code == 307
    assert "nudgy_session" in r.headers["set-cookie"]
    assert 'nudgy_oauth_state=""' in r.headers["set-cookie"]  # state burned
