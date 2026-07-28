"""Plan endpoints: members answer their step of the cascade; Nudgy creates plans.

GET  /groups/{group_id}/plans      -> plans for a group, each carrying THIS
                                      member's current ballot (and, for the
                                      host, the decision box)
POST /groups/{group_id}/plans      -> create a plan directly from the app UI
                                      (title required; candidate times and
                                      location optional — an empty slot list is
                                      a pure "who's in?" interest check)
PATCH /plans/{plan_id}             {"deadline_iso": ..., "auto_book": true}
                                   -> host: when voting closes, and whether a
                                      unanimous plan may book itself
POST /plans/{plan_id}/interest     {"yes": true}  -> stage 1 answer
POST /plans/{plan_id}/time-vote    {"yes": true, "round_id": 3} -> stage 2 answer

The cascade is visible in the responses: answering interest=yes comes straight
back with `ballot.stage == "time"` — that one yes opened the time question for
that member, without waiting on anybody else.

Note what is NOT here: no rule fires on a vote. Voting never rejects and never
advances a time; those are host moves (deterministic endpoints below, or the
agent's use_next_time / lock_in_time), which is what keeps a human in the loop
before anything reaches a calendar. The single exception is opt-in auto-book,
and it only fires on unanimity — when there was nothing left for a human to
decide. See tools/plan_deadlines.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import APP_BASE_URL
from app.db.models import Plan, User
from app.db import repo
from app.db.session import get_session
from app.tools.plan_service import (
    HOST_DECIDABLE, advance_to_next_time, confirm_active_time, day_label,
    load_plan_state, maybe_auto_book, member_ballot, plan_tally, time_label,
)

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
    expected_count: int | None = Field(default=None, ge=1, le=100)
    # async convergence, both optional — a plan with neither behaves exactly as
    # it did before this existed
    deadline_iso: str | None = None
    auto_book: bool = False


class PlanSettingsBody(BaseModel):
    """Host edits to how a plan converges. Every field is optional AND
    nullable-meaningful, so `unset` (leave alone) has to be distinguishable from
    `null` (clear the deadline) — hence the sentinel default on deadline_iso."""
    deadline_iso: str | None = Field(default="__unset__")
    auto_book: bool | None = None


class AddRoundsBody(BaseModel):
    slots: list[SlotBody] = Field(min_length=1, max_length=6)


def _plan_json(session: Session, plan: Plan, viewer: User, tz_name: str) -> dict:
    # one fetch of the plan's vote state, reused by both the ballot and (for the
    # host) the tally — see plan_service.load_plan_state
    state = load_plan_state(session, plan)
    active = state.active
    ballot = member_ballot(session, plan, viewer, state=state)
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
        "expected_count": plan.expected_count,
        # async convergence: the deadline instant (UTC, the frontend renders the
        # countdown in local time), whether the plan may book itself, and whether
        # the ballot is still answerable at all
        "deadline_iso": plan.deadline.isoformat() if plan.deadline else None,
        "auto_book": plan.auto_book,
        "voting_open": plan.status == "open",
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
        # the link itself is host-only: it's a bearer credential, and a member
        # who wants to invite someone can ask the host for it
        out["share_url"] = share_url(plan.share_token)
        t = plan_tally(session, plan, tz_name, state=state)
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
    if plan.status == "expired":
        raise HTTPException(status_code=400,
                            detail="The deadline for this plan has passed; voting is closed.")
    if plan.status != "open":
        raise HTTPException(status_code=400, detail=f"This plan is {plan.status}; voting is closed.")
    # The ticker flips a plan to `expired` within a minute of its deadline, so
    # for up to a minute an open plan can be past it. Enforce the deadline here
    # too — the instant the host set is the promise, not when the job wakes up.
    if plan.deadline is not None and datetime.now(timezone.utc) >= plan.deadline:
        raise HTTPException(status_code=400,
                            detail="The deadline for this plan has passed; voting is closed.")
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


def _parse_deadline(value: str | None) -> datetime | None:
    """A vote deadline from the client: ISO-8601 with an offset, in the future."""
    if value is None:
        return None
    deadline = _parse_iso_utc(value, "deadline_iso")
    if deadline <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400,
                            detail="The vote deadline has to be in the future.")
    return deadline


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

    deadline = _parse_deadline(body.deadline_iso)
    group = next(g for g in repo.get_user_groups(session, user) if g.id == group_id)
    location = (body.location or "").strip() or None

    # Backstop the client-side guard (which only catches an exact title match):
    # if an open plan for this place and these times already exists, return it
    # instead of creating a near-duplicate with a different title.
    dup = repo.find_duplicate_open_plan(session, group, location, slots)
    if dup is not None:
        log.info("[plan %d] %s tried to create a duplicate (same place+times) — returning existing",
                 dup.id, user.email)
        return _plan_json(session, dup, user, user.timezone)

    plan = repo.create_plan(
        session, group, user, title=body.title.strip(),
        slots=slots, location=location,
        expected_count=body.expected_count,
        deadline=deadline, auto_book=body.auto_book,
    )
    log.info("[plan %d] %s created it directly from the app (%d candidate times%s%s)",
             plan.id, user.email, len(slots),
             f", closes {deadline:%Y-%m-%d %H:%M}Z" if deadline else "",
             ", auto-book" if body.auto_book else "")
    return _plan_json(session, plan, user, user.timezone)


@router.patch("/plans/{plan_id}")
def update_plan_settings(
    plan_id: int,
    body: PlanSettingsBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host-only: set/move/clear the vote deadline and toggle auto-book.

    Works on an EXPIRED plan too — that's the point. Giving a plan that ran out
    of time a fresh deadline reopens voting (repo.set_plan_deadline), which is
    how a host says "a couple of you never answered, take another day" instead
    of rebuilding the plan from scratch.
    """
    plan = repo.get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan.")
    _require_membership(session, user, plan.group_id)
    if user.id != plan.created_by:
        raise HTTPException(status_code=403,
                            detail="Only the host who suggested this plan can change it.")
    if plan.status in ("scheduled", "dead"):
        raise HTTPException(status_code=400, detail=f"This plan is {plan.status}.")

    if body.auto_book is not None:
        repo.set_plan_auto_book(session, plan, body.auto_book)
    if body.deadline_iso != "__unset__":
        repo.set_plan_deadline(session, plan, _parse_deadline(body.deadline_iso))
        log.info("[plan %d] host %s set the vote deadline to %s",
                 plan.id, user.email, body.deadline_iso or "none")

    # Turning auto-book ON can land on an already-unanimous plan — book it now
    # rather than making the host wait for a vote that may never come.
    if plan.status == "open" and plan.auto_book:
        maybe_auto_book(session, plan, user.timezone)
    return _plan_json(session, plan, user, user.timezone)


