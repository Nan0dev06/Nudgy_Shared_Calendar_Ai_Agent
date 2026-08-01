"""HTTP surface for async voting: PATCH /plans/{id} (deadline + the minimum),
what voting does once a deadline has passed, and the convergence that fires on
the vote itself (app/api/plan_routes.py).

Rewritten for the 2026-08-01 engine. `auto_book` is gone: a poll converges when
a time clears its bar and nobody is left to answer, and the bar is the
all-members RULE unless the creator typed a number.
"""
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
SLOT2 = (datetime(2026, 8, 1, 19, tzinfo=timezone.utc),
         datetime(2026, 8, 1, 20, tzinfo=timezone.utc))


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


def _create(client, group_id, slots=(SLOT,), **extra):
    body = {"title": "Coffee", "location": "Cafe",
            "slots": [{"start_iso": s.isoformat(), "end_iso": e.isoformat()}
                      for s, e in slots]}
    body.update(extra)
    r = client.post(f"/groups/{group_id}/plans", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _vote(client, plan_id, round_id, answer="yes"):
    return client.post(f"/plans/{plan_id}/time-vote",
                       json={"round_id": round_id, "answer": answer})


# ----------------------------------------------------------------- creating

def test_a_poll_can_be_created_with_a_deadline_and_a_minimum(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"],
                   deadline_iso=_iso(timedelta(hours=6)), expected_count=2)
    assert plan["minimum"] == 2
    assert plan["requires_all_members"] is False
    assert plan["deadline_iso"] is not None
    assert plan["voting_open"] is True


def test_a_poll_defaults_to_needing_every_member(ctx):
    """Not a number the app guessed — a rule. Only a human typing a count makes
    a partial booking possible."""
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"])
    assert plan["deadline_iso"] is None
    assert plan["requires_all_members"] is True
    assert plan["minimum"] == 2          # shown as a number, but means "those two"


def test_a_deadline_in_the_past_is_rejected(ctx):
    client, _, ids, _ = ctx
    r = client.post(f"/groups/{ids['group']}/plans", json={
        "title": "Coffee", "slots": [], "deadline_iso": _iso(timedelta(hours=-1)),
    })
    assert r.status_code == 400
    assert "future" in r.json()["detail"]


def test_a_poll_with_times_does_not_ask_the_interest_question(ctx):
    """Quick / Pick-a-time: a yes on any time IS the interest signal, so asking
    separately was the redundant tap the redesign removed."""
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"])
    assert plan["asks_interest"] is False
    assert plan["mode"] == "quick"
    r = client.post(f"/plans/{plan['id']}/interest", json={"yes": True})
    assert r.status_code == 400


def test_a_poll_with_no_times_is_a_float_and_does_ask(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"], slots=())
    assert plan["asks_interest"] is True
    assert plan["mode"] == "float"
    assert client.post(f"/plans/{plan['id']}/interest", json={"yes": True}).status_code == 200


def test_several_times_make_it_a_pick_a_time(ctx):
    client, _, ids, _ = ctx
    assert _create(client, ids["group"], slots=(SLOT, SLOT2))["mode"] == "pick_a_time"


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


def test_setting_a_minimum_leaves_the_deadline_alone(ctx):
    client, _, ids, _ = ctx
    plan = _create(client, ids["group"], deadline_iso=_iso(timedelta(hours=6)))
    r = client.patch(f"/plans/{plan['id']}", json={"minimum": 1})
    assert r.json()["minimum"] == 1
    assert r.json()["requires_all_members"] is False
    assert r.json()["deadline_iso"] == plan["deadline_iso"]


def test_a_member_cannot_change_the_poll_settings(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    current["id"] = ids["amy"]
    r = client.patch(f"/plans/{plan['id']}", json={"minimum": 1})
    assert r.status_code == 403


def test_lowering_the_minimum_books_an_already_complete_poll(ctx):
    """The votes were already in; the bar was the only thing in the way. Making
    the host wait for a vote that will never come would be pointless."""
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    round_id = plan["times"][0]["round_id"]
    _vote(client, plan["id"], round_id, "yes")          # host yes
    current["id"] = ids["amy"]
    _vote(client, plan["id"], round_id, "no")           # amy can't — nothing qualifies
    current["id"] = ids["host"]

    body = client.patch(f"/plans/{plan['id']}", json={"minimum": 1}).json()

    assert body["status"] == "booked"


# ------------------------------------------------------- after the deadline

def _expire(TS, plan_id):
    """Push a poll's deadline into the past, the way the clock would."""
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
    r = _vote(client, plan["id"], plan["times"][0]["round_id"])
    assert r.status_code == 400
    assert "deadline" in r.json()["detail"]


def test_an_expired_poll_can_still_be_locked_in_by_its_host(ctx):
    """The deadline closes VOTING, not the host's ability to act on the votes
    that did arrive."""
    client, current, ids, TS = ctx
    plan = _create(client, ids["group"])
    round_id = plan["times"][0]["round_id"]
    _vote(client, plan["id"], round_id)
    s = TS()
    repo.set_plan_status(s, repo.get_plan(s, plan["id"]), "expired")
    s.close()

    r = client.post(f"/plans/{plan['id']}/lock-in", json={"round_id": round_id})

    assert r.status_code == 200
    assert r.json()["plan"]["status"] == "booked"


def test_a_fresh_deadline_reopens_an_expired_poll(ctx):
    client, current, ids, TS = ctx
    plan = _create(client, ids["group"])
    s = TS()
    repo.set_plan_status(s, repo.get_plan(s, plan["id"]), "expired")
    s.close()

    r = client.patch(f"/plans/{plan['id']}", json={"deadline_iso": _iso(timedelta(days=1))})
    assert r.json()["status"] == "open"
    assert r.json()["voting_open"] is True

    current["id"] = ids["amy"]
    assert _vote(client, plan["id"], plan["times"][0]["round_id"]).status_code == 200


# ----------------------------------------------------------------- converging

def test_the_last_answer_books_the_poll_when_everyone_is_in(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    round_id = plan["times"][0]["round_id"]

    _vote(client, plan["id"], round_id)
    current["id"] = ids["amy"]
    body = _vote(client, plan["id"], round_id).json()

    assert body["status"] == "booked"


def test_if_needed_counts_when_it_has_to(ctx):
    """"I can make this work, I'd rather not" is still an answer the group can
    be booked on — it is what distinguishes inconvenient from impossible."""
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    round_id = plan["times"][0]["round_id"]

    _vote(client, plan["id"], round_id, "yes")
    current["id"] = ids["amy"]
    body = _vote(client, plan["id"], round_id, "if_needed").json()

    assert body["status"] == "booked"


def test_one_no_keeps_the_decision_with_the_host(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    round_id = plan["times"][0]["round_id"]

    _vote(client, plan["id"], round_id, "yes")
    current["id"] = ids["amy"]
    body = _vote(client, plan["id"], round_id, "no").json()

    assert body["status"] == "open"


def test_a_silent_member_keeps_the_decision_with_the_host(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    body = _vote(client, plan["id"], plan["times"][0]["round_id"]).json()
    # amy hasn't answered, so "everyone is in" isn't true yet
    assert body["status"] == "open"


def test_a_met_minimum_still_waits_while_anyone_is_pending(ctx):
    """The guard is "nobody left to answer", not merely "the bar is met" — else
    a lowered bar books on the early replies while the rest are asleep."""
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], expected_count=1)
    body = _vote(client, plan["id"], plan["times"][0]["round_id"]).json()
    assert body["status"] == "open"


def test_answering_only_one_of_two_times_does_not_finish_the_poll(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], slots=(SLOT, SLOT2))
    first, second = [t["round_id"] for t in plan["times"]]

    _vote(client, plan["id"], first)
    _vote(client, plan["id"], second)
    current["id"] = ids["amy"]
    body = _vote(client, plan["id"], first).json()   # amy still owes the second

    assert body["status"] == "open"


def test_the_winning_time_is_the_one_everyone_can_make(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], slots=(SLOT, SLOT2))
    first, second = [t["round_id"] for t in plan["times"]]

    _vote(client, plan["id"], first, "no")
    _vote(client, plan["id"], second, "yes")
    current["id"] = ids["amy"]
    _vote(client, plan["id"], first, "yes")
    body = _vote(client, plan["id"], second, "yes").json()

    assert body["status"] == "booked"
    booked = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert booked["status"] == "booked"


def test_a_vote_reports_back_what_this_member_said(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"], slots=(SLOT, SLOT2))
    first = plan["times"][0]["round_id"]
    body = _vote(client, plan["id"], first, "if_needed").json()
    times = {t["round_id"]: t for t in body["times"]}
    assert times[first]["my_answer"] == "if_needed"


def test_an_unknown_vote_state_is_rejected(ctx):
    client, current, ids, _ = ctx
    plan = _create(client, ids["group"])
    r = _vote(client, plan["id"], plan["times"][0]["round_id"], "maybe-ish")
    assert r.status_code == 400
