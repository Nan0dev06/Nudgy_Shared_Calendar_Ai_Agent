"""The calendar-provider seam.

Everything the app does against a user's external calendar goes through a
`CalendarProvider`, so the rest of the code never imports Google (or, soon,
Microsoft Graph) directly. One implementation per backend; a factory
(`app.calendars.provider_for_account`) picks the right one from a
CalendarAccount's `provider` field.

Privacy principle carried across every provider: availability reads return ONLY
busy time ranges (never titles/attendees), and location reads return ONLY the
location strings users typed on their own events — nothing else about an event
is ever requested. Keep that invariant in each implementation.

All datetimes crossing this boundary are timezone-aware (UTC internally).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

# (start, end) busy range, both tz-aware UTC — same shape app.tools.slots uses.
Interval = tuple[datetime, datetime]


@dataclass
class CreatedEvent:
    """What a write returns: the provider's event id (for later delete/update)
    and a human link to open it, if the provider gives one."""
    id: str
    link: str | None = None


class CalendarProvider(ABC):
    """One connected calendar, ready to read availability from and write events to.

    Construct via a concrete provider's `from_account(session, account)`, which
    resolves credentials and persists any silent token refresh before returning.
    """

    @abstractmethod
    def get_busy(self, time_min: datetime, time_max: datetime) -> list[Interval]:
        """Merged busy ranges in [time_min, time_max]. Ranges only — no details."""

    @abstractmethod
    def get_event_locations(
        self, slot_start: datetime, slot_end: datetime, window_hours: int = 2,
    ) -> list[str]:
        """Location strings the user typed on their own events near the slot
        (+/- window_hours). ONLY the location field is read — never titles."""

    @abstractmethod
    def create_event(
        self, *, summary: str, start: datetime, end: datetime,
        attendee_emails: list[str], location: str | None = None,
        description: str | None = None,
    ) -> CreatedEvent:
        """Create an event and invite the attendees (the provider emails them).
        The connected account is the organizer."""

    @abstractmethod
    def update_event(
        self, event_id: str, *, summary: str | None = None,
        start: datetime | None = None, end: datetime | None = None,
        location: str | None = None,
    ) -> CreatedEvent:
        """Patch an existing event (two-way sync). Only the given fields change."""

    @abstractmethod
    def delete_event(self, event_id: str) -> None:
        """Delete an event the account organizes and notify its attendees."""
