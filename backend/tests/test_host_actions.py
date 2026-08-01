"""Deterministic host-action endpoints: POST /plans/{id}/lock-in, /spotlight,
and the member-scoped DELETE /plans/{id}/rounds/{round_id}.

They drive the same plan_service moves the agent uses, but without routing a
booking through the LLM (app/api/plan_routes.py). Rewritten for the 2026-08-01
engine: /next-time is gone, and lock-in names the time it books.
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import plan_routes
from app.api.deps import get_current_user
from app.db.models import Base, User
from app.db import repo
from app.db.session import get_session

DAY = datetime(2026, 7, 20, 17, tzinfo=timezone.utc)
BOOKED_OK = {"booked": True, "event_link": "http://cal/x", "event_id": "e1"}


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    s = TS()
    host, other = User(email="host@x.com"), User(email="a@x.com")
    s.add_all([host, other])
    s.commit()
    group = repo.create_group(s, "Crew", host)
    repo.add_member(s, group, other)
    plan = repo.create_plan(
        s, group, host, title="Coffee", location="Cafe",
        slots=[(DAY, DAY.replace(hour=18)), (DAY.replace(hour=19), DAY.replace(hour=20))],
    )
    ids = {"host": host.id, "other": other.id, "plan": plan.id,
           "first": plan.rounds[0].id, "second": plan.rounds[1].id}
    s.close()

    app = FastAPI()
    app.include_router(plan_routes.router)
    current = {"id": ids["host"]}

    def override_user():
        return TS().get(User, current["id"])

    def override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_session] = override_session
    return TestClient(app), current, ids, TS


def _vote(TS, ids, who, round_key, answer):
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    round_ = next(r for r in plan.rounds if r.id == ids[round_key])
    repo.cast_time_vote(s, round_, s.get(User, ids[who]), answer)
    s.close()


# ----------------------------------------------------------------- spotlight

def test_spotlight_marks_a_time_without_touching_any_vote(ctx):
    """The whole reason the spotlight replaced /next-time: the old move made
    every vote already cast irrelevant, so hosts avoided using it."""
    client, current, ids, TS = ctx
    _vote(TS, ids, "other", "first", "yes")

    r = client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["second"]})

    assert r.status_code == 200
    times = {t["round_id"]: t for t in r.json()["plan"]["times"]}
    assert times[ids["second"]]["spotlit"] is True
    assert times[ids["first"]]["yes"] == 1      # the earlier vote survived


def test_the_spotlight_can_be_moved_back(ctx):
    client, current, ids, TS = ctx
    client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["second"]})
    r = client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["first"]})
    times = {t["round_id"]: t for t in r.json()["plan"]["times"]}
    assert times[ids["first"]]["spotlit"] is True
    assert times[ids["second"]]["spotlit"] is False


def test_the_spotlight_can_be_cleared(ctx):
    client, current, ids, TS = ctx
    client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["second"]})
    r = client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": None})
    assert r.json()["plan"]["spotlight_round_id"] is None


def test_spotlight_says_out_loud_that_nothing_was_lost(ctx):
    """People assume a move like this wipes their answer, because the engine it
    replaced genuinely did."""
    client, current, ids, TS = ctx
    r = client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["first"]})
    assert "still counts" in r.json()["note"]


def test_spotlight_forbidden_for_non_host(ctx):
    client, current, ids, TS = ctx
    current["id"] = ids["other"]
    r = client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["first"]})
    assert r.status_code == 403


def test_spotlight_refuses_a_time_from_another_poll(ctx):
    client, current, ids, TS = ctx
    r = client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": 999999})
    assert r.status_code == 400


# ----------------------------------------------------------------- lock-in

def test_action_on_missing_plan_is_404(ctx):
    client, current, ids, TS = ctx
    r = client.post("/plans/999999/lock-in", json={"round_id": 1})
    assert r.status_code == 404


def test_lock_in_refuses_when_nobody_said_yes(ctx):
    client, current, ids, TS = ctx
    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["first"]})
    assert r.status_code == 400
    assert "nobody" in r.json()["detail"].lower()


def test_lock_in_books_the_time_the_host_named_not_the_spotlit_one(ctx, monkeypatch):
    """"Leaning toward" and "committing to" are different statements — the host
    can lock in a time that isn't the one they highlighted."""
    client, current, ids, TS = ctx
    client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": ids["first"]})
    _vote(TS, ids, "host", "second", "yes")
    monkeypatch.setattr("app.tools.booking.book_round_event", lambda *a, **k: BOOKED_OK)

    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["second"]})

    assert r.status_code == 200
    assert r.json()["action"] == "booked"
    assert r.json()["plan"]["status"] == "booked"
    # the spotlit time was the FIRST — this must have committed the second
    assert r.json()["round_id"] == ids["second"]


