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

import json
import logging
from datetime import datetime, time, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.auth.google import credentials_from_json
from app.calendars.base import (
    CalendarProvider, CreatedEvent, ExternalEventData, Interval, SyncCalendar,
    SyncResult,
)
from app.db import repo
from app.db.models import CalendarAccount
from app.tools.freebusy import OWNED_ROLES, query_busy
from app.tools.locations import get_adjacent_event_locations

log = logging.getLogger("nudgy.calendars")

# The events.list query is FROZEN for the lifetime of a sync token: Google's sync
# guide says every incremental call must repeat the initial call's parameters,
# and flipping one mid-stream is undefined behaviour rather than an error. So
# these are module constants, not arguments.
#   singleEvents=True  -> concrete instances, never an RRULE we'd have to expand
#   showDeleted=True   -> required; an incremental sync may not set it False,
#                         so the FULL sync sets it too or the sets wouldn't match
_SYNC_QUERY = {"singleEvents": True, "showDeleted": True, "maxResults": 250}

# Partial-response masks. The lean one is the privacy boundary in request form:
# with titles off we never ASK for a summary, so there is nothing to discard and
# nothing to leak through a log or a crash report. Because the mask is part of
# the frozen query, flipping the opt-in has to mint a new token — which is
# exactly what repo.set_account_read_titles arranges.
_FIELDS_TIMES = (
    "nextPageToken,nextSyncToken,"
    "items(id,status,start,end,transparency,eventType)"
)
_FIELDS_TITLES = (
    "nextPageToken,nextSyncToken,"
    "items(id,status,start,end,transparency,eventType,summary,location)"
)

# Not appointments. workingLocation is a "I'm in the office today" marker Google
# models as an event; mirroring it would put a full-day block on a calendar whose
# whole job is showing what you actually have on.
_SKIP_EVENT_TYPES = {"workingLocation"}


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _error_reason(exc: HttpError) -> str:
    """The `reason` string inside a Google error body ("fullSyncRequired", …).

    A bare 410 is ambiguous — the errors guide lists three, and only one of them
    means "your sync token is stale" — so the reason is what we branch on.
    """
    try:
        return json.loads(exc.content)["error"]["errors"][0]["reason"]
    except Exception:
        return ""


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

    # --- inbound sync ---------------------------------------------------------

    def list_sync_calendars(self) -> list[SyncCalendar]:
        """Every calendar this account owns or can edit, with its display name.

        Deliberately the SAME filter tools/freebusy uses (OWNED_ROLES, so
        subscribed read-only calendars like Holidays are skipped): what makes you
        busy and what gets mirrored have to be the same set, or you get busy
        blocks that can never be explained.
        """
        out: list[SyncCalendar] = []
        page_token = None
        while True:
            resp = self._svc().calendarList().list(
                pageToken=page_token,
                fields="nextPageToken,items(id,summary,accessRole)",
            ).execute()
            for cal in resp.get("items", []):
                if cal.get("accessRole") in OWNED_ROLES:
                    out.append(SyncCalendar(id=cal["id"], name=cal.get("summary")))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        # Same fallback as _owned_calendar_ids: an empty calendarList must not be
        # read as "this person has no calendars".
        return out or [SyncCalendar(id="primary", name=None)]

    def sync_events(
        self, calendar_id: str, *, token: str | None,
        window_start: datetime, window_end: datetime, want_titles: bool,
    ) -> SyncResult:
        """One round of events.list incremental sync (Google's syncToken flow).

        Paging and the token share one channel: nextSyncToken arrives ONLY on the
        final page, so the loop runs to completion or returns nothing worth
        saving. A 410 fullSyncRequired is caught here and retried as a full read
        — Google publishes no token TTL and invalidates on unrelated ACL changes,
        so treating it as an exception would mean the feature silently dies for
        somebody one day.
        """
        try:
            return self._sync_round(
                calendar_id, token, window_start, window_end, want_titles,
            )
        except HttpError as exc:
            if exc.resp.status == 410 and token:
                log.info(
                    "[sync] google %s: token rejected (%s) — falling back to a "
                    "full read", calendar_id, _error_reason(exc) or "410",
                )
                return self._sync_round(
                    calendar_id, None, window_start, window_end, want_titles,
                )
            raise

    def _sync_round(
        self, calendar_id: str, token: str | None,
        window_start: datetime, window_end: datetime, want_titles: bool,
    ) -> SyncResult:
        params = dict(
            _SYNC_QUERY,
            calendarId=calendar_id,
            fields=_FIELDS_TITLES if want_titles else _FIELDS_TIMES,
        )
        if token:
            # timeMin/timeMax are FORBIDDEN alongside syncToken (400, not a
            # warning). The window is re-applied on our side instead — see the
            # _in_window filter below.
            params["syncToken"] = token
        else:
            params["timeMin"] = _rfc3339(window_start)
            params["timeMax"] = _rfc3339(window_end)

        changed: list[ExternalEventData] = []
        deleted: list[str] = []
        page_token = None
        next_token = None
        while True:
            call = dict(params)
            if page_token:
                call["pageToken"] = page_token
            resp = self._svc().events().list(**call).execute()

            for item in resp.get("items", []):
                # A tombstone is guaranteed to carry nothing but an id, so it is
                # matched on that alone.
                if item.get("status") == "cancelled":
                    deleted.append(item["id"])
                    continue
                if item.get("eventType") in _SKIP_EVENT_TYPES:
                    deleted.append(item["id"])  # may have been mirrored before
                    continue
                parsed = _parse_event(item, want_titles)
                if parsed is None:
                    continue
                # An incremental round ignores our window entirely, so anything
                # that drifted outside it is dropped rather than mirrored — and
                # dropped as a DELETE, since it may already be in the mirror
                # from when it was in range.
                if parsed.end <= window_start or parsed.start >= window_end:
                    deleted.append(parsed.external_id)
                    continue
                changed.append(parsed)

            page_token = resp.get("nextPageToken")
            if page_token:
                continue          # nextSyncToken is not on this page — keep going
            next_token = resp.get("nextSyncToken")
            break

        return SyncResult(
            changed=changed, deleted_ids=deleted,
            next_token=next_token, full=token is None,
        )


