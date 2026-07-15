"""Pure interval math: turn everyone's busy blocks into common free slots.

No network, no Google types — just tz-aware datetimes in and out. That makes
this the most unit-testable (and most bug-prone, hence most tested) piece.

Timezone rules, enforced hard:
- Every datetime entering this module MUST be tz-aware (naive -> ValueError).
- All interval math happens in UTC.
- Local timezones appear in exactly one place: clipping candidate windows to
  "reasonable hours" (e.g. 09:00-22:00), which is inherently a local-time
  concept. The caller says which timezone that filter applies in.

Pipeline (see find_common_slots):
  busy blocks of all members
    -> union/merge          (anyone busy = group busy)
    -> complement in window (group free = window minus merged busy)
    -> clip to local hours  (no 3am slots)
    -> keep windows >= requested duration
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r} — all times must be tz-aware")
    return dt.astimezone(timezone.utc)


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Union of intervals: sorted, overlaps and touching edges merged."""
    ivs = sorted(
        ((_ensure_utc(s), _ensure_utc(e)) for s, e in intervals if e > s),
        key=lambda iv: iv[0],
    )
    merged: list[Interval] = []
    for s, e in ivs:
        if merged and s <= merged[-1][1]:  # overlaps or touches previous
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def complement(merged_busy: list[Interval], window_start: datetime, window_end: datetime) -> list[Interval]:
    """Gaps inside [window_start, window_end) not covered by merged_busy."""
    window_start, window_end = _ensure_utc(window_start), _ensure_utc(window_end)
    free: list[Interval] = []
    cursor = window_start
    for s, e in merged_busy:
        if e <= cursor or s >= window_end:
            continue
        if s > cursor:
            free.append((cursor, min(s, window_end)))
        cursor = max(cursor, e)
        if cursor >= window_end:
            break
    if cursor < window_end:
        free.append((cursor, window_end))
    return free


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Intersection of two sorted, non-overlapping interval lists."""
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s = max(a[i][0], b[j][0])
        e = min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        # advance whichever interval ends first
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def reasonable_hours(
    window_start: datetime,
    window_end: datetime,
    tz_name: str,
    earliest_hour: int,
    latest_hour: int,
) -> list[Interval]:
    """Allowed [earliest, latest] block of each LOCAL day, as UTC intervals.

    Computed per-day in the local timezone (so DST shifts are handled by
    zoneinfo), then converted to UTC for the interval math.
    """
    if not (0 <= earliest_hour < latest_hour <= 23):
        raise ValueError("need 0 <= earliest_hour < latest_hour <= 23")
    tz = ZoneInfo(tz_name)
    start_local = _ensure_utc(window_start).astimezone(tz)
    end_local = _ensure_utc(window_end).astimezone(tz)

    allowed: list[Interval] = []
    day = start_local.date()
    while day <= end_local.date():
        day_start = datetime.combine(day, time(earliest_hour), tzinfo=tz)
        day_end = datetime.combine(day, time(latest_hour), tzinfo=tz)
        allowed.append((day_start.astimezone(timezone.utc), day_end.astimezone(timezone.utc)))
        day += timedelta(days=1)
    return allowed


def find_common_slots(
    busy_by_member: dict[str, list[Interval]],
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    tz_name: str = "Asia/Beirut",
    earliest_hour: int = 9,
    latest_hour: int = 22,
) -> list[Interval]:
    """Windows where EVERY member is free, within reasonable local hours,
    long enough for the requested duration. Returned in UTC, chronological.

    A member with an empty busy list counts as fully free — the caller is
    responsible for distinguishing "free all week" from "calendar not
    connected" BEFORE building busy_by_member.
    """
    all_busy = [iv for member_busy in busy_by_member.values() for iv in member_busy]
    group_free = complement(merge_intervals(all_busy), window_start, window_end)
    in_hours = intersect(group_free, reasonable_hours(
        window_start, window_end, tz_name, earliest_hour, latest_hour
    ))
    need = timedelta(minutes=duration_minutes)
    return [(s, e) for s, e in in_hours if e - s >= need]