def test_lock_in_works_below_the_minimum(ctx, monkeypatch):
    """The bar governs what happens WITHOUT a human. Blocking the host from
    booking the people who CAN make it would be the app overruling them."""
    client, current, ids, TS = ctx
    _vote(TS, ids, "host", "first", "yes")   # 1 of 2 members
    monkeypatch.setattr("app.tools.booking.book_round_event", lambda *a, **k: BOOKED_OK)

    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["first"]})

    assert r.status_code == 200
    assert r.json()["action"] == "booked"


def test_lock_in_forbidden_for_non_host(ctx):
    client, current, ids, TS = ctx
    current["id"] = ids["other"]
    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["first"]})
    assert r.status_code == 403


def test_a_calendar_failure_is_a_502_not_a_400(ctx, monkeypatch):
    """A refused booking is a retry-able upstream problem, not a bad request —
    and the poll stays exactly as it was so the host CAN retry."""
    client, current, ids, TS = ctx
    _vote(TS, ids, "host", "first", "yes")
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"error": "Host has no connected calendar."},
    )

    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["first"]})

    assert r.status_code == 502
    assert "calendar" in r.json()["detail"].lower()
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    assert plan.status == "open"
    assert not any(r_.booked for r_ in plan.rounds)
    s.close()


# ------------------------------------------------- suggesting / removing times

def test_any_member_can_suggest_a_time(ctx):
    """[LOCKED] since 2026-07-25 as "members can propose alternative times", and
    host-only in code until the redesign."""
    client, current, ids, TS = ctx
    current["id"] = ids["other"]
    r = client.post(
        f"/plans/{ids['plan']}/rounds",
        json={"slots": [{"start_iso": "2026-07-20T21:00:00+00:00",
                         "end_iso": "2026-07-20T22:00:00+00:00"}]},
    )
    assert r.status_code == 200
    added = r.json()["times"][-1]
    assert added["suggested_by"] == "a@x.com"
    assert added["can_remove"] is True


def test_you_can_remove_a_time_you_suggested(ctx):
    client, current, ids, TS = ctx
    current["id"] = ids["other"]
    add = client.post(
        f"/plans/{ids['plan']}/rounds",
        json={"slots": [{"start_iso": "2026-07-20T21:00:00+00:00",
                         "end_iso": "2026-07-20T22:00:00+00:00"}]},
    )
    new_id = add.json()["times"][-1]["round_id"]

    r = client.delete(f"/plans/{ids['plan']}/rounds/{new_id}")

    assert r.status_code == 200
    assert new_id not in [t["round_id"] for t in r.json()["times"]]


def test_you_cannot_remove_somebody_elses_time(ctx):
    """Otherwise one member could quietly delete the option the group was
    converging on — and the votes cast on it."""
    client, current, ids, TS = ctx
    current["id"] = ids["other"]
    r = client.delete(f"/plans/{ids['plan']}/rounds/{ids['first']}")
    assert r.status_code == 403
    assert "suggested yourself" in r.json()["detail"]


def test_removing_a_time_clears_a_spotlight_that_pointed_at_it(ctx):
    client, current, ids, TS = ctx
    add = client.post(
        f"/plans/{ids['plan']}/rounds",
        json={"slots": [{"start_iso": "2026-07-20T21:00:00+00:00",
                         "end_iso": "2026-07-20T22:00:00+00:00"}]},
    )
    new_id = add.json()["times"][-1]["round_id"]
    client.post(f"/plans/{ids['plan']}/spotlight", json={"round_id": new_id})

    r = client.delete(f"/plans/{ids['plan']}/rounds/{new_id}")

    assert r.json()["spotlight_round_id"] is None


def test_removing_a_time_takes_its_votes_with_it(ctx):
    client, current, ids, TS = ctx
    add = client.post(
        f"/plans/{ids['plan']}/rounds",
        json={"slots": [{"start_iso": "2026-07-20T21:00:00+00:00",
                         "end_iso": "2026-07-20T22:00:00+00:00"}]},
    )
    new_id = add.json()["times"][-1]["round_id"]
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    round_ = next(r_ for r_ in plan.rounds if r_.id == new_id)
    repo.cast_time_vote(s, round_, s.get(User, ids["other"]), "yes")
    s.close()

    client.delete(f"/plans/{ids['plan']}/rounds/{new_id}")

    s = TS()
    assert repo.get_votes_by_time(s, repo.get_plan(s, ids["plan"])).get(new_id) is None
    s.close()
