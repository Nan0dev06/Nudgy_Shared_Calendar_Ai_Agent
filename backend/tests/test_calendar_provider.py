"""The CalendarProvider seam (Phase 1 Branch 2): factory dispatch, silent-token
refresh persistence, and the Google event-write body — all without touching the
network (the Google API service is faked).
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.calendars import provider_for_account
from app.calendars import google as gmod
from app.calendars.base import CalendarProvider, CreatedEvent
from app.calendars.google import GoogleCalendarProvider
from app.db import repo
from app.db.models import Base, CalendarAccount


@pytest.fixture
def Session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ------------------------------------------------------------ factory dispatch

def test_google_account_yields_google_provider(monkeypatch):
    monkeypatch.setattr(gmod, "credentials_from_json", lambda tj: ("CREDS", None))
    account = CalendarAccount(provider="google", external_email="a@x.com", token_json="{}")
    provider = provider_for_account(None, account)
    assert isinstance(provider, GoogleCalendarProvider)
    assert isinstance(provider, CalendarProvider)


def test_microsoft_account_yields_microsoft_provider():
    from app.calendars.microsoft import MicrosoftCalendarProvider
    # a non-expired token so from_account returns without any network refresh
    token = '{"access_token":"t","refresh_token":"r","expires_at":9999999999}'
    account = CalendarAccount(provider="microsoft", external_email="a@out.com", token_json=token)
    assert isinstance(provider_for_account(None, account), MicrosoftCalendarProvider)


def test_unknown_provider_raises():
    account = CalendarAccount(provider="apple", external_email="a@icloud.com", token_json="{}")
    with pytest.raises(ValueError):
        provider_for_account(None, account)


# --------------------------------------------------- silent refresh persistence

def test_from_account_persists_a_refreshed_token(Session, monkeypatch):
    monkeypatch.setattr(gmod, "credentials_from_json",
                        lambda tj: ("CREDS", '{"token":"REFRESHED"}'))
    with Session() as s:
        user = repo.login_with_google(s, "a@x.com", '{"token":"OLD"}')
        account = repo.get_primary_calendar_account(s, user)
        GoogleCalendarProvider.from_account(s, account)
        # the expired token was refreshed and written back to the account
        assert account.token_json == '{"token":"REFRESHED"}'


def test_from_account_leaves_a_live_token_untouched(Session, monkeypatch):
    monkeypatch.setattr(gmod, "credentials_from_json", lambda tj: ("CREDS", None))
    with Session() as s:
        user = repo.login_with_google(s, "a@x.com", '{"token":"LIVE"}')
        account = repo.get_primary_calendar_account(s, user)
        GoogleCalendarProvider.from_account(s, account)
        assert account.token_json == '{"token":"LIVE"}'


# ----------------------------------------------------------- Google event write

class _FakeExec:
    def __init__(self, result):
        self._result = result
    def execute(self):
        return self._result


class _FakeEvents:
    def __init__(self, sink):
        self.sink = sink
    def insert(self, calendarId, body, sendUpdates):
        self.sink["insert"] = dict(calendarId=calendarId, body=body, sendUpdates=sendUpdates)
        return _FakeExec({"id": "evt123", "htmlLink": "https://cal/evt123"})
    def patch(self, calendarId, eventId, body, sendUpdates):
        self.sink["patch"] = dict(calendarId=calendarId, eventId=eventId, body=body, sendUpdates=sendUpdates)
        return _FakeExec({"id": eventId, "htmlLink": "https://cal/" + eventId})
    def delete(self, calendarId, eventId, sendUpdates):
        self.sink["delete"] = dict(calendarId=calendarId, eventId=eventId, sendUpdates=sendUpdates)
        return _FakeExec({})


class _FakeService:
    def __init__(self, sink):
        self._events = _FakeEvents(sink)
    def events(self):
        return self._events


def _provider_with_fake_service():
    sink: dict = {}
    provider = GoogleCalendarProvider(creds=None)
    provider._service = _FakeService(sink)  # skip build(); no network
    return provider, sink


def test_create_event_builds_the_expected_body():
    provider, sink = _provider_with_fake_service()
    start = datetime(2026, 1, 5, 17, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, 19, tzinfo=timezone.utc)
    created = provider.create_event(
        summary="Coffee", start=start, end=end,
        attendee_emails=["a@x.com", "b@x.com"], location="BHive Cafe",
        description="Scheduled by Nudgy",
    )
    assert isinstance(created, CreatedEvent)
    assert created.id == "evt123"
    assert created.link == "https://cal/evt123"
    body = sink["insert"]["body"]
    assert sink["insert"]["calendarId"] == "primary"
    assert sink["insert"]["sendUpdates"] == "all"  # Google emails the invites
    assert body["summary"] == "Coffee"
    assert body["location"] == "BHive Cafe"
    assert body["attendees"] == [{"email": "a@x.com"}, {"email": "b@x.com"}]
    assert body["start"]["dateTime"].startswith("2026-01-05T17:00:00")


def test_create_event_omits_location_when_none():
    provider, sink = _provider_with_fake_service()
    now = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    provider.create_event(summary="Sync", start=now, end=now,
                          attendee_emails=[], description="x")
    assert "location" not in sink["insert"]["body"]


def test_delete_event_notifies_attendees():
    provider, sink = _provider_with_fake_service()
    provider.delete_event("evt123")
    assert sink["delete"] == dict(calendarId="primary", eventId="evt123", sendUpdates="all")
