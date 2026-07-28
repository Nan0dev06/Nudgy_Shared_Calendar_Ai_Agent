"""Voting without an account — the public face of a plan.

GET  /share/{token}             -> the plan as an outsider sees it
POST /share/{token}/join        {"name": "Sam", "email": null} -> become a guest
POST /share/{token}/interest    {"yes": true}
POST /share/{token}/time-vote   {"yes": true, "round_id": 3}

These are the ONLY unauthenticated write endpoints in the app, so the rules they
work under are worth stating plainly:

- The token is a bearer credential scoped to ONE plan. It reveals that plan and
  lets the holder answer its two questions. Nothing else — not the group, not
  the other plans, not anyone's calendar.
- The response deliberately carries no email addresses. A member is shown by
  first name only; the link may travel further than the group ever intended.
- Guests are capped per plan and pinned to a browser cookie, so the link is not
  a ballot-stuffing machine. The host can regenerate it (killing every copy) or
  revoke it outright at any time.
- Everything else — the deadline, whether voting is open, the cascade — is the
  same code members go through. A guest's vote is a real vote, counted in the
  host's tally and in the unanimity check that can auto-book a plan.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_KWARGS
from app.auth.guest_tokens import (
    GUEST_COOKIE, GUEST_TTL_SECONDS, guest_id_for, with_guest,
)
from app.db.models import Plan, PlanGuest, User
from app.db import repo
from app.db.session import get_session
from app.tools.plan_service import (
    day_label, guest_ballot, load_plan_state, maybe_auto_book, time_label,
)

log = logging.getLogger("nudgy.agent")

router = APIRouter(prefix="/share", tags=["share"])


class JoinBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    # optional, and used for one thing only: the calendar invite if this gets
    # booked. Voting never requires it.
    email: str | None = Field(default=None, max_length=200)


class InterestBody(BaseModel):
    yes: bool


class TimeVoteBody(BaseModel):
    yes: bool
    round_id: int


def _first_name(user: User) -> str:
    """A member's display name for outside eyes — never their email."""
    if user.display_name:
        return user.display_name.split()[0]
    local = user.email.split("@")[0].split(".")[0].split("+")[0]
    return (local[:1].upper() + local[1:]) if local else "Someone"


def _plan_for_token(session: Session, token: str) -> Plan:
    plan = repo.get_plan_by_share_token(session, token)
    if plan is None:
        # Same answer for "never existed" and "host revoked it": the holder of a
        # dead link learns nothing about whether the plan is real.
        raise HTTPException(status_code=404, detail="This link isn't active any more.")
    return plan


def _votable(plan: Plan) -> None:
    if plan.status == "expired":
        raise HTTPException(status_code=400,
                            detail="The deadline for this plan has passed; voting is closed.")
    if plan.status != "open":
        raise HTTPException(status_code=400,
                            detail=f"This plan is {plan.status}; voting is closed.")
    if plan.deadline is not None and datetime.now(timezone.utc) >= plan.deadline:
        raise HTTPException(status_code=400,
                            detail="The deadline for this plan has passed; voting is closed.")


def _share_json(session: Session, plan: Plan, guest: PlanGuest | None) -> dict:
    """The plan as a link-holder sees it: enough to answer honestly, nothing more.

    Counts instead of rosters, first names instead of addresses. Times carry
    their UTC instant as well as a host-timezone label, so the page can render
    them in the reader's own zone — a guest has no stored timezone to use.
    """
    state = load_plan_state(session, plan)
    host = session.get(User, plan.created_by)
    tz = host.timezone if host else "UTC"
    group = repo.get_group(session, plan.group_id)
    interested = [e for e, v in state.interest_votes.items() if v]

    out = {
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, tz),
        "status": plan.status,
        "voting_open": plan.status == "open",
        "deadline_iso": plan.deadline.isoformat() if plan.deadline else None,
        "host_name": _first_name(host) if host else "Someone",
        "group_name": group.name if group else None,
        # counts, not a roster: how many are in out of how many were asked
        "going_count": len(interested),
        "people_count": len(state.participants),
        "times": [
            {
                "round_id": r.id,
                "label": time_label(r, tz),
                "status": r.status,
                "start_iso": r.start.isoformat(),
                "end_iso": r.end.isoformat(),
            }
            for r in plan.rounds
        ],
        "active_round_id": state.active.id if state.active else None,
        "active_time_label": time_label(state.active, tz) if state.active else None,
    }
    if guest is not None:
        b = guest_ballot(plan, guest, state=state)
        out["me"] = {"name": guest.name, "stage": b.stage, "note": b.note}
    else:
        out["me"] = None
    return out


