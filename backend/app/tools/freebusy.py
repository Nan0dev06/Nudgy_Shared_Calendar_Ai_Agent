"""Live availability via Google Calendar's freebusy endpoint.

PRIVACY BY ARCHITECTURE: freebusy.query returns ONLY busy time ranges —
no titles, no descriptions, no attendees. Event contents never enter this
system. This module is the only place member availability is read.

We read every calendar the member OWNS or can EDIT (personal, uni, summer
semester, …) and union their busy blocks — being busy on ANY of them makes
the member busy. Read-only *subscribed* calendars (Holidays, a friend's
calendar) are skipped so they don't over-block the group's availability.
"""
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.tools.slots import merge_intervals

Interval = tuple[datetime, datetime]

# calendarList accessRoles that mean "this is my own commitment" — we skip
# "reader" and "freeBusyReader", which are subscribed/read-only calendars.
# Public because inbound sync (calendars/google.py) must mirror exactly the set
# of calendars that makes somebody busy here: a calendar that blocks your time
# but is never mirrored shows up as an unexplainable block.
OWNED_ROLES = {"owner", "writer"}


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r} — all times must be tz-aware")
    return dt.astimezone(timezone.utc).isoformat()


def _parse_utc(s: str) -> datetime:
    # Google returns RFC3339 like "2026-07-14T09:00:00Z"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _owned_calendar_ids(service) -> list[str]:
    """IDs of every calendar the account owns or can edit, across all pages.

    Falls back to ["primary"] if the list comes back empty, so a member is
    never treated as fully-free just because calendarList was unavailable.
    """
    ids: list[str] = []
    page_token = None
    while True:
        resp = service.calendarList().list(pageToken=page_token).execute()
        for cal in resp.get("items", []):
            if cal.get("accessRole") in OWNED_ROLES:
                ids.append(cal["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids or ["primary"]


def query_busy(creds: Credentials, time_min: datetime, time_max: datetime) -> list[Interval]:
    """Merged busy ranges (UTC) across all calendars the account owns/edits.

    One freebusy call covers every owned calendar; busy blocks are unioned so
    an overlap on two calendars collapses into one range.
    """
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    cal_ids = _owned_calendar_ids(service)
    body = {
        "timeMin": _to_utc_iso(time_min),
        "timeMax": _to_utc_iso(time_max),
        "items": [{"id": cid} for cid in cal_ids],
    }
    resp = service.freebusy().query(body=body).execute()
    calendars = resp.get("calendars", {})
    busy = [
        (_parse_utc(b["start"]), _parse_utc(b["end"]))
        for cid in cal_ids
        for b in calendars.get(cid, {}).get("busy", [])
    ]
    return merge_intervals(busy)
