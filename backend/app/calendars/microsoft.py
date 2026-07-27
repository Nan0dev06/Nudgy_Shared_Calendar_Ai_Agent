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

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.auth.microsoft import access_token, refresh_token
from app.calendars.base import CalendarProvider, CreatedEvent, Interval
from app.db import repo
from app.db.models import CalendarAccount
from app.tools.slots import merge_intervals

_GRAPH = "https://graph.microsoft.com/v1.0"
# freeBusyStatus values that count as busy (compared lowercased). oof = out of
# office; unknown is treated as busy so ambiguous data never causes a double-book.
_BUSY = {"busy", "tentative", "oof", "unknown"}
_UTC_PREFER = 'outlook.timezone="UTC"'


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
