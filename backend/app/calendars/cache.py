"""Short-TTL in-process cache for member busy ranges.

Availability is read constantly — every group load, the poll's periodic refresh,
and each step of a multi-step agent run — but a person's busy blocks don't change
second-to-second. Caching each account's freebusy for a short window (default
90s, see config.FREEBUSY_CACHE_TTL_SECONDS) cuts redundant provider/API calls and
keeps us inside free calendar-API quotas.

CRITICAL (v1 decision): this is a DISPLAY cache. Booking never reads it — the
availability truth at the moment of booking must be live, so no conflict can slip
through a stale entry. After a booking lands, the organizer's entry is
invalidated (see tools/booking.py) so the very next read reflects the new event.

Coverage & clipping: an entry fetched for one window satisfies any request for a
SUB-window (the cached blocks are clipped to what was asked). A request for a
WIDER window misses and refetches, replacing the entry — so an account's entry
grows to the largest window seen. Entries are keyed by CalendarAccount id, so a
user's calendars are cached independently and unioned by the caller.
"""
from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.calendars.base import Interval
from app.core.config import FREEBUSY_CACHE_TTL_SECONDS

# A miss handler: given (time_min, time_max), return live busy ranges for them.
Fetch = Callable[[datetime, datetime], list[Interval]]


@dataclass
class _Entry:
    window_start: datetime
    window_end: datetime
    busy: list[Interval]
    fetched_at: float  # monotonic seconds — immune to wall-clock changes


def _clip(busy: list[Interval], lo: datetime, hi: datetime) -> list[Interval]:
    """Blocks intersected with [lo, hi]. Input is merged/non-overlapping, so the
    output stays merged too (clipping preserves disjointness)."""
    out: list[Interval] = []
    for start, end in busy:
        if end <= lo or start >= hi:
            continue
        out.append((max(start, lo), min(end, hi)))
    return out


class FreebusyCache:
    def __init__(
        self,
        ttl_seconds: float = FREEBUSY_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = _time.monotonic,
    ):
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._store: dict[int, _Entry] = {}

    def get_busy(
        self, account_id: int | None, fetch: Fetch,
        time_min: datetime, time_max: datetime,
    ) -> list[Interval]:
        """Cached busy for [time_min, time_max], calling `fetch` only on a miss.

        `fetch` runs OUTSIDE the lock (it may hit the network), so a slow
        calendar call never blocks other accounts' reads. At our scale the rare
        duplicate fetch from two concurrent misses is a fine trade."""
        if account_id is None:  # unsaved account — nothing stable to key on
            return fetch(time_min, time_max)

        with self._lock:
            entry = self._store.get(account_id)
            if (
                entry is not None
                and self._clock() - entry.fetched_at < self._ttl
                and entry.window_start <= time_min
                and entry.window_end >= time_max
            ):
                return _clip(entry.busy, time_min, time_max)

        busy = fetch(time_min, time_max)
        with self._lock:
            self._store[account_id] = _Entry(time_min, time_max, busy, self._clock())
        return busy

    def invalidate(self, account_id: int | None) -> None:
        """Drop an account's entry so the next read is live (e.g. right after a
        booking wrote a new event to that calendar)."""
        if account_id is None:
            return
        with self._lock:
            self._store.pop(account_id, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# App-wide singleton — one process-local cache shared across requests.
freebusy_cache = FreebusyCache()
