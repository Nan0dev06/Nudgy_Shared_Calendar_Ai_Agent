"""GET /groups/{id}/stream — the authenticated SSE feed (app/api/stream_routes).

The interesting cases are the two guards (a stream is a live view of a group, so
it takes the same cookie and membership check every other group endpoint takes)
and the round trip: a vote cast by one member reaches another member's open
stream as a `plans` poke.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import plan_routes, stream_routes
from app.api.deps import COOKIE_NAME, make_session_cookie
from app.db.models import Base, User
from app.db import repo
from app.realtime import bus

DAY = datetime(2026, 7, 20, 17, tzinfo=timezone.utc)


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    s = TS()
    host, member, outsider = (User(email="host@x.com"), User(email="m@x.com"),
                              User(email="nope@x.com"))
    s.add_all([host, member, outsider])
    s.commit()
    group = repo.create_group(s, "Crew", host)
    repo.add_member(s, group, member)
    plan = repo.create_plan(s, group, host, title="Coffee", location="Cafe",
                            slots=[(DAY, DAY.replace(hour=18))])
    ids = {"host": host.id, "member": member.id, "outsider": outsider.id,
           "group": group.id, "plan": plan.id}
    s.close()

    app = FastAPI()
    app.include_router(stream_routes.router)
    # the stream deliberately does NOT take get_session (it would pin a DB
    # connection for the life of the connection), so its own factory is swapped
    monkeypatch.setattr(stream_routes, "SessionLocal", TS)
    # keep a stalled test moving: heartbeats bound how long a read can block
    monkeypatch.setattr(stream_routes, "SSE_HEARTBEAT_SECONDS", 0.1)
    return app, ids, TS


async def _open(ids, who, group_id=None):
    """Open the feed by calling the endpoint directly, and return its response.

    Not through TestClient: its transport runs the ASGI app to completion before
    handing back a response (starlette/testclient.py), so a stream that never
    ends never returns. The two guard tests below DO go through TestClient —
    they reject before any streaming starts, which is exactly the case it can
    handle.
    """
    return await stream_routes.group_stream(
        group_id if group_id is not None else ids["group"],
        None,  # the Request is only consulted for is_disconnected()
        nudgy_session=make_session_cookie(ids[who]),
    )


async def _read_until(frames, needle, limit=40):
    """Pull frames looking for one. Bounded so a missing poke fails the test
    instead of hanging it (heartbeats keep the iterator moving)."""
    for _ in range(limit):
        frame = await anext(frames, None)
        if frame is None:
            return False
        if frame.startswith(needle):
            return True
    return False


def test_stream_requires_a_session(ctx):
    app, ids, _ = ctx
    client = TestClient(app)
    assert client.get(f"/groups/{ids['group']}/stream").status_code == 401


def test_stream_refuses_a_non_member(ctx):
    app, ids, _ = ctx
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, make_session_cookie(ids["outsider"]))
    assert client.get(f"/groups/{ids['group']}/stream").status_code == 403


def test_a_members_vote_reaches_the_hosts_stream(ctx):
    """The whole point: the host's decision box updates from someone else's
    action, with no request of the host's own."""
    _, ids, TS = ctx

    async def go():
        response = await _open(ids, "host")
        assert response.media_type == "text/event-stream"
        assert response.headers["x-accel-buffering"] == "no"
        frames = response.body_iterator
        # hello proves the subscription exists before anything is published
        assert await _read_until(frames, "event: hello")

        session = TS()
        try:
            member = session.get(User, ids["member"])
            plan_routes.vote_interest(ids["plan"], plan_routes.InterestBody(yes=True),
                                      member, session)
        finally:
            session.close()

        seen = await _read_until(frames, "event: plans")
        await frames.aclose()
        return seen

    assert asyncio.run(go())


def test_closing_the_connection_unsubscribes(ctx):
    """Every closed tab has to leave the registry, or a long-running process
    accumulates a dead subscriber per visit."""
    _, ids, _ = ctx

    async def go():
        response = await _open(ids, "host")
        frames = response.body_iterator
        assert await _read_until(frames, "event: hello")
        watching = bus.subscriber_count(ids["group"])
        await frames.aclose()  # what a dropped connection does to the generator
        return watching, bus.subscriber_count(ids["group"])

    assert asyncio.run(go()) == (1, 0)
