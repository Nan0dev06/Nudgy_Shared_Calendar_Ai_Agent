"""The pure convergence rules (app/tools/plan_deadlines.py): when a poll nudges,
when it closes, and which candidate time is allowed to book itself.

Rewritten for the 2026-08-01 engine (docs/poll-edit-redesign.md §1.5). The
reminder half is unchanged — it never depended on how times were voted on.
"""
from datetime import datetime, timedelta, timezone

from app.tools.plan_deadlines import (
    BOOK, EXPIRE, NOTHING, choose_winner, deadline_outcome, next_reminder_at,
    ready_to_book_early, reminder_due,
)
from app.tools.plan_rules import TimeResult

NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
HALF_DAY = timedelta(hours=12)


def _time(key, *, yes=0, if_needed=0, no=0, waiting=0, guest_yes=0, guest_if_needed=0):
    """A TimeResult with the given counts — names don't matter to the rules."""
    who = lambda n, tag: [f"{tag}{i}@x.com" for i in range(n)]
    return TimeResult(
        key=key,
        yes=who(yes, "y"), if_needed=who(if_needed, "m"),
        no=who(no, "n"), waiting=who(waiting, "w"),
        guest_yes=who(guest_yes, "gy"), guest_if_needed=who(guest_if_needed, "gm"),
    )


# ----------------------------------------------------------------- reminders

def test_first_nudge_lands_one_interval_after_the_plan_appeared():
    assert next_reminder_at(
        created_at=NOW, deadline=None, last_reminder_at=None, interval=HALF_DAY,
    ) == NOW + HALF_DAY


def test_later_nudges_are_spaced_from_the_previous_one():
    last = NOW + timedelta(hours=20)
    assert next_reminder_at(
        created_at=NOW, deadline=None, last_reminder_at=last, interval=HALF_DAY,
    ) == last + HALF_DAY


def test_short_fused_plan_is_nudged_before_its_deadline_not_after():
    """A 4-hour deadline with a 12-hour interval: the regular slot would land 8
    hours after voting closed, so the one nudge moves to the midpoint."""
    deadline = NOW + timedelta(hours=4)
    due = next_reminder_at(
        created_at=NOW, deadline=deadline, last_reminder_at=None, interval=HALF_DAY,
    )
    assert due == NOW + timedelta(hours=2)
    assert due < deadline


def test_short_fused_plan_gets_only_that_one_nudge():
    deadline = NOW + timedelta(hours=4)
    assert next_reminder_at(
        created_at=NOW, deadline=deadline,
        last_reminder_at=NOW + timedelta(hours=2), interval=HALF_DAY,
    ) is None


def test_nobody_pending_means_no_nudge():
    assert not reminder_due(
        now=NOW + timedelta(days=5), created_at=NOW, deadline=None,
        last_reminder_at=None, interval=HALF_DAY, pending=False,
    )


def test_nudge_fires_once_the_interval_has_passed():
    kw = dict(created_at=NOW, deadline=None, last_reminder_at=None,
              interval=HALF_DAY, pending=True)
    assert not reminder_due(now=NOW + timedelta(hours=11), **kw)
    assert reminder_due(now=NOW + timedelta(hours=13), **kw)


def test_no_nudging_after_voting_has_closed():
    deadline = NOW + timedelta(hours=1)
    assert not reminder_due(
        now=NOW + timedelta(hours=2), created_at=NOW, deadline=deadline,
        last_reminder_at=None, interval=HALF_DAY, pending=True,
    )


# ------------------------------------------------- the default bar: everyone in

def test_default_bar_needs_every_member_not_just_a_majority():
    """expected_count is None -> the rule is "all of them", so 4 of 5 is not
    enough no matter how lopsided."""
    assert choose_winner(
        [_time(1, yes=4, waiting=1)], minimum=None, member_total=5, spotlight=None,
    ) is None


def test_default_bar_books_when_every_member_is_in():
    assert choose_winner(
        [_time(1, yes=5)], minimum=None, member_total=5, spotlight=None,
    ) == 1


