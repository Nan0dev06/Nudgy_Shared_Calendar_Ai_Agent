"""The in-process pub/sub behind the live feed (app/realtime/bus.py) and the SSE
frame generator that reads from it (app/api/stream_routes.stream_frames).

Everything here is driven with asyncio.run rather than a plugin: the bus is only
async at the point where a consumer waits, so a coroutine per test is enough.
"""
import asyncio
import threading

from app.api.stream_routes import stream_frames
from app.realtime.bus import EventBus


def test_publish_wakes_a_subscriber():
    async def go():
        bus = EventBus()
        sub = bus.subscribe(7)
        assert bus.publish(7, "plans") == 1
        return await sub.wait(timeout=1)

    assert asyncio.run(go()) == {"plans"}


def test_a_burst_coalesces_into_one_wake():
    """Five votes in the same second are one refetch, not five replays — and the
    kinds survive the merge so nothing is silently dropped."""
    async def go():
        bus = EventBus()
        sub = bus.subscribe(7)
        for _ in range(5):
            bus.publish(7, "plans")
        bus.publish(7, "events")
        first = await sub.wait(timeout=1)
        # nothing left behind after the take
        second = await sub.wait(timeout=0.05)
        return first, second

    first, second = asyncio.run(go())
    assert first == {"plans", "events"}
    assert second is None


def test_wait_times_out_when_nothing_happens():
    """A quiet group has to fall through to the heartbeat instead of hanging."""
    async def go():
        bus = EventBus()
        sub = bus.subscribe(1)
        return await sub.wait(timeout=0.05)

    assert asyncio.run(go()) is None


def test_groups_are_isolated():
    async def go():
        bus = EventBus()
        mine = bus.subscribe(1)
        bus.subscribe(2)
        assert bus.publish(2, "plans") == 1
        return await mine.wait(timeout=0.05)

    assert asyncio.run(go()) is None


def test_unsubscribe_stops_delivery_and_empties_the_registry():
    async def go():
        bus = EventBus()
        sub = bus.subscribe(3)
        bus.unsubscribe(sub)
        reached = bus.publish(3, "plans")
        return reached, bus.subscriber_count(), bus.subscriber_count(3)

    assert asyncio.run(go()) == (0, 0, 0)


def test_publish_from_another_thread_reaches_the_loop():
    """The plan ticker runs run_tick in a worker thread (jobs/plan_ticker), so a
    deadline expiring publishes from off the event loop."""
    async def go():
        bus = EventBus()
        sub = bus.subscribe(9)
        threading.Thread(target=bus.publish, args=(9, "plans")).start()
        return await sub.wait(timeout=2)

    assert asyncio.run(go()) == {"plans"}


def test_stream_frames_opens_with_retry_and_hello_then_relays_kinds():
    async def go():
        bus = EventBus()
        sub = bus.subscribe(4)
        frames = stream_frames(sub, None, heartbeat=0.05)
        opening = [await anext(frames), await anext(frames)]
        bus.publish(4, "plans")
        bus.publish(4, "events")
        # one wake, both kinds -> two frames, sorted for a stable stream
        relayed = [await anext(frames), await anext(frames)]
        heartbeat = await anext(frames)
        await frames.aclose()
        return opening, relayed, heartbeat

    opening, relayed, heartbeat = asyncio.run(go())
    assert opening[0].startswith("retry: ")
    assert opening[1] == 'event: hello\ndata: {"group_id": 4}\n\n'
    assert relayed == ['event: events\ndata: {"group_id": 4}\n\n',
                       'event: plans\ndata: {"group_id": 4}\n\n']
    assert heartbeat == ": ping\n\n"


def test_frames_carry_no_plan_contents():
    """The poke is deliberately contentless: what a viewer may see is decided by
    the endpoint they refetch, not by whoever happened to publish."""
    async def go():
        bus = EventBus()
        sub = bus.subscribe(4)
        frames = stream_frames(sub, None, heartbeat=0.05)
        await anext(frames)  # retry
        await anext(frames)  # hello
        bus.publish(4, "plans")
        frame = await anext(frames)
        await frames.aclose()
        return frame

    assert asyncio.run(go()) == 'event: plans\ndata: {"group_id": 4}\n\n'
