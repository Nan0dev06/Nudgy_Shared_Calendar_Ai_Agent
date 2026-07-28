"""The background job that makes voting async (app/jobs/plan_ticker.py).

run_tick takes an explicit `now`, so these tests move a plan through days of
life without sleeping. Email lands in a MemoryEmailSender outbox and booking is
stubbed — no Google, no clock, no waiting.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, User
from app.db import repo
from app.jobs.plan_ticker import run_tick
from app.mailer import set_email_sender
from app.mailer.console import ConsoleEmailSender
from app.mailer.memory import MemoryEmailSender

NOW = datetime(2026, 7, 20, 9, tzinfo=timezone.utc)
SLOT = (datetime(2026, 7, 25, 17, tzinfo=timezone.utc),
        datetime(2026, 7, 25, 18, tzinfo=timezone.utc))
INTERVAL = timedelta(hours=12)


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)
    s = TS()
    host = User(email="host@x.com", timezone="UTC")
    amy, bo = User(email="amy@x.com", timezone="UTC"), User(email="bo@x.com", timezone="UTC")
    s.add_all([host, amy, bo])
    s.commit()
    group = repo.create_group(s, "Crew", host)
    repo.add_member(s, group, amy)
    repo.add_member(s, group, bo)

    outbox = MemoryEmailSender()
    set_email_sender(outbox)
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
    )
    yield s, group, {"host": host, "amy": amy, "bo": bo}, outbox
    set_email_sender(ConsoleEmailSender())
    s.close()


def _plan(session, group, host, **kw):
    kw.setdefault("slots", [SLOT])
    plan = repo.create_plan(session, group, host, title="Coffee", location="Cafe", **kw)
    # created_at defaults to real now; pin it so the reminder math is deterministic
    plan.created_at = NOW
    session.commit()
    return plan


# ----------------------------------------------------------------- reminders

def test_quiet_plan_nudges_the_people_who_have_not_answered(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"])

    assert run_tick(session, NOW + timedelta(hours=1), INTERVAL)["reminded"] == 0
    counts = run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert counts["reminded"] == 1
    # everyone owes something: amy and bo the plan itself, the host the time
    # (creating a plan records your interest, not your availability)
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com", "bo@x.com"}
    assert "Are you in" in next(e for e in outbox.outbox if e.to == "amy@x.com").text
    assert "work for you" in next(e for e in outbox.outbox if e.to == "host@x.com").text


def test_the_same_plan_is_not_nudged_again_until_the_interval_passes(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"])

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)
    outbox.outbox.clear()
    run_tick(session, NOW + timedelta(hours=14), INTERVAL)
    assert outbox.outbox == []

    run_tick(session, NOW + timedelta(hours=26), INTERVAL)
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com", "bo@x.com"}


def test_a_member_who_answered_is_left_alone(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"])
    repo.cast_interest(session, plan, users["amy"], False)  # amy is out, she's done

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)
    assert "amy@x.com" not in {e.to for e in outbox.outbox}
    assert "bo@x.com" in {e.to for e in outbox.outbox}


def test_an_interested_member_is_nudged_about_the_time(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"])
    repo.cast_interest(session, plan, users["amy"], True)  # in, but silent on the time

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)
    amy_mail = next(e for e in outbox.outbox if e.to == "amy@x.com")
    assert "work for you" in amy_mail.text


def test_a_fully_answered_plan_is_never_nudged(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"])
    active = repo.get_active_round(session, plan)
    for u in (users["host"], users["amy"], users["bo"]):
        repo.cast_interest(session, plan, u, True)
        repo.cast_time_vote(session, active, u, True)

    assert run_tick(session, NOW + timedelta(days=3), INTERVAL)["reminded"] == 0
    assert outbox.outbox == []


# ----------------------------------------------------------------- deadlines

def test_the_deadline_closes_voting_and_tells_the_host(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["expired"] == 1
    session.refresh(plan)
    assert plan.status == "expired"
    assert [e.to for e in outbox.outbox] == ["host@x.com"]
    assert "Voting closed" in outbox.last.subject


def test_an_expired_plan_is_not_processed_twice(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))
    run_tick(session, NOW + timedelta(hours=3), INTERVAL)
    outbox.outbox.clear()

    assert run_tick(session, NOW + timedelta(hours=4), INTERVAL)["scanned"] == 0
    assert outbox.outbox == []


def test_the_deadline_books_an_auto_book_plan_for_its_yes_voters(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"],
                 deadline=NOW + timedelta(hours=2), auto_book=True)
    active = repo.get_active_round(session, plan)
    repo.cast_time_vote(session, active, users["host"], True)
    repo.cast_interest(session, plan, users["amy"], True)
    repo.cast_time_vote(session, active, users["amy"], True)
    # bo never answers — the deadline decides without him

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["booked"] == 1
    session.refresh(plan)
    assert plan.status == "scheduled"
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com"}
    assert "Booked" in outbox.last.subject


def test_auto_book_with_nobody_available_expires_instead_of_booking(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"],
                 deadline=NOW + timedelta(hours=2), auto_book=True)

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert (counts["booked"], counts["expired"]) == (0, 1)
    session.refresh(plan)
    assert plan.status == "expired"


def test_a_failed_deadline_booking_expires_rather_than_hanging(ctx, monkeypatch):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"],
                 deadline=NOW + timedelta(hours=2), auto_book=True)
    active = repo.get_active_round(session, plan)
    repo.cast_time_vote(session, active, users["host"], True)
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"booked": False, "error": "calendar said no"},
    )

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["expired"] == 1
    session.refresh(plan)
    assert plan.status == "expired"
    # the time went back on the table, so the host can retry the lock-in
    assert repo.get_active_round(session, plan) is not None


def test_one_broken_plan_does_not_stop_the_others(ctx, monkeypatch):
    session, group, users, outbox = ctx
    broken = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))
    healthy = _plan(session, group, users["host"], slots=[],
                    deadline=NOW + timedelta(hours=2))

    import app.jobs.plan_ticker as ticker
    real = ticker.resolve_deadline

    def flaky(session_, plan, now, tz):
        if plan.id == broken.id:
            raise RuntimeError("boom")
        return real(session_, plan, now, tz)

    monkeypatch.setattr(ticker, "resolve_deadline", flaky)
    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["scanned"] == 2
    assert counts["expired"] == 1
    session.refresh(broken)
    session.refresh(healthy)
    assert (broken.status, healthy.status) == ("open", "expired")
