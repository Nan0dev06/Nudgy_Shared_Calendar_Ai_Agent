"""The background job that makes voting async (app/jobs/plan_ticker.py).

run_tick takes an explicit `now`, so these tests move a poll through days of
life without sleeping. Email lands in a MemoryEmailSender outbox and booking is
stubbed — no Google, no clock, no waiting.

Rewritten for the 2026-08-01 engine: there is no auto_book flag, so what the
deadline does is decided by whether a time clears the poll's bar.
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
SLOT2 = (datetime(2026, 7, 25, 19, tzinfo=timezone.utc),
         datetime(2026, 7, 25, 20, tzinfo=timezone.utc))
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


def _all_say(session, plan, users, answer="yes", round_index=0):
    for u in users:
        repo.cast_time_vote(session, plan.rounds[round_index], u, answer)


# ----------------------------------------------------------------- reminders

def test_quiet_poll_nudges_the_people_who_have_not_answered(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"])

    assert run_tick(session, NOW + timedelta(hours=1), INTERVAL)["reminded"] == 0
    counts = run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert counts["reminded"] == 1
    # everyone owes an answer, the host included: creating a poll says nothing
    # about your own availability
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com", "bo@x.com"}


def test_the_same_poll_is_not_nudged_again_until_the_interval_passes(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"])

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)
    outbox.outbox.clear()
    run_tick(session, NOW + timedelta(hours=14), INTERVAL)
    assert outbox.outbox == []

    run_tick(session, NOW + timedelta(hours=26), INTERVAL)
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com", "bo@x.com"}


def test_a_member_who_answered_every_time_is_left_alone(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"])
    repo.cast_time_vote(session, plan.rounds[0], users["amy"], "no")   # answered, she's done

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert "amy@x.com" not in {e.to for e in outbox.outbox}
    assert "bo@x.com" in {e.to for e in outbox.outbox}


def test_answering_only_some_of_the_times_still_earns_a_nudge(ctx):
    """Times are answered independently, so a partial ballot is an unfinished
    one — the old engine could only ask about whichever time was active."""
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], slots=[SLOT, SLOT2])
    repo.cast_time_vote(session, plan.rounds[0], users["amy"], "yes")

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert "amy@x.com" in {e.to for e in outbox.outbox}


def test_someone_out_of_a_float_poll_is_never_chased_about_times(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], slots=[])
    repo.append_rounds(session, plan, [SLOT])
    repo.cast_interest(session, plan, users["amy"], False)

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert "amy@x.com" not in {e.to for e in outbox.outbox}


def test_a_float_poll_asks_the_interest_question(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"], slots=[])

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert "Are you in" in next(e for e in outbox.outbox if e.to == "amy@x.com").text


def test_a_single_time_poll_names_that_time(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"])

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert "work for you" in next(e for e in outbox.outbox if e.to == "amy@x.com").text


def test_the_spotlight_sharpens_the_nudge(ctx):
    """Half of what the spotlight is FOR: "the group's leaning toward X" is a
    far better prompt than "you have times to answer"."""
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], slots=[SLOT, SLOT2])
    repo.set_plan_spotlight(session, plan, plan.rounds[1].id)

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    text = next(e for e in outbox.outbox if e.to == "amy@x.com").text
    assert "leaning toward" in text
    assert "19:00" in text


def test_several_times_with_no_spotlight_ask_for_all_of_them(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"], slots=[SLOT, SLOT2])

    run_tick(session, NOW + timedelta(hours=13), INTERVAL)

    assert "Which of the 2 times" in next(e for e in outbox.outbox if e.to == "amy@x.com").text


def test_a_fully_answered_poll_is_never_nudged(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"])
    _all_say(session, plan, [users["host"], users["amy"], users["bo"]], "no")

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


def test_a_deadline_pokes_the_live_feed(ctx, monkeypatch):
    """The one state change nobody triggered: no request comes back to refresh
    the card, so an open browser learns it closed only from a poke."""
    session, group, users, _ = ctx
    poked = []
    monkeypatch.setattr("app.jobs.plan_ticker.plans_changed", poked.append)
    _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))

    run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert poked == [group.id]


def test_the_deadline_books_the_time_the_whole_group_can_make(ctx):
    """The default bar is a RULE — every account-holding member — so this books
    only because all three answered yes."""
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))
    _all_say(session, plan, [users["host"], users["amy"], users["bo"]])

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["booked"] == 1
    session.refresh(plan)
    assert plan.status == "booked"
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com", "bo@x.com"}
    assert "Booked" in outbox.last.subject


def test_one_silent_member_stops_the_deadline_booking_by_default(ctx):
    """Under the default rule a silent member is not a yes, so the poll parks
    for the host instead of booking without them."""
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))
    _all_say(session, plan, [users["host"], users["amy"]])   # bo never answers

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert (counts["booked"], counts["expired"]) == (0, 1)
    session.refresh(plan)
    assert plan.status == "expired"


def test_a_typed_minimum_lets_the_deadline_book_without_everyone(ctx):
    """Only a human lowering the bar makes a partial booking possible — which
    is what keeps automatic convergence safe."""
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2),
                 expected_count=2)
    _all_say(session, plan, [users["host"], users["amy"]])   # bo never answers

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["booked"] == 1
    session.refresh(plan)
    assert plan.status == "booked"
    # bo said nothing, so nothing reaches bo's calendar
    assert {e.to for e in outbox.outbox} == {"host@x.com", "amy@x.com"}


def test_the_deadline_prefers_the_time_that_clears_the_bar_on_firm_yeses(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], slots=[SLOT, SLOT2],
                 deadline=NOW + timedelta(hours=2), expected_count=2)
    # first time squeaks through only on "if needed"; second has real yeses
    for u in (users["host"], users["amy"]):
        repo.cast_time_vote(session, plan.rounds[0], u, "if_needed")
        repo.cast_time_vote(session, plan.rounds[1], u, "yes")

    run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    session.refresh(plan)
    assert plan.status == "booked"
    assert "19:00" in outbox.last.text or "19:00" in outbox.last.subject


def test_nothing_bookable_expires_instead_of_booking(ctx):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert (counts["booked"], counts["expired"]) == (0, 1)
    session.refresh(plan)
    assert plan.status == "expired"


def test_an_expired_poll_is_not_processed_twice(ctx):
    session, group, users, outbox = ctx
    _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))
    run_tick(session, NOW + timedelta(hours=3), INTERVAL)
    outbox.outbox.clear()

    assert run_tick(session, NOW + timedelta(hours=4), INTERVAL)["scanned"] == 0
    assert outbox.outbox == []


def test_a_failed_deadline_booking_expires_rather_than_hanging(ctx, monkeypatch):
    session, group, users, outbox = ctx
    plan = _plan(session, group, users["host"], deadline=NOW + timedelta(hours=2))
    _all_say(session, plan, [users["host"], users["amy"], users["bo"]])
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"booked": False, "error": "calendar said no"},
    )

    counts = run_tick(session, NOW + timedelta(hours=3), INTERVAL)

    assert counts["expired"] == 1
    session.refresh(plan)
    assert plan.status == "expired"
    # nothing was marked booked, so the host can still retry the lock-in
    assert not any(r.booked for r in plan.rounds)


def test_one_broken_poll_does_not_stop_the_others(ctx, monkeypatch):
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
