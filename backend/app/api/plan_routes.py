"""Poll endpoints: members answer; the host decides; the deadline converges.

GET  /groups/{group_id}/plans      -> polls for a group, each carrying THIS
                                      member's ballot (and, for the host, the
                                      decision box)
POST /groups/{group_id}/plans      -> create a poll from the app UI. No times =
                                      Float-an-idea ("who's in?"); times = a
                                      time poll. See plan_rules.mode_of.
PATCH /plans/{plan_id}             {"deadline_iso": ..., "minimum": 4}
                                   -> host: when voting closes, and how many
                                      members must be able to make a time
                                      before it books without anyone
POST /plans/{plan_id}/rounds       -> ANY member adds candidate times
POST /plans/{plan_id}/spotlight    {"round_id": 3 | null} -> host: lean toward
                                      one time. Resets no votes.
POST /plans/{plan_id}/lock-in      {"round_id": 3} -> host: book that time
POST /plans/{plan_id}/interest     {"yes": true}  -> Float only
POST /plans/{plan_id}/time-vote    {"round_id": 3, "answer": "yes"|"no"|"if_needed"}

Every candidate time is votable at once, so a member's ballot is "which of these
work for you?", not one question at a time. There is no active round and no
queue (docs/poll-edit-redesign.md §1.2).

What a vote CAN now do that it couldn't before: finish the poll. When the last
outstanding answer lands and a time meets the minimum, the poll books itself —
see plan_service.converge. That is safe because booking only ever invites people
who said yes or if-needed, so nothing reaches the calendar of somebody who
declined or stayed silent. The host is never gated by the minimum; they can lock
in any time for whoever can make it.
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
from app.realtime import events_changed, plans_changed
from app.tools.plan_rules import VOTE_STATES, mode_of
from app.tools.plan_service import (
    HOST_DECIDABLE, confirm_time, converge, day_label, load_plan_state,
    member_ballot, minimum_for, plan_tally, set_spotlight, time_label,
)

log = logging.getLogger("nudgy.agent")

router = APIRouter(tags=["plans"])


class InterestBody(BaseModel):
    yes: bool


class TimeVoteBody(BaseModel):
    round_id: int
    answer: str


class SlotBody(BaseModel):
    start_iso: str
    end_iso: str


class CreatePlanBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    slots: list[SlotBody] = Field(default_factory=list, max_length=6)
    # how many MEMBERS must be able to make a time before it books itself.
    # Omitted -> the whole group, the safe default: a poll can then only converge
    # on everybody, and lowering the bar is always a deliberate human act.
    expected_count: int | None = Field(default=None, ge=1, le=100)
    deadline_iso: str | None = None


class PlanSettingsBody(BaseModel):
    """Host edits to how a poll converges. Every field is optional AND
    nullable-meaningful, so `unset` (leave alone) has to be distinguishable from
    `null` (clear the deadline) — hence the sentinel default on deadline_iso."""
    deadline_iso: str | None = Field(default="__unset__")
    minimum: int | None = Field(default=None, ge=1, le=100)


class SpotlightBody(BaseModel):
    round_id: int | None = None


class LockInBody(BaseModel):
    round_id: int


class AddRoundsBody(BaseModel):
    slots: list[SlotBody] = Field(min_length=1, max_length=6)


def _plan_json(session: Session, plan: Plan, viewer: User, tz_name: str) -> dict:
    # one fetch of the poll's vote state, reused by the ballot, the per-time
    # counts and (for the host) the tally — see plan_service.load_plan_state
    state = load_plan_state(session, plan)
    ballot = member_ballot(session, plan, viewer, state=state)
    is_host = viewer.id == plan.created_by
    host = session.get(User, plan.created_by)
    t = plan_tally(session, plan, tz_name, state=state)
    by_key = {r.key: r for r in t.times}
    my_votes = state.votes_by_time
    # who suggested each time, resolved once rather than per round
    suggesters = {
        u.id: u.email for u in repo.get_group_members(session, plan.group_id)
    }

    out = {
        "id": plan.id,
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, tz_name),
        "status": plan.status,
        "host": host.email if host else None,
        "is_host": is_host,
        "mode": mode_of(asks_interest=plan.asks_interest, times_total=len(plan.rounds)),
        "asks_interest": plan.asks_interest,
        # The bar for booking without a human. `requires_all_members` says which
        # KIND it is: the default rule (those specific people, guests can't
        # substitute) or a count the creator typed (guests count toward it).
        "minimum": state.minimum,
        "requires_all_members": state.explicit_minimum is None,
        "guest_count": len(state.guest_labels),
        "spotlight_round_id": plan.spotlight_round_id,
        "deadline_iso": plan.deadline.isoformat() if plan.deadline else None,
        "voting_open": plan.status == "open",
        "times": [
            {
                "round_id": r.id,
                "ordinal": r.ordinal,
                "label": time_label(r, tz_name),
                "booked": r.booked,
                "event_link": r.event_link,
                "spotlit": r.id == plan.spotlight_round_id,
                # raw instants so the frontend can place booked times on the
                # calendar and detect duplicate proposals
                "start_iso": r.start.astimezone(timezone.utc).isoformat(),
                "end_iso": r.end.astimezone(timezone.utc).isoformat(),
                # every time's standing, so the whole grid renders from one GET
                "yes": by_key[r.id].member_yes,
                "if_needed": len(by_key[r.id].if_needed),
                "no": len(by_key[r.id].no),
                "waiting": len(by_key[r.id].waiting),
                "guest_yes": len(by_key[r.id].guest_yes) + len(by_key[r.id].guest_if_needed),
                "qualifies": by_key[r.id].qualifies(
                    minimum=state.explicit_minimum,
                    member_total=len(state.member_emails),
                ),
                # "suggested by X" on the card. Only the suggester may remove it,
                # and never once it's booked.
                "suggested_by": suggesters.get(r.created_by),
                "can_remove": (r.created_by == viewer.id and not r.booked
                               and plan.status == "open"),
                # what THIS viewer said, so their choice stays visible
                "my_answer": my_votes.get(r.id, {}).get(viewer.email),
            }
            for r in plan.rounds
        ],
        "ballot": {
            "stage": ballot.stage,
            "note": ballot.note,
            "unanswered": ballot.unanswered,
        },
    }
    if is_host:
        # the link itself is host-only: it's a bearer credential, and a member
        # who wants to invite someone can ask the host for it
        out["share_url"] = share_url(plan.share_token)
        out["host_box"] = {
            "interested": t.interested,
            "not_interested": t.not_interested,
            "no_answer": t.no_interest_answer,
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

    # expected_count omitted -> stored as NULL, which is the DEFAULT RULE: every
    # account-holding member must be able to make a time before it books itself.
    # Deliberately not written as a number here — a number could be satisfied by
    # guests, and "the group agreed" is a statement about those specific people.
    plan = repo.create_plan(
        session, group, user, title=body.title.strip(),
        slots=slots, location=location,
        expected_count=body.expected_count, deadline=deadline,
    )
    log.info("[plan %d] %s created it from the app (%d candidate times, bar %s%s)",
             plan.id, user.email, len(slots),
             body.expected_count or "all members",
             f", closes {deadline:%Y-%m-%d %H:%M}Z" if deadline else "")
    # Everyone else in the group is looking at a plan list that no longer has
    # this in it. Poke them so their card appears now, not at the next poll —
    # here and after every other mutation below (see app/realtime).
    plans_changed(group_id)
    return _plan_json(session, plan, user, user.timezone)


@router.patch("/plans/{plan_id}")
def update_plan_settings(
    plan_id: int,
    body: PlanSettingsBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host-only: set/move/clear the vote deadline, and change the minimum.

    Works on an EXPIRED poll too — that's the point. Giving a poll that ran out
    of time a fresh deadline reopens voting (repo.set_plan_deadline), which is
    how a host says "a couple of you never answered, take another day" instead
    of rebuilding it from scratch. Lowering the minimum on an expired poll is the
    other way back: the group answered, just not in the numbers first asked for.
    """
    plan = repo.get_plan(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan.")
    _require_membership(session, user, plan.group_id)
    if user.id != plan.created_by:
        raise HTTPException(status_code=403,
                            detail="Only the host who suggested this plan can change it.")
    if plan.status == "booked":
        raise HTTPException(status_code=400, detail="This plan is already booked.")

    if body.minimum is not None:
        repo.set_plan_minimum(session, plan, body.minimum)
        log.info("[plan %d] host %s set the minimum to %d", plan.id, user.email, body.minimum)
    if body.deadline_iso != "__unset__":
        repo.set_plan_deadline(session, plan, _parse_deadline(body.deadline_iso))
        log.info("[plan %d] host %s set the vote deadline to %s",
                 plan.id, user.email, body.deadline_iso or "none")

    # Lowering the bar can retroactively make an already-complete poll bookable,
    # so re-check rather than making the host wait for a vote that won't come.
    converge(session, plan, user.timezone)
    plans_changed(plan.group_id)
    if plan.status == "booked":
        events_changed(plan.group_id)
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
    group_id = plan.group_id
    repo.delete_plan(session, plan)
    log.info("[plan %d] deleted by host %s", plan_id, user.email)
    plans_changed(group_id)
    return {"deleted": True, "plan_id": plan_id}


@router.post("/plans/{plan_id}/rounds")
def add_rounds(
    plan_id: int,
    body: AddRoundsBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """ANY member adds candidate times to an open poll.

    Members propose, the host decides (docs/poll-edit-redesign.md §1.6). This was
    `[LOCKED]` on 2026-07-25 as "members can propose alternative times" and was
    host-only in code until now — someone who spots a time that works for
    everyone can put it up, and nobody but the host commits the group to it.

    New times are votable immediately and disturb no existing vote."""
    plan = _get_plan_for_member(session, user, plan_id)
    if len(plan.rounds) + len(body.slots) > 8:
        raise HTTPException(status_code=400, detail="A plan can hold at most 8 candidate times.")
    slots = []
    for i, s in enumerate(body.slots):
        start = _parse_iso_utc(s.start_iso, f"slots[{i}].start_iso")
        end = _parse_iso_utc(s.end_iso, f"slots[{i}].end_iso")
        if end <= start:
            raise HTTPException(status_code=400, detail=f"slots[{i}]: end must be after start.")
        slots.append((start, end))
    repo.append_rounds(session, plan, slots, suggested_by=user)
    log.info("[plan %d] %s suggested %d candidate time(s)", plan.id, user.email, len(slots))
    plans_changed(plan.group_id)
    return _plan_json(session, plan, user, user.timezone)


@router.delete("/plans/{plan_id}/rounds/{round_id}")
def remove_round(
    plan_id: int,
    round_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Take back a time YOU suggested. Only yours, and only before it's booked.

    Scoped to your own suggestion rather than gated on the host: adding a time is
    open to every member, so being able to undo your own mistake is part of the
    same affordance. Removing someone else's would let one member quietly delete
    the option the group was converging on — and the votes cast on it.
    """
    plan = _get_plan_for_member(session, user, plan_id)
    round_ = next((r for r in plan.rounds if r.id == round_id), None)
    if round_ is None:
        raise HTTPException(status_code=404,
                            detail="That time isn't one of this poll's candidates.")
    if round_.created_by != user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only remove a time you suggested yourself.",
        )
    if round_.booked:
        raise HTTPException(status_code=400,
                            detail="That time is booked — it can't be removed.")
    repo.delete_round(session, plan, round_)
    log.info("[plan %d] %s removed their suggested time %d", plan.id, user.email, round_id)
    # Removing a candidate can leave the remaining ones complete, so re-check.
    converge(session, plan, user.timezone)
    plans_changed(plan.group_id)
    if plan.status == "booked":
        events_changed(plan.group_id)
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


@router.post("/plans/{plan_id}/spotlight")
def spotlight_time(
    plan_id: int,
    body: SpotlightBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host move — lean toward one time (or clear it). Replaces /next-time.

    Nothing is skipped, nothing is reset, and it can be moved back. That is the
    entire point: the endpoint it replaces made every prior vote irrelevant, so
    hosts avoided using it.
    """
    plan = _host_open_plan(session, user, plan_id)
    result = set_spotlight(session, plan, user, body.round_id, user.timezone)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    log.info("[plan %d] host %s moved the spotlight", plan.id, user.email)
    plans_changed(plan.group_id)
    return {"action": result.get("action"), "note": result.get("note"),
            "plan": _plan_json(session, plan, user, user.timezone)}


@router.post("/plans/{plan_id}/lock-in")
def lock_in_time(
    plan_id: int,
    body: LockInBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Host move — book the time the host NAMES, for everyone who said yes or
    if-needed to it.

    The host picks explicitly rather than inheriting the spotlight: "what we're
    leaning toward" and "what we're committing to" are different statements, and
    coupling them would make locking in a different time a two-step dance.

    Deliberately not gated by the minimum — that bar governs what happens without
    a human. This is a direct, deterministic path to the calendar; the agent can
    do it via chat too, but a real booking should never hinge on the model
    interpreting a sentence."""
    plan = _host_open_plan(session, user, plan_id)
    result = confirm_time(session, plan, user, body.round_id, user.timezone)
    # book_failed FIRST: those results carry an "error" too, so checking the
    # generic error branch first made every calendar failure a 400 and left the
    # 502 unreachable — i.e. "you asked wrong" for something that was a
    # retry-able upstream problem, with the round already put back to active.
    if result.get("action") == "book_failed":
        raise HTTPException(status_code=502,
                            detail=result.get("error") or "The calendar booking failed — try again.")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    log.info("[plan %d] host %s locked in round %d via API",
             plan.id, user.email, body.round_id)
    # The card settles AND an event lands on the group's calendar — both views
    # are stale for every other member until they hear about it.
    plans_changed(plan.group_id)
    events_changed(plan.group_id)
    return {
        "action": result.get("action"),
        # WHICH time was committed, and who it went to. The card needs this to
        # say "booked Fri 7pm for 4 of you" without re-deriving it from the plan.
        "round_id": result.get("round_id"),
        "time": result.get("time"),
        "attendees": result.get("attendees"),
        "event_link": result.get("event_link"),
        "plan": _plan_json(session, plan, user, user.timezone),
    }


@router.post("/plans/{plan_id}/interest")
def vote_interest(
    plan_id: int,
    body: InterestBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Float-an-idea only: "are you in at all?".

    A poll created with candidate times never asks this — a yes on any time is
    the interest signal, and asking separately is a redundant tap."""
    plan = _get_plan_for_member(session, user, plan_id)
    if not plan.asks_interest:
        raise HTTPException(
            status_code=400,
            detail="This poll asks about times directly — just answer the times.",
        )
    repo.cast_interest(session, plan, user, body.yes)
    log.info("[plan %d] %s is %s for the plan", plan.id, user.email,
             "IN" if body.yes else "OUT")
    converge(session, plan, user.timezone)
    plans_changed(plan.group_id)
    if plan.status == "booked":
        events_changed(plan.group_id)
    return _plan_json(session, plan, user, user.timezone)


@router.post("/plans/{plan_id}/time-vote")
def vote_time(
    plan_id: int,
    body: TimeVoteBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Answer ONE candidate time: yes, no, or if_needed.

    Every time is answerable independently and at any moment — there is no
    active round to race against, which is what removed the old 409 ("the host
    moved on"). `round_id` still identifies which time is being answered.
    """
    plan = _get_plan_for_member(session, user, plan_id)
    if body.answer not in VOTE_STATES:
        raise HTTPException(status_code=400,
                            detail=f"answer must be one of {', '.join(VOTE_STATES)}.")
    if plan.asks_interest and not repo.get_interest_votes(session, plan).get(user.email):
        raise HTTPException(
            status_code=403,
            detail="Say you're in for the plan first — times are only asked of people who are.",
        )
    round_ = next((r for r in plan.rounds if r.id == body.round_id), None)
    if round_ is None:
        raise HTTPException(status_code=404,
                            detail="That time isn't one of this poll's candidates.")

    repo.cast_time_vote(session, round_, user, body.answer)
    log.info("[plan %d] %s answered %s to %s", plan.id, user.email,
             body.answer, time_label(round_, user.timezone))
    # The vote that completes a poll is the one that books it: when this was the
    # last outstanding answer and a time meets the minimum, there is nothing left
    # for anyone to decide. See plan_service.converge.
    converge(session, plan, user.timezone)
    plans_changed(plan.group_id)
    if plan.status == "booked":
        events_changed(plan.group_id)
    return _plan_json(session, plan, user, user.timezone)