@router.get("/{token}")
def view_shared_plan(
    token: str,
    nudgy_guest: str | None = Cookie(default=None, alias=GUEST_COOKIE),
    session: Session = Depends(get_session),
):
    plan = _plan_for_token(session, token)
    guest = _resolve_guest(session, plan, nudgy_guest)
    return _share_json(session, plan, guest)


def _resolve_guest(session: Session, plan: Plan, cookie: str | None) -> PlanGuest | None:
    """The guest this browser is, on THIS plan. The plan check matters: a cookie
    naming a guest of some other plan must not be usable here."""
    guest = repo.get_guest(session, guest_id_for(cookie, plan.id))
    return guest if (guest is not None and guest.plan_id == plan.id) else None


def _require_guest(session: Session, plan: Plan, cookie: str | None) -> PlanGuest:
    guest = _resolve_guest(session, plan, cookie)
    if guest is None:
        raise HTTPException(status_code=401,
                            detail="Tell us your name first so your answer has someone on it.")
    return guest


@router.post("/{token}/join")
def join_shared_plan(
    token: str,
    body: JoinBody,
    response: Response,
    nudgy_guest: str | None = Cookie(default=None, alias=GUEST_COOKIE),
    session: Session = Depends(get_session),
):
    """Claim a name on this plan and get the cookie that keeps it.

    Re-joining with the name this browser already holds is a no-op rename-safe
    path (people refresh); claiming a name someone ELSE is using is refused,
    because silently reusing it would drop that person's ballot on the floor.
    """
    plan = _plan_for_token(session, token)
    _votable(plan)

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A name is needed — any name.")

    mine = _resolve_guest(session, plan, nudgy_guest)
    existing = repo.find_guest_by_name(session, plan, name)
    if existing is not None and (mine is None or existing.id != mine.id):
        raise HTTPException(
            status_code=409,
            detail=f"Someone is already voting as {existing.name} here. Add a last "
                   "initial so the host can tell you apart.",
        )
    if mine is not None:
        guest = mine
        guest.name = name
        if body.email:
            guest.email = body.email.strip() or None
        session.commit()
    else:
        if len(repo.get_plan_guests(session, plan)) >= repo.MAX_GUESTS_PER_PLAN:
            raise HTTPException(
                status_code=403,
                detail="This plan has taken as many link votes as it can hold.",
            )
        guest = repo.create_guest(session, plan, name, (body.email or "").strip() or None)
        log.info("[plan %d] %s joined through the share link", plan.id, guest.label)

    response.set_cookie(
        GUEST_COOKIE, with_guest(nudgy_guest, plan.id, guest.id),
        max_age=GUEST_TTL_SECONDS, **COOKIE_KWARGS,
    )
    return _share_json(session, plan, guest)


@router.post("/{token}/interest")
def guest_interest(
    token: str,
    body: InterestBody,
    nudgy_guest: str | None = Cookie(default=None, alias=GUEST_COOKIE),
    session: Session = Depends(get_session),
):
    plan = _plan_for_token(session, token)
    _votable(plan)
    guest = _require_guest(session, plan, nudgy_guest)
    repo.cast_guest_interest(session, guest, body.yes)
    log.info("[plan %d] %s is %s for the plan", plan.id, guest.label,
             "IN" if body.yes else "OUT")
    maybe_auto_book(session, plan, "UTC")
    return _share_json(session, plan, guest)


@router.post("/{token}/time-vote")
def guest_time_vote(
    token: str,
    body: TimeVoteBody,
    nudgy_guest: str | None = Cookie(default=None, alias=GUEST_COOKIE),
    session: Session = Depends(get_session),
):
    plan = _plan_for_token(session, token)
    _votable(plan)
    guest = _require_guest(session, plan, nudgy_guest)

    state = load_plan_state(session, plan)
    if not state.interest_votes.get(guest.label):
        raise HTTPException(
            status_code=403,
            detail="Say you're in for the plan first — times are only asked of people who are.",
        )
    if state.active is None:
        raise HTTPException(status_code=400, detail="No time is on the table for this plan.")
    # same guard members get: a vote cast while the host was switching times
    # must not land on the wrong question
    if state.active.id != body.round_id:
        raise HTTPException(status_code=409, detail="The host moved on to a different time.")

    repo.cast_guest_time_vote(session, state.active, guest, body.yes)
    log.info("[plan %d] %s said %s to the active time", plan.id, guest.label,
             "YES" if body.yes else "NO")
    maybe_auto_book(session, plan, "UTC")
    return _share_json(session, plan, guest)
