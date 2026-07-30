"""The live feed: one long-lived SSE connection per open group.

GET /groups/{group_id}/stream   -> text/event-stream

Frames are pokes, not state:

    retry: 3000

    event: hello
    data: {"group_id": 4}

    event: plans
    data: {"group_id": 4}

    : ping

On `plans` the client refetches GET /groups/{id}/plans; on `events`, the
group's events. That refetch goes through the normal authenticated endpoint, so
the stream never has to decide what this particular viewer is allowed to see —
see realtime/bus.py.

Why SSE and not WebSockets: the traffic is one-way (server tells the browser
something moved), EventSource reconnects on its own with backoff, and it rides
the same cookie-authenticated HTTP origin — no second auth path, no upgrade
handshake, works through the Vite dev proxy unchanged.

Deliberately NOT holding a DB session for the life of the connection: the
membership check opens its own short session and closes it before the response
starts streaming. A `Depends(get_session)` here would pin one connection per
watching browser tab for as long as the tab is open.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.deps import COOKIE_NAME, resolve_session_user
from app.core.config import SSE_HEARTBEAT_SECONDS, SSE_MAX_CONNECTIONS
from app.db import repo
from app.db.session import SessionLocal
from app.realtime import Subscriber, bus

log = logging.getLogger("nudgy.realtime")

router = APIRouter(tags=["live"])

# How long the browser waits before reconnecting after a drop. EventSource's own
# default is 3s in most engines; sending it makes the behaviour explicit rather
# than per-browser.
RETRY_MS = 3000

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx (Render's edge) buffers responses by default, which would hold every
    # frame until the connection closed — i.e. deliver nothing, forever.
    "X-Accel-Buffering": "no",
}


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_frames(sub: Subscriber, request: Request | None,
                        heartbeat: float) -> AsyncIterator[str]:
    """Turn one subscriber's pokes into SSE frames until the client goes away.

    Split out from the endpoint so it can be driven directly in tests: feed it a
    Subscriber, publish, read the frame. `request` is optional for that reason.
    """
    yield f"retry: {RETRY_MS}\n\n"
    # An immediate frame flushes headers and proxy buffers, so the client knows
    # it is live rather than waiting up to a heartbeat to find out.
    yield _frame("hello", {"group_id": sub.group_id})

    while True:
        kinds = await sub.wait(heartbeat)
        if kinds is None:
            # A comment is a valid, ignorable frame — it exists to keep the
            # connection warm and to fail the write once the peer is gone.
            if request is not None and await request.is_disconnected():
                return
            yield ": ping\n\n"
            continue
        for kind in sorted(kinds):
            yield _frame(kind, {"group_id": sub.group_id})


@router.get("/groups/{group_id}/stream", include_in_schema=False)
async def group_stream(
    group_id: int,
    request: Request,
    nudgy_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    session = SessionLocal()
    try:
        user = resolve_session_user(nudgy_session, session)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in.")
        if group_id not in {g.id for g in repo.get_user_groups(session, user)}:
            raise HTTPException(status_code=403, detail="You are not in this group.")
        watcher = user.email
    finally:
        session.close()

    if bus.subscriber_count() >= SSE_MAX_CONNECTIONS:
        # Not an error the user can act on, so say retry-later and let the
        # frontend's poll fallback carry the session.
        log.warning("live feed at capacity (%d streams) — refusing %s",
                    SSE_MAX_CONNECTIONS, watcher)
        raise HTTPException(status_code=503, detail="Live updates are busy; retrying shortly.")

    sub = bus.subscribe(group_id)
    log.info("[group %d] %s opened the live feed (%d watching)",
             group_id, watcher, bus.subscriber_count(group_id))

    async def body() -> AsyncIterator[str]:
        try:
            async for chunk in stream_frames(sub, request, SSE_HEARTBEAT_SECONDS):
                yield chunk
        finally:
            # Runs on a clean close AND on the GeneratorExit/CancelledError a
            # dropped connection raises — without it the registry would grow a
            # dead subscriber per closed tab.
            bus.unsubscribe(sub)
            log.info("[group %d] %s left the live feed", group_id, watcher)

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)
