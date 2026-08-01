"""Inbound calendar sync: pull external events INTO Nudgy on a clock.

Outbound sync has always existed — book a plan, the event lands on everyone's
Google Calendar. Inbound is the other direction, and until now the only thing
that came back was opaque busy time. That was enough to stop Nudgy
double-booking anybody and not enough to ever tell them WHY their Tuesday was
gone. This job fills that in: once every few minutes, each connected calendar is
asked "what changed since last time?" and the answer is mirrored into
`external_events`.

POLLING FIRST, WEBHOOKS LATER — and deliberately not a rewrite when that lands.
Both providers answer the same question the same way: hand back an opaque token,
get only what changed. A push notification from either one carries no useful
payload; it says "something moved" and the handler's entire job is to run this
exact token loop. So webhooks replace the TIMER, not the sync, and
`_sync_calendar` below is already the single ingestion path both would call.

`run_sync(session, now)` is plain and synchronous, taking an explicit clock, so
tests drive whole sync histories without sleeping — the same shape
jobs/plan_ticker.py uses, for the same reason. The asyncio loop around it is
started and stopped by main.py's lifespan.

FAILURE IS SCOPED. One dead token must not cost every other user their sync, so
each account and each calendar inside it is processed in its own try/except; a
failure is recorded on the calendar's row (Settings shows it) and skipped until
the next tick. The token is NOT discarded on error — most failures are transient,
and throwing away a valid bookmark turns a blip into a full re-read.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.calendars import provider_for_account
from app.core.config import (
    CALENDAR_SYNC_DAYS_AHEAD, CALENDAR_SYNC_ENABLED, CALENDAR_SYNC_INTERVAL_SECONDS,
    CALENDAR_SYNC_PAST_DAYS, CALENDAR_SYNC_REBASE_DAYS, CALENDAR_SYNC_SECONDS,
)
from app.db import repo
from app.db.models import CalendarAccount, CalendarSyncState
from app.db.session import SessionLocal

log = logging.getLogger("nudgy.jobs")


def sync_window(now: datetime) -> tuple[datetime, datetime]:
    """The stretch of calendar Nudgy mirrors: a little behind, months ahead.

    The past week is included because an event that started yesterday and runs
    through today is still occupying today; beyond that, history is not what this
    app is for. The horizon is long enough that planning "sometime next term"
    still sees real conflicts.
    """
    return (
        now - timedelta(days=CALENDAR_SYNC_PAST_DAYS),
        now + timedelta(days=CALENDAR_SYNC_DAYS_AHEAD),
    )


def plan_round(state: CalendarSyncState, now: datetime) -> tuple[datetime, datetime, str | None]:
    """Decide this round's window and whether the stored bookmark still applies.

    The window is part of a token's identity, not a parameter alongside it.
    Graph literally encodes startDateTime/endDateTime into the delta token, so a
    token minted for a +120d horizon is STILL a +120d horizon three months later
    — it does not roll forward on its own. Google won't even accept timeMin on
    an incremental call, so its window is ours to re-apply either way.

    Hence the re-base: while the stored horizon is comfortably ahead of now, keep
    the token and its original window. Once now is closing on that horizon, start
    a fresh full round on a fresh window. That is routine maintenance, not an
    error path, and `full=True` makes the caller reconcile by absence so the
    events that fell off the back of the old window are cleaned up.
    """
    if (
        state.sync_token
        and state.window_start is not None
        and state.window_end is not None
        and state.window_end - now > timedelta(days=CALENDAR_SYNC_REBASE_DAYS)
    ):
        return state.window_start, state.window_end, state.sync_token
    start, end = sync_window(now)
    return start, end, None


def due(state: CalendarSyncState, now: datetime, interval: timedelta) -> bool:
    """Has this calendar waited long enough to be polled again?

    The job ticks more often than any one calendar needs, so that a calendar
    connected a moment ago syncs promptly instead of waiting out a full period.
    """
    if state.synced is None:
        return True
    return now - state.synced >= interval


def run_sync(
    session: Session, now: datetime | None = None,
    interval: timedelta | None = None, account_id: int | None = None,
) -> dict:
    """One pass over every connected calendar. Returns counts, for logs and tests.

    `account_id` narrows the pass to a single account and bypasses the per-
    calendar interval — that is the "Sync now" button, sharing this code path
    rather than growing a second one.
    """
    now = now or datetime.now(timezone.utc)
    interval = interval or timedelta(seconds=CALENDAR_SYNC_INTERVAL_SECONDS)
    counts = {"accounts": 0, "calendars": 0, "added": 0, "updated": 0,
              "deleted": 0, "failed": 0}

    accounts = repo.accounts_with_tokens(session)
    if account_id is not None:
        accounts = [a for a in accounts if a.id == account_id]

    for account in accounts:
        counts["accounts"] += 1
        try:
            _sync_account(session, account, now, interval,
                          force=account_id is not None, counts=counts)
        except Exception:
            counts["failed"] += 1
            log.exception("[sync] account %d failed", account.id)
    return counts


def _sync_account(
    session: Session, account: CalendarAccount, now: datetime,
    interval: timedelta, force: bool, counts: dict,
) -> None:
    provider = provider_for_account(session, account)
    calendars = provider.list_sync_calendars()

    for cal in calendars:
        state = repo.upsert_sync_state(session, account, cal.id, cal.name)
        if not force and not due(state, now, interval):
            continue
        counts["calendars"] += 1
        try:
            _sync_calendar(session, provider, account, state, now, counts)
        except Exception as exc:
            counts["failed"] += 1
            repo.record_sync_error(session, state, f"{type(exc).__name__}: {exc}")
            log.exception("[sync] account %d calendar %r failed", account.id, cal.id)


def _sync_calendar(
    session: Session, provider, account: CalendarAccount,
    state: CalendarSyncState, now: datetime, counts: dict,
) -> None:
    """One calendar, one round. THE single ingestion path — a webhook handler
    would call exactly this, with the timer swapped for a notification."""
    window_start, window_end, token = plan_round(state, now)
    result = provider.sync_events(
        state.calendar_id, token=token,
        window_start=window_start, window_end=window_end,
        # Read from the account, not cached earlier: an opt-in switched off while
        # this round was in flight must not write a title back in.
        want_titles=account.read_titles,
    )
    applied = repo.save_sync_round(
        session, state,
        changed=result.changed, deleted_ids=result.deleted_ids,
        next_token=result.next_token, full=result.full,
        window_start=window_start, window_end=window_end, now=now,
        store_titles=account.read_titles,
    )
    for key in ("added", "updated", "deleted"):
        counts[key] += applied[key]
    if any(applied.values()):
        log.info("[sync] account %d calendar %r: %s", account.id, state.calendar_id, applied)


# ----------------------------------------------------------------- the loop

async def _loop(interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        session = SessionLocal()
        try:
            # run_sync is blocking (HTTP to two calendar APIs, plus DB), so it
            # goes off the event loop or a slow provider stalls every request.
            counts = await asyncio.to_thread(run_sync, session)
            if counts["added"] or counts["updated"] or counts["deleted"] or counts["failed"]:
                log.info("calendar sync: %s", counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("calendar sync crashed")
        finally:
            session.close()


def start_sync() -> asyncio.Task | None:
    """Start the background loop. None when disabled (tests, multi-process runs)."""
    if not CALENDAR_SYNC_ENABLED:
        log.info("calendar sync disabled (CALENDAR_SYNC_ENABLED=0)")
        return None
    log.info("calendar sync every %ss", CALENDAR_SYNC_SECONDS)
    return asyncio.create_task(_loop(CALENDAR_SYNC_SECONDS))


async def stop_sync(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
