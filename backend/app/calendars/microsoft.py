"""Microsoft Graph implementation of CalendarProvider (Outlook calendars).

Reads use `/me/calendarView` (NOT `getSchedule`, which rejects personal
Microsoft accounts — and Nudgy is consumer-first). The privacy invariant is kept
by `$select`: busy reads ask for only start/end/showAs, location reads ask for
only location — never subject, attendees, or body.

Writes use `/me/events`. Graph has no `sendUpdates` knob: creating an event with
attendees always emails the invites and deleting a meeting always sends a
cancellation, so the Google `sendUpdates="all"` behaviour is simply the default.

Datetime gotcha: Graph wants a naive wall-clock string (no `Z`/offset) paired
with a separate `timeZone`; and it RETURNS `dateTime` with 7 fractional digits
and no zone. `_graph_dt`/`_parse_utc` handle both — do NOT reuse Google's RFC3339
formatter (it emits a `Z` Graph mishandles).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.auth.microsoft import access_token, refresh_token
from app.calendars.base import (
    CalendarProvider, CreatedEvent, ExternalEventData, Interval, SyncCalendar,
    SyncResult,
)
from app.db import repo
from app.db.models import CalendarAccount
from app.tools.slots import merge_intervals

log = logging.getLogger("nudgy.calendars")

_GRAPH = "https://graph.microsoft.com/v1.0"
# freeBusyStatus values that count as busy (compared lowercased). oof = out of
# office; unknown is treated as busy so ambiguous data never causes a double-book.
_BUSY = {"busy", "tentative", "oof", "unknown"}
_UTC_PREFER = 'outlook.timezone="UTC"'

# Inbound sync (delta query). Two Prefer values, comma-joined into one header:
#   odata.maxpagesize  — an upper bound on page size, re-sent every request
#                        because it lives in the header, not in the token
#   IdType="ImmutableId" — WITHOUT this, Graph event ids change when an item
#                        moves between folders, which would silently duplicate
#                        every moved event in a mirror keyed on that id
_DELTA_PREFER = 'odata.maxpagesize=50, IdType="ImmutableId"'
# Graph error codes meaning "that bookmark is gone, start over". The status is
# the reliable signal (410); the codes are a documented-but-not-contractual
# second chance, so both are checked.
_RESYNC_CODES = {
    "syncStateNotFound", "syncStateNotFoundPartialSyncSupported", "resyncRequired",
}
# A runaway nextLink chain would burn the whole per-mailbox request budget
# (10,000 requests / 10 minutes), so paging is bounded.
_MAX_DELTA_PAGES = 200


def _graph_dt(dt: datetime) -> str:
    """UTC wall-clock with NO trailing Z/offset — the zone travels in a separate
    `timeZone: "UTC"` field."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_utc(node: dict) -> datetime:
    """Graph dateTimeTimeZone -> tz-aware UTC. Graph returns 7 fractional digits
    and no zone; we requested UTC, so truncate to 6 digits and stamp UTC."""
    s = node["dateTime"]
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class MicrosoftCalendarProvider(CalendarProvider):
    def __init__(self, token_json: str):
        self._token = access_token(token_json)  # a fresh access token

    @classmethod
    def from_account(cls, session: Session, account: CalendarAccount) -> "MicrosoftCalendarProvider":
        """Build from a stored account, persisting a silent token refresh — the
        exact refresh-persist pattern GoogleCalendarProvider.from_account uses."""
        token_json, refreshed = refresh_token(account.token_json)
        if refreshed:
            repo.set_account_token(session, account, token_json)
        return cls(token_json)

    # --- HTTP helpers -------------------------------------------------------

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _calendar_view(self, time_min: datetime, time_max: datetime, select: str) -> list[dict]:
        """All events overlapping [time_min, time_max], following paging, asking
        Graph for ONLY the `select` fields and UTC times."""
        headers = {**self._auth(), "Prefer": _UTC_PREFER}
        params = {
            "startDateTime": time_min.astimezone(timezone.utc).isoformat(),
            "endDateTime": time_max.astimezone(timezone.utc).isoformat(),
            "$select": select,
            "$top": "100",
        }
        items: list[dict] = []
        url = f"{_GRAPH}/me/calendarView"
        first = True
        while url:
            resp = httpx.get(url, params=params if first else None, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")  # already carries the query params
            first = False
        return items

    # --- reads (privacy-scoped via $select) ---------------------------------

    def get_busy(self, time_min: datetime, time_max: datetime) -> list[Interval]:
        items = self._calendar_view(time_min, time_max, "start,end,showAs")
        intervals = [
            (_parse_utc(it["start"]), _parse_utc(it["end"]))
            for it in items
            # missing/null showAs -> "unknown" -> busy, so ambiguous data never
            # causes a double-book (matches the _BUSY note above)
            if str(it.get("showAs") or "unknown").lower() in _BUSY
        ]
        return merge_intervals(intervals)

    def get_event_locations(
        self, slot_start: datetime, slot_end: datetime, window_hours: int = 2,
    ) -> list[str]:
        lo = slot_start - timedelta(hours=window_hours)
        hi = slot_end + timedelta(hours=window_hours)
        items = self._calendar_view(lo, hi, "location")
        out: list[str] = []
        for it in items:
            name = (it.get("location") or {}).get("displayName")
            if name and name.strip():
                out.append(name.strip())
        return out

    # --- writes -------------------------------------------------------------

    def create_event(
        self, *, summary: str, start: datetime, end: datetime,
        attendee_emails: list[str], location: str | None = None,
        description: str | None = None,
    ) -> CreatedEvent:
        body: dict = {
            "subject": summary,
            "start": {"dateTime": _graph_dt(start), "timeZone": "UTC"},
            "end": {"dateTime": _graph_dt(end), "timeZone": "UTC"},
            "attendees": [
                {"emailAddress": {"address": e}, "type": "required"}
                for e in attendee_emails
            ],
        }
        if description is not None:
            body["body"] = {"contentType": "text", "content": description}
        if location:
            body["location"] = {"displayName": location}
        r = httpx.post(f"{_GRAPH}/me/events", json=body, headers=self._auth(), timeout=30)
        r.raise_for_status()
        ev = r.json()
        return CreatedEvent(id=ev.get("id"), link=ev.get("webLink"))

    def update_event(
        self, event_id: str, *, summary: str | None = None,
        start: datetime | None = None, end: datetime | None = None,
        location: str | None = None,
    ) -> CreatedEvent:
        patch: dict = {}
        if summary is not None:
            patch["subject"] = summary
        if start is not None:
            patch["start"] = {"dateTime": _graph_dt(start), "timeZone": "UTC"}
        if end is not None:
            patch["end"] = {"dateTime": _graph_dt(end), "timeZone": "UTC"}
        if location is not None:
            patch["location"] = {"displayName": location}
        r = httpx.patch(
            f"{_GRAPH}/me/events/{event_id}", json=patch, headers=self._auth(), timeout=30,
        )
        r.raise_for_status()
        ev = r.json()
        return CreatedEvent(id=ev.get("id"), link=ev.get("webLink"))

    def delete_event(self, event_id: str) -> None:
        r = httpx.delete(f"{_GRAPH}/me/events/{event_id}", headers=self._auth(), timeout=30)
        r.raise_for_status()

    # --- inbound sync ---------------------------------------------------------

    def list_sync_calendars(self) -> list[SyncCalendar]:
        """Exactly one calendar: the mailbox's own calendarView.

        Graph v1.0 documents delta on `/me/calendarView` (and the app-permission
        `/users/{id}/calendarView`) — the per-calendar
        `/me/calendars/{id}/calendarView/delta` form appears only on the beta
        reference. So this mirrors the mailbox view, which is precisely what
        `get_busy` above already reads: the two stay in step, and nobody gets a
        busy block from a calendar the mirror can't explain. Mirroring each
        Outlook calendar separately waits on that endpoint reaching v1.0.

        The id is the empty string — there is no per-calendar id in this form —
        and the name is fetched for the UI, best-effort.
        """
        name = None
        try:
            r = httpx.get(
                f"{_GRAPH}/me/calendar", params={"$select": "name"},
                headers=self._auth(), timeout=30,
            )
            if r.status_code == 200:
                name = r.json().get("name")
        except httpx.HTTPError:
            pass  # a display label is not worth failing a sync over
        return [SyncCalendar(id="", name=name)]

    def sync_events(
        self, calendar_id: str, *, token: str | None,
        window_start: datetime, window_end: datetime, want_titles: bool,
    ) -> SyncResult:
        """One round of Graph's calendarView delta.

        PRIVACY, HONESTLY: unlike Google's field mask, this request cannot ask
        for less — `$select` is not supported on a delta call, so the body,
        bodyPreview, attendees and location of every event come over the wire
        whatever the owner chose. The opt-in is therefore enforced at ingestion
        (`_parse_delta_item` drops them before anything is returned) rather than
        at the request. Nothing untitled is ever persisted, but the honest
        statement is "we don't keep it", not "we never received it".
        """
        start_url = token or self._delta_url(window_start, window_end)
        try:
            return self._delta_round(start_url, want_titles, full=token is None)
        except _ResyncRequired as exc:
            if not token:
                raise  # a resync demand on a fresh full read is a real failure
            log.info("[sync] microsoft: delta token rejected — full read")
            fresh = exc.location or self._delta_url(window_start, window_end)
            return self._delta_round(fresh, want_titles, full=True)

    @staticmethod
    def _delta_url(window_start: datetime, window_end: datetime) -> str:
        """The initial (token-less) delta call.

        startDateTime/endDateTime are REQUIRED here and are then encoded INTO the
        token, which is why they must never be re-sent on a follow-up call — and
        why the window can't drift: a token minted for this window stays this
        window forever. Rolling the horizon means starting a fresh round, which
        the caller decides by comparing its stored bounds (see
        jobs/calendar_sync.py).
        """
        return (
            f"{_GRAPH}/me/calendarView/delta"
            f"?startDateTime={_graph_dt(window_start)}Z"
            f"&endDateTime={_graph_dt(window_end)}Z"
        )

    def _delta_round(self, url: str, want_titles: bool, full: bool) -> SyncResult:
        headers = {**self._auth(), "Prefer": _DELTA_PREFER}
        changed: list[ExternalEventData] = []
        deleted: list[str] = []
        next_token = None

        for _ in range(_MAX_DELTA_PAGES):
            resp = httpx.get(url, headers=headers, timeout=30)
            if _is_resync(resp):
                raise _ResyncRequired(resp.headers.get("Location"))
            resp.raise_for_status()
            page = resp.json()

            for item in page.get("value", []):
                # "@removed" covers BOTH "the user deleted it" and "it was edited
                # out of our window" — same action either way, it leaves the
                # mirror. What it does NOT mean is that the meeting is cancelled,
                # so nothing else may be inferred from it.
                if "@removed" in item:
                    deleted.append(item["id"])
                    continue
                if item.get("isCancelled"):
                    deleted.append(item["id"])
                    continue
                parsed = _parse_delta_item(item, want_titles)
                if parsed is None:
                    continue
                changed.append(parsed)

            # Exactly one of the two links is present. deltaLink means the round
            # is complete and this is the bookmark; nextLink means keep walking
            # and there is no bookmark yet. An empty page with a nextLink is
            # legal, so the loop terminates on the links, never on emptiness.
            if "@odata.deltaLink" in page:
                next_token = page["@odata.deltaLink"]
                break
            url = page.get("@odata.nextLink")
            if not url:
                raise RuntimeError("Graph delta page had neither nextLink nor deltaLink")
        else:
            raise RuntimeError("Graph delta paging did not terminate")

        return SyncResult(
            changed=changed, deleted_ids=deleted, next_token=next_token, full=full,
        )


class _ResyncRequired(Exception):
    """Graph says the stored deltaLink is unusable; re-read the whole window."""

    def __init__(self, location: str | None = None):
        super().__init__(location or "")
        self.location = location


def _is_resync(resp: httpx.Response) -> bool:
    """Does this response mean "throw the bookmark away"?

    410 is the documented synchronization reset. Microsoft is deliberately vague
    about expiry ("a 40X-series error with error codes such as syncStateNotFound"),
    so a 4xx is also inspected — status first, code second, because the code set
    is not contractual. 429 is NOT here: that is throttling, and the same link
    should be retried rather than discarded.
    """
    if resp.status_code == 410:
        return True
    if not 400 <= resp.status_code < 500:
        return False
    try:
        return (resp.json().get("error") or {}).get("code") in _RESYNC_CODES
    except ValueError:
        return False


def _parse_delta_item(item: dict, want_titles: bool) -> ExternalEventData | None:
    """One delta payload entry -> the mirror's shape, or None if unusable.

    This is where the titles opt-in is enforced for Graph (see sync_events):
    subject and location are read only when asked for, and body / bodyPreview /
    attendees / organizer are never read at all, so they cannot be stored by
    accident later.
    """
    start_node, end_node = item.get("start") or {}, item.get("end") or {}
    try:
        start, end = _parse_utc(start_node), _parse_utc(end_node)
    except (KeyError, ValueError):
        return None
    if end <= start:
        return None
    show_as = str(item.get("showAs") or "unknown").lower()
    return ExternalEventData(
        external_id=item["id"],
        start=start,
        end=end,
        title=(item.get("subject") or None) if want_titles else None,
        location=(
            ((item.get("location") or {}).get("displayName") or None)
            if want_titles else None
        ),
        # Graph has no date-only shape: an all-day event is midnight-to-midnight
        # with a flag, which is why the flag is the only reliable test.
        all_day=bool(item.get("isAllDay")),
        # Same rule get_busy uses, so a mirrored event and the busy block it
        # explains never disagree about whether the time is occupied.
        busy=show_as in _BUSY,
    )
