"""Glue between plan storage and the poll rules.

plan_rules.py and plan_deadlines.py are pure and only report. THIS file is the
single place a poll's state actually transitions, and there are now three moves:

  set_spotlight()   — host: "we're leaning toward this one". Changes NOTHING
      else: no vote, no status, no other time. Reversible, and repeatable.
  confirm_time()    — host: "lock this one in". Books the time the host names,
      for whoever said yes or if-needed to it, whether or not the minimum was
      met. The host is never gated by the minimum; it exists to constrain what
      happens WITHOUT a human.
  converge()        — the automatic path. Books the best qualifying time when
      nobody is left to answer, or at the deadline.

A vote never transitions anything by itself; it only moves that voter forward.
What makes the automatic path safe is that booking only ever invites people who
said yes or if-needed — nothing can reach the calendar of somebody who declined
or never answered. See docs/poll-edit-redesign.md §1.5.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Plan, TimeRound, User
from app.db import repo
from app.tools.plan_deadlines import (
    BOOK, EXPIRE, choose_winner, deadline_outcome, ready_to_book_early,
)
from app.tools.plan_rules import (
    IF_NEEDED, YES, Ballot, Tally, ballot_for, eligible_voters, tally,
)

log = logging.getLogger("nudgy.agent")

# Statuses a host can still act on. `expired` means voting closed, NOT that the
# poll is over: the host can lock in whatever came in before the deadline.
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
    """A poll's live vote state, fetched once and reused.

    GET /plans builds the member ballot AND (for the host) the tally per poll;
    without this each did its own round-trips. Load once, pass to both.

    Members and guests are kept in separate lists all the way through. They were
    merged in the old engine because every consumer wanted one list of people who
    owe an answer — but the minimum is counted on members only, so the two have
    to stay distinguishable. `participants` is still available for the places
    that genuinely mean "everyone".
    """
    rounds: list[TimeRound] = field(default_factory=list)
    member_emails: list[str] = field(default_factory=list)
    guest_labels: list[str] = field(default_factory=list)
    interest_votes: dict[str, bool] = field(default_factory=dict)
    votes_by_time: dict[int, dict[str, str]] = field(default_factory=dict)
    guest_votes_by_time: dict[int, dict[str, str]] = field(default_factory=dict)
    minimum: int = 0                 # the bar as a number, for display
    explicit_minimum: int | None = None   # None = the default all-members rule
    spotlight: int | None = None

    @property
    def participants(self) -> list[str]:
        return self.member_emails + self.guest_labels

    @property
    def time_keys(self) -> list[int]:
        return [r.id for r in self.rounds]

    def merged_votes(self) -> dict[int, dict[str, str]]:
        """Members and guests in one map per time — what the tally reads."""
        return {
            key: {**self.votes_by_time.get(key, {}),
                  **self.guest_votes_by_time.get(key, {})}
            for key in self.time_keys
        }


def requires_all_members(plan: Plan) -> bool:
    """True when this poll uses the DEFAULT rule — every account-holding member
    must be able to make a time before it books itself — rather than a count the
    creator typed. Guests cannot satisfy this; see plan_rules.TimeResult."""
    return not plan.expected_count


def minimum_for(session: Session, plan: Plan) -> int:
    """The bar as a NUMBER, for display and for the agent's reporting.

    Under the default rule that number is the group size, but it is not the same
    thing: `requires_all_members` means those specific people, not any N. Use
    this to show a bar, never to decide one — qualifying goes through
    TimeResult.qualifies.
    """
    if plan.expected_count:
        return plan.expected_count
    return len(repo.get_group_members(session, plan.group_id))


def load_plan_state(session: Session, plan: Plan) -> PlanState:
    members = [m.email for m in repo.get_group_members(session, plan.group_id)]
    # guests count from the moment they JOIN, not from their first vote — a guest
    # who opened the link and went quiet is someone the poll is waiting on, the
    # same as a silent member.
    guests = [g.label for g in repo.get_plan_guests(session, plan)]
    return PlanState(
        rounds=list(plan.rounds),
        member_emails=members,
        guest_labels=guests,
        interest_votes={
            **repo.get_interest_votes(session, plan),
            **repo.get_guest_interest_votes(session, plan),
        },
        votes_by_time=repo.get_votes_by_time(session, plan),
        guest_votes_by_time=repo.get_guest_votes_by_time(session, plan),
        minimum=minimum_for(session, plan),
        explicit_minimum=plan.expected_count,
        spotlight=plan.spotlight_round_id,
    )


def plan_tally(session: Session, plan: Plan, tz_name: str,
               *, state: PlanState | None = None) -> Tally:
    """The host's summary box, across every candidate time at once."""
    st = state or load_plan_state(session, plan)
    return tally(
        st.member_emails,
        st.guest_labels,
        st.interest_votes,
        st.merged_votes(),
        asks_interest=plan.asks_interest,
        time_keys=st.time_keys,
        minimum=st.minimum,
        explicit_minimum=st.explicit_minimum,
        member_total=len(st.member_emails),
        labels={r.id: time_label(r, tz_name) for r in st.rounds},
        spotlight=st.spotlight,
    )


