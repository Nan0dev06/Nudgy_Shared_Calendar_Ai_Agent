"""Glue between plan storage and the cascade rules.

The rules in plan_rules.py are pure and only report. THIS file is the single
place a plan's state actually transitions, and there are exactly two host
moves that can do it:

  advance_to_next_time() — "5 PM doesn't work, try 7 PM". The active round is
      skipped and the next queued one goes live to the WHOLE interested cohort
      (not just the people who said no to 5 PM — 7 PM is a new question, and
      someone free at 5 might be busy at 7). No times left -> the plan is dead.
  confirm_active_time()  — "lock in 5 PM". Books ONLY the people who said yes
      to that specific time.

Votes never transition anything by themselves: no majority, no unanimity, no
auto-booking on silence. A member's vote only moves that member forward
through their own cascade. The host is the decider, which is also what keeps a
human in the loop before anything is written to a calendar.

Two async paths (Phase 2) can also transition a plan, and both are bounded so
they never violate that rule — see tools/plan_deadlines.py for the reasoning:
  maybe_auto_book()   — opt-in, and only on true unanimity, i.e. only when no
      human had anything left to decide.
  resolve_deadline()  — the vote deadline passing. Expires the plan (voting
      closes, the host can still act) or, for an auto-book plan, books it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Plan, TimeRound, User
from app.db import repo
from app.tools.plan_deadlines import (
    BOOK, EXPIRE, deadline_outcome, everyone_said_yes,
)
from app.tools.plan_rules import Ballot, Tally, ballot_for, tally

log = logging.getLogger("nudgy.agent")

# Statuses a host can still act on. `expired` means voting closed, NOT that the
# plan is over: the host can lock in whatever came in before the deadline.
HOST_DECIDABLE = ("open", "expired")


# ----------------------------------------------------------------- labels

def time_label(round_: TimeRound, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    return (f"{round_.start.astimezone(tz):%a %d %b %H:%M}"
            f"-{round_.end.astimezone(tz):%H:%M}")


def day_label(plan: Plan, tz_name: str) -> str:
    """The plan's day, read in the viewer's zone (never stored — always derived)."""
    if not plan.rounds:
        return "an unspecified day"
    tz = ZoneInfo(tz_name)
    return f"{plan.rounds[0].start.astimezone(tz):%A %d %B}"


# ----------------------------------------------------------------- reading

@dataclass
class PlanState:
    """A plan's live vote state, fetched once and reused. GET /plans builds both
    the member ballot AND (for the host) the tally per plan; without this each
    did its own round-trips for the active round + both vote sets — doubled up,
    every 5s, for every open plan. Load once, pass to both."""
    active: TimeRound | None
    member_emails: list[str]
    interest_votes: dict[str, bool]
    time_votes: dict[str, bool]
    times_left: int


def load_plan_state(session: Session, plan: Plan) -> PlanState:
    active = repo.get_active_round(session, plan)
    return PlanState(
        active=active,
        member_emails=[m.email for m in repo.get_group_members(session, plan.group_id)],
        interest_votes=repo.get_interest_votes(session, plan),
        time_votes=repo.get_time_votes(session, active),
        times_left=repo.count_queued_rounds(session, plan),
    )


def plan_tally(session: Session, plan: Plan, tz_name: str, *, state: PlanState | None = None) -> Tally:
    """The host's summary box for the plan's active time."""
    st = state or load_plan_state(session, plan)
    return tally(
        st.member_emails,
        st.interest_votes,
        st.time_votes,
        active_time_label=time_label(st.active, tz_name) if st.active else None,
        times_left=st.times_left,
    )


def member_ballot(session: Session, plan: Plan, user: User, *, state: PlanState | None = None) -> Ballot:
    """What this member should be answering right now — their step of the cascade."""
    st = state or load_plan_state(session, plan)
    return ballot_for(
        interest=st.interest_votes.get(user.email),
        time_vote=st.time_votes.get(user.email),
        has_active_time=st.active is not None,
        plan_status=plan.status,
    )


def pending_voters(state: PlanState) -> list[str]:
    """Members who still owe this plan an answer — who a reminder goes to.

    Two ways to be pending: never answered the plan at all, or said you're in
    and haven't answered the time on the table. Someone who said no to the plan
    is done; someone who said no to the time has answered it.
    """
    waiting = []
    for email in state.member_emails:
        interest = state.interest_votes.get(email)
        if interest is None:
            waiting.append(email)
        elif interest and state.active is not None and email not in state.time_votes:
            waiting.append(email)
    return waiting


# ----------------------------------------------------------------- host moves

def advance_to_next_time(session: Session, plan: Plan, actor: User, tz_name: str) -> dict:
    """Host: this time doesn't work — put the next candidate to the cohort."""
    if actor.id != plan.created_by:
        return {"error": "Only the host who suggested this plan can change the time."}
    if plan.status == "expired":
        # Putting a new time up asks people to vote, and voting is shut. Moving
        # the deadline is the host's way of saying "keep going".
        return {"error": "Voting on this plan has closed. Extend the deadline to "
                         "put another time to the group."}
    if plan.status != "open":
        return {"error": f"This plan is already {plan.status}."}

    active = repo.get_active_round(session, plan)
    if active is not None:
        repo.set_round_status(session, active, "skipped")

    nxt = repo.get_next_queued_round(session, plan)
    if nxt is None:
        repo.set_plan_status(session, plan, "dead")
        log.info("[plan %d] no candidate times left -> dead", plan.id)
        return {
            "action": "out_of_times",
            "note": ("Every candidate time has been tried. The plan is closed — "
                     "search for fresh times and suggest a new plan."),
        }

    repo.set_round_status(session, nxt, "active")
    cohort = [e for e, v in repo.get_interest_votes(session, plan).items() if v]
    log.info("[plan %d] host skipped round %s -> round %d (%s) live to %d interested",
             plan.id, active.ordinal if active else "-", nxt.ordinal,
             time_label(nxt, tz_name), len(cohort))
    return {
        "action": "next_time",
        "time": time_label(nxt, tz_name),
        "asked": cohort,
        "times_left": repo.count_queued_rounds(session, plan),
        "note": (f"{time_label(nxt, tz_name)} is now the question, and everyone who "
                 "said they're in for the plan has been asked it — including the "
                 "people who were fine with the previous time."),
    }


