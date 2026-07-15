"""Write the approved meetup to Google Calendar.

Approach: insert ONE event on the organizer's primary calendar with every
group member as an attendee, sendUpdates="all". Google then puts the event on
every member's calendar and emails them an invite — which is exactly the
"shared calendar all members see" + "rely on Google's invite emails" behavior
the spec asks for, without managing a separate calendar + ACLs.

Safety: callers must only invoke this after evaluate_poll() returned APPROVED.
The booking itself never re-decides; it just writes what was approved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.auth.google import credentials_from_json
from app.db.models import Poll, User
from app.db import repo

log = logging.getLogger("orbi.agent")


def book_poll_event(session: Session, poll: Poll, organizer: User, attendee_emails: list[str]) -> dict:
    """Create the calendar event for an APPROVED poll. Returns event info."""
    if poll.status != "approved":
        return {"error": f"Poll {poll.id} is '{poll.status}', not approved — refusing to book."}
    if poll.booked:
        return {"error": f"Poll {poll.id} is already booked.", "event_link": poll.event_link}
    if not organizer.calendar_connected:
        return {"error": "Organizer has no connected calendar."}

    creds, refreshed = credentials_from_json(organizer.token_json)
    if refreshed:
        repo.set_user_token(session, organizer, refreshed)

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body = {
        "summary": poll.title,
        "start": {"dateTime": poll.start.astimezone(timezone.utc).isoformat()},
        "end": {"dateTime": poll.end.astimezone(timezone.utc).isoformat()},
        "attendees": [{"email": e} for e in attendee_emails],
        "description": "Scheduled by Orbi after the group approved it in a poll.",
    }
    if poll.location:
        body["location"] = poll.location

    event = service.events().insert(
        calendarId="primary", body=body, sendUpdates="all"
    ).execute()
    link = event.get("htmlLink")
    repo.mark_poll_booked(session, poll, link)
    log.info("[booking] poll %d booked -> %s", poll.id, link)
    return {
        "booked": True,
        "event_link": link,
        "event_id": event.get("id"),
        "attendees": attendee_emails,
    }
