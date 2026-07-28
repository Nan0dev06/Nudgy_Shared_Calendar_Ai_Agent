"""The plan cascade — implemented exactly once, as pure functions.

Spec (decide-once, implement exactly):

STAGE 1 — INTEREST. Every group member is asked about the plan itself (place +
day), NOT about a time. "Sam suggested the usual coffee shop today, Monday 20
July. Coming?"
  no  -> they are OUT of the plan entirely. Never asked about any time.
  yes -> they join the interested cohort, and their yes IMMEDIATELY opens the
         time question for them. This is per-person: nobody waits for the rest
         of the group to answer stage 1 first.

STAGE 2 — TIME. Only the interested cohort is asked, and only about the ONE
time that is currently active (5 PM).
  no  -> out of THAT time only. They stay in the cohort, because if the host
         moves on to 7 PM they get asked again.

THE HOST DECIDES — there is no automatic threshold here. No majority rule, no
unanimity, no auto-booking, and no auto-rejection on a single no. These
functions only REPORT: ballot_for() says what one member should be answering
right now, tally() builds the summary box the host reads. The host then either
confirms the active time (booking only the people who said yes to it) or moves
to the next time, which re-asks the WHOLE interested cohort — including the
people who already said yes to 5 PM, since 7 PM is a different question.
State transitions live in plan_service.py; nothing here touches the DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# What a given member should be looking at right now.
INTEREST = "interest"   # asked stage 1: are you in at all?
TIME = "time"           # asked stage 2: does the active time work?
WAITING = "waiting"     # answered everything currently on the table
OUT = "out"             # said no to the plan itself
CLOSED = "closed"       # the plan is scheduled or dead; nothing to answer


@dataclass
class Ballot:
    stage: str            # INTEREST | TIME | WAITING | OUT | CLOSED
    note: str             # human-readable, shown to that member


@dataclass
class Tally:
    """The host's summary box: who's in, who's out, and who's in/out on the time."""
    interested: list[str] = field(default_factory=list)       # said yes to the plan
    not_interested: list[str] = field(default_factory=list)   # said no to the plan
    no_interest_answer: list[str] = field(default_factory=list)  # silent on the plan
    time_yes: list[str] = field(default_factory=list)         # cohort: this time works
    time_no: list[str] = field(default_factory=list)          # cohort: this time doesn't
    time_waiting: list[str] = field(default_factory=list)     # cohort: silent on this time
    host_note: str = ""   # what the host is being asked to decide


def ballot_for(
    *,
    interest: bool | None,        # None = hasn't answered stage 1 yet
    time_vote: bool | None,       # their vote on the ACTIVE round; None = not answered
    has_active_time: bool,        # is a time round currently active?
    plan_status: str,             # "open" | "scheduled" | "dead"
) -> Ballot:
    """What this one member should be answering right now.

    This function IS the cascade: the instant `interest` flips to True the
    returned stage becomes TIME, which is what "the first poll opens the
    second poll" means. It is evaluated per member, so one person's yes opens
    their own time question regardless of what anyone else has done.
    """
    if plan_status == "expired":
        # not settled — the vote deadline simply ran out. Worth its own wording:
        # "settled" would read as a decision nobody actually made.
        return Ballot(CLOSED, "The deadline passed — voting is closed on this one.")
    if plan_status != "open":
        return Ballot(CLOSED, "This plan is settled — nothing to vote on.")
    if interest is None:
        return Ballot(INTEREST, "Are you in for this plan?")
    if interest is False:
        return Ballot(OUT, "You said you're not in for this one — you won't be asked about times.")

    # interested from here on
    if not has_active_time:
        return Ballot(WAITING, "You're in. Waiting for the host to put a time up.")
    if time_vote is None:
        return Ballot(TIME, "Does this time work for you?")
    if time_vote:
        return Ballot(WAITING, "You said this time works — waiting on the host to lock it in.")
    return Ballot(
        WAITING,
        "This time doesn't work for you. You're still in for the plan — if the "
        "host tries another time, you'll be asked again.",
    )


def tally(
    member_emails: list[str],
    interest_votes: dict[str, bool],   # email -> yes/no, only members who answered
    time_votes: dict[str, bool],       # email -> yes/no on the ACTIVE round
    *,
    active_time_label: str | None,     # e.g. "Mon 20 Jul 17:00" — None if no active round
    times_left: int,                   # queued times after the active one
) -> Tally:
    """Build the host's decision box for the active time.

    Time columns only ever cover the interested cohort — someone who said no to
    the plan is never counted as silent on a time they were never asked.
    """
    t = Tally()
    for e in member_emails:
        v = interest_votes.get(e)
        if v is None:
            t.no_interest_answer.append(e)
        elif v:
            t.interested.append(e)
        else:
            t.not_interested.append(e)

    for e in t.interested:
        v = time_votes.get(e)
        if v is None:
            t.time_waiting.append(e)
        elif v:
            t.time_yes.append(e)
        else:
            t.time_no.append(e)

    t.host_note = _host_note(t, active_time_label, times_left)
    return t


def _host_note(t: Tally, active_time_label: str | None, times_left: int) -> str:
    if not t.interested:
        if t.no_interest_answer:
            return ("Nobody has said they're in yet. Waiting on "
                    f"{', '.join(t.no_interest_answer)} to answer the plan itself.")
        return "Everyone said no to the plan. Nothing to schedule — suggest a different day or place."

    if active_time_label is None:
        return (f"{len(t.interested)} interested, but no time is on the table. "
                "All the candidate times have been used up — find new ones.")

    parts = [f"For {active_time_label}: "
             f"{len(t.time_yes)} of {len(t.interested)} interested can make it."]
    if t.time_yes:
        parts.append(f"IN: {', '.join(t.time_yes)}.")
    if t.time_no:
        parts.append(f"OUT for this time (still in for the plan): {', '.join(t.time_no)}.")
    if t.time_waiting:
        parts.append(f"Not answered yet: {', '.join(t.time_waiting)}.")
    if t.not_interested:
        parts.append(f"Not in for the plan at all: {', '.join(t.not_interested)}.")
    if t.no_interest_answer:
        parts.append(f"Haven't answered the plan: {', '.join(t.no_interest_answer)}.")

    # The decision is the host's — spell out both branches, recommend neither.
    if t.time_yes and times_left:
        parts.append(f"Your call: lock in {active_time_label} for the people who said "
                     f"yes, or move to the next time and ask everyone who's in again "
                     f"({times_left} time{'s' if times_left > 1 else ''} left).")
    elif t.time_yes:
        parts.append(f"Your call: lock in {active_time_label} for the people who said "
                     "yes, or drop it — this was the last candidate time.")
    elif times_left:
        parts.append("Nobody can make this time yet. You can wait, or move to the next "
                     f"time and ask everyone who's in ({times_left} left).")
    else:
        parts.append("Nobody can make this time and it was the last candidate. "
                     "You'd need new times.")
    return " ".join(parts)
