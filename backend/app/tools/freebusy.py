"""Live availability via Google Calendar's freebusy endpoint.

PRIVACY BY ARCHITECTURE: freebusy.query returns ONLY busy time ranges —
no titles, no descriptions, no attendees. Event contents never enter this
system. This module is the only place member availability is read.
"""
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

Interval = tuple[datetime, datetime]


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r} — all times must be tz-aware")
    return dt.astimezone(timezone.utc).isoformat()


def _parse_utc(s: str) -> datetime:
    # Google returns RFC3339 like "2026-07-14T09:00:00Z"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def query_busy(creds: Credentials, time_min: datetime, time_max: datetime) -> list[Interval]:
    """Busy ranges (UTC) on the account's primary calendar, live at call time."""
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body = {
        "timeMin": _to_utc_iso(time_min),
        "timeMax": _to_utc_iso(time_max),
        "items": [{"id": "primary"}],
    }
    resp = service.freebusy().query(body=body).execute()
    busy = resp["calendars"]["primary"].get("busy", [])
    return [(_parse_utc(b["start"]), _parse_utc(b["end"])) for b in busy]
