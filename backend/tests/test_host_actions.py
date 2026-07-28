"""Deterministic host-action endpoints: POST /plans/{id}/lock-in and /next-time.
They enforce host-only access and drive the same plan_service moves the agent
uses, but without routing a booking through the LLM (app/api/plan_routes.py)."""
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
    ids = {"host": host.id, "other": other.id, "plan": plan.id}
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


def test_next_time_advances_to_the_next_candidate(ctx):
    client, current, ids, _ = ctx
    r = client.post(f"/plans/{ids['plan']}/next-time")
    assert r.status_code == 200
    assert r.json()["action"] == "next_time"


def test_next_time_forbidden_for_non_host(ctx):
    client, current, ids, _ = ctx
    current["id"] = ids["other"]  # act as a plain member
    r = client.post(f"/plans/{ids['plan']}/next-time")
    assert r.status_code == 403


def test_action_on_missing_plan_is_404(ctx):
    client, current, ids, _ = ctx
    r = client.post("/plans/999999/lock-in")
    assert r.status_code == 404


def test_lock_in_refuses_when_nobody_said_yes(ctx):
    client, current, ids, _ = ctx
    r = client.post(f"/plans/{ids['plan']}/lock-in")
    assert r.status_code == 400
    assert "nobody" in r.json()["detail"].lower()


def test_a_calendar_failure_is_a_502_not_a_400(ctx, monkeypatch):
    """A refused booking is a retry-able upstream problem, not a bad request —
    and the round goes back on the table so the host CAN retry."""
    client, current, ids, TS = ctx
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    repo.cast_time_vote(s, repo.get_active_round(s, plan), s.get(User, ids["host"]), True)
    s.close()
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"error": "Host has no connected calendar."},
    )

    r = client.post(f"/plans/{ids['plan']}/lock-in")

    assert r.status_code == 502
    assert "calendar" in r.json()["detail"].lower()
    s = TS()
    assert repo.get_active_round(s, repo.get_plan(s, ids["plan"])) is not None
    s.close()


def test_lock_in_books_the_yes_voters(ctx, monkeypatch):
    client, current, ids, TS = ctx
    # host votes the active time works, then booking is stubbed (no Google)
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    host = s.get(User, ids["host"])
    active = repo.get_active_round(s, plan)
    repo.cast_time_vote(s, active, host, True)
    s.close()

    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
    )
    r = client.post(f"/plans/{ids['plan']}/lock-in")
    assert r.status_code == 200
    assert r.json()["action"] == "booked"
    assert r.json()["plan"]["status"] == "scheduled"
