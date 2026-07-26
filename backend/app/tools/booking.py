"""Write the host-confirmed time to Google Calendar.

Approach: insert ONE event on the host's primary calendar with the attendees as
attendees, sendUpdates="all". Google then puts the event on every attendee's
calendar and emails them an invite — which is exactly the "shared calendar all
members see" + "rely on Google's invite emails" behavior the spec asks for,
without managing a separate calendar + ACLs.

Who gets invited: ONLY the members who voted yes to this specific time. People
who were interested in the plan but said no to this time are deliberately left
off — they told us this time doesn't work. The host is the organizer, so the
event lands on their calendar whether or not they voted on the time.

Safety: callers must only invoke this after the host confirmed the round
(status "confirmed"). The booking never re-decides; it writes what was locked in.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.calendars import provider_for_account
from app.calendars.cache import freebusy_cache
from app.db.models import Plan, TimeRound, User
from app.db import repo

log = logging.getLogger("nudgy.agent")


def book_round_event(
    session: Session,
    plan: Plan,
    round_: TimeRound,
    organizer: User,
    attendee_emails: list[str],
) -> dict:
    """Create the calendar event for a CONFIRMED time. Returns event info."""
    if round_.status != "confirmed":
        return {"error": f"Time is '{round_.status}', not confirmed by the host — refusing to book."}
    if round_.booked:
        return {"error": "This time is already booked.", "event_link": round_.event_link}
    if not attendee_emails:
        return {"error": "Nobody said this time works — refusing to book an empty event."}
    account = repo.get_primary_calendar_account(session, organizer)
    if account is None:
        return {"error": "Host has no connected calendar."}

    provider = provider_for_account(session, account)
    created = provider.create_event(
        summary=plan.title,
        start=round_.start,
        end=round_.end,
        attendee_emails=attendee_emails,
        location=plan.location,
        description="Scheduled by Nudgy — the people here said this time works.",
    )
    repo.mark_round_booked(session, round_, created.link)
    # The organizer's calendar just gained this event — drop their cached busy so
    # the next availability read reflects it immediately (attendees' entries age
    # out via TTL). Booking itself never reads the cache.
    freebusy_cache.invalidate(account.id)
    log.info("[booking] plan %d time %d booked -> %s", plan.id, round_.ordinal, created.link)
    return {
        "booked": True,
        "event_link": created.link,
        "event_id": created.id,
        "attendees": attendee_emails,
    }
