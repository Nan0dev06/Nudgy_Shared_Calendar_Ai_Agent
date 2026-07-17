"""Availability service — the bridge between the DB and the Phase 1 slot math.

Given a group, this fetches LIVE freebusy for every connected member (fresh at
call time, per the real-time requirement), then reuses app/tools/slots.py to
compute common free windows and a partial-availability breakdown for graceful
degradation when no window works for everyone.

Privacy: only busy time ranges are ever read (freebusy). No event titles.
Token refresh: if a member's OAuth token was refreshed during load, the new
token is written back to the DB immediately so it never silently goes stale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.auth.google import credentials_from_json
from app.db.models import Group, User
from app.db import repo
from app.tools.freebusy import query_busy
from app.tools.slots import (
    Interval,
    complement,
    find_common_slots,
    intersect,
    merge_intervals,
    reasonable_hours,
)

log = logging.getLogger("orbi.agent")


@dataclass
class MemberBusy:
    email: str
    connected: bool
    busy: list[Interval] = field(default_factory=list)


@dataclass
class Slot:
    start: datetime  # UTC
    end: datetime    # UTC


@dataclass
class PartialWindow:
    """A window where SOME (not all) members are free — used to answer
    'no time works for everyone' with the closest alternatives."""
    start: datetime
    end: datetime
    free_emails: list[str]
    busy_emails: list[str]


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%a %d %b %H:%M")


def fetch_busy_for_group(
    session: Session, group: Group, now: datetime, days_ahead: int
) -> list[MemberBusy]:
    """Live freebusy for every member. Logs each call so the loop is visible."""
    window_end = now + timedelta(days=days_ahead)
    members = repo.get_group_members(session, group.id)
    results: list[MemberBusy] = []
    for user in members:
        if not user.calendar_connected:
            log.info("[freebusy] %s — NOT connected, skipping", user.email)
            results.append(MemberBusy(email=user.email, connected=False))
            continue
        creds, refreshed = credentials_from_json(user.token_json)
        if refreshed:  # token was expired and we refreshed it — persist it
            repo.set_user_token(session, user, refreshed)
            log.info("[freebusy] %s — token refreshed & saved", user.email)
        busy = query_busy(creds, now, window_end)
        log.info("[freebusy] %s — %d busy block(s)", user.email, len(busy))
        results.append(MemberBusy(email=user.email, connected=True, busy=busy))
    return results


def compute_availability(
    session: Session,
    group: Group,
    now: datetime,
    days_ahead: int,
    duration_minutes: int,
    tz_name: str,
    earliest_hour: int = 9,
    latest_hour: int = 22,
    include_member_busy: bool = False,
) -> dict:
    """Full availability picture for the agent: common slots + partial windows.

    Returns a plain dict (JSON-serializable) — this is exactly what the agent
    tool hands back to the model, so keep it readable and free of raw datetimes
    the model would have to parse. All times are pre-formatted in tz_name.
    """
    tz = ZoneInfo(tz_name)
    window_end = now + timedelta(days=days_ahead)
    members = fetch_busy_for_group(session, group, now, days_ahead)

    connected = [m for m in members if m.connected]
    not_connected = [m.email for m in members if not m.connected]

    busy_by_member = {m.email: m.busy for m in connected}
    slots = find_common_slots(
        busy_by_member, now, window_end,
        duration_minutes=duration_minutes, tz_name=tz_name,
        earliest_hour=earliest_hour, latest_hour=latest_hour,
    )
    log.info("[intersect] %d common slot(s) across %d connected member(s)",
             len(slots), len(connected))

    result = {
        "now_local": _fmt(now, tz),
        "timezone": tz_name,
        "window_days": days_ahead,
        "duration_minutes": duration_minutes,
        "reasonable_hours": f"{earliest_hour:02d}:00-{latest_hour:02d}:00",
        "members_total": len(members),
        "members_connected": len(connected),
        "members_not_connected": not_connected,
        "common_slots": [
            {
                "start": _fmt(s, tz),
                "end": _fmt(e, tz),
                # ISO forms are for tool calls (create_poll) — copy verbatim
                "start_iso": s.astimezone(tz).isoformat(),
                "end_iso": e.astimezone(tz).isoformat(),
                "duration_minutes": int((e - s).total_seconds() // 60),
            }
            for s, e in slots
        ],
    }

    # For the calendar UI: raw busy ranges per member (still only ranges —
    # freebusy never exposes titles/details, so neither can this).
    if include_member_busy:
        result["members_busy"] = [
            {
                "email": m.email,
                "connected": m.connected,
                "busy": [
                    {
                        "start_iso": s.astimezone(tz).isoformat(),
                        "end_iso": e.astimezone(tz).isoformat(),
                    }
                    for s, e in m.busy
                ],
            }
            for m in members
        ]

    # Graceful degradation: if nobody-can-all-meet, compute windows where the
    # MOST members overlap, so the model can offer the closest alternatives.
    if not slots and len(connected) >= 2:
        result["partial_windows"] = _best_partial_windows(
            connected, now, window_end, duration_minutes, tz_name,
            earliest_hour, latest_hour, tz,
        )
    return result


def _best_partial_windows(
    connected: list[MemberBusy],
    now: datetime,
    window_end: datetime,
    duration_minutes: int,
    tz_name: str,
    earliest_hour: int,
    latest_hour: int,
    tz: ZoneInfo,
    max_windows: int = 3,
) -> list[dict]:
    """Windows (>= duration, in reasonable hours) ranked by how many members
    are free. Lets Orbi say '4 of 5 are free Thursday 5pm'."""
    hours = reasonable_hours(now, window_end, tz_name, earliest_hour, latest_hour)
    need = timedelta(minutes=duration_minutes)

    # free intervals per member (within reasonable hours)
    free_per_member: dict[str, list[Interval]] = {}
    for m in connected:
        member_free = complement(merge_intervals(m.busy), now, window_end)
        free_per_member[m.email] = intersect(member_free, hours)

    # candidate boundaries: every free-interval edge
    edges = sorted({t for ivs in free_per_member.values() for iv in ivs for t in iv})
    windows: list[PartialWindow] = []
    for a, b in zip(edges, edges[1:]):
        if b - a < need:
            continue
        free = [email for email, ivs in free_per_member.items()
                if any(s <= a and b <= e for s, e in ivs)]
        if 0 < len(free) < len(connected):
            busy = [m.email for m in connected if m.email not in free]
            windows.append(PartialWindow(a, b, free, busy))

    windows.sort(key=lambda w: (-len(w.free_emails), w.start))
    return [
        {
            "start": _fmt(w.start, tz),
            "end": _fmt(w.end, tz),
            "free_count": len(w.free_emails),
            "free_members": w.free_emails,
            "busy_members": w.busy_emails,
        }
        for w in windows[:max_windows]
    ]
