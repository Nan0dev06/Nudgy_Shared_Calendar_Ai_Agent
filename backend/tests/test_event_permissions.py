"""Who may change an event or task (tools/event_rules.py + the PATCH/DELETE routes).

The hole this closes: DELETE and PATCH used to check group membership only, so
any member could delete another member's personal event — including an
anonymous one whose title they were never allowed to read.

The rules, and what each protects:
  - personal events belong to their owner (nobody else edits, ticks, or deletes)
  - shared group events aren't editable by one person, creator included —
    changes go to a group vote, which isn't built yet
  - deleting a shared event is creator-only (tightened from any member)
  - ticking a SHARED task done stays open to everyone: finishing work together
    is the point, and it doesn't change what or when the thing is
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import COOKIE_NAME, make_session_cookie
from app.api.event_routes import router
from app.db import repo
from app.db.models import Base, User
from app.db.session import get_session
from app.tools import event_rules

DAY = datetime(2026, 7, 20, tzinfo=timezone.utc)


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    return TestClient(app), Session


def _group_of_two(Session):
    """A group with an owner and one other member — both legitimate members,
    which is exactly what made the old membership-only check insufficient."""
    with Session() as s:
        owner = User(email="sam@x.com")
        other = User(email="mo@x.com")
        s.add_all([owner, other])
        s.commit()
        group = repo.create_group(s, "Crew", owner)
        repo.add_member(s, group, other)
        return owner.id, other.id, group.id


def _event(Session, group_id, creator_id, *, personal=False, kind="event", **kw):
    with Session() as s:
        ev = repo.create_event(
            s, group_id=group_id, created_by=creator_id, kind=kind,
            title=kw.get("title", "Picnic"), category="Event", location="Park",
            start_utc=DAY.replace(hour=14),
            end_utc=DAY.replace(hour=16) if kind == "event" else DAY.replace(hour=14),
            personal=personal, anonymous=kw.get("anonymous", True),
        )
        return ev.id


def _auth(client, user_id):
    client.cookies.set(COOKIE_NAME, make_session_cookie(user_id))


# ----------------------------------------------------------------- pure rules

def _fake(created_by, personal=False):
    class E:
        pass
    e = E()
    e.created_by, e.personal = created_by, personal
    return e


def test_owner_may_do_everything_to_their_personal_event():
    mine = _fake(1, personal=True)
    assert event_rules.can_edit(mine, 1)
    assert event_rules.can_delete(mine, 1)
    assert event_rules.can_toggle_done(mine, 1)


@pytest.mark.parametrize("rule", ["can_edit", "can_delete", "can_toggle_done"])
def test_nobody_touches_someone_elses_personal_event(rule):
    theirs = _fake(1, personal=True)
    decision = getattr(event_rules, rule)(theirs, 2, "sam@x.com")
    assert not decision
    assert "sam@x.com" in decision.reason


def test_shared_event_is_not_editable_even_by_its_creator():
    """The deliberate one — a change to a shared event is the group's call."""
    shared = _fake(1, personal=False)
    for uid in (1, 2):
        decision = event_rules.can_edit(shared, uid)
        assert not decision
        assert "vote" in decision.reason.lower()


def test_shared_event_delete_is_creator_only():
    shared = _fake(1, personal=False)
    assert event_rules.can_delete(shared, 1)
    assert not event_rules.can_delete(shared, 2)


def test_anyone_may_tick_a_shared_task_done():
    """Collaborative completion — this one stays open on purpose."""
    assert event_rules.can_toggle_done(_fake(1, personal=False), 2)


def test_decision_is_falsy_when_denied():
    """The routes rely on `if not decision`, so the bool protocol is load-bearing."""
    assert not event_rules.Decision(False, "nope")
    assert event_rules.Decision(True)


# ------------------------------------------------------------------ the routes

def test_member_cannot_delete_anothers_personal_event(ctx):
    """The actual hole: a groupmate deleting something they can't even read."""
    client, Session = ctx
    owner_id, other_id, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True)

    _auth(client, other_id)
    r = client.delete(f"/events/{ev}")
    assert r.status_code == 403
    assert "sam@x.com" in r.json()["detail"]

    with Session() as s:
        assert repo.get_event(s, ev) is not None  # still there


