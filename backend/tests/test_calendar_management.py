"""Calendar-account management endpoints (identity/calendar split, Phase 1 FE).

A user can connect several calendars (Google/Outlook). These endpoints manage the
set: list, set colour, set sync mode (stored only for now), promote one to primary
(writable), and disconnect. Ownership is enforced — one user can never touch
another's calendar. The OAuth *connect* flow that adds a calendar reuses
repo.upsert_calendar_account, whose "attach to this user, first-connected =
primary" behaviour is asserted here too.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth_routes import router
from app.api.deps import COOKIE_NAME, make_session_cookie
from app.db import repo
from app.db.models import Base
from app.db.session import get_session

SECRET = '{"refresh_token": "super-secret-value", "token": "abc"}'


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    client = TestClient(app, follow_redirects=False)
    return client, Session


def _seed_user_with_google(Session, email="sam@x.com"):
    """A verified user with one connected Google calendar (the primary)."""
    with Session() as s:
        user = repo.login_with_google(s, email, SECRET)
        return user.id


def _auth(client, user_id):
    client.cookies.set(COOKIE_NAME, make_session_cookie(user_id))


# --------------------------------------------------------------------- list

def test_list_returns_connected_calendars(ctx):
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)

    r = client.get("/auth/me/calendars")
    assert r.status_code == 200
    cals = r.json()
    assert len(cals) == 1
    assert cals[0]["provider"] == "google"
    assert cals[0]["external_email"] == "sam@x.com"
    assert cals[0]["is_primary"] is True
    assert cals[0]["sync_setting"] == "two_way"  # v1 default
    assert cals[0]["color"] is None


def test_list_requires_auth(ctx):
    client, _ = ctx
    assert client.get("/auth/me/calendars").status_code == 401


# --------------------------------------------------------------- color / sync

def test_patch_color_and_sync(ctx):
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    r = client.patch(
        f"/auth/me/calendars/{cal_id}",
        json={"color": "#DCA744", "sync_setting": "one_way"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["color"] == "#DCA744"
    assert body["sync_setting"] == "one_way"
    # and it persists
    assert client.get("/auth/me/calendars").json()[0]["sync_setting"] == "one_way"


def test_patch_rejects_unknown_sync_setting(ctx):
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    r = client.patch(f"/auth/me/calendars/{cal_id}", json={"sync_setting": "sideways"})
    assert r.status_code == 400


def test_patch_empty_color_clears_it(ctx):
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    client.patch(f"/auth/me/calendars/{cal_id}", json={"color": "#111111"})
    r = client.patch(f"/auth/me/calendars/{cal_id}", json={"color": ""})
    assert r.json()["color"] is None


# ------------------------------------------------------------------- primary

def _seed_user_with_two(Session, email="sam@x.com"):
    with Session() as s:
        user = repo.login_with_google(s, email, SECRET)
        repo.upsert_calendar_account(s, user, "microsoft", "sam@outlook.com", SECRET)
        return user.id


def test_make_primary_switches_exactly_one(ctx):
    client, Session = ctx
    uid = _seed_user_with_two(Session)
    _auth(client, uid)
    cals = client.get("/auth/me/calendars").json()
    ms = next(c for c in cals if c["provider"] == "microsoft")
    assert ms["is_primary"] is False

    r = client.patch(f"/auth/me/calendars/{ms['id']}", json={"is_primary": True})
    assert r.status_code == 200

    cals = client.get("/auth/me/calendars").json()
    assert sum(c["is_primary"] for c in cals) == 1
    assert next(c for c in cals if c["provider"] == "microsoft")["is_primary"] is True
    assert next(c for c in cals if c["provider"] == "google")["is_primary"] is False


# ---------------------------------------------------------------- disconnect

def test_disconnect_removes_and_promotes_survivor(ctx):
    client, Session = ctx
    uid = _seed_user_with_two(Session)
    _auth(client, uid)
    cals = client.get("/auth/me/calendars").json()
    google = next(c for c in cals if c["provider"] == "google")  # the primary
    assert google["is_primary"] is True

    r = client.delete(f"/auth/me/calendars/{google['id']}")
    assert r.status_code == 200

    cals = client.get("/auth/me/calendars").json()
    assert len(cals) == 1
    assert cals[0]["provider"] == "microsoft"
    assert cals[0]["is_primary"] is True  # survivor promoted


def test_disconnect_last_calendar_is_allowed(ctx):
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    assert client.delete(f"/auth/me/calendars/{cal_id}").status_code == 200
    assert client.get("/auth/me/calendars").json() == []


# ---------------------------------------------------------------- ownership

def test_cannot_touch_another_users_calendar(ctx):
    client, Session = ctx
    victim = _seed_user_with_google(Session, email="victim@x.com")
    attacker = _seed_user_with_google(Session, email="attacker@x.com")
    # the victim's calendar id
    _auth(client, victim)
    victim_cal = client.get("/auth/me/calendars").json()[0]["id"]

    _auth(client, attacker)
    assert client.patch(f"/auth/me/calendars/{victim_cal}", json={"color": "#000"}).status_code == 404
    assert client.delete(f"/auth/me/calendars/{victim_cal}").status_code == 404
    # and the victim's calendar is untouched
    _auth(client, victim)
    assert client.get("/auth/me/calendars").json()[0]["color"] is None


# ------------------------------------------------- inbound sync: titles opt-in

def _mirror_row(Session, uid, account_id, title="Dentist"):
    """One mirrored external event, as inbound sync would have written it.

    An upsert, like the real thing: a purge nulls the title and leaves the row,
    so re-syncing has to find it rather than insert a second copy.
    """
    from datetime import datetime, timedelta, timezone

    from app.db.models import ExternalEvent

    start = datetime(2026, 8, 3, 9, tzinfo=timezone.utc)
    with Session() as s:
        row = s.query(ExternalEvent).filter_by(user_id=uid, external_id="evt-1").first()
        if row is None:
            row = ExternalEvent(
                user_id=uid, account_id=account_id, calendar_id="primary",
                external_id="evt-1", start_utc=start,
                end_utc=start + timedelta(hours=1), updated_at=start,
            )
            s.add(row)
        row.title = title
        s.commit()


def _titles(Session, uid):
    from app.db.models import ExternalEvent

    with Session() as s:
        return [e.title for e in s.query(ExternalEvent).filter_by(user_id=uid)]


def test_titles_are_off_by_default(ctx):
    """An opt-in that arrives switched on is not an opt-in (v1-decisions #5)."""
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    assert client.get("/auth/me/calendars").json()[0]["read_titles"] is False


def test_turning_titles_on_and_off_through_the_api(ctx):
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    r = client.patch(f"/auth/me/calendars/{cal_id}", json={"read_titles": True})
    assert r.status_code == 200 and r.json()["read_titles"] is True
    assert client.get("/auth/me/calendars").json()[0]["read_titles"] is True


def test_turning_titles_off_purges_them_unless_the_user_keeps_them(ctx):
    """The client asks the user; the answer rides on the same PATCH. Defaulting
    `keep_titles` to False means a client that forgets to ask errs towards
    deleting text somebody withdrew consent for."""
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal = client.get("/auth/me/calendars").json()[0]
    client.patch(f"/auth/me/calendars/{cal['id']}", json={"read_titles": True})
    _mirror_row(Session, uid, cal["id"])

    client.patch(f"/auth/me/calendars/{cal['id']}", json={"read_titles": False})
    assert _titles(Session, uid) == [None]

    # ...and the "keep them" answer is honoured
    client.patch(f"/auth/me/calendars/{cal['id']}", json={"read_titles": True})
    _mirror_row(Session, uid, cal["id"])  # re-synced with its title
    client.patch(
        f"/auth/me/calendars/{cal['id']}",
        json={"read_titles": False, "keep_titles": True},
    )
    assert _titles(Session, uid) == ["Dentist"]


def test_disconnect_deletes_the_mirror_unless_asked_to_keep_it(ctx):
    client, Session = ctx
    uid = _seed_user_with_two(Session)
    _auth(client, uid)
    cals = client.get("/auth/me/calendars").json()
    google = next(c for c in cals if c["provider"] == "google")
    _mirror_row(Session, uid, google["id"])

    r = client.delete(f"/auth/me/calendars/{google['id']}")
    assert r.json() == {"ok": True, "kept_events": False}
    assert _titles(Session, uid) == []


def test_disconnect_can_keep_the_mirror(ctx):
    """"Keep my events" has to survive the account row going away — which is why
    ExternalEvent hangs off the user with a nullable account_id."""
    client, Session = ctx
    uid = _seed_user_with_two(Session)
    _auth(client, uid)
    cals = client.get("/auth/me/calendars").json()
    google = next(c for c in cals if c["provider"] == "google")
    _mirror_row(Session, uid, google["id"])

    r = client.delete(f"/auth/me/calendars/{google['id']}?keep_events=true")
    assert r.json() == {"ok": True, "kept_events": True}
    assert _titles(Session, uid) == ["Dentist"]


def test_list_reports_per_calendar_sync_state(ctx):
    """One connection can carry several calendars that fail independently, so a
    stale token on the uni calendar says so by name instead of making the whole
    connection look broken."""
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    from app.db.models import CalendarSyncState

    with Session() as s:
        s.add(CalendarSyncState(
            account_id=cal_id, calendar_id="uni@x.com", name="Uni",
            error="HttpError: 403",
        ))
        s.commit()

    cal = client.get("/auth/me/calendars").json()[0]
    assert cal["calendars"] == [
        {"calendar_id": "uni@x.com", "name": "Uni", "synced_at": None,
         "error": "HttpError: 403"},
    ]


def test_sync_now_runs_the_same_job_the_ticker_does(ctx, monkeypatch):
    """The button must not be able to behave differently from the thing it is
    impatient with, so it calls run_sync narrowed to one account."""
    client, Session = ctx
    uid = _seed_user_with_google(Session)
    _auth(client, uid)
    cal_id = client.get("/auth/me/calendars").json()[0]["id"]

    seen = {}

    def fake_run_sync(session, account_id=None, **kwargs):
        seen["account_id"] = account_id
        return {"accounts": 1, "calendars": 1, "added": 2, "updated": 0,
                "deleted": 0, "failed": 0}

    import app.api.auth_routes as ar
    monkeypatch.setattr(ar, "run_sync", fake_run_sync)

    r = client.post(f"/auth/me/calendars/{cal_id}/sync")
    assert r.status_code == 200
    assert seen["account_id"] == cal_id
    assert r.json()["ok"] is True
    assert r.json()["counts"]["added"] == 2


def test_sync_now_is_ownership_checked(ctx):
    client, Session = ctx
    victim = _seed_user_with_google(Session, email="victim2@x.com")
    attacker = _seed_user_with_google(Session, email="attacker2@x.com")
    _auth(client, victim)
    victim_cal = client.get("/auth/me/calendars").json()[0]["id"]

    _auth(client, attacker)
    assert client.post(f"/auth/me/calendars/{victim_cal}/sync").status_code == 404


# ------------------------------- connect-flow core (upsert attaches to a user)

def test_connect_second_provider_attaches_and_keeps_primary(ctx):
    """The OAuth connect flow reuses upsert_calendar_account: a second calendar
    hangs off the SAME identity and does NOT steal primary from the first."""
    _, Session = ctx
    with Session() as s:
        user = repo.login_with_google(s, "sam@x.com", SECRET)
        repo.upsert_calendar_account(s, user, "microsoft", "sam@outlook.com", SECRET)
        accounts = repo.get_calendar_accounts(s, user)
        assert len(accounts) == 2
        primaries = [a for a in accounts if a.is_primary]
        assert len(primaries) == 1
        assert primaries[0].provider == "google"  # first connected stays primary