def _parse_event(item: dict, want_titles: bool) -> ExternalEventData | None:
    """One events.list item -> the mirror's shape, or None if unusable.

    All-day events arrive as plain dates with an EXCLUSIVE end, and carry no zone
    at all — there is no correct UTC instant for them, so they are pinned to UTC
    midnight and flagged `all_day` for the UI to render as a day rather than a
    time. Everything else in the app is already UTC-internally / rendered-per-
    viewer, so this is the same trade made everywhere else.
    """
    start_node, end_node = item.get("start") or {}, item.get("end") or {}
    all_day = "date" in start_node
    try:
        if all_day:
            start = datetime.combine(
                datetime.fromisoformat(start_node["date"]).date(),
                time.min, tzinfo=timezone.utc,
            )
            end = datetime.combine(
                datetime.fromisoformat(end_node["date"]).date(),
                time.min, tzinfo=timezone.utc,
            )
        else:
            start = datetime.fromisoformat(start_node["dateTime"]).astimezone(timezone.utc)
            end = datetime.fromisoformat(end_node["dateTime"]).astimezone(timezone.utc)
    except (KeyError, ValueError):
        # endTimeUnspecified events and anything malformed. Skipping beats
        # mirroring a block at a time nobody agreed to.
        return None
    if end <= start:
        return None
    return ExternalEventData(
        external_id=item["id"],
        start=start,
        end=end,
        # Absent from the response entirely unless the mask asked for it, so this
        # is belt-and-braces rather than the actual guard.
        title=(item.get("summary") or None) if want_titles else None,
        location=(item.get("location") or None) if want_titles else None,
        all_day=all_day,
        # "transparent" = on the calendar without occupying it (Google's default
        # for all-day events and birthdays). Anything else blocks time.
        busy=item.get("transparency") != "transparent",
    )
