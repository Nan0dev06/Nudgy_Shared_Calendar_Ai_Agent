"""The app-facing face of the live feed: one bus, and named pokes.

Call sites say WHAT changed, not how it travels:

    from app.realtime import plans_changed
    plans_changed(plan.group_id)

Everything routes through here so swapping the in-process bus for Redis (the
day the app runs more than one worker) is a change to one module, not to every
route that mutates a plan. See bus.py for why the payload is only a kind.
"""
from __future__ import annotations

from app.realtime.bus import EventBus, Subscriber

# Kinds the frontend knows how to react to. Keep this list and the client's
# listeners in step — an unknown kind is simply ignored by the browser.
PLANS = "plans"
EVENTS = "events"

bus = EventBus()


def plans_changed(group_id: int | None) -> int:
    """A vote landed, a host moved, a plan was created/settled/deleted."""
    return bus.publish(group_id, PLANS) if group_id else 0


def events_changed(group_id: int | None) -> int:
    """The group's calendar changed — booked, edited, RSVP'd, removed."""
    return bus.publish(group_id, EVENTS) if group_id else 0


__all__ = ["EventBus", "Subscriber", "bus", "plans_changed", "events_changed",
           "PLANS", "EVENTS"]