def confirm_active_time(session: Session, plan: Plan, actor: User, tz_name: str) -> dict:
    """Host: lock this time in — book the members who said yes to THIS time."""
    if actor.id != plan.created_by:
        return {"error": "Only the host who suggested this plan can lock in a time."}
    if plan.status not in HOST_DECIDABLE:
        return {"error": f"This plan is already {plan.status}."}
    return _confirm(session, plan, tz_name)


def _confirm(session: Session, plan: Plan, tz_name: str) -> dict:
    """Book the active time for its yes-voters. Status/permission checks are the
    CALLER's job — the three callers each authorize differently (host action,
    unanimous auto-book, deadline resolution) but the booking itself is one
    path, so a change to how a plan reaches the calendar can't diverge."""
    from app.tools.booking import book_round_event

    active = repo.get_active_round(session, plan)
    if active is None:
        return {"error": "No time is on the table for this plan."}

    votes = repo.get_time_votes(session, active)
    going = sorted(e for e, v in votes.items() if v)
    if not going:
        return {"error": f"Nobody has said {time_label(active, tz_name)} works for them "
                         "— there is no one to book it for."}

    repo.set_round_status(session, active, "confirmed")
    log.info("[decision] plan %d: confirming round %d (%s) for %d member(s)",
             plan.id, active.ordinal, time_label(active, tz_name), len(going))

    # Google can refuse or throw (expired token, network). Either way the time
    # must go back to "active" — a round left "confirmed" with no event would
    # be a dead end: not bookable again, and not skippable to the next time.
    organizer = session.get(User, plan.created_by)
    try:
        result = book_round_event(session, plan, active, organizer, going)
    except Exception as exc:
        repo.set_round_status(session, active, "active")
        log.exception("[decision] plan %d booking raised", plan.id)
        return {"action": "book_failed", "error": f"{type(exc).__name__}: {exc}"}
    if not result.get("booked"):
        repo.set_round_status(session, active, "active")
        log.warning("[decision] plan %d booking failed: %s", plan.id, result.get("error"))
        return {"action": "book_failed", "error": result.get("error")}

    repo.set_plan_status(session, plan, "scheduled")
    return {
        "action": "booked",
        "time": time_label(active, tz_name),
        "attendees": going,
        "event_link": result["event_link"],
        "event_id": result["event_id"],
    }