def _answered_count(state: PlanState, who: str) -> int:
    votes = state.merged_votes()
    return sum(1 for key in state.time_keys if who in votes.get(key, {}))


def member_ballot(session: Session, plan: Plan, user: User,
                  *, state: PlanState | None = None) -> Ballot:
    """What this member should be answering right now."""
    st = state or load_plan_state(session, plan)
    return ballot_for(
        asks_interest=plan.asks_interest,
        interest=st.interest_votes.get(user.email),
        times_total=len(st.rounds),
        times_answered=_answered_count(st, user.email),
        plan_status=plan.status,
    )


def guest_ballot(plan: Plan, guest, *, state: PlanState) -> Ballot:
    """The share-link visitor's step of the same questions members answer."""
    return ballot_for(
        asks_interest=plan.asks_interest,
        interest=state.interest_votes.get(guest.label),
        times_total=len(state.rounds),
        times_answered=_answered_count(state, guest.label),
        plan_status=plan.status,
    )


def pending_voters(state: PlanState, plan: Plan) -> list[str]:
    """MEMBERS who still owe this poll an answer — who a reminder goes to.

    Two ways to be pending: never answered the interest question (Float only), or
    being eligible and not having answered every candidate time. Somebody who
    said no to the plan is done.

    Members only, even though guests can be pending too: a nudge needs somewhere
    to land, and a guest gave us at most an optional address for the invite.
    Chasing them is whoever shared the link's job.
    """
    votes = state.merged_votes()
    waiting = []
    for email in state.member_emails:
        if plan.asks_interest:
            interest = state.interest_votes.get(email)
            if interest is None:
                waiting.append(email)
                continue
            if not interest:
                continue
        if any(email not in votes.get(key, {}) for key in state.time_keys):
            waiting.append(email)
    return waiting


def pending_answers(state: PlanState, plan: Plan) -> int:
    """How many (participant, time) answers are still outstanding, guests
    included. Zero means no further vote can arrive, which is what lets a poll
    book before its deadline."""
    votes = state.merged_votes()
    voters = eligible_voters(state.participants, state.interest_votes,
                             asks_interest=plan.asks_interest)
    missing = 0
    if plan.asks_interest:
        missing += sum(1 for p in state.participants
                       if state.interest_votes.get(p) is None)
    for key in state.time_keys:
        answered = votes.get(key, {})
        missing += sum(1 for p in voters if p not in answered)
    return missing


def winning_round(session: Session, plan: Plan, tz_name: str,
                  *, state: PlanState | None = None) -> TimeRound | None:
    """Which time would book right now, or None if nothing qualifies."""
    st = state or load_plan_state(session, plan)
    t = plan_tally(session, plan, tz_name, state=st)
    key = choose_winner(t.times, minimum=st.explicit_minimum,
                        member_total=len(st.member_emails),
                        spotlight=st.spotlight, order=st.time_keys)
    if key is None:
        return None
    return next((r for r in st.rounds if r.id == key), None)


