"""Google Calendar implementation of CalendarProvider.

Reads (busy ranges, event locations) delegate to the privacy-scoped helpers in
app.tools.freebusy / app.tools.locations — the single places allowed to touch
Google's read APIs, with their fields-restricted requests. Writes (create /
update / delete) live here, centralized so booking and in-app event sync share
one code path instead of duplicating the insert body.

Events are written to the account's `primary` calendar with the invitees as
attendees and sendUpdates="all", so Google puts the event on every attendee's
calendar and emails the invite — the "one shared event everyone sees" behavior
without managing a separate calendar + ACLs.
"""
from __future__ import annotations

from datetime import datetime, timezone

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.auth.google import credentials_from_json
from app.calendars.base import CalendarProvider, CreatedEvent, Interval
from app.db import repo
from app.db.models import CalendarAccount
from app.tools.freebusy import query_busy
from app.tools.locations import get_adjacent_event_locations


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class GoogleCalendarProvider(CalendarProvider):
    def __init__(self, creds):
        self._creds = creds
        self._service = None  # built lazily; reads may never need it

    @classmethod
    def from_account(cls, session: Session, account: CalendarAccount) -> "GoogleCalendarProvider":
        """Build from a stored account, persisting a silent token refresh so a
        token that expired mid-session never goes stale on disk."""
        creds, refreshed = credentials_from_json(account.token_json)
        if refreshed:
            repo.set_account_token(session, account, refreshed)
        return cls(creds)

    def _svc(self):
        if self._service is None:
            self._service = build(
                "calendar", "v3", credentials=self._creds, cache_discovery=False
            )
        return self._service

    # --- reads (delegated to the privacy-scoped helpers) --------------------

    def get_busy(self, time_min: datetime, time_max: datetime) -> list[Interval]:
        return query_busy(self._creds, time_min, time_max)

    def get_event_locations(
        self, slot_start: datetime, slot_end: datetime, window_hours: int = 2,
    ) -> list[str]:
        return get_adjacent_event_locations(
            self._creds, slot_start, slot_end, window_hours=window_hours
        )

    # --- writes -------------------------------------------------------------

    def create_event(
        self, *, summary: str, start: datetime, end: datetime,
        attendee_emails: list[str], location: str | None = None,
        description: str | None = None,
    ) -> CreatedEvent:
        body: dict = {
            "summary": summary,
            "start": {"dateTime": _rfc3339(start)},
            "end": {"dateTime": _rfc3339(end)},
            "attendees": [{"email": e} for e in attendee_emails],
        }
        if description is not None:
            body["description"] = description
        if location:
            body["location"] = location
        event = self._svc().events().insert(
            calendarId="primary", body=body, sendUpdates="all"
        ).execute()
        return CreatedEvent(id=event.get("id"), link=event.get("htmlLink"))

    def update_event(
        self, event_id: str, *, summary: str | None = None,
        start: datetime | None = None, end: datetime | None = None,
        location: str | None = None,
    ) -> CreatedEvent:
        patch: dict = {}
        if summary is not None:
            patch["summary"] = summary
        if start is not None:
            patch["start"] = {"dateTime": _rfc3339(start)}
        if end is not None:
            patch["end"] = {"dateTime": _rfc3339(end)}
        if location is not None:
            patch["location"] = location
        event = self._svc().events().patch(
            calendarId="primary", eventId=event_id, body=patch, sendUpdates="all"
        ).execute()
        return CreatedEvent(id=event.get("id"), link=event.get("htmlLink"))

    def delete_event(self, event_id: str) -> None:
        self._svc().events().delete(
            calendarId="primary", eventId=event_id, sendUpdates="all"
        ).execute()
