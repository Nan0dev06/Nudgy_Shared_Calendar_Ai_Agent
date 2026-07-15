"""Poll endpoints: members see polls and vote; Orbi (the agent) creates them.

GET  /groups/{group_id}/polls      -> polls for a group (newest first)
POST /polls/{poll_id}/vote         {"yes": true}  -> updated poll state

Voting re-evaluates the decision rule immediately, so the returned poll
carries its up-to-date status (open/approved/rejected).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Poll, User
from app.db import repo
from app.db.session import get_session
from app.tools.poll_service import refresh_poll_status

router = APIRouter(tags=["polls"])


class VoteBody(BaseModel):
    yes: bool


def _poll_json(session: Session, poll: Poll, tz_name: str) -> dict:
    decision = refresh_poll_status(session, poll)
    tz = ZoneInfo(tz_name)
    return {
        "id": poll.id,
        "title": poll.title,
        "location": poll.location,
        "start_local": poll.start.astimezone(tz).strftime("%a %d %b %H:%M"),
        "end_local": poll.end.astimezone(tz).strftime("%a %d %b %H:%M"),
        "status": poll.status,
        "booked": poll.booked,
        "event_link": poll.event_link,
        "yes": decision.yes_count,
        "no": decision.no_count,
        "min_yes": poll.min_yes,
        "waiting_on": decision.missing_voters,
    }


def _require_membership(session: Session, user: User, group_id: int) -> None:
    if group_id not in {g.id for g in repo.get_user_groups(session, user)}:
        raise HTTPException(status_code=403, detail="You are not in this group.")


@router.get("/groups/{group_id}/polls")
def group_polls(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _require_membership(session, user, group_id)
    polls = repo.get_group_polls(session, group_id)[:10]
    return [_poll_json(session, p, user.timezone) for p in polls]


@router.post("/polls/{poll_id}/vote")
def vote(
    poll_id: int,
    body: VoteBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    poll = repo.get_poll(session, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="No such poll.")
    _require_membership(session, user, poll.group_id)
    if poll.status not in ("open", "approved"):
        raise HTTPException(status_code=400, detail=f"Poll is {poll.status}; voting is closed.")
    if poll.booked:
        raise HTTPException(status_code=400, detail="Poll is already booked.")
    repo.cast_vote(session, poll, user, body.yes)
    return _poll_json(session, poll, user.timezone)
