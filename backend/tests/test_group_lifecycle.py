"""Group lifecycle: rename, regenerate invite code, leave, kick, delete.

Ownership = Group.created_by. Owner-only: rename, regenerate code, kick, delete.
Any member may leave; when the OWNER leaves, ownership passes to the earliest-
joined survivor, or the group is deleted if they were the last one.
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import group_routes
from app.api.deps import get_current_user
from app.db import repo
from app.db.models import Base, Group, User
from app.db.session import get_session


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    s = TS()
    owner, m1, m2 = User(email="owner@x.com"), User(email="m1@x.com"), User(email="m2@x.com")
    s.add_all([owner, m1, m2])
    s.commit()
    group = repo.create_group(s, "Crew", owner)   # owner joins first
    repo.add_member(s, group, m1)
    repo.add_member(s, group, m2)
    ids = {"owner": owner.id, "m1": m1.id, "m2": m2.id,
           "group": group.id, "code": group.invite_code}
    s.close()

    app = FastAPI()
    app.include_router(group_routes.router)
    current = {"id": ids["owner"]}

    def override_user():
        return TS().get(User, current["id"])

    def override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_session] = override_session
    return TestClient(app), current, ids, TS


# --------------------------------------------------------------------- rename

def test_rename_by_owner(ctx):
    client, current, ids, _ = ctx
    r = client.patch(f"/groups/{ids['group']}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"
    assert r.json()["is_owner"] is True


def test_rename_by_member_forbidden(ctx):
    client, current, ids, _ = ctx
    current["id"] = ids["m1"]
    assert client.patch(f"/groups/{ids['group']}", json={"name": "Nope"}).status_code == 403


# ------------------------------------------------------------- invite code

def test_regenerate_code_owner_only_and_invalidates_old(ctx):
    client, current, ids, TS = ctx
    new_code = client.post(f"/groups/{ids['group']}/regenerate-code").json()["invite_code"]
    assert new_code != ids["code"]

    with TS() as s:
        newbie = User(email="new@x.com")
        s.add(newbie)
        s.commit()
        newbie_id = newbie.id
    current["id"] = newbie_id
    assert client.post("/groups/join", json={"invite_code": ids["code"]}).status_code == 404
    assert client.post("/groups/join", json={"invite_code": new_code}).status_code == 200


def test_regenerate_code_member_forbidden(ctx):
    client, current, ids, _ = ctx
    current["id"] = ids["m1"]
    assert client.post(f"/groups/{ids['group']}/regenerate-code").status_code == 403


# ---------------------------------------------------------- leave / kick

def test_member_can_leave(ctx):
    client, current, ids, TS = ctx
    current["id"] = ids["m1"]
    assert client.delete(f"/groups/{ids['group']}/members/{ids['m1']}").status_code == 200
    with TS() as s:
        assert repo.get_membership(s, ids["group"], ids["m1"]) is None


def test_owner_can_kick(ctx):
    client, current, ids, TS = ctx
    assert client.delete(f"/groups/{ids['group']}/members/{ids['m1']}").status_code == 200
    with TS() as s:
        assert repo.get_membership(s, ids["group"], ids["m1"]) is None


def test_member_cannot_kick_another(ctx):
    client, current, ids, _ = ctx
    current["id"] = ids["m1"]
    assert client.delete(f"/groups/{ids['group']}/members/{ids['m2']}").status_code == 403


def test_leave_endpoint_removes_self(ctx):
    client, current, ids, TS = ctx
    current["id"] = ids["m1"]
    assert client.post(f"/groups/{ids['group']}/leave").status_code == 200
    with TS() as s:
        assert repo.get_membership(s, ids["group"], ids["m1"]) is None


def test_leave_endpoint_owner_transfers(ctx):
    client, current, ids, TS = ctx
    r = client.post(f"/groups/{ids['group']}/leave")  # owner leaves via /leave
    assert r.status_code == 200
    with TS() as s:
        assert s.get(Group, ids["group"]).created_by == ids["m1"]


def test_owner_leaving_transfers_ownership(ctx):
    client, current, ids, TS = ctx
    r = client.delete(f"/groups/{ids['group']}/members/{ids['owner']}")
    assert r.status_code == 200
    assert r.json().get("deleted") is None
    with TS() as s:
        group = s.get(Group, ids["group"])
        assert group is not None
        assert group.created_by == ids["m1"]  # earliest-joined survivor
        assert repo.get_membership(s, ids["group"], ids["owner"]) is None


def test_owner_leaving_as_last_member_deletes_group(ctx):
    client, current, ids, TS = ctx
    solo = client.post("/groups", json={"name": "Solo"}).json()
    r = client.delete(f"/groups/{solo['id']}/members/{ids['owner']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    with TS() as s:
        assert s.get(Group, solo["id"]) is None


# --------------------------------------------------------------------- delete

def test_delete_group_by_owner(ctx):
    client, current, ids, TS = ctx
    assert client.delete(f"/groups/{ids['group']}").status_code == 200
    with TS() as s:
        assert s.get(Group, ids["group"]) is None
        assert repo.get_membership(s, ids["group"], ids["m1"]) is None  # memberships gone


def test_delete_group_member_forbidden(ctx):
    client, current, ids, _ = ctx
    current["id"] = ids["m1"]
    assert client.delete(f"/groups/{ids['group']}").status_code == 403


def test_delete_group_removes_its_plans(ctx):
    client, current, ids, TS = ctx
    d = datetime(2026, 7, 20, 17, tzinfo=timezone.utc)
    with TS() as s:
        group = s.get(Group, ids["group"])
        owner = s.get(User, ids["owner"])
        repo.create_plan(s, group, owner, title="Coffee", location="Cafe",
                         slots=[(d, d.replace(hour=18))])
    assert client.delete(f"/groups/{ids['group']}").status_code == 200
    with TS() as s:
        assert s.get(Group, ids["group"]) is None
        assert repo.get_group_plans(s, ids["group"]) == []


# --------------------------------------------------------------------- members

def test_members_endpoint_exposes_id_and_owner(ctx):
    client, current, ids, _ = ctx
    members = client.get(f"/groups/{ids['group']}/members").json()
    owner_row = next(m for m in members if m["id"] == ids["owner"])
    assert owner_row["is_owner"] is True
    assert sum(m["is_owner"] for m in members) == 1


def test_non_member_cannot_view_members(ctx):
    client, current, ids, TS = ctx
    with TS() as s:
        outsider = User(email="out@x.com")
        s.add(outsider)
        s.commit()
        current["id"] = outsider.id
    assert client.get(f"/groups/{ids['group']}/members").status_code == 403
