"""The pure poll engine (app/tools/plan_rules.py): what each person is being
asked, and what the host's box says.

Rewritten for the 2026-08-01 engine (docs/poll-edit-redesign.md §1). The old
suite tested a two-stage cascade over a QUEUE of times — one "active" time, the
host walking it. Both are gone: every candidate is votable at once, and the
interest stage survives only in Float-an-idea.
"""
from app.tools.plan_rules import (
    CLOSED, FLOAT, IF_NEEDED, INTEREST, NO, OUT, PICK_A_TIME, QUICK, TIME,
    WAITING, YES, ballot_for, eligible_voters, mode_of, tally,
)

MEMBERS = ["amir@x.com", "sam@x.com", "lea@x.com"]


# ----------------------------------------------------------------- modes

def test_a_plan_with_no_times_floats_an_idea():
    assert mode_of(asks_interest=True, times_total=0) == FLOAT


def test_one_time_is_a_quick_plan():
    assert mode_of(asks_interest=False, times_total=1) == QUICK


def test_several_times_is_pick_a_time():
    assert mode_of(asks_interest=False, times_total=3) == PICK_A_TIME


def test_a_float_plan_stays_a_float_plan_once_it_gains_times():
    """asks_interest is stored, not re-derived. Otherwise adding a time would
    silently change the question a plan had already asked people."""
    assert mode_of(asks_interest=True, times_total=3) == FLOAT


# ----------------------------------------------------------------- the ballot

def _ballot(**kw):
    base = dict(asks_interest=False, interest=None, times_total=2,
                times_answered=0, plan_status="open")
    return ballot_for(**{**base, **kw})


def test_a_timed_poll_asks_about_times_immediately():
    """The headline saving: no interest tap before the real question."""
    b = _ballot()
    assert b.stage == TIME
    assert b.unanswered == 2


def test_a_float_poll_asks_interest_first():
    assert _ballot(asks_interest=True, times_total=0).stage == INTEREST


def test_saying_no_to_a_float_poll_takes_you_out_of_it():
    assert _ballot(asks_interest=True, interest=False, times_total=2).stage == OUT


def test_being_in_with_no_times_yet_is_waiting():
    assert _ballot(asks_interest=True, interest=True, times_total=0).stage == WAITING


def test_partly_answered_still_counts_the_ones_left():
    b = _ballot(times_total=3, times_answered=1)
    assert b.stage == TIME
    assert b.unanswered == 2


def test_answering_every_time_leaves_nothing_to_do():
    assert _ballot(times_total=3, times_answered=3).stage == WAITING


def test_a_booked_poll_is_closed():
    assert _ballot(plan_status="booked").stage == CLOSED


def test_an_expired_poll_says_the_deadline_passed_not_that_it_was_decided():
    """Wording matters here: "settled" would read as a decision nobody made."""
    b = _ballot(plan_status="expired")
    assert b.stage == CLOSED
    assert "deadline" in b.note.lower()


# ------------------------------------------------------------- who is asked

def test_without_an_interest_stage_everyone_is_asked_about_times():
    assert eligible_voters(MEMBERS, {}, asks_interest=False) == MEMBERS


def test_with_an_interest_stage_only_the_people_who_said_yes_are_asked():
    votes = {"amir@x.com": True, "sam@x.com": False}
    assert eligible_voters(MEMBERS, votes, asks_interest=True) == ["amir@x.com"]


# ----------------------------------------------------------------- the tally

def _tally(votes_by_time, *, guests=(), guest_votes=None, asks_interest=False,
           interest=None, explicit_minimum=None, spotlight=None):
    merged = {k: dict(v) for k, v in votes_by_time.items()}
    for key, gv in (guest_votes or {}).items():
        merged.setdefault(key, {}).update(gv)
    keys = list(merged)
    return tally(
        MEMBERS, list(guests), interest or {}, merged,
        asks_interest=asks_interest,
        time_keys=keys,
        minimum=explicit_minimum or len(MEMBERS),
        explicit_minimum=explicit_minimum,
        member_total=len(MEMBERS),
        labels={k: f"time {k}" for k in keys},
        spotlight=spotlight,
    )