def test_guests_cannot_substitute_for_a_member_under_the_default_bar():
    """The reason the default is a RULE and not the number len(members): three
    members plus two guests must not pass for "the whole group agreed"."""
    assert choose_winner(
        [_time(1, yes=3, waiting=2, guest_yes=2)],
        minimum=None, member_total=5, spotlight=None,
    ) is None


def test_if_needed_can_carry_the_default_bar_when_nothing_else_does():
    assert choose_winner(
        [_time(1, yes=3, if_needed=2)], minimum=None, member_total=5, spotlight=None,
    ) == 1


# --------------------------------------------- a typed count: guests do count

def test_typed_count_is_reached_by_members_and_guests_together():
    """Once the creator says "4 is enough", a guest who said yes is one of them."""
    assert choose_winner(
        [_time(1, yes=2, guest_yes=2)], minimum=4, member_total=5, spotlight=None,
    ) == 1


def test_typed_count_below_the_bar_does_not_book():
    assert choose_winner(
        [_time(1, yes=2, guest_yes=1)], minimum=4, member_total=5, spotlight=None,
    ) is None


# ----------------------------------------------------------------- ranking

def test_a_firm_yes_winner_beats_one_that_needs_the_maybes():
    """Tier 1 excludes tier 2 entirely: an "if needed" majority never outranks a
    real one, even when its raw total is higher."""
    firm = _time(1, yes=3)
    maybes = _time(2, yes=1, if_needed=3)
    assert choose_winner([maybes, firm], minimum=3, member_total=5,
                         spotlight=None) == 1


def test_among_qualifiers_the_most_yes_wins():
    assert choose_winner(
        [_time(1, yes=3), _time(2, yes=5)], minimum=3, member_total=5, spotlight=None,
    ) == 2


def test_the_spotlight_breaks_a_tie():
    assert choose_winner(
        [_time(1, yes=3), _time(2, yes=3)],
        minimum=3, member_total=5, spotlight=2, order=[1, 2],
    ) == 2


def test_without_a_spotlight_a_tie_goes_to_the_earliest_time():
    assert choose_winner(
        [_time(2, yes=3), _time(1, yes=3)],
        minimum=3, member_total=5, spotlight=None, order=[1, 2],
    ) == 1


def test_the_spotlight_only_breaks_ties_it_does_not_win_on_its_own():
    """Spelled out because it's the promise made to the host: spotlighting is
    not a thumb on the scale, so a better-supported time still wins."""
    assert choose_winner(
        [_time(1, yes=5), _time(2, yes=3)],
        minimum=3, member_total=5, spotlight=2, order=[1, 2],
    ) == 1


def test_no_candidates_means_no_winner():
    assert choose_winner([], minimum=None, member_total=3, spotlight=None) is None


# --------------------------------------------------------------- early booking

def test_early_booking_waits_for_the_last_outstanding_answer():
    """The guard that stops a lowered bar booking on three quick replies while
    the rest of the group is still asleep."""
    assert not ready_to_book_early(winner=1, pending_answers=2)


def test_early_booking_fires_once_nobody_is_pending():
    assert ready_to_book_early(winner=1, pending_answers=0)


def test_nothing_books_early_without_a_qualifying_time():
    assert not ready_to_book_early(winner=None, pending_answers=0)


# ----------------------------------------------------------------- deadline

def test_nothing_happens_before_the_deadline():
    assert deadline_outcome(
        now=NOW, deadline=NOW + timedelta(hours=1), status="open", winner=1,
    ) == NOTHING


def test_deadlineless_polls_are_never_touched():
    assert deadline_outcome(
        now=NOW, deadline=None, status="open", winner=1,
    ) == NOTHING


def test_passed_deadline_books_the_qualifying_time():
    assert deadline_outcome(
        now=NOW + timedelta(minutes=1), deadline=NOW, status="open", winner=3,
    ) == BOOK


def test_passed_deadline_expires_when_nothing_qualifies():
    """Votes are kept — expiring parks the poll for the host rather than
    throwing the group's answers away."""
    assert deadline_outcome(
        now=NOW, deadline=NOW, status="open", winner=None,
    ) == EXPIRE


def test_an_already_settled_poll_is_left_alone():
    assert deadline_outcome(
        now=NOW, deadline=NOW - timedelta(days=1), status="booked", winner=1,
    ) == NOTHING