# ------------------------------------------------------------- async transitions

def maybe_auto_book(session: Session, plan: Plan, tz_name: str) -> dict | None:
    """Book an opted-in plan the moment it becomes unanimous. None = not yet.

    Called after every vote. The guard is `everyone_said_yes`: every member has
    answered the plan and every interested member said yes to the time on the
    table. At that point a host lock-in would be a formality, so a plan that
    asked for it skips the wait — which is the whole point for a group whose
    host is asleep.

    A booking failure is swallowed on purpose: the vote that triggered this was
    still cast and must still return 200. The plan stays open with its time
    active, so the next vote (or the host) retries.
    """
    if not plan.auto_book or plan.status != "open":
        return None
    st = load_plan_state(session, plan)
    if st.active is None:
        return None
    t = tally(st.member_emails, st.interest_votes, st.time_votes,
              active_time_label=None, times_left=st.times_left)
    if not everyone_said_yes(
        interested=len(t.interested),
        silent_on_interest=len(t.no_interest_answer),
        time_yes=len(t.time_yes),
        time_no=len(t.time_no),
        time_waiting=len(t.time_waiting),
    ):
        return None

    log.info("[plan %d] unanimous and auto-book is on -> booking %s",
             plan.id, time_label(st.active, tz_name))
    result = _confirm(session, plan, tz_name)
    if result.get("action") != "booked":
        log.warning("[plan %d] auto-book did not go through: %s",
                    plan.id, result.get("error"))
        return None
    return result


def resolve_deadline(session: Session, plan: Plan, now: datetime, tz_name: str) -> dict | None:
    """Apply a passed vote deadline. None = the deadline hasn't passed.

    Returns {"action": "booked"|"expired", ...}. Booking only happens for
    auto-book plans; everything else parks as `expired` with its votes intact,
    so the host still has a decision to make rather than a deleted plan.
    """
    active = repo.get_active_round(session, plan)
    yes_count = sum(1 for v in repo.get_time_votes(session, active).values() if v)
    outcome = deadline_outcome(
        now=now,
        deadline=plan.deadline,
        status=plan.status,
        auto_book=plan.auto_book,
        has_active_time=active is not None,
        time_yes_count=yes_count,
    )
    if outcome == BOOK:
        result = _confirm(session, plan, tz_name)
        if result.get("action") == "booked":
            log.info("[plan %d] deadline passed -> auto-booked %s", plan.id, result["time"])
            return result
        # Couldn't book (calendar refused). Don't leave it hanging past its own
        # deadline — close voting and hand it back to the host.
        log.warning("[plan %d] deadline auto-book failed (%s) -> expiring instead",
                    plan.id, result.get("error"))
        outcome = EXPIRE
    if outcome == EXPIRE:
        repo.set_plan_status(session, plan, "expired")
        log.info("[plan %d] vote deadline passed -> expired", plan.id)
        return {"action": "expired"}
    return None