# ----------------------------------------------------------------- host moves

def set_spotlight(session: Session, plan: Plan, actor: User,
                  round_id: int | None, tz_name: str) -> dict:
    """Host: point at the time the group is leaning toward (or clear it)."""
    if actor.id != plan.created_by:
        return {"error": "Only the host who suggested this plan can spotlight a time."}
    if plan.status not in HOST_DECIDABLE:
        return {"error": f"This plan is already {plan.status}."}
    if round_id is not None:
        target = next((r for r in plan.rounds if r.id == round_id), None)
        if target is None:
            return {"error": "That time isn't one of this plan's candidates."}
    repo.set_plan_spotlight(session, plan, round_id)
    label = (time_label(next(r for r in plan.rounds if r.id == round_id), tz_name)
             if round_id is not None else None)
    log.info("[plan %d] host spotlighted %s", plan.id, label or "nothing")
    return {
        "action": "spotlight",
        "time": label,
        # Say the quiet part: people who already voted often assume a change like
        # this wiped their answer, because the old engine genuinely did.
        "note": (f"{label} is highlighted as the one you're leaning toward. "
                 "Every vote already cast still counts — this only breaks a tie."
                 if label else "Spotlight cleared."),
    }


def confirm_time(session: Session, plan: Plan, actor: User,
                 round_id: int, tz_name: str) -> dict:
    """Host: lock in the time they name — NOT necessarily the spotlit one.

    Deliberately not gated by the minimum. The minimum governs what may happen
    without a human; a host choosing a time IS the human, and blocking them from
    booking the four people who can make it would be the app overruling the
    person it exists to serve.
    """
    if actor.id != plan.created_by:
        return {"error": "Only the host who suggested this plan can lock in a time."}
    if plan.status not in HOST_DECIDABLE:
        return {"error": f"This plan is already {plan.status}."}
    target = next((r for r in plan.rounds if r.id == round_id), None)
    if target is None:
        return {"error": "That time isn't one of this plan's candidates."}
    return _confirm(session, plan, target, tz_name)


def _confirm(session: Session, plan: Plan, round_: TimeRound, tz_name: str) -> dict:
    """Book one time for everyone who can make it.

    Status/permission checks are the CALLER's job — the three callers authorize
    differently (host lock-in, early convergence, deadline) but the booking
    itself is one path, so a change to how a poll reaches the calendar can't
    diverge between them.
    """
    from app.tools.booking import book_round_event

    votes = repo.get_time_votes(session, round_)
    going = sorted(e for e, a in votes.items() if a in (YES, IF_NEEDED))
    # Guests count as attending, but their tally key is "Name (guest)", not an
    # address — only the ones who left an email can be invited. Keeping the two
    # lists apart is what stops a display label being handed to Google as a
    # recipient.
    guest_votes = repo.get_guest_time_votes(session, round_)
    guests_going = [g for g in repo.get_plan_guests(session, plan)
                    if guest_votes.get(g.label) in (YES, IF_NEEDED)]
    going += [g.label for g in guests_going]
    if not going:
        return {"error": f"Nobody said {time_label(round_, tz_name)} works for them "
                         "— there is no one to book it for."}

    invite = sorted(e for e, a in votes.items() if a in (YES, IF_NEEDED))
    invite += [g.email for g in guests_going if g.email]
    if not invite:
        # Only email-less guests can make it. The event still belongs on the
        # host's calendar — they're the organizer and they locked it in — so
        # book it there and simply send no invites.
        invite = [session.get(User, plan.created_by).email]

    log.info("[decision] plan %d: confirming time %d (%s) for %d attendee(s), %d invited",
             plan.id, round_.ordinal, time_label(round_, tz_name), len(going), len(invite))

    # Google can refuse or throw (expired token, network). The poll must stay
    # exactly as it was so the host can retry — nothing is marked until the
    # calendar has actually accepted it.
    organizer = session.get(User, plan.created_by)
    try:
        result = book_round_event(session, plan, round_, organizer, invite)
    except Exception as exc:
        log.exception("[decision] plan %d booking raised", plan.id)
        return {"action": "book_failed", "error": f"{type(exc).__name__}: {exc}"}
    if not result.get("booked"):
        log.warning("[decision] plan %d booking failed: %s", plan.id, result.get("error"))
        return {"action": "book_failed", "error": result.get("error")}

    repo.set_plan_status(session, plan, "booked")
    # The booking becomes a real group event (docs/poll-edit-redesign.md §2).
    # `going` is written straight from the vote — these people already said this
    # time works, and asking them to RSVP to what they just voted for would be
    # the same question twice. This is what finally gives a booked poll an edit
    # path; until now it was a Plan with nothing editable attached.
    event = repo.create_event_from_booking(
        session, plan, round_, going,
        gcal_event_id=result.get("event_id"), gcal_link=result.get("event_link"),
    )
    return {
        "action": "booked",
        "time": time_label(round_, tz_name),
        "round_id": round_.id,
        "event_id_local": event.id,
        # who is coming (members by email, guests by label) vs. who we can
        # actually write to — never assume the first list is mailable
        "attendees": going,
        "invited": invite,
        "event_link": result["event_link"],
        "event_id": result["event_id"],
    }


