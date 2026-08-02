"""Group endpoints: create with invite code, join by code, list mine.

POST /groups            {"name": "Beirut Crew"}         -> group + invite_code
POST /groups/join       {"invite_code": "4PYJU8"}       -> joined group
GET  /groups            -> groups the current user belongs to
GET  /groups/{id}/members -> members + calendar connection status
GET  /groups/{id}/availability -> live free/busy for the calendar UI
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.availability import compute_availability
from app.api.deps import get_current_user
from app.db.models import Group, User
from app.db import repo
from app.db.session import get_session

log = logging.getLogger("nudgy.api")

router = APIRouter(prefix="/groups", tags=["groups"])


class CreateGroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class JoinGroupBody(BaseModel):
    invite_code: str = Field(min_length=6, max_length=6)


def _group_json(group: Group, user: User) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "invite_code": group.invite_code,
        "is_owner": group.created_by == user.id,
    }


def _get_group_or_404(session: Session, group_id: int) -> Group:
    group = session.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="No such group.")
    return group


def _require_member(session: Session, group_id: int, user: User) -> None:
    if repo.get_membership(session, group_id, user.id) is None:
        raise HTTPException(status_code=403, detail="You are not in this group.")


def _require_owner(group: Group, user: User) -> None:
    if group.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the group owner can do that.")


@router.post("")
def create_group(
    body: CreateGroupBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = repo.create_group(session, body.name, user)
    return _group_json(group, user)


@router.post("/join")
def join_group(
    body: JoinGroupBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = repo.get_group_by_code(session, body.invite_code)
    if group is None:
        raise HTTPException(status_code=404, detail="No group with that invite code.")
    repo.add_member(session, group, user)
    return _group_json(group, user)


@router.get("")
def my_groups(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return [_group_json(g, user) for g in repo.get_user_groups(session, user)]


class RenameGroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/{group_id}")
def rename_group(
    group_id: int,
    body: RenameGroupBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = _get_group_or_404(session, group_id)
    _require_owner(group, user)
    repo.rename_group(session, group, body.name.strip())
    return _group_json(group, user)


@router.post("/{group_id}/regenerate-code")
def regenerate_code(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Roll a new invite code — the old shared link stops working."""
    group = _get_group_or_404(session, group_id)
    _require_owner(group, user)
    repo.regenerate_invite_code(session, group)
    return _group_json(group, user)


@router.delete("/{group_id}")
def delete_group(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = _get_group_or_404(session, group_id)
    _require_owner(group, user)
    repo.delete_group(session, group)
    return {"ok": True}


@router.delete("/{group_id}/members/{member_id}")
def remove_member(
    group_id: int,
    member_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Leave (member_id == you) or kick (owner removing someone else). When the
    OWNER leaves, ownership passes to the earliest-joined remaining member, or the
    group is deleted if they were the last one."""
    group = _get_group_or_404(session, group_id)
    _require_member(session, group_id, user)
    is_self = member_id == user.id
    is_owner = group.created_by == user.id
    # kicking anyone but yourself requires ownership. (A non-owner therefore can
    # never remove the owner — this gate stops them first. The owner removing
    # themselves is a leave, handled below.)
    if not is_self and not is_owner:
        raise HTTPException(status_code=403, detail="Only the owner can remove members.")

    if not repo.remove_membership(session, group_id, member_id):
        raise HTTPException(status_code=404, detail="That person isn't in this group.")

    if is_self and is_owner:
        return _handle_owner_departure(session, group)
    return {"ok": True}


@router.post("/{group_id}/leave")
def leave_group(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Self-service leave (the frontend needn't know its own member id). When the
    owner leaves, ownership transfers to the earliest survivor, or the group is
    deleted if they were the last one."""
    group = _get_group_or_404(session, group_id)
    _require_member(session, group_id, user)
    repo.remove_membership(session, group_id, user.id)
    if group.created_by == user.id:
        return _handle_owner_departure(session, group)
    return {"ok": True}


def _handle_owner_departure(session: Session, group: Group) -> dict:
    """The owner just left: hand the group to the earliest-joined survivor, or
    delete it if nobody remains."""
    remaining = repo.get_group_members(session, group.id)  # ordered by joined_at
    if remaining:
        repo.transfer_ownership(session, group, remaining[0].id)
        return {"ok": True}
    repo.delete_group(session, group)
    return {"ok": True, "deleted": True}


@router.get("/{group_id}/members")
def group_members(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _require_member(session, group_id, user)
    group = _get_group_or_404(session, group_id)
    members = repo.get_group_members(session, group_id)
    return [
        {
            "id": m.id,
            "email": m.email,
            "calendar_connected": m.calendar_connected,
            "is_owner": m.id == group.created_by,
        }
        for m in members
    ]


@router.get("/{group_id}/availability")
def group_availability(
    group_id: int,
    days_ahead: int = Query(default=7, ge=1, le=30),
    duration_minutes: int = Query(default=60, ge=15, le=480),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Live free/busy picture for the calendar UI: per-member busy ranges plus
    the common free windows. Hits Google live for every connected member, so
    expect a couple of seconds.

    Ranges only, with one deliberate exception: the CALLER's own blocks come back
    labelled with what is in them, for any calendar they switched titles on
    (docs/inbound-sync.md). `viewer_id` is the caller's id and nobody else's, so
    the same group loaded by two people yields two different payloads — each
    person sees their own detail and everyone else's opaque busy time."""
    groups = {g.id for g in repo.get_user_groups(session, user)}
    if group_id not in groups:
        raise HTTPException(status_code=403, detail="You are not in this group.")
    group = session.get(Group, group_id)
    try:
        result = compute_availability(
            session, group, datetime.now(timezone.utc),
            days_ahead=days_ahead, duration_minutes=duration_minutes,
            tz_name=user.timezone, include_member_busy=True, viewer_id=user.id,
        )
    except Exception:  # a stale token or Google hiccup shouldn't 500 the UI
        log.exception("availability failed for group %d", group_id)
        return {"members_busy": [], "common_slots": [], "error": "Couldn't reach Google Calendar — try again."}
    # With nothing known about anybody the slot math would call the whole window
    # "free"; that's meaningless, so surface no free windows instead. The test is
    # members_with_source, NOT members_connected: a group that keeps its events in
    # Nudgy and connected no external calendar has a genuine availability picture
    # and must get real slots (docs/poll-edit-redesign.md §4).
    if result.get("members_with_source", 0) == 0:
        result["common_slots"] = []
    return result
