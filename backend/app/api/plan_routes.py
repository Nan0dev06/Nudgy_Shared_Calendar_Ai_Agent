"""Plan endpoints: members answer their step of the cascade; Nudgy creates plans.

GET  /groups/{group_id}/plans      -> plans for a group, each carrying THIS
                                      member's current ballot (and, for the
                                      host, the decision box)
POST /groups/{group_id}/plans      -> create a plan directly from the app UI
                                      (title required; candidate times and
                                      location optional — an empty slot list is
                                      a pure "who's in?" interest check)
POST /plans/{plan_id}/interest     {"yes": true}  -> stage 1 answer
POST /plans/{plan_id}/time-vote    {"yes": true, "round_id": 3} -> stage 2 answer

The cascade is visible in the responses: answering interest=yes comes straight
back with `ballot.stage == "time"` — that one yes opened the time question for
that member, without waiting on anybody else.

Note what is NOT here: no rule fires on a vote. Voting never books, never
rejects, never advances a time. Those are host moves and they go through the
agent (see agent/tools.py -> use_next_time / lock_in_time), which is what keeps
a human in the loop before anything reaches a calendar.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Plan, User
from app.db import repo
from app.db.session import get_session
from app.tools.plan_service import day_label, member_ballot, plan_tally, time_label

log = logging.getLogger("nudgy.agent")

router = APIRouter(tags=["plans"])


class InterestBody(BaseModel):
    yes: bool


class TimeVoteBody(BaseModel):
    yes: bool
    round_id: int


class SlotBody(BaseModel):
    start_iso: str
    end_iso: str


class CreatePlanBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    slots: list[SlotBody] = Field(default_factory=list, max_length=6)


def _plan_json(session: Session, plan: Plan, viewer: User, tz_name: str) -> dict:
    active = repo.get_active_round(session, plan)
    ballot = member_ballot(session, plan, viewer)
    is_host = viewer.id == plan.created_by
    host = session.get(User, plan.created_by)

    out = {
        "id": plan.id,
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, tz_name),
        "status": plan.status,
        "host": host.email if host else None,
        "is_host": is_host,
        "times": [
            {
                "round_id": r.id,
                "ordinal": r.ordinal,
                "label": time_label(r, tz_name),
                "status": r.status,
                "booked": r.booked,
                "event_link": r.event_link,
                # raw instants so the frontend can place booked times on the
                # calendar and detect duplicate proposals
                "start_iso": r.start.astimezone(timezone.utc).isoformat(),
                "end_iso": r.end.astimezone(timezone.utc).isoformat(),
            }
            for r in plan.rounds
        ],
        "ballot": {
            "stage": ballot.stage,
            "note": ballot.note,
            # the question the member is being asked, if any
            "round_id": active.id if (active and ballot.stage == "time") else None,
            "time_label": time_label(active, tz_name) if (active and ballot.stage == "time") else None,
        },
    }
    if is_host:
        t = plan_tally(session, plan, tz_name)
        out["host_box"] = {
            "interested": t.interested,
            "not_interested": t.not_interested,
            "no_answer": t.no_interest_answer,
            "time_yes": t.time_yes,
            "time_no": t.time_no,
            "time_waiting": t.time_waiting,
            "note": t.host_note,
        }
    return out


def _require_membership(session: Session, user: User, group_id: int) -> None:
    if group_id not in {g.id for g in repo.get_user_groups(session, user)}:
        raise HTTPException(status_code=403, detail="You are not in this group.")


def _get_plan_for_member(session: Session, user: User, plan_id: int) -> Plan:
    plan = repo.get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan.")
    _require_membership(session, user, plan.group_id)
    if plan.status != "open":
        raise HTTPException(status_code=400, detail=f"This plan is {plan.status}; voting is closed.")
    return plan


@router.get("/groups/{group_id}/plans")
def group_plans(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _require_membership(session, user, group_id)
    plans = repo.get_group_plans(session, group_id)[:10]
    return [_plan_json(session, p, user, user.timezone) for p in plans]


def _parse_iso_utc(value: str, name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad {name}: not ISO 8601.")
    if dt.tzinfo is None:
        raise HTTPException(status_code=400, detail=f"Bad {name}: must include a timezone offset.")
    return dt.astimezone(timezone.utc)


@router.post("/groups/{group_id}/plans")
def create_plan(
    group_id: int,
    body: CreatePlanBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Direct plan creation from the app UI — same shape as the agent's
    create_plan tool. An empty slot list is a pure interest check ("who's in?");
    the host can queue times later once they know who's coming."""
    _require_membership(session, user, group_id)
    slots = []
    for i, s in enumerate(body.slots):
        start = _parse_iso_utc(s.start_iso, f"slots[{i}].start_iso")
        end = _parse_iso_utc(s.end_iso, f"slots[{i}].end_iso")
        if end <= start:
            raise HTTPException(status_code=400, detail=f"slots[{i}]: end must be after start.")
        slots.append((start, end))

    group = next(g for g in repo.get_user_groups(session, user) if g.id == group_id)
    plan = repo.create_plan(
        session, group, user, title=body.title.strip(),
        slots=slots, location=(body.location or "").strip() or None,
    )
    log.info("[plan %d] %s created it directly from the app (%d candidate times)",
             plan.id, user.email, len(slots))
    return _plan_json(session, plan, user, user.timezone)


@router.post("/plans/{plan_id}/interest")
def vote_interest(
    plan_id: int,
    body: InterestBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Stage 1. A yes here immediately opens the active time question for this
    member — the response's ballot already carries it."""
    plan = _get_plan_for_member(session, user, plan_id)
    repo.cast_interest(session, plan, user, body.yes)
    log.info("[plan %d] %s is %s for the plan", plan.id, user.email,
             "IN" if body.yes else "OUT")
    return _plan_json(session, plan, user, user.timezone)


@router.post("/plans/{plan_id}/time-vote")
def vote_time(
    plan_id: int,
    body: TimeVoteBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Stage 2. Only the interested cohort may answer, and only the time that is
    active right now — round_id is required so a vote cast while the host was
    switching times can't silently land on the wrong one."""
    plan = _get_plan_for_member(session, user, plan_id)
    if not repo.get_interest_votes(session, plan).get(user.email):
        raise HTTPException(
            status_code=403,
            detail="Say you're in for the plan first — times are only asked of people who are.",
        )
    active = repo.get_active_round(session, plan)
    if active is None:
        raise HTTPException(status_code=400, detail="No time is on the table for this plan.")
    if active.id != body.round_id:
        raise HTTPException(
            status_code=409,
            detail=f"The host moved on — the question is now {time_label(active, user.timezone)}.",
        )

    repo.cast_time_vote(session, active, user, body.yes)
    log.info("[plan %d] %s said %s to %s", plan.id, user.email,
             "YES" if body.yes else "NO", time_label(active, user.timezone))
    return _plan_json(session, plan, user, user.timezone)