# ------------------------------------------------------------- automatic paths

def converge(session: Session, plan: Plan, tz_name: str) -> dict | None:
    """Book a poll early, the moment nobody is left to answer. None = not yet.

    Called after every vote. The guard is `ready_to_book_early`: a time meets the
    minimum AND no participant owes any answer. At that point no further vote can
    arrive, so waiting for the deadline would achieve nothing — and with the
    default minimum (the whole group) this fires exactly when everyone is in.

    A booking failure is swallowed on purpose: the vote that triggered this was
    still cast and must still return 200. The poll stays open, so the next vote —
    or the host — retries.
    """
    if plan.status != "open":
        return None
    st = load_plan_state(session, plan)
    if not st.rounds:
        return None
    winner = winning_round(session, plan, tz_name, state=st)
    if not ready_to_book_early(
        winner=winner.id if winner else None,
        pending_answers=pending_answers(st, plan),
    ):
        return None

    log.info("[plan %d] everyone has answered and %s qualifies -> booking",
             plan.id, time_label(winner, tz_name))
    result = _confirm(session, plan, winner, tz_name)
    if result.get("action") != "booked":
        log.warning("[plan %d] early booking did not go through: %s",
                    plan.id, result.get("error"))
        return None
    return result


def resolve_deadline(session: Session, plan: Plan, now: datetime,
                     tz_name: str) -> dict | None:
    """Apply a passed vote deadline. None = the deadline hasn't passed.

    Returns {"action": "booked"|"expired", ...}. The best qualifying time books
    for the people who can make it; with nothing qualifying the poll parks as
    `expired` with its votes intact, so the host still has a decision to make
    rather than a deleted poll.
    """
    winner = winning_round(session, plan, tz_name)
    outcome = deadline_outcome(
        now=now,
        deadline=plan.deadline,
        status=plan.status,
        winner=winner.id if winner else None,
    )
    if outcome == BOOK:
        result = _confirm(session, plan, winner, tz_name)
        if result.get("action") == "booked":
            log.info("[plan %d] deadline passed -> booked %s", plan.id, result["time"])
            return result
        # Couldn't book (calendar refused). Don't leave it hanging past its own
        # deadline — close voting and hand it back to the host.
        log.warning("[plan %d] deadline booking failed (%s) -> expiring instead",
                    plan.id, result.get("error"))
        outcome = EXPIRE
    if outcome == EXPIRE:
        repo.set_plan_status(session, plan, "expired")
        log.info("[plan %d] vote deadline passed -> expired", plan.id)
        return {"action": "expired"}
    return None
