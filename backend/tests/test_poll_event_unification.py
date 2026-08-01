"""A booked poll becomes a real group event (docs/poll-edit-redesign.md §2).

Before this, a booked `Plan` and a `GroupEvent` were separate worlds and
`EventRsvp` explicitly excluded poll bookings — which is why a booked poll had
no edit path at all: no attendance record to reset, no row for `update_event` to
act on. These tests pin the unification down:

  - locking in produces an event carrying the poll's title, place and time
  - the yes / if-needed voters arrive as `going` RSVPs, because the vote already
    was their consent
  - people who said no, or never answered, are NOT on it
  - guests get no RSVP row (they have no user), but the booking still succeeds
  - a retried booking doesn't mint a second event for the same poll
  - the resulting event edits like any other, resetting attendance (§3)
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import plan_routes
from app.api.deps import get_current_user
from app.db.models import Base, GroupEvent, User
from app.db import repo
from app.db.session import get_session
from app.tools.plan_rules import IF_NEEDED, NO, YES

DAY = datetime(2026, 7, 20, 17, tzinfo=timezone.utc)
BOOKED_OK = {"booked": True, "event_link": "http://cal/x", "event_id": "gcal-1"}


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr("app.tools.booking.book_round_event", lambda *a, **k: BOOKED_OK)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    s = TS()
    host = User(email="host@x.com")
    yes_voter = User(email="yes@x.com")
    maybe_voter = User(email="maybe@x.com")
    no_voter = User(email="no@x.com")
    silent = User(email="silent@x.com")
    s.add_all([host, yes_voter, maybe_voter, no_voter, silent])
    s.commit()
    group = repo.create_group(s, "Crew", host)
    for u in (yes_voter, maybe_voter, no_voter, silent):
        repo.add_member(s, group, u)
    plan = repo.create_plan(
        s, group, host, title="Karaoke", location="Cheers",
        slots=[(DAY, DAY.replace(hour=20))],
    )
    ids = {"host": host.id, "yes": yes_voter.id, "maybe": maybe_voter.id,
           "no": no_voter.id, "silent": silent.id,
           "plan": plan.id, "round": plan.rounds[0].id, "group": group.id}
    s.close()

    app = FastAPI()
    app.include_router(plan_routes.router)
    current = {"id": ids["host"]}
    app.dependency_overrides[get_current_user] = lambda: TS().get(User, current["id"])

    def override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_session
    return TestClient(app), current, ids, TS


def _vote(TS, ids, who, answer):
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    repo.cast_time_vote(s, plan.rounds[0], s.get(User, ids[who]), answer)
    s.close()


def _event_of(TS, plan_id):
    s = TS()
    try:
        return s.scalar(select(GroupEvent).where(GroupEvent.plan_id == plan_id))
    finally:
        s.close()


def _rsvps(TS, event_id):
    """email -> status for the booked event."""
    s = TS()
    try:
        ev = repo.get_event(s, event_id)
        return {s.get(User, r.user_id).email: r.status for r in ev.rsvps}
    finally:
        s.close()


def test_locking_in_creates_the_group_event(ctx):
    client, current, ids, TS = ctx
    _vote(TS, ids, "yes", YES)

    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["round"]})
    assert r.status_code == 200

    ev = _event_of(TS, ids["plan"])
    assert ev is not None
    assert ev.title == "Karaoke"
    assert ev.location == "Cheers"
    assert ev.kind == "event"
    assert ev.start == DAY and ev.end == DAY.replace(hour=20)
    assert ev.created_by == ids["host"]
    # a poll booking is the group's business: never personal, never anonymous
    assert ev.personal is False and ev.anonymous is False
    # and it carries the calendar identity, so edits can propagate
    assert ev.synced is True and ev.gcal_event_id == "gcal-1"


def test_attendees_are_the_people_who_said_they_could_make_it(ctx):
    """yes and if-needed come; no and silence do not. The vote WAS the consent,
    so they arrive already `going` rather than being asked twice."""
    client, current, ids, TS = ctx
    _vote(TS, ids, "yes", YES)
    _vote(TS, ids, "maybe", IF_NEEDED)
    _vote(TS, ids, "no", NO)
    # `silent` never answers

    client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["round"]})

    ev = _event_of(TS, ids["plan"])
    assert _rsvps(TS, ev.id) == {"yes@x.com": "going", "maybe@x.com": "going"}


def test_a_guest_does_not_get_an_rsvp_row(ctx):
    """A guest has no user, and EventRsvp.user_id is a real FK. They already got
    their invite from the booking; what they don't get is a seat in the group's
    attendance record — they aren't in the group."""
    client, current, ids, TS = ctx
    _vote(TS, ids, "yes", YES)
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    guest = repo.create_guest(s, plan, "Rami", "rami@x.com")
    repo.cast_guest_time_vote(s, plan.rounds[0], guest, YES)
    s.close()

    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["round"]})
    assert r.status_code == 200

    ev = _event_of(TS, ids["plan"])
    assert _rsvps(TS, ev.id) == {"yes@x.com": "going"}  # the guest label is not a member


def test_booking_twice_does_not_make_two_events(ctx):
    """Booking is retried after calendar failures, and two events for one poll
    would be worse than none."""
    client, current, ids, TS = ctx
    _vote(TS, ids, "yes", YES)
    client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["round"]})

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    round_ = plan.rounds[0]
    first = repo.create_event_from_booking(s, plan, round_, ["yes@x.com"])
    again = repo.create_event_from_booking(s, plan, round_, ["yes@x.com"])
    assert first.id == again.id
    assert s.scalars(
        select(GroupEvent).where(GroupEvent.plan_id == ids["plan"])
    ).all().__len__() == 1
    s.close()


def test_the_booked_event_can_then_be_edited(ctx):
    """The point of §2: a booked poll finally HAS an edit path, and using it
    resets the attendance the vote produced (§3)."""
    from app.api import event_routes

    client, current, ids, TS = ctx
    _vote(TS, ids, "yes", YES)
    client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["round"]})
    ev = _event_of(TS, ids["plan"])
    assert _rsvps(TS, ev.id)["yes@x.com"] == "going"

    app = FastAPI()
    app.include_router(event_routes.router)
    app.dependency_overrides[get_current_user] = lambda: TS().get(User, ids["host"])

    def override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_session
    ec = TestClient(app)

    r = ec.patch(f"/events/{ev.id}", json={"location": "Somewhere else"})
    assert r.status_code == 200
    assert r.json()["needs_reconfirm"] == ["yes@x.com"]
    assert _rsvps(TS, ev.id)["yes@x.com"] == "needs_reconfirm"
