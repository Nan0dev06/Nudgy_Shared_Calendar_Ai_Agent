"""The clock behind async voting: nudge non-voters, close plans at the deadline.

Without this the cascade only advances while somebody is looking at the app,
which is exactly the hackathon assumption this phase is undoing. Once a minute:

  1. Any plan whose vote deadline has passed is resolved — auto-booked if it
     opted in and somebody can make the time, otherwise expired (voting shuts,
     the host keeps the decision).
  2. Any plan still waiting on answers gets a rate-limited nudge to the people
     who owe one.

`run_tick` is a plain synchronous function taking an explicit `now`, so tests
drive months of plan life in milliseconds without sleeping or patching clocks.
The async loop is a thin wrapper that supplies the real clock and a session.

One plan's failure must not cost the others their tick, so each is processed in
its own try/except; a plan that raises is logged and skipped until next minute.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import (
    PLAN_REMINDER_INTERVAL_SECONDS, PLAN_TICK_SECONDS, PLAN_TICKER_ENABLED,
)
from app.db.models import Plan, User
from app.db import repo
from app.db.session import SessionLocal
from app.notify.plans import (
    notify_plan_auto_booked, notify_plan_expired, notify_vote_reminder,
)
from app.tools.plan_deadlines import reminder_due
from app.tools.plan_service import (
    load_plan_state, pending_voters, plan_tally, resolve_deadline, time_label,
)

log = logging.getLogger("nudgy.jobs")


def _label(when: datetime, tz_name: str) -> str:
    return f"{when.astimezone(ZoneInfo(tz_name)):%a %d %b %H:%M}"


def run_tick(session: Session, now: datetime | None = None,
             reminder_interval: timedelta | None = None) -> dict:
    """One pass over every open plan. Returns counts, for logs and tests."""
    now = now or datetime.now(timezone.utc)
    interval = reminder_interval or timedelta(seconds=PLAN_REMINDER_INTERVAL_SECONDS)
    counts = {"scanned": 0, "booked": 0, "expired": 0, "reminded": 0}

    for plan in repo.get_open_plans(session):
        counts["scanned"] += 1
        try:
            _process_plan(session, plan, now, interval, counts)
        except Exception:
            log.exception("[plan %d] tick failed", plan.id)
    return counts


def _process_plan(session: Session, plan: Plan, now: datetime,
                  interval: timedelta, counts: dict) -> None:
    host = session.get(User, plan.created_by)
    host_tz = host.timezone if host else "UTC"

    outcome = resolve_deadline(session, plan, now, host_tz)
    if outcome is not None:
        if outcome["action"] == "booked":
            counts["booked"] += 1
            _announce_booking(session, plan, outcome)
        else:
            counts["expired"] += 1
            if host is not None:
                summary = plan_tally(session, plan, host_tz).host_note
                notify_plan_expired(plan, host.email, summary)
        return  # the plan is no longer open; nothing left to remind about

    if _remind(session, plan, now, interval):
        counts["reminded"] += 1


def _remind(session: Session, plan: Plan, now: datetime, interval: timedelta) -> bool:
    """Send this plan's nudges if one is due. True if anything went out."""
    state = load_plan_state(session, plan)
    waiting = pending_voters(state)
    if not reminder_due(
        now=now,
        created_at=plan.created,
        deadline=plan.deadline,
        last_reminder_at=plan.reminded_at,
        interval=interval,
        pending=bool(waiting),
    ):
        return False

    by_email = {m.email: m for m in repo.get_group_members(session, plan.group_id)}
    for email in waiting:
        member = by_email.get(email)
        if member is None:
            continue
        tz = member.timezone or "UTC"
        if state.interest_votes.get(email) is None:
            question = "Are you in for this plan?"
        else:
            question = f"Does {time_label(state.active, tz)} work for you?"
        notify_vote_reminder(
            plan, email, question,
            deadline_label=_label(plan.deadline, tz) if plan.deadline else None,
        )

    # Stamped even if every send failed: the mailer is best-effort, and a broken
    # provider must not turn into a nudge every single minute once it recovers.
    repo.mark_plan_reminded(session, plan, now)
    log.info("[plan %d] nudged %d member(s) who haven't answered", plan.id, len(waiting))
    return True


def _announce_booking(session: Session, plan: Plan, outcome: dict) -> None:
    """Tell the attendees a plan booked itself — nobody pressed a button, so the
    calendar invite alone would be a surprise.

    The time reads in the HOST's zone for everyone (that's the label the booking
    came back with, and the round is no longer 'active' to re-label per reader).
    The calendar invite that lands beside this mail shows each person their own
    zone, so nobody has to do the arithmetic from here."""
    for email in outcome.get("attendees", []):
        notify_plan_auto_booked(plan, email, outcome.get("time", ""),
                                outcome.get("event_link"))


# ----------------------------------------------------------------- the loop

async def _loop(interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        session = SessionLocal()
        try:
            # run_tick is blocking (DB + outbound mail); off the event loop it
            # goes, or a slow calendar call stalls every request in flight.
            counts = await asyncio.to_thread(run_tick, session)
            if counts["booked"] or counts["expired"] or counts["reminded"]:
                log.info("plan tick: %s", counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("plan tick crashed")
        finally:
            session.close()


def start_ticker() -> asyncio.Task | None:
    """Start the background loop. None when disabled (tests, multi-process runs)."""
    if not PLAN_TICKER_ENABLED:
        log.info("plan ticker disabled (PLAN_TICKER_ENABLED=0)")
        return None
    log.info("plan ticker every %ss", PLAN_TICK_SECONDS)
    return asyncio.create_task(_loop(PLAN_TICK_SECONDS))


async def stop_ticker(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
