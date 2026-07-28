"""What a plan actually says when it emails somebody.

Three moments, all driven by jobs/plan_ticker.py:
  - a nudge to a member who hasn't answered yet,
  - "voting closed" to the host when a deadline passes with no booking,
  - "it's booked" to an attendee when a plan converged on its own.

Every function takes ONE recipient and pre-rendered time labels, because times
must be shown in the reader's own timezone — the caller knows whose inbox this
is, this file doesn't. Send failures are swallowed and logged: a dead mailbox
must never abort the tick and leave the remaining plans unprocessed. The state
change is the point; the email is best-effort.
"""
from __future__ import annotations

import logging

from app.core.config import APP_BASE_URL
from app.db.models import Plan
from app.mailer import send_email

log = logging.getLogger("nudgy.notify")


def _plan_link() -> str:
    # No per-plan route exists yet (the SPA has no router), so links land on the
    # app and the member opens the plan from there. When routing lands, this is
    # the single place that becomes f"{APP_BASE_URL}/plans/{plan.id}".
    return APP_BASE_URL


def _send(to: str, subject: str, text: str) -> bool:
    try:
        send_email(to, subject, text)
        return True
    except Exception:
        log.exception("notification to %s failed (%s)", to, subject)
        return False


def _where(plan: Plan) -> str:
    return f" at {plan.location}" if plan.location else ""


def notify_vote_reminder(plan: Plan, to: str, question: str,
                         deadline_label: str | None = None) -> bool:
    """Nudge one member who still owes an answer.

    `question` is what they're being asked right now — the plan itself, or the
    time on the table — so the mail says something more useful than "you have a
    pending vote".
    """
    closes = f"Voting closes {deadline_label}.\n" if deadline_label else ""
    return _send(
        to,
        f"Still waiting on you: {plan.title}",
        f"{plan.title}{_where(plan)} is waiting on your answer.\n\n"
        f"{question}\n{closes}\n{_plan_link()}\n",
    )


def notify_plan_expired(plan: Plan, to: str, summary: str) -> bool:
    """Tell the host voting closed — with the tally, since the decision is now
    theirs to make on whatever came in."""
    return _send(
        to,
        f"Voting closed: {plan.title}",
        f"The deadline for {plan.title}{_where(plan)} has passed, so voting is "
        f"closed.\n\n{summary}\n\nYou can still lock in a time for the people who "
        f"said yes, or give the group longer by pushing the deadline back.\n\n"
        f"{_plan_link()}\n",
    )


def notify_plan_auto_booked(plan: Plan, to: str, when: str,
                            event_link: str | None = None) -> bool:
    """Confirm a plan that booked itself. Calendar invites go out separately (the
    booking writes them); this is the "why did this appear in my calendar"
    message, which matters because no human pressed a button."""
    link = f"{event_link}\n" if event_link else ""
    return _send(
        to,
        f"Booked: {plan.title}",
        f"Everyone said yes, so {plan.title}{_where(plan)} is booked for "
        f"{when}.\n{link}\n{_plan_link()}\n",
    )
