"""In-process pub/sub behind the live feed.

The app used to keep itself current by refetching `GET /groups/{id}/plans`
every 5 seconds while any plan was open — a request per member per 5s, most of
them returning exactly what the client already had, and still up to 5s late.
Now the server says when something changed and the client refetches once.

What travels on the bus is deliberately tiny: a group id and a KIND ("plans",
"events"). Never plan contents. Two reasons:

  1. Privacy/authorization. A plan's JSON is viewer-specific — the host sees the
     tally and the share link, a member sees only their own ballot. Pushing
     rendered state down a socket would mean rendering it per subscriber and
     getting that right forever. A poke carries nothing the subscriber wasn't
     already allowed to fetch, so the existing endpoint stays the single place
     that decides who sees what.
  2. Coalescing. Five votes landing in the same second are one refetch, not
     five events replayed in order. `Subscriber` holds a SET of pending kinds
     and a flag, not a queue, so a burst can never build a backlog and there is
     no bounded-queue overflow case to get wrong.

Scope: one process. `publish` reaches subscribers connected to THIS worker, so
a multi-worker deployment needs Redis (or similar) behind the same interface —
which is why publishing goes through `app.realtime.plans_changed(...)` rather
than touching this module directly. Until then the frontend keeps a slow poll
as a backstop, so a missed poke costs latency, never correctness.

Thread safety: the ticker calls `run_tick` in a worker thread (see
jobs/plan_ticker), so `publish` can be called from a thread that is not the
event loop's. The registry is guarded by a plain lock, and each subscriber is
woken through its own loop with `call_soon_threadsafe`.
"""
from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("nudgy.realtime")


class Subscriber:
    """One connected client's inbox. Created by `EventBus.subscribe` on the
    event loop that will consume it."""

    def __init__(self, group_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.group_id = group_id
        self.loop = loop
        self._pending: set[str] = set()
        self._ready = asyncio.Event()

    # -- producer side (runs ON the consumer's loop, via call_soon_threadsafe)
    def offer(self, kind: str) -> None:
        self._pending.add(kind)
        self._ready.set()

    # -- consumer side
    async def wait(self, timeout: float) -> set[str] | None:
        """Block until something is pending, then take it. None on timeout,
        which is the stream's cue to send a heartbeat."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        kinds, self._pending = self._pending, set()
        self._ready.clear()
        return kinds


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[Subscriber]] = {}
        self._lock = threading.Lock()

    def subscribe(self, group_id: int) -> Subscriber:
        sub = Subscriber(group_id, asyncio.get_running_loop())
        with self._lock:
            self._subs.setdefault(group_id, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            group = self._subs.get(sub.group_id)
            if group is None:
                return
            group.discard(sub)
            if not group:  # don't accumulate an entry per group ever visited
                self._subs.pop(sub.group_id, None)

    def publish(self, group_id: int, kind: str) -> int:
        """Poke everyone watching this group. Returns how many were reached.

        Never raises: this is called from the middle of request handlers and a
        booking must not fail because a listener's loop went away.
        """
        with self._lock:
            subs = list(self._subs.get(group_id, ()))
        reached = 0
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub.offer, kind)
                reached += 1
            except RuntimeError:
                # the consumer's loop is closed — its connection is gone and the
                # `finally` that unsubscribes never got to run
                self.unsubscribe(sub)
            except Exception:  # pragma: no cover - defensive
                log.exception("publish to a %s subscriber failed", kind)
        return reached

    def subscriber_count(self, group_id: int | None = None) -> int:
        with self._lock:
            if group_id is not None:
                return len(self._subs.get(group_id, ()))
            return sum(len(s) for s in self._subs.values())
