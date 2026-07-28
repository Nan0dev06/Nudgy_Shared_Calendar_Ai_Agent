"""The pure async-convergence rules (app/tools/plan_deadlines.py): when a plan
nudges, when it closes, and when it is allowed to book itself."""
from datetime import datetime, timedelta, timezone

from app.tools.plan_deadlines import (
    BOOK, EXPIRE, NOTHING, deadline_outcome, everyone_said_yes,
    next_reminder_at, reminder_due,
)

NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
HALF_DAY = timedelta(hours=12)


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


# ----------------------------------------------------------------- deadline

def test_nothing_happens_before_the_deadline():
    assert deadline_outcome(
        now=NOW, deadline=NOW + timedelta(hours=1), status="open",
        auto_book=False, has_active_time=True, time_yes_count=2,
    ) == NOTHING


def test_deadlineless_plans_are_never_touched():
    assert deadline_outcome(
        now=NOW, deadline=None, status="open",
        auto_book=True, has_active_time=True, time_yes_count=2,
    ) == NOTHING


def test_passed_deadline_expires_a_normal_plan():
    assert deadline_outcome(
        now=NOW, deadline=NOW, status="open",
        auto_book=False, has_active_time=True, time_yes_count=3,
    ) == EXPIRE


def test_passed_deadline_books_an_auto_book_plan():
    assert deadline_outcome(
        now=NOW + timedelta(minutes=1), deadline=NOW, status="open",
        auto_book=True, has_active_time=True, time_yes_count=1,
    ) == BOOK


def test_auto_book_with_nobody_available_still_just_expires():
    assert deadline_outcome(
        now=NOW, deadline=NOW, status="open",
        auto_book=True, has_active_time=True, time_yes_count=0,
    ) == EXPIRE


def test_an_already_settled_plan_is_left_alone():
    assert deadline_outcome(
        now=NOW, deadline=NOW - timedelta(days=1), status="scheduled",
        auto_book=True, has_active_time=True, time_yes_count=2,
    ) == NOTHING


# ----------------------------------------------------------------- unanimity

def test_unanimous_when_everyone_answered_and_everyone_said_yes():
    assert everyone_said_yes(
        interested=3, silent_on_interest=0, time_yes=3, time_no=0, time_waiting=0,
    )


def test_not_unanimous_while_someone_has_not_opened_the_app():
    assert not everyone_said_yes(
        interested=2, silent_on_interest=1, time_yes=2, time_no=0, time_waiting=0,
    )


def test_not_unanimous_with_a_single_no_on_the_time():
    assert not everyone_said_yes(
        interested=3, silent_on_interest=0, time_yes=2, time_no=1, time_waiting=0,
    )


def test_not_unanimous_while_a_yes_voter_still_owes_a_time_answer():
    assert not everyone_said_yes(
        interested=3, silent_on_interest=0, time_yes=2, time_no=0, time_waiting=1,
    )


def test_nobody_interested_is_not_unanimity():
    assert not everyone_said_yes(
        interested=0, silent_on_interest=0, time_yes=0, time_no=0, time_waiting=0,
    )
