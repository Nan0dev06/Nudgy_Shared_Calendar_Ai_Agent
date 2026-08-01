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
from dataclasses import dataclass, field
from datetime import datetime

# (start, end) busy range, both tz-aware UTC — same shape app.tools.slots uses.
Interval = tuple[datetime, datetime]


@dataclass
class CreatedEvent:
    """What a write returns: the provider's event id (for later delete/update)
    and a human link to open it, if the provider gives one."""
    id: str
    link: str | None = None


@dataclass
class SyncCalendar:
    """One calendar inbound sync should mirror, as the provider names it.

    `id` is whatever the provider's sync call wants back (a Google calendarList
    id; the empty string for Graph's mailbox calendarView). `name` is for the
    owner's eyes — "which calendar is this from" in the UI.
    """
    id: str
    name: str | None = None


@dataclass
class ExternalEventData:
    """One mirrored event, already reduced to what app.db.models.ExternalEvent
    stores — times, and title/location only if titles were asked for.

    Providers do the reduction themselves rather than handing back raw API
    payloads, so descriptions, attendees, organisers and bodies never leave the
    provider module. That is the same shape the freebusy boundary has: the
    privacy promise is kept by what crosses the seam, not by what a caller
    remembers to ignore.
    """
    external_id: str
    start: datetime          # tz-aware UTC
    end: datetime            # tz-aware UTC
    title: str | None = None
    location: str | None = None
    all_day: bool = False
    busy: bool = True        # False for "on my calendar but not blocking it"


@dataclass
class SyncResult:
    """One completed round of incremental sync against ONE calendar.

    A round is all-or-nothing on purpose. Both providers only hand over the next
    token on the LAST page, so a run that dies mid-pagination has no token to
    save and must replay from the previous one — which is safe precisely because
    `changed` is applied as an idempotent upsert.

    `full` means this round re-read the whole window rather than a delta (first
    ever sync, an invalidated token, or a re-based window). The caller uses it to
    reconcile by absence: on a full round anything in the mirror for this
    calendar that did NOT come back has gone, which is the only way to catch
    deletions that happened while we held a token the provider had already
    forgotten.
    """
    changed: list[ExternalEventData] = field(default_factory=list)
    deleted_ids: list[str] = field(default_factory=list)
    next_token: str | None = None
    full: bool = False


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

    # --- inbound sync --------------------------------------------------------
    # The one place the app reads event CONTENT rather than opaque busy time.
    # Everything above this line is freebusy-only; everything below is gated on
    # the owner's per-calendar `read_titles` opt-in.

    @abstractmethod
    def list_sync_calendars(self) -> list[SyncCalendar]:
        """The calendars inbound sync should mirror for this account.

        Must agree with what `get_busy` reads — a calendar that makes the owner
        busy but is never mirrored leaves them staring at a blank block they
        cannot explain, which is the whole problem inbound sync exists to fix.
        """

    @abstractmethod
    def sync_events(
        self, calendar_id: str, *, token: str | None,
        window_start: datetime, window_end: datetime, want_titles: bool,
    ) -> SyncResult:
        """One round of incremental sync against `calendar_id`.

        `token` is the opaque bookmark from the previous round, or None for a
        full read of [window_start, window_end]. An expired or rejected token
        must NOT raise: fall back to a full read and return `full=True`, because
        invalidation is a routine event on both providers (Google documents no
        TTL and drops tokens on unrelated ACL changes; Graph evicts them from a
        shared cache) and a sync that dies on it would simply stop working one
        day with no signal.

        `want_titles` is the owner's opt-in. Where the provider can express it in
        the request, it must — asking for less is a stronger promise than
        discarding more. Where it cannot, drop the fields before returning.
        """
