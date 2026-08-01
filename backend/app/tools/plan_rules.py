"""The poll engine — implemented exactly once, as pure functions.

Redesigned 2026-08-01 (docs/poll-edit-redesign.md §1). The previous engine walked
candidate times in a QUEUE: one time was "active", the host advanced to the next,
and advancing re-asked the whole cohort because 7 PM was treated as a different
question from 5 PM. With an async group that cost one full response cycle — often
a day — per candidate time.

Now every candidate time is votable at once, and the host's only positional move
is the SPOTLIGHT, which resets nothing.

MODES (composer-level, not engine-level — one engine underneath, they differ only
in how many questions get asked):

  QUICK        one candidate time      -> a single yes/no
  PICK_A_TIME  2..8 candidate times    -> time votes only
  FLOAT        no times yet            -> interest first, times when they arrive

Interest exists ONLY in Float-an-idea. Everywhere else a yes on any time IS the
interest signal, so asking separately is a redundant tap. Whether a plan asks it
is fixed when the plan is created (`Plan.asks_interest`) and never re-derived, so
a Float plan that later gains times keeps the answers it already collected.

VOTE STATES are three: yes / no / if needed. "If needed" is Doodle's if-need-be —
*I can make this work, I'd rather not* — which distinguishes impossible from
inconvenient. It counts toward the minimum only when yes alone cannot reach it.

THE MINIMUM decides what may book WITHOUT a human. It is counted on MEMBERS only
(guests are shown separately), and it never constrains the host: a host lock-in
books whatever time they choose for whoever said yes, minimum or not. See
plan_deadlines.py for convergence itself; nothing here touches the DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Vote states on one candidate time.
YES = "yes"
NO = "no"
IF_NEEDED = "if_needed"
VOTE_STATES = (YES, NO, IF_NEEDED)

# Poll modes. Derived, never stored — `asks_interest` plus the number of times
# is enough, and a stored mode could contradict the plan it describes.
QUICK = "quick"
PICK_A_TIME = "pick_a_time"
FLOAT = "float"

# What a given member should be looking at right now.
INTEREST = "interest"   # Float only: are you in at all?
TIME = "time"           # vote on the candidate times you haven't answered
WAITING = "waiting"     # answered everything currently on the table
OUT = "out"             # said no to the plan itself (Float only)
CLOSED = "closed"       # booked or expired; nothing to answer


def mode_of(*, asks_interest: bool, times_total: int) -> str:
    if asks_interest:
        return FLOAT
    return QUICK if times_total <= 1 else PICK_A_TIME


@dataclass
class Ballot:
    stage: str            # INTEREST | TIME | WAITING | OUT | CLOSED
    note: str             # human-readable, shown to that member
    unanswered: int = 0   # candidate times this person still owes an answer on


@dataclass
class TimeResult:
    """One candidate time's standing. Members and guests are kept apart all the
    way through: the minimum is a statement about the GROUP, but a guest's yes is
    still a person who is coming, so both numbers reach the UI."""
    key: int                                              # the round's id
    yes: list[str] = field(default_factory=list)          # members
    if_needed: list[str] = field(default_factory=list)    # members
    no: list[str] = field(default_factory=list)           # members
    waiting: list[str] = field(default_factory=list)      # members, silent
    guest_yes: list[str] = field(default_factory=list)
    guest_if_needed: list[str] = field(default_factory=list)

    @property
    def member_yes(self) -> int:
        return len(self.yes)

    @property
    def member_committed(self) -> int:
        """Yes plus if-needed — everyone who COULD make this time."""
        return len(self.yes) + len(self.if_needed)

    @property
    def total_yes(self) -> int:
        """Members and guests who said plain yes — the ranking number."""
        return len(self.yes) + len(self.guest_yes)

    @property
    def attendees(self) -> list[str]:
        """Who gets booked if this time wins: yes and if-needed, both kinds.
        `no` and silence never land on anyone's calendar."""
        return self.yes + self.if_needed + self.guest_yes + self.guest_if_needed


@dataclass
class Tally:
    """The host's summary box."""
    interested: list[str] = field(default_factory=list)
    not_interested: list[str] = field(default_factory=list)
    no_interest_answer: list[str] = field(default_factory=list)
    times: list[TimeResult] = field(default_factory=list)
    guests_total: int = 0
    minimum: int = 0
    host_note: str = ""


def eligible_voters(
    participants: list[str], interest_votes: dict[str, bool], *, asks_interest: bool
) -> list[str]:
    """Who is being asked about times at all.

    Without an interest stage that is everybody. With one it is the people who
    said yes — somebody who said no to the plan is never asked a time, and is
    never counted as silent on a question they were never put.
    """
    if not asks_interest:
        return list(participants)
    return [p for p in participants if interest_votes.get(p)]


