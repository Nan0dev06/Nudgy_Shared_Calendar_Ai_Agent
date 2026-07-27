"""sync_setting enforcement: "none" opts a calendar out of OUTBOUND writes.

Reads (freebusy/availability) are unaffected — a "none" calendar still counts
toward busy time. Only the write paths honour it: booking a plan and syncing an
in-app event skip the calendar create when the target's primary is "none". The
group's decision still stands (the round is booked in Nudgy), just no calendar
event / Google invite goes out.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import event_routes
from app.db import repo
from app.db.models import Base, GroupEvent, User
from app.tools import booking

SECRET = '{"refresh_token": "r", "token": "t"}'
DAY = datetime(2026, 7, 20, 17, tzinfo=timezone.utc)


@pytest.fixture
def Session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class _FakeProvider:
    """Records create_event calls so a test can assert a write did/didn't happen."""
    def __init__(self):
        self.calls = []

    def create_event(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(id="evt1", link="http://cal/evt1")


# ---------------------------------------------------------- the policy predicate

def test_account_syncs_out_predicate(Session):
    with Session() as s:
        user = repo.login_with_google(s, "u@x.com", SECRET)
        acct = repo.get_primary_calendar_account(s, user)
        assert repo.account_syncs_out(acct) is True  # default two_way
        repo.set_account_sync_setting(s, acct, "one_way")
        assert repo.account_syncs_out(acct) is True
        repo.set_account_sync_setting(s, acct, "none")
        assert repo.account_syncs_out(acct) is False


# ------------------------------------------------------------------- booking

def _confirmed_plan(s, host):
    group = repo.create_group(s, "Crew", host)
    plan = repo.create_plan(
        s, group, host, title="Coffee", location="Cafe",
        slots=[(DAY, DAY.replace(hour=18))],
    )
    round_ = repo.get_active_round(s, plan)
    round_.status = "confirmed"
    s.commit()
    return plan, round_


def test_booking_skips_calendar_write_when_sync_none(Session, monkeypatch):
    with Session() as s:
        host = repo.login_with_google(s, "host@x.com", SECRET)
        repo.set_account_sync_setting(s, repo.get_primary_calendar_account(s, host), "none")
        plan, round_ = _confirmed_plan(s, host)

        # if the provider is built at all, that's a failed test — sync is off
        monkeypatch.setattr(
            booking, "provider_for_account",
            lambda *a, **k: pytest.fail("must not write to a sync=none calendar"),
        )
        out = booking.book_round_event(s, plan, round_, host, ["host@x.com"])

        assert out["booked"] is True
        assert out["sync_skipped"] is True
        assert out["event_link"] is None
        assert round_.booked is True
        assert round_.event_link is None


def test_booking_writes_when_sync_enabled(Session, monkeypatch):
    with Session() as s:
        host = repo.login_with_google(s, "host@x.com", SECRET)  # default two_way
        plan, round_ = _confirmed_plan(s, host)

        fake = _FakeProvider()
        monkeypatch.setattr(booking, "provider_for_account", lambda *a, **k: fake)
        out = booking.book_round_event(s, plan, round_, host, ["host@x.com"])

        assert out["booked"] is True
        assert out.get("sync_skipped") is None
        assert out["event_link"] == "http://cal/evt1"
        assert len(fake.calls) == 1  # one calendar event written
        assert round_.event_link == "http://cal/evt1"


# ---------------------------------------------------- in-app event sync path

def _group_event(s, creator):
    group = repo.create_group(s, "Crew", creator)
    event = repo.create_event(
        s, group_id=group.id, created_by=creator.id, kind="event", title="Lunch",
        category="Event", location="Cafe", start_utc=DAY, end_utc=DAY.replace(hour=18),
        personal=False, anonymous=False,
    )
    return group, event


def test_event_sync_blocked_when_sync_none(Session, monkeypatch):
    with Session() as s:
        creator = repo.login_with_google(s, "c@x.com", SECRET)
        repo.set_account_sync_setting(s, repo.get_primary_calendar_account(s, creator), "none")
        group, event = _group_event(s, creator)

        monkeypatch.setattr(
            event_routes, "provider_for_account",
            lambda *a, **k: pytest.fail("must not sync to a sync=none calendar"),
        )
        out = event_routes._sync_to_google(s, event, creator, [], group.id)
        assert out["ok"] is False
        assert "off" in out["reason"].lower()
        assert event.synced is False


def test_event_sync_writes_when_sync_enabled(Session, monkeypatch):
    with Session() as s:
        creator = repo.login_with_google(s, "c@x.com", SECRET)  # default two_way
        group, event = _group_event(s, creator)

        fake = _FakeProvider()
        monkeypatch.setattr(event_routes, "provider_for_account", lambda *a, **k: fake)
        out = event_routes._sync_to_google(s, event, creator, [], group.id)
        assert out["ok"] is True
        assert len(fake.calls) == 1
        assert event.synced is True
