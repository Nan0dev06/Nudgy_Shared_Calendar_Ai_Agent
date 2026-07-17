"""Poll endpoints: members see polls, create them, and vote.

GET  /groups/{group_id}/polls      -> polls for a group (newest first)
POST /groups/{group_id}/polls      -> create a poll directly (no agent needed)
POST /polls/{poll_id}/vote         {"yes": true}  -> updated poll state

Voting re-evaluates the decision rule immediately. THE AUTONOMOUS PART of the
propose -> poll -> observe -> commit/re-plan loop lives here: the vote that
flips a poll's status triggers the follow-through on the spot —
  open -> approved : the event is booked to every member's calendar (safe by
                     construction: approval requires zero NO votes)
  open -> rejected : fresh alternative slots are computed immediately so the
                     re-plan is already waiting for the group
Each auto action is logged under the same orbi.agent logger as the tool calls,
so the whole decision loop is visible in one log stream.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Group, Poll, User
from app.db import repo
from app.db.session import get_session
from app.tools.poll_service import refresh_poll_status

log = logging.getLogger("orbi.agent")

router = APIRouter(tags=["polls"])


class VoteBody(BaseModel):
    yes: bool


class CreatePollBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_iso: str
    end_iso: str
    location: str | None = Field(default=None, max_length=200)
    min_yes: int | None = Field(default=None, ge=1)


def _poll_json(session: Session, poll: Poll, tz_name: str) -> dict:
    decision = refresh_poll_status(session, poll)
    tz = ZoneInfo(tz_name)
    return {
        "id": poll.id,
        "title": poll.title,
        "location": poll.location,
        "start_local": poll.start.astimezone(tz).strftime("%a %d %b %H:%M"),
        "end_local": poll.end.astimezone(tz).strftime("%a %d %b %H:%M"),
        # ISO forms so the frontend can place booked events on the calendar
        "start_iso": poll.start.astimezone(tz).isoformat(),
        "end_iso": poll.end.astimezone(tz).isoformat(),
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


@router.post("/groups/{group_id}/polls")
def create_poll(
    group_id: int,
    body: CreatePollBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Direct poll creation — same rules as the agent's create_poll tool
    (min_yes defaults to unanimous, clamped to the member count)."""
    _require_membership(session, user, group_id)
    try:
        start = datetime.fromisoformat(body.start_iso)
        end = datetime.fromisoformat(body.end_iso)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Bad ISO datetime: {e}")
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=400, detail="Datetimes must include a timezone offset.")
    if end <= start:
        raise HTTPException(status_code=400, detail="Slot end must be after start.")

    group = session.get(Group, group_id)
    members = repo.get_group_members(session, group_id)
    min_yes = body.min_yes or len(members)
    poll = repo.create_poll(
        session, group, user,
        title=body.title,
        slot_start_utc=start.astimezone(timezone.utc),
        slot_end_utc=end.astimezone(timezone.utc),
        min_yes=min(min_yes, len(members)),
        location=body.location,
    )
    log.info("[polls] %s created poll %d directly", user.email, poll.id)
    return _poll_json(session, poll, user.timezone)


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

    was_open = poll.status == "open"
    repo.cast_vote(session, poll, user, body.yes)
    out = _poll_json(session, poll, user.timezone)  # refreshes status via the rule

    # ---- autonomous follow-through on a status transition ----
    if was_open and poll.status == "approved" and not poll.booked:
        out["auto"] = _auto_book(session, poll)
    elif was_open and poll.status == "rejected":
        out["auto"] = _auto_replan(session, poll, user.timezone)
    out["booked"] = poll.booked
    out["event_link"] = poll.event_link
    return out


def _auto_book(session: Session, poll: Poll) -> dict:
    """Approved (zero NOs + enough YESes) -> commit to everyone's calendar now."""
    from app.tools.booking import book_poll_event

    organizer = session.get(User, poll.created_by)
    members = repo.get_group_members(session, poll.group_id)
    log.info("[decision] poll %d approved by rule -> auto-booking for %d members",
             poll.id, len(members))
    result = book_poll_event(session, poll, organizer, [m.email for m in members])
    if result.get("booked"):
        return {"action": "booked", "event_link": result["event_link"]}
    log.warning("[decision] poll %d auto-book failed: %s", poll.id, result.get("error"))
    return {"action": "book_failed", "error": result.get("error")}


def _auto_replan(session: Session, poll: Poll, tz_name: str) -> dict:
    """Rejected (someone said no) -> never book; compute the next options now."""
    from app.agent.availability import compute_availability

    group = session.get(Group, poll.group_id)
    duration = max(15, int((poll.end - poll.start).total_seconds() // 60))
    log.info("[decision] poll %d rejected by rule -> auto re-planning (%d min)",
             poll.id, duration)
    avail = compute_availability(
        session, group, datetime.now(timezone.utc), days_ahead=7,
        duration_minutes=duration, tz_name=tz_name,
    )
    alts = avail.get("common_slots", [])[:3]
    log.info("[decision] poll %d re-plan -> %d alternative slot(s)", poll.id, len(alts))
    return {"action": "replan", "alternative_slots": alts,
            "note": "This slot was declined; here are the next common windows. "
                    "Ask Orbi to poll one of them."}
