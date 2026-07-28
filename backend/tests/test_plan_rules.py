"""Tests for the exact plan cascade rules from the spec."""
from app.tools.plan_rules import (
    CLOSED, INTEREST, OUT, TIME, WAITING, ballot_for, tally,
)

MEMBERS = ["a@x.com", "b@x.com", "c@x.com"]


def _ballot(interest=None, time_vote=None, has_active_time=True, plan_status="open"):
    return ballot_for(
        interest=interest, time_vote=time_vote,
        has_active_time=has_active_time, plan_status=plan_status,
    )


# --------------------------------------------------- stage 1 -> stage 2 cascade

def test_unanswered_member_is_asked_the_plan_first_not_the_time():
    assert _ballot().stage == INTEREST


def test_yes_to_the_plan_immediately_opens_the_time_question():
    # THE cascade: one yes opens that member's second question on the spot,
    # with no reference to what anyone else did.
    assert _ballot(interest=True).stage == TIME


def test_no_to_the_plan_puts_them_out_and_never_asks_a_time():
    b = _ballot(interest=False)
    assert b.stage == OUT
    assert "won't be asked about times" in b.note


def test_interested_but_no_time_on_the_table_waits():
    assert _ballot(interest=True, has_active_time=False).stage == WAITING


def test_yes_to_the_time_waits_on_the_host_never_self_books():
    b = _ballot(interest=True, time_vote=True)
    assert b.stage == WAITING
    assert "host" in b.note


def test_no_to_the_time_keeps_them_in_the_plan():
    # "he's sort of out" — out of THIS time, still in for the plan, because the
    # next time gets asked of the whole cohort.
    b = _ballot(interest=True, time_vote=False)
    assert b.stage == WAITING
    assert "still in for the plan" in b.note


def test_settled_plan_has_nothing_to_answer():
    assert _ballot(interest=True, plan_status="scheduled").stage == CLOSED
    assert _ballot(interest=True, plan_status="dead").stage == CLOSED


def test_an_expired_plan_says_the_deadline_passed_not_that_it_was_settled():
    b = _ballot(interest=None, plan_status="expired")
    assert b.stage == CLOSED
    assert "deadline" in b.note


# --------------------------------------------------- the host's tally

def test_time_columns_only_ever_cover_the_interested_cohort():
    t = tally(
        MEMBERS,
        {"a@x.com": True, "b@x.com": False},   # c hasn't answered the plan
        {"a@x.com": True},
        active_time_label="Mon 20 Jul 17:00", times_left=1,
    )
    assert t.interested == ["a@x.com"]
    assert t.not_interested == ["b@x.com"]
    assert t.no_interest_answer == ["c@x.com"]
    assert t.time_yes == ["a@x.com"]
    # b said no to the plan, so b is never counted as silent on a time
    assert t.time_no == [] and t.time_waiting == []


def test_declined_time_is_reported_not_decided():
    # 1 yes, 2 no on the time — no rule kills it; the host is handed the choice.
    t = tally(
        MEMBERS,
        {e: True for e in MEMBERS},
        {"a@x.com": True, "b@x.com": False, "c@x.com": False},
        active_time_label="Mon 20 Jul 17:00", times_left=1,
    )
    assert t.time_yes == ["a@x.com"]
    assert t.time_no == ["b@x.com", "c@x.com"]
    assert "Your call" in t.host_note
    assert "still in for the plan" in t.host_note


def test_majority_yes_is_still_the_hosts_call_not_an_approval():
    t = tally(
        MEMBERS,
        {e: True for e in MEMBERS},
        {"a@x.com": True, "b@x.com": True, "c@x.com": False},
        active_time_label="Mon 20 Jul 17:00", times_left=1,
    )
    assert "Your call" in t.host_note
    # no language that implies the system decided anything
    assert "approved" not in t.host_note.lower()


def test_silence_on_a_time_is_reported_as_silence():
    t = tally(
        MEMBERS,
        {e: True for e in MEMBERS},
        {"a@x.com": True},
        active_time_label="Mon 20 Jul 17:00", times_left=1,
    )
    assert set(t.time_waiting) == {"b@x.com", "c@x.com"}
    assert "Not answered yet" in t.host_note


def test_last_candidate_time_tells_the_host_there_is_no_fallback():
    t = tally(
        MEMBERS,
        {e: True for e in MEMBERS},
        {"a@x.com": True},
        active_time_label="Mon 20 Jul 19:00", times_left=0,
    )
    assert "last candidate time" in t.host_note


def test_nobody_can_make_the_time_offers_waiting_or_moving_on():
    t = tally(
        MEMBERS,
        {e: True for e in MEMBERS},
        {e: False for e in MEMBERS},
        active_time_label="Mon 20 Jul 17:00", times_left=1,
    )
    assert t.time_yes == []
    assert "wait" in t.host_note


def test_no_interest_yet_points_the_host_at_stage_one():
    t = tally(MEMBERS, {}, {}, active_time_label="Mon 20 Jul 17:00", times_left=1)
    assert t.interested == []
    assert "Nobody has said they're in yet" in t.host_note


def test_everyone_out_of_the_plan_means_nothing_to_schedule():
    t = tally(
        MEMBERS, {e: False for e in MEMBERS}, {},
        active_time_label="Mon 20 Jul 17:00", times_left=1,
    )
    assert "Nothing to schedule" in t.host_note


def test_out_of_times_is_reported_to_the_host():
    t = tally(
        MEMBERS, {e: True for e in MEMBERS}, {},
        active_time_label=None, times_left=0,
    )
    assert "used up" in t.host_note
