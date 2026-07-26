"""Short-TTL freebusy cache (Phase 1 Branch 3): fetch only on a miss, serve
sub-windows from a wider cached entry (clipped), expire on TTL, key per account,
and support explicit invalidation. Clock is injected so nothing sleeps.
"""
from datetime import datetime, timezone

from app.calendars.cache import FreebusyCache, _clip


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _dt(hour, minute=0):
    return datetime(2026, 1, 5, hour, minute, tzinfo=timezone.utc)


def _counting_fetch(busy):
    """A fetch that records the windows it was asked for and returns `busy`."""
    calls = []

    def fetch(lo, hi):
        calls.append((lo, hi))
        return list(busy)

    return fetch, calls


def test_miss_then_hit_within_ttl():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([(_dt(9), _dt(10))])
    lo, hi = _dt(8), _dt(12)

    first = cache.get_busy(1, fetch, lo, hi)
    second = cache.get_busy(1, fetch, lo, hi)

    assert first == [(_dt(9), _dt(10))]
    assert second == first
    assert len(calls) == 1  # the second read came from cache


def test_entry_expires_after_ttl():
    clock = Clock()
    cache = FreebusyCache(ttl_seconds=100, clock=clock)
    fetch, calls = _counting_fetch([(_dt(9), _dt(10))])
    lo, hi = _dt(8), _dt(12)

    cache.get_busy(1, fetch, lo, hi)
    clock.t = 99
    cache.get_busy(1, fetch, lo, hi)   # still fresh
    assert len(calls) == 1
    clock.t = 101
    cache.get_busy(1, fetch, lo, hi)   # now stale -> refetch
    assert len(calls) == 2


def test_subwindow_is_served_from_a_wider_entry():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([(_dt(9), _dt(10)), (_dt(11), _dt(11, 30))])

    cache.get_busy(1, fetch, _dt(8), _dt(12))            # wide fetch
    clipped = cache.get_busy(1, fetch, _dt(9, 30), _dt(11, 15))  # sub-window

    assert len(calls) == 1  # no refetch
    # 09:00–10:00 clipped to 09:30; 11:00–11:30 clipped to 11:15
    assert clipped == [(_dt(9, 30), _dt(10)), (_dt(11), _dt(11, 15))]


def test_wider_window_misses_and_replaces_entry():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([(_dt(9), _dt(10))])

    cache.get_busy(1, fetch, _dt(9), _dt(11))   # narrow
    cache.get_busy(1, fetch, _dt(8), _dt(12))   # wider -> not covered -> refetch
    assert len(calls) == 2
    # the entry now covers the wider window: a sub-window of it hits cache
    cache.get_busy(1, fetch, _dt(8, 30), _dt(11, 30))
    assert len(calls) == 2


def test_accounts_are_cached_independently():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([(_dt(9), _dt(10))])
    lo, hi = _dt(8), _dt(12)

    cache.get_busy(1, fetch, lo, hi)
    cache.get_busy(2, fetch, lo, hi)   # different account -> its own miss
    cache.get_busy(1, fetch, lo, hi)   # account 1 still cached
    assert len(calls) == 2


def test_none_account_id_never_caches():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([(_dt(9), _dt(10))])
    lo, hi = _dt(8), _dt(12)

    cache.get_busy(None, fetch, lo, hi)
    cache.get_busy(None, fetch, lo, hi)
    assert len(calls) == 2  # uncacheable -> always live


def test_invalidate_forces_a_refetch():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([(_dt(9), _dt(10))])
    lo, hi = _dt(8), _dt(12)

    cache.get_busy(1, fetch, lo, hi)
    cache.invalidate(1)
    cache.get_busy(1, fetch, lo, hi)
    assert len(calls) == 2


def test_fetch_is_asked_for_the_exact_window():
    cache = FreebusyCache(ttl_seconds=100, clock=Clock())
    fetch, calls = _counting_fetch([])
    cache.get_busy(1, fetch, _dt(8), _dt(12))
    assert calls == [(_dt(8), _dt(12))]


def test_clip_drops_non_overlapping_blocks():
    busy = [(_dt(7), _dt(8)), (_dt(9), _dt(10)), (_dt(13), _dt(14))]
    assert _clip(busy, _dt(8, 30), _dt(11)) == [(_dt(9), _dt(10))]
