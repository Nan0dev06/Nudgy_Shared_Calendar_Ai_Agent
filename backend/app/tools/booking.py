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
from datetime import timezone

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.auth.google import credentials_from_json
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
    if not organizer.calendar_connected:
        return {"error": "Host has no connected calendar."}

    creds, refreshed = credentials_from_json(organizer.token_json)
    if refreshed:
        repo.set_user_token(session, organizer, refreshed)

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body = {
        "summary": plan.title,
        "start": {"dateTime": round_.start.astimezone(timezone.utc).isoformat()},
        "end": {"dateTime": round_.end.astimezone(timezone.utc).isoformat()},
        "attendees": [{"email": e} for e in attendee_emails],
        "description": "Scheduled by Nudgy — the people here said this time works.",
    }
    if plan.location:
        body["location"] = plan.location

    event = service.events().insert(
        calendarId="primary", body=body, sendUpdates="all"
    ).execute()
    link = event.get("htmlLink")
    repo.mark_round_booked(session, round_, link)
    log.info("[booking] plan %d time %d booked -> %s", plan.id, round_.ordinal, link)
    return {
        "booked": True,
        "event_link": link,
        "event_id": event.get("id"),
        "attendees": attendee_emails,
    }