def test_member_cannot_delete_anothers_shared_event(ctx):
    client, Session = ctx
    owner_id, other_id, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id)

    _auth(client, other_id)
    assert client.delete(f"/events/{ev}").status_code == 403

    _auth(client, owner_id)
    assert client.delete(f"/events/{ev}").status_code == 200


def test_non_member_still_gets_403_not_404(ctx):
    """Membership is checked before ownership, so an outsider learns nothing
    about whether the event exists."""
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id)
    with Session() as s:
        outsider = User(email="nope@x.com")
        s.add(outsider)
        s.commit()
        outsider_id = outsider.id

    _auth(client, outsider_id)
    assert client.delete(f"/events/{ev}").status_code == 403


def test_owner_edits_their_personal_event(ctx):
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True)

    _auth(client, owner_id)
    r = client.patch(f"/events/{ev}", json={
        "title": "Dentist", "location": "Hamra", "anonymous": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Dentist"
    assert body["location"] == "Hamra"
    assert body["anonymous"] is False


def test_editing_a_shared_event_is_refused_with_a_reason(ctx):
    """Not "forbidden" in the abstract — the user is told changes go to the
    group, because a bare 403 on your own group's event reads like a bug."""
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id)

    _auth(client, owner_id)
    r = client.patch(f"/events/{ev}", json={"title": "Renamed"})
    assert r.status_code == 403
    assert "vote" in r.json()["detail"].lower()


def test_clearing_a_field_differs_from_omitting_it(ctx):
    """`location: null` means clear it; leaving it out means don't touch it.
    Reading model_fields_set rather than the value is what makes that work."""
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True)
    _auth(client, owner_id)

    assert client.patch(f"/events/{ev}", json={"title": "A"}).json()["location"] == "Park"
    assert client.patch(f"/events/{ev}", json={"location": None}).json()["location"] is None


def test_editing_times_validates_the_order(ctx):
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True)
    _auth(client, owner_id)

    r = client.patch(f"/events/{ev}", json={
        "start_iso": "2026-07-20T18:00:00+00:00",
        "end_iso": "2026-07-20T17:00:00+00:00",
    })
    assert r.status_code == 400

    r = client.patch(f"/events/{ev}", json={"end_iso": "2026-07-20T19:00:00+00:00"})
    assert r.status_code == 200


def test_editing_a_task_keeps_end_mirroring_its_due_date(ctx):
    """A task's end_utc mirrors start_utc — the shape create_event builds."""
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True, kind="task")
    _auth(client, owner_id)

    r = client.patch(f"/events/{ev}", json={"start_iso": "2026-07-22T09:00:00+00:00"})
    assert r.status_code == 200
    assert r.json()["start_iso"] == r.json()["end_iso"]


def test_ticking_a_shared_task_stays_open_to_the_group(ctx):
    client, Session = ctx
    owner_id, other_id, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, kind="task")

    _auth(client, other_id)
    r = client.patch(f"/events/{ev}", json={"done": True})
    assert r.status_code == 200
    assert r.json()["done"] is True


def test_ticking_someone_elses_personal_task_is_refused(ctx):
    client, Session = ctx
    owner_id, other_id, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True, kind="task")

    _auth(client, other_id)
    assert client.patch(f"/events/{ev}", json={"done": True}).status_code == 403


def test_empty_patch_is_rejected(ctx):
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True)

    _auth(client, owner_id)
    assert client.patch(f"/events/{ev}", json={}).status_code == 400


def test_kind_and_personal_cannot_be_edited(ctx):
    """Both decide which rules govern the row, so changing one would move an
    event between mechanisms silently. They're not in the schema at all."""
    client, Session = ctx
    owner_id, _, gid = _group_of_two(Session)
    ev = _event(Session, gid, owner_id, personal=True)

    _auth(client, owner_id)
    r = client.patch(f"/events/{ev}", json={"personal": False, "kind": "task"})
    assert r.status_code == 400  # nothing recognised was sent

    with Session() as s:
        ev_row = repo.get_event(s, ev)
        assert ev_row.personal is True and ev_row.kind == "event"
