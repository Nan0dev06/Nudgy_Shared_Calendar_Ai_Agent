"""HTTP surface for async voting: PATCH /plans/{id} (deadline + auto-book),
what voting does once a deadline has passed, and the unanimity auto-book that
fires on the vote itself (app/api/plan_routes.py)."""
from datetime import datetime, timedelta, timezone

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

SLOT = (datetime(2026, 8, 1, 17, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 18, tzinfo=timezone.utc))


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    s = TS()
    host, amy = User(email="host@x.com"), User(email="amy@x.com")
    s.add_all([host, amy])
    s.commit()
    group = repo.create_group(s, "Crew", host)
    repo.add_member(s, group, amy)
    ids = {"host": host.id, "amy": amy.id, "group": group.id}
    s.close()

    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
    )

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


def _create(client, group_id, **extra):
    body = {"title": "Coffee", "location": "Cafe",
            "slots": [{"start_iso": SLOT[0].isoformat(), "end_iso": SLOT[1].isoformat()}]}
    body.update(extra)
    r = client.post(f"/groups/{group_id}/plans", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------------------- creating

def test_a_plan_can_be_created_with_a_deadline_and_auto_book(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"],
                   deadline_iso=_iso(timedelta(hours=6)), auto_book=True)
    assert plan["auto_book"] is True
    assert plan["deadline_iso"] is not None
    assert plan["voting_open"] is True


def test_plans_still_work_with_neither(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"])
    assert (plan["deadline_iso"], plan["auto_book"]) == (None, False)


def test_a_deadline_in_the_past_is_rejected(ctx):
    client, _, ids, _ = ctx
    r = client.post(f"/groups/{ids['group']}/plans", json={
        "title": "Coffee", "slots": [], "deadline_iso": _iso(timedelta(hours=-1)),
    })
    assert r.status_code == 400
    assert "future" in r.json()["detail"]


# ----------------------------------------------------------------- patching

def test_the_host_can_add_a_deadline_later(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"])
    r = client.patch(f"/plans/{plan['id']}", json={"deadline_iso": _iso(timedelta(days=1))})
    assert r.status_code == 200
    assert r.json()["deadline_iso"] is not None


def test_the_host_can_clear_a_deadline(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"], deadline_iso=_iso(timedelta(hours=6)))
    r = client.patch(f"/plans/{plan['id']}", json={"deadline_iso": None})
    assert r.json()["deadline_iso"] is None


def test_toggling_auto_book_leaves_the_deadline_alone(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"], deadline_iso=_iso(timedelta(hours=6)))
    r = client.patch(f"/plans/{plan['id']}", json={"auto_book": True})
    assert r.json()["auto_book"] is True
    assert r.json()["deadline_iso"] == plan["deadline_iso"]


def test_a_member_cannot_change_the_plan_settings(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    current["id"] = ids["amy"]
    r = client.patch(f"/plans/{plan['id']}", json={"auto_book": True})
    assert r.status_code == 403


# ------------------------------------------------------- after the deadline

def _expire(TS, plan_id):
    """Push a plan's deadline into the past, the way the clock would."""
    s = TS()
    plan = repo.get_plan(s, plan_id)
    plan.deadline_utc = datetime.now(timezone.utc) - timedelta(minutes=1)
    s.commit()
    s.close()


def test_voting_is_refused_once_the_deadline_has_passed(ctx):
    """Even before the ticker has flipped the status — the instant the host set
    is the promise, not the moment the background job wakes up."""
    client, current, ids, TS = ctx
    plan = _create(client, ids["group"], deadline_iso=_iso(timedelta(hours=1)))
    _expire(TS, plan["id"])

    current["id"] = ids["amy"]
    r = client.post(f"/plans/{plan['id']}/interest", json={"yes": True})
    assert r.status_code == 400
    assert "deadline" in r.json()["detail"]


def test_an_expired_plan_can_still_be_locked_in_by_its_host(ctx, monkeypatch):
    client, current, ids, TS = ctx
    plan = _create(client, ids["group"])
    s = TS()
    p = repo.get_plan(s, plan["id"])
    repo.cast_time_vote(s, repo.get_active_round(s, p), s.get(User, ids["host"]), True)
    repo.set_plan_status(s, p, "expired")
    s.close()

    r = client.post(f"/plans/{plan['id']}/lock-in")
    assert r.status_code == 200
    assert r.json()["plan"]["status"] == "scheduled"


def test_an_expired_plan_cannot_have_a_new_time_put_up(ctx):
    client, current, ids, TS = ctx
    plan = _create(client, ids["group"])
    s = TS()
    repo.set_plan_status(s, repo.get_plan(s, plan["id"]), "expired")
    s.close()

    r = client.post(f"/plans/{plan['id']}/next-time")
    assert r.status_code == 400
    assert "Extend the deadline" in r.json()["detail"]


def test_a_fresh_deadline_reopens_an_expired_plan(ctx):
    client, current, ids, TS = ctx
    plan = _create(client, ids["group"])
    s = TS()
    repo.set_plan_status(s, repo.get_plan(s, plan["id"]), "expired")
    s.close()

    r = client.patch(f"/plans/{plan['id']}", json={"deadline_iso": _iso(timedelta(days=1))})
    assert r.json()["status"] == "open"
    assert r.json()["voting_open"] is True

    current["id"] = ids["amy"]
    assert client.post(f"/plans/{plan['id']}/interest", json={"yes": True}).status_code == 200


# ----------------------------------------------------------------- auto-book

def test_the_last_yes_books_a_unanimous_auto_book_plan(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], auto_book=True)
    round_id = plan["times"][0]["round_id"]

    client.post(f"/plans/{plan['id']}/time-vote", json={"yes": True, "round_id": round_id})
    current["id"] = ids["amy"]
    client.post(f"/plans/{plan['id']}/interest", json={"yes": True})
    body = client.post(f"/plans/{plan['id']}/time-vote",
                       json={"yes": True, "round_id": round_id}).json()

    assert body["status"] == "scheduled"
    # `booked` is flipped inside book_round_event, which is stubbed here — the
    # round reaching "confirmed" is what this path is responsible for
    assert body["times"][0]["status"] == "confirmed"


def test_one_no_keeps_the_decision_with_the_host(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], auto_book=True)
    round_id = plan["times"][0]["round_id"]

    client.post(f"/plans/{plan['id']}/time-vote", json={"yes": True, "round_id": round_id})
    current["id"] = ids["amy"]
    client.post(f"/plans/{plan['id']}/interest", json={"yes": True})
    body = client.post(f"/plans/{plan['id']}/time-vote",
                       json={"yes": False, "round_id": round_id}).json()

    assert body["status"] == "open"


def test_a_silent_member_keeps_the_decision_with_the_host(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], auto_book=True)
    round_id = plan["times"][0]["round_id"]
    body = client.post(f"/plans/{plan['id']}/time-vote",
                       json={"yes": True, "round_id": round_id}).json()
    # amy hasn't answered at all, so "everyone said yes" isn't true yet
    assert body["status"] == "open"


def test_without_the_opt_in_a_unanimous_plan_waits_for_the_host(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])  # auto_book off (the default)
    round_id = plan["times"][0]["round_id"]

    client.post(f"/plans/{plan['id']}/time-vote", json={"yes": True, "round_id": round_id})
    current["id"] = ids["amy"]
    client.post(f"/plans/{plan['id']}/interest", json={"yes": True})
    body = client.post(f"/plans/{plan['id']}/time-vote",
                       json={"yes": True, "round_id": round_id}).json()

    assert body["status"] == "open"


def test_turning_auto_book_on_books_an_already_unanimous_plan(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    round_id = plan["times"][0]["round_id"]
    client.post(f"/plans/{plan['id']}/time-vote", json={"yes": True, "round_id": round_id})
    current["id"] = ids["amy"]
    client.post(f"/plans/{plan['id']}/interest", json={"yes": True})
    client.post(f"/plans/{plan['id']}/time-vote", json={"yes": True, "round_id": round_id})

    current["id"] = ids["host"]
    body = client.patch(f"/plans/{plan['id']}", json={"auto_book": True}).json()
    assert body["status"] == "scheduled"
