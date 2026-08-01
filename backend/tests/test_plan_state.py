"""The deduped poll-state load returns the same tally/ballot as the separate
fetches it replaces, and the joinedload'd vote reads return correct maps
(repo.get_interest_votes / get_votes_by_time + plan_service.load_plan_state)."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, User
from app.db import repo
from app.tools import plan_service

DAY = datetime(2026, 7, 20, 17, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _setup(session, slots=None):
    host, a, b = User(email="host@x.com"), User(email="a@x.com"), User(email="b@x.com")
    session.add_all([host, a, b])
    session.commit()
    group = repo.create_group(session, "Crew", host)
    repo.add_member(session, group, a)
    repo.add_member(session, group, b)
    plan = repo.create_plan(
        session, group, host, title="Coffee",
        slots=slots if slots is not None else [(DAY, DAY.replace(hour=18))],
        location="Cafe",
    )
    return host, a, b, plan


def test_vote_reads_return_correct_maps(session):
    host, a, b, plan = _setup(session)
    repo.cast_time_vote(session, plan.rounds[0], a, "yes")
    repo.cast_time_vote(session, plan.rounds[0], b, "if_needed")
    assert repo.get_time_votes(session, plan.rounds[0]) == {
        "a@x.com": "yes", "b@x.com": "if_needed",
    }


def test_votes_for_every_time_come_back_in_one_map(session):
    """Times are answered in parallel, so the page needs them all at once —
    fetching per round would be a query per candidate on every refresh."""
    host, a, b, plan = _setup(
        session, slots=[(DAY, DAY.replace(hour=18)),
                        (DAY.replace(hour=19), DAY.replace(hour=20))],
    )
    first, second = plan.rounds
    repo.cast_time_vote(session, first, a, "yes")
    repo.cast_time_vote(session, second, a, "no")

    by_time = repo.get_votes_by_time(session, plan)
    assert by_time[first.id] == {"a@x.com": "yes"}
    assert by_time[second.id] == {"a@x.com": "no"}


def test_a_timed_poll_records_no_interest_vote_for_its_host(session):
    """A poll created WITH times never asks interest, so recording the host as
    "in" would invent an answer to a question nobody was asked."""
    host, a, b, plan = _setup(session)
    assert plan.asks_interest is False
    assert repo.get_interest_votes(session, plan) == {}


def test_a_float_poll_records_its_host_as_in(session):
    host, a, b, plan = _setup(session, slots=[])
    assert plan.asks_interest is True
    assert repo.get_interest_votes(session, plan) == {"host@x.com": True}


def test_deduped_state_matches_separate_fetches(session):
    host, a, b, plan = _setup(session)
    repo.cast_time_vote(session, plan.rounds[0], a, "yes")

    state = plan_service.load_plan_state(session, plan)
    assert plan_service.plan_tally(session, plan, "UTC", state=state) == \
           plan_service.plan_tally(session, plan, "UTC")
    assert plan_service.member_ballot(session, plan, a, state=state).stage == \
           plan_service.member_ballot(session, plan, a).stage


def test_pending_voters_are_the_members_who_still_owe_an_answer(session):
    host, a, b, plan = _setup(session)
    repo.cast_time_vote(session, plan.rounds[0], a, "yes")

    state = plan_service.load_plan_state(session, plan)
    assert plan_service.pending_voters(state, plan) == ["host@x.com", "b@x.com"]


def test_answering_one_time_of_several_leaves_you_pending(session):
    host, a, b, plan = _setup(
        session, slots=[(DAY, DAY.replace(hour=18)),
                        (DAY.replace(hour=19), DAY.replace(hour=20))],
    )
    repo.cast_time_vote(session, plan.rounds[0], a, "yes")
    state = plan_service.load_plan_state(session, plan)
    assert "a@x.com" in plan_service.pending_voters(state, plan)


def test_someone_out_of_a_float_plan_is_not_chased_about_times(session):
    host, a, b, plan = _setup(session, slots=[])
    repo.append_rounds(session, plan, [(DAY, DAY.replace(hour=18))])
    repo.cast_interest(session, plan, a, False)

    state = plan_service.load_plan_state(session, plan)
    assert "a@x.com" not in plan_service.pending_voters(state, plan)


def test_the_default_bar_is_the_group_and_is_not_a_typed_number(session):
    host, a, b, plan = _setup(session)
    assert plan_service.requires_all_members(plan) is True
    assert plan_service.minimum_for(session, plan) == 3   # for display only


def test_a_typed_minimum_stops_it_being_the_all_members_rule(session):
    host, a, b, plan = _setup(session)
    repo.set_plan_minimum(session, plan, 2)
    assert plan_service.requires_all_members(plan) is False
    assert plan_service.minimum_for(session, plan) == 2