@router.post("/plans/{plan_id}/share")
def share_plan(
    plan_id: int,
    regenerate: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host-only: get this plan's public vote link, minting one if needed.

    `regenerate=true` mints a fresh token, which kills every copy of the old link
    at once — the move for "that got forwarded further than I meant". Votes
    already cast through the old link stay; the people who cast them were
    invited in good faith.
    """
    plan = _host_plan(session, user, plan_id)
    token = (repo.regenerate_share_token(session, plan) if regenerate
             else repo.ensure_share_token(session, plan))
    log.info("[plan %d] host %s %s the share link", plan.id, user.email,
             "regenerated" if regenerate else "opened")
    return {"share_url": share_url(token), "share_token": token}


@router.delete("/plans/{plan_id}/share")
def unshare_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host-only: turn the link off. Guest votes already cast are kept."""
    plan = _host_plan(session, user, plan_id)
    repo.revoke_share_token(session, plan)
    log.info("[plan %d] host %s revoked the share link", plan.id, user.email)
    return {"share_url": None}


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host-only: remove a poll entirely (any status — open, booked, or dead).
    Deletes its candidate times and votes with it; a Google Calendar event a
    booked round created is left in place."""
    plan = repo.get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan.")
    _require_membership(session, user, plan.group_id)
    if user.id != plan.created_by:
        raise HTTPException(status_code=403, detail="Only the host who made this poll can delete it.")
    repo.delete_plan(session, plan)
    log.info("[plan %d] deleted by host %s", plan_id, user.email)
    return {"deleted": True, "plan_id": plan_id}


@router.post("/plans/{plan_id}/rounds")
def add_rounds(
    plan_id: int,
    body: AddRoundsBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host-only: append candidate times to an existing OPEN plan — the way a
    timeless "who's in?" check grows into a timed poll without starting over.
    If the plan had no live time, the first appended one activates immediately."""
    plan = _get_plan_for_member(session, user, plan_id)
    if user.id != plan.created_by:
        raise HTTPException(status_code=403, detail="Only the host can add times.")
    if len(plan.rounds) + len(body.slots) > 8:
        raise HTTPException(status_code=400, detail="A plan can hold at most 8 candidate times.")
    slots = []
    for i, s in enumerate(body.slots):
        start = _parse_iso_utc(s.start_iso, f"slots[{i}].start_iso")
        end = _parse_iso_utc(s.end_iso, f"slots[{i}].end_iso")
        if end <= start:
            raise HTTPException(status_code=400, detail=f"slots[{i}]: end must be after start.")
        slots.append((start, end))
    repo.append_rounds(session, plan, slots)
    log.info("[plan %d] %s appended %d candidate time(s)", plan.id, user.email, len(slots))
    return _plan_json(session, plan, user, user.timezone)


def share_url(token: str | None) -> str | None:
    """The link a host copies. Lands on the SPA (which has no router), so the
    token rides in the query string — same shape as the password-reset link."""
    return f"{APP_BASE_URL}/?share={token}" if token else None


def _host_plan(session: Session, user: User, plan_id: int) -> Plan:
    """Resolve a plan the user hosts, in any status. For host moves that stay
    valid after the plan settles — sharing, unsharing, deleting."""
    plan = repo.get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan.")
    _require_membership(session, user, plan.group_id)
    if user.id != plan.created_by:
        raise HTTPException(status_code=403,
                            detail="Only the host who suggested this plan can do that.")
    return plan


def _host_open_plan(session: Session, user: User, plan_id: int) -> Plan:
    """Resolve a plan for a HOST-ONLY move: it must exist, be in the user's
    group, the user must be its host, and it must still be decidable.

    `expired` counts as decidable: the deadline closes VOTING, not the host's
    ability to act on the votes that did arrive. plan_service draws the finer
    line — lock-in works on an expired plan, putting a new time up doesn't."""
    plan = repo.get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan.")
    _require_membership(session, user, plan.group_id)
    if user.id != plan.created_by:
        raise HTTPException(status_code=403, detail="Only the host who suggested this plan can do that.")
    if plan.status not in HOST_DECIDABLE:
        raise HTTPException(status_code=400, detail=f"This plan is already {plan.status}.")
    return plan


@router.post("/plans/{plan_id}/lock-in")
def lock_in_time(
    plan_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host move — commit the active time and book ONLY its yes-voters. This is a
    direct, deterministic path to the calendar; the agent can also do it via chat,
    but a real booking should never hinge on the model interpreting a sentence.
    The host guard + revert-on-failure live in plan_service.confirm_active_time."""
    plan = _host_open_plan(session, user, plan_id)
    result = confirm_active_time(session, plan, user, user.timezone)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    if result.get("action") == "book_failed":
        # Google refused/threw; the round was reverted to active so the host retries.
        raise HTTPException(status_code=502,
                            detail=result.get("error") or "The calendar booking failed — try again.")
    log.info("[plan %d] host %s locked in via API", plan.id, user.email)
    return {"action": result.get("action"), "plan": _plan_json(session, plan, user, user.timezone)}


@router.post("/plans/{plan_id}/next-time")
def next_time(
    plan_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host move — drop the active time and ask the next queued one to the whole
    interested cohort. Out of times -> the plan closes (dead)."""
    plan = _host_open_plan(session, user, plan_id)
    result = advance_to_next_time(session, plan, user, user.timezone)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    log.info("[plan %d] host %s advanced time via API (%s)", plan.id, user.email, result.get("action"))
    return {"action": result.get("action"), "plan": _plan_json(session, plan, user, user.timezone)}


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
    maybe_auto_book(session, plan, user.timezone)
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
    # The vote that completes a unanimous plan is the one that books it (opt-in
    # only) — see plan_service.maybe_auto_book. A no-op for everything else.
    maybe_auto_book(session, plan, user.timezone)
    return _plan_json(session, plan, user, user.timezone)