def test_every_candidate_time_gets_its_own_standing():
    t = _tally({
        1: {"amir@x.com": YES, "sam@x.com": NO},
        2: {"amir@x.com": IF_NEEDED},
    })
    first, second = t.times
    assert first.yes == ["amir@x.com"]
    assert first.no == ["sam@x.com"]
    assert first.waiting == ["lea@x.com"]
    assert second.if_needed == ["amir@x.com"]
    # silence on one time says nothing about the others
    assert second.waiting == ["sam@x.com", "lea@x.com"]


def test_a_no_on_one_time_leaves_the_person_in_for_the_others():
    t = _tally({1: {"sam@x.com": NO}, 2: {"sam@x.com": YES}})
    assert t.times[0].no == ["sam@x.com"]
    assert t.times[1].yes == ["sam@x.com"]


def test_guests_are_counted_separately_from_members():
    t = _tally(
        {1: {"amir@x.com": YES}},
        guests=["Rita (guest)"],
        guest_votes={1: {"Rita (guest)": YES}},
    )
    r = t.times[0]
    assert r.yes == ["amir@x.com"]
    assert r.guest_yes == ["Rita (guest)"]
    assert r.member_yes == 1        # the members-only number
    assert r.total_yes == 2         # the ranking number


def test_people_who_said_no_to_a_float_plan_are_not_silent_on_its_times():
    """They were never asked, so counting them as waiting would make the host
    chase somebody who already answered."""
    t = _tally(
        {1: {}},
        asks_interest=True,
        interest={"amir@x.com": True, "sam@x.com": False, "lea@x.com": True},
    )
    assert t.not_interested == ["sam@x.com"]
    assert sorted(t.times[0].waiting) == ["amir@x.com", "lea@x.com"]


def test_attendees_are_yes_and_if_needed_never_the_silent():
    t = _tally({1: {"amir@x.com": YES, "sam@x.com": IF_NEEDED}})
    assert sorted(t.times[0].attendees) == ["amir@x.com", "sam@x.com"]
    assert "lea@x.com" not in t.times[0].attendees


# ---------------------------------------------------------- qualifying rules

def test_the_default_bar_needs_every_member():
    t = _tally({1: {"amir@x.com": YES, "sam@x.com": YES}})
    assert not t.times[0].qualifies(minimum=None, member_total=3)


def test_a_typed_count_can_be_reached_with_a_guest():
    t = _tally(
        {1: {"amir@x.com": YES}},
        guests=["Rita (guest)"], guest_votes={1: {"Rita (guest)": YES}},
        explicit_minimum=2,
    )
    assert t.times[0].qualifies(minimum=2, member_total=3)


def test_a_guest_cannot_help_reach_the_default_bar():
    t = _tally(
        {1: {"amir@x.com": YES, "sam@x.com": YES}},
        guests=["Rita (guest)"], guest_votes={1: {"Rita (guest)": YES}},
    )
    assert not t.times[0].qualifies(minimum=None, member_total=3)


# --------------------------------------------------------------- host wording

def test_the_host_note_names_the_whole_group_under_the_default_bar():
    """"3 of 3" would misdescribe the rule — it needs those three, not any three."""
    t = _tally({1: {m: YES for m in MEMBERS}})
    assert "all 3 of you" in t.host_note


def test_the_host_note_uses_the_number_when_the_creator_typed_one():
    t = _tally({1: {"amir@x.com": YES, "sam@x.com": YES}}, explicit_minimum=2)
    assert "2 people" in t.host_note


def test_the_host_is_told_when_a_time_only_clears_on_if_needed():
    t = _tally({1: {"amir@x.com": YES, "sam@x.com": IF_NEEDED, "lea@x.com": IF_NEEDED}})
    assert "if needed" in t.host_note


def test_the_host_is_reminded_they_can_lock_in_regardless():
    """The minimum governs what happens WITHOUT a human; it never blocks them."""
    t = _tally({1: {"amir@x.com": YES}})
    assert "lock in" in t.host_note.lower()


def test_the_host_is_told_when_their_spotlight_is_not_the_leader():
    t = _tally({1: {m: YES for m in MEMBERS}, 2: {"amir@x.com": YES}}, spotlight=2)
    assert "isn't the current leader" in t.host_note


def test_a_poll_with_no_times_says_so_rather_than_ranking_nothing():
    assert "no times" in _tally({}).host_note.lower()