def ballot_for(
    *,
    asks_interest: bool,
    interest: bool | None,        # None = hasn't answered; ignored unless asks_interest
    times_total: int,
    times_answered: int,
    plan_status: str,             # "open" | "booked" | "expired"
) -> Ballot:
    """What this one participant should be answering right now.

    Evaluated per person, so one member's progress never waits on anyone else's.
    """
    if plan_status == "expired":
        # Not settled — the deadline simply ran out. Worth its own wording:
        # "settled" would read as a decision nobody actually made.
        return Ballot(CLOSED, "The deadline passed — voting is closed on this one.")
    if plan_status != "open":
        return Ballot(CLOSED, "This plan is settled — nothing to vote on.")

    if asks_interest:
        if interest is None:
            return Ballot(INTEREST, "Are you in for this plan?")
        if interest is False:
            return Ballot(OUT, "You said you're not in for this one — you won't be "
                               "asked about times.")

    if times_total == 0:
        return Ballot(WAITING, "You're in. Waiting for a time to be put up.")

    left = times_total - times_answered
    if left > 0:
        return Ballot(
            TIME,
            "Which of these times work for you?" if times_total > 1
            else "Does this time work for you?",
            unanswered=left,
        )
    return Ballot(
        WAITING,
        "You've answered every time on the table — waiting on the rest of the group.",
    )


def tally(
    members: list[str],
    guests: list[str],
    interest_votes: dict[str, bool],
    votes_by_time: dict[int, dict[str, str]],   # round id -> participant -> state
    *,
    asks_interest: bool,
    time_keys: list[int],                       # every candidate time, in display order
    minimum: int,
    labels: dict[int, str],                     # round id -> human label
    spotlight: int | None,
) -> Tally:
    """Build the host's decision box across ALL candidate times at once."""
    t = Tally(minimum=minimum, guests_total=len(guests))

    if asks_interest:
        for e in members + guests:
            v = interest_votes.get(e)
            if v is None:
                t.no_interest_answer.append(e)
            elif v:
                t.interested.append(e)
            else:
                t.not_interested.append(e)
    else:
        # No interest stage: everyone is a candidate voter, and "interested" is
        # simply nobody-has-opted-out. Reported empty rather than faked.
        t.no_interest_answer = []

    member_voters = eligible_voters(members, interest_votes, asks_interest=asks_interest)
    guest_voters = eligible_voters(guests, interest_votes, asks_interest=asks_interest)

    for key in time_keys:
        votes = votes_by_time.get(key, {})
        r = TimeResult(key=key)
        for e in member_voters:
            state = votes.get(e)
            if state == YES:
                r.yes.append(e)
            elif state == IF_NEEDED:
                r.if_needed.append(e)
            elif state == NO:
                r.no.append(e)
            else:
                r.waiting.append(e)
        for g in guest_voters:
            state = votes.get(g)
            if state == YES:
                r.guest_yes.append(g)
            elif state == IF_NEEDED:
                r.guest_if_needed.append(g)
        t.times.append(r)

    t.host_note = _host_note(t, labels, spotlight)
    return t


def _host_note(t: Tally, labels: dict[int, str], spotlight: int | None) -> str:
    if not t.times:
        if t.interested:
            return (f"{len(t.interested)} in so far, but no times are up yet. "
                    "Add some and everyone who's in gets asked.")
        return "No times are up yet. Add some, or wait to see who's interested."

    parts = []
    ranked = sorted(t.times, key=lambda r: (-r.total_yes, r.key))
    best = ranked[0]
    label = labels.get(best.key, "the best time")

    if best.member_yes >= t.minimum:
        parts.append(f"{label} has {best.member_yes} of the {t.minimum} needed — "
                     "it can book itself.")
    elif best.member_committed >= t.minimum:
        parts.append(f"{label} reaches {t.minimum} only by counting "
                     f"\"if needed\" ({best.member_yes} firm yes, "
                     f"{len(best.if_needed)} if needed).")
    else:
        parts.append(f"Best so far is {label} with {best.member_yes} yes — "
                     f"{t.minimum} are needed to book without you.")

    if best.guest_yes or best.guest_if_needed:
        parts.append(f"Plus {len(best.guest_yes) + len(best.guest_if_needed)} guest(s).")

    still = [e for e in best.waiting]
    if still:
        parts.append(f"Haven't answered: {', '.join(still)}.")
    if t.not_interested:
        parts.append(f"Not in for the plan: {', '.join(t.not_interested)}.")

    if spotlight is not None and spotlight != best.key:
        parts.append(f"You're spotlighting {labels.get(spotlight, 'another time')}, "
                     "which isn't the current leader — it wins a tie, not a count.")

    # The decision stays the host's: spell out the option, recommend nothing.
    parts.append("You can lock in any time yourself — it books for whoever said "
                 "yes, whether or not the minimum is met.")
    return " ".join(parts)
