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
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Plan, TimeRound, User
from app.db import repo
from app.tools.plan_rules import Ballot, Tally, ballot_for, tally

log = logging.getLogger("nudgy.agent")


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

def plan_tally(session: Session, plan: Plan, tz_name: str) -> Tally:
    """The host's summary box for the plan's active time."""
    active = repo.get_active_round(session, plan)
    return tally(
        [m.email for m in repo.get_group_members(session, plan.group_id)],
        repo.get_interest_votes(session, plan),
        repo.get_time_votes(session, active),
        active_time_label=time_label(active, tz_name) if active else None,
        times_left=repo.count_queued_rounds(session, plan),
    )


def member_ballot(session: Session, plan: Plan, user: User) -> Ballot:
    """What this member should be answering right now — their step of the cascade."""
    active = repo.get_active_round(session, plan)
    return ballot_for(
        interest=repo.get_interest_votes(session, plan).get(user.email),
        time_vote=repo.get_time_votes(session, active).get(user.email),
        has_active_time=active is not None,
        plan_status=plan.status,
    )


# ----------------------------------------------------------------- host moves

def advance_to_next_time(session: Session, plan: Plan, actor: User, tz_name: str) -> dict:
    """Host: this time doesn't work — put the next candidate to the cohort."""
    if actor.id != plan.created_by:
        return {"error": "Only the host who suggested this plan can change the time."}
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
    from app.tools.booking import book_round_event

    if actor.id != plan.created_by:
        return {"error": "Only the host who suggested this plan can lock in a time."}
    if plan.status != "open":
        return {"error": f"This plan is already {plan.status}."}

    active = repo.get_active_round(session, plan)
    if active is None:
        return {"error": "No time is on the table for this plan."}

    votes = repo.get_time_votes(session, active)
    going = sorted(e for e, v in votes.items() if v)
    if not going:
        return {"error": f"Nobody has said {time_label(active, tz_name)} works for them "
                         "— there is no one to book it for."}

    repo.set_round_status(session, active, "confirmed")
    log.info("[decision] plan %d: host locked in round %d (%s) for %d member(s)",
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
