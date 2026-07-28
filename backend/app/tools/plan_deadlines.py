"""Async convergence — when to nudge, when to close, when to book itself.

Companion to plan_rules.py, and pure in the same way: these functions take
timestamps and counts and return a decision. Nothing here touches the DB or
sends anything; jobs/plan_ticker.py is what acts on the answers.

The problem being solved: the cascade in plan_rules.py assumes somebody
eventually answers. Real groups don't — half of them see the notification on
the bus and forget. So a plan gets three async affordances:

  REMINDERS   a nudge to whoever hasn't answered, rate-limited so a quiet plan
              doesn't turn into a mailing list.
  DEADLINE    voting closes at a fixed instant. The plan goes `expired`, not
              deleted: the host can still lock in what came in, or push the
              deadline out and reopen it.
  AUTO-BOOK   opt-in. If literally everybody is in and everybody said yes to
              the time on the table, there is nothing left for a human to
              decide, so the plan may book itself.

Auto-book is deliberately unanimity-only. Anything softer (majority, "most
people", "enough by the deadline") means the app books a calendar event that
some member never agreed to, which is the one thing this product must not do.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Deadline outcomes (what the ticker should do with a plan whose time is up).
NOTHING = "nothing"
BOOK = "book"
EXPIRE = "expire"


def next_reminder_at(
    *,
    created_at: datetime,
    deadline: datetime | None,
    last_reminder_at: datetime | None,
    interval: timedelta,
) -> datetime | None:
    """When the next nudge to non-voters is due — None if there shouldn't be one.

    Base case: `interval` after the plan appeared, then every `interval` after
    the last nudge. The wrinkle is short-fused plans: "vote by 6 PM" with a
    12-hour reminder interval would nudge people three hours after the vote
    closed, which is useless. So when the regular slot would land past the
    deadline we pull the single reminder forward to the midpoint between now-ish
    and the deadline — one well-timed nudge instead of a late one — and after
    that reminder there is no second one to give.
    """
    base = last_reminder_at or created_at
    due = base + interval
    if deadline is None:
        return due
    if due < deadline:
        return due
    if last_reminder_at is not None:
        return None  # already nudged, and there's no room for another
    return base + (deadline - base) / 2


def reminder_due(
    *,
    now: datetime,
    created_at: datetime,
    deadline: datetime | None,
    last_reminder_at: datetime | None,
    interval: timedelta,
    pending: bool,
) -> bool:
    """Should this plan nudge its non-voters right now?

    `pending` is "somebody still owes an answer" — with nobody to chase there is
    nothing to send. Past the deadline the question is closed, so the answer is
    always no; the deadline mail takes over from there.
    """
    if not pending:
        return False
    if deadline is not None and now >= deadline:
        return False
    due = next_reminder_at(
        created_at=created_at, deadline=deadline,
        last_reminder_at=last_reminder_at, interval=interval,
    )
    return due is not None and now >= due


def deadline_outcome(
    *,
    now: datetime,
    deadline: datetime | None,
    status: str,
    auto_book: bool,
    has_active_time: bool,
    time_yes_count: int,
) -> str:
    """What happens to a plan when its vote deadline passes.

    A plan that opted into auto-book asked to converge unattended, so if there
    is a time on the table that at least one person can make, the deadline
    books it for those people — the same subset rule a host lock-in uses. With
    auto-book off (the default), or with nothing bookable, voting simply closes:
    EXPIRE parks the plan for the host rather than throwing the votes away.
    """
    if status != "open" or deadline is None or now < deadline:
        return NOTHING
    if auto_book and has_active_time and time_yes_count > 0:
        return BOOK
    return EXPIRE


def everyone_said_yes(
    *,
    interested: int,
    silent_on_interest: int,
    time_yes: int,
    time_no: int,
    time_waiting: int,
) -> bool:
    """True when there is genuinely nothing left to decide about the active time.

    Every member has answered the plan, at least one is in, and every single
    person who is in has said this time works. One "no", one person who hasn't
    opened the app yet, and this is False — a human decides instead.
    """
    return (
        interested > 0
        and silent_on_interest == 0
        and time_no == 0
        and time_waiting == 0
        and time_yes == interested
    )
