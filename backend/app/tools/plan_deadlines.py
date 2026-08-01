"""Convergence — when to nudge, when to close, and what may book itself.

Companion to plan_rules.py, and pure in the same way: these take counts and
timestamps and return a decision. Nothing here touches the DB or sends anything;
jobs/plan_ticker.py and tools/plan_service.py act on the answers.

The problem: the engine assumes somebody eventually answers. Real groups don't —
half of them see the notification and forget. So a poll gets three affordances:

  REMINDERS   a nudge to whoever hasn't answered, rate-limited so a quiet poll
              doesn't turn into a mailing list.
  DEADLINE    voting closes at a fixed instant. Nothing that qualifies -> the
              poll goes `expired`, not deleted: the host can extend it, add
              times, lower the minimum, or lock in what came in.
  CONVERGENCE the best qualifying time books itself.

WHAT MAKES AUTOMATIC BOOKING SAFE (docs/poll-edit-redesign.md §1.4-1.5). The old
rule was unanimity, which almost never fires. The new rule rests on two things:

  1. A voter's own yes IS their consent. Booking only ever invites the people who
     said yes or if-needed, so no automatic path can put an event on the calendar
     of somebody who did not agree to it. That was always true — the host's
     lock-in never protected anyone's calendar, it only chose which time.
  2. The BAR answers "how many of us make this worth doing?". Without it,
     "most-voted time wins" would book a 10-person outing for the 3 people who
     replied. By DEFAULT the bar is not a number at all but a rule — every
     account-holding member must be able to make the time — so an unattended
     booking always means the whole group agreed. Only a human lowering it to a
     count makes anything less possible.

Guests count toward a creator-typed COUNT: the creator said that many people is
enough, and a guest who said yes is one of them. They never count toward the
DEFAULT rule — people who joined through a link are not the group, and letting
them stand in for a member would mean sharing a link makes a plan book EASIER.

`if needed` counts toward the minimum only as a fallback — a time that clears the
bar on firm yeses always beats one that needs the maybes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Deadline outcomes (what the ticker should do with a poll whose time is up).
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

    Base case: `interval` after the poll appeared, then every `interval` after
    the last nudge. The wrinkle is short-fused polls: "vote by 6 PM" with a
    12-hour reminder interval would nudge people three hours after voting closed,
    which is useless. So when the regular slot would land past the deadline we
    pull the single reminder forward to the midpoint between now-ish and the
    deadline — one well-timed nudge instead of a late one — and after that
    reminder there is no second one to give.
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
    """Should this poll nudge its non-voters right now?

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


def choose_winner(
    candidates: list,          # TimeResult-shaped (see plan_rules.TimeResult)
    *,
    minimum: int | None,       # None = the default rule: every member is in
    member_total: int,
    spotlight: int | None,
    order: list[int] | None = None,   # keys in display order, for the final tiebreak
) -> int | None:
    """Which candidate time should book — None when none qualifies.

    The order is deliberate (docs/poll-edit-redesign.md §1.5):

      1. Times that qualify on FIRM yeses. If any exist, only they are
         considered — an "if needed" majority never beats a real one.
      2. Otherwise times that qualify once `if needed` is counted too.
      3. Among the qualifiers, most total yes wins.
      4. Tie -> the spotlit time. The host set it by hand, which makes it a
         better tiebreak than anything the app could infer.
      5. Still tied -> earliest in display order, which is chronological.

    What "qualify" means is TimeResult.qualifies — either every member is in
    (the default) or a creator-typed count is reached, guests included.
    """
    if not candidates:
        return None

    firm = [c for c in candidates
            if c.qualifies(minimum=minimum, member_total=member_total, firm_only=True)]
    pool = firm or [c for c in candidates
                    if c.qualifies(minimum=minimum, member_total=member_total)]
    if not pool:
        return None

    position = {k: i for i, k in enumerate(order or [])}
    best = max(
        pool,
        key=lambda c: (
            c.total_yes,
            1 if c.key == spotlight else 0,
            -position.get(c.key, 0),
        ),
    )
    return best.key


def ready_to_book_early(
    *,
    winner: int | None,
    pending_answers: int,
) -> bool:
    """May a poll book before its deadline?

    Only when a time qualifies AND nobody is still pending — every eligible
    participant has answered every candidate time, guests included. At that point
    no further vote can arrive, so the ranking is final and waiting for the clock
    would achieve nothing.

    Requiring "nobody pending" rather than merely "the minimum is met" is what
    stops a lowered minimum from booking Tuesday on three early replies while
    seven people are still asleep. With the default minimum (the whole group)
    this fires exactly when everyone is in.
    """
    return winner is not None and pending_answers == 0


def deadline_outcome(
    *,
    now: datetime,
    deadline: datetime | None,
    status: str,
    winner: int | None,
) -> str:
    """What happens to a poll when its vote deadline passes.

    A qualifying time books for its yes and if-needed voters. Nothing qualifying
    means voting simply closes: EXPIRE parks the poll for the host — with every
    vote intact — rather than throwing the group's answers away.
    """
    if status != "open" or deadline is None or now < deadline:
        return NOTHING
    return BOOK if winner is not None else EXPIRE
