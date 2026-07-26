"""The deduped plan-state load returns the same tally/ballot as the separate
fetches it replaces, and the joinedload'd vote reads return correct maps
(repo.get_interest_votes/get_time_votes + plan_service.load_plan_state)."""
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


def _setup(session):
    host, a, b = User(email="host@x.com"), User(email="a@x.com"), User(email="b@x.com")
    session.add_all([host, a, b])
    session.commit()
    group = repo.create_group(session, "Crew", host)
    repo.add_member(session, group, a)
    repo.add_member(session, group, b)
    plan = repo.create_plan(
        session, group, host, title="Coffee",
        slots=[(DAY, DAY.replace(hour=18))], location="Cafe",
    )
    return host, a, b, plan


def test_vote_reads_return_correct_maps(session):
    host, a, b, plan = _setup(session)
    repo.cast_interest(session, plan, a, True)
    repo.cast_interest(session, plan, b, False)
    # host's interest is auto-recorded yes at creation
    assert repo.get_interest_votes(session, plan) == {
        "host@x.com": True, "a@x.com": True, "b@x.com": False,
    }
    active = repo.get_active_round(session, plan)
    repo.cast_time_vote(session, active, a, True)
    assert repo.get_time_votes(session, active) == {"a@x.com": True}


def test_deduped_state_matches_separate_fetches(session):
    host, a, b, plan = _setup(session)
    repo.cast_interest(session, plan, a, True)
    active = repo.get_active_round(session, plan)
    repo.cast_time_vote(session, active, a, True)

    state = plan_service.load_plan_state(session, plan)
    assert plan_service.plan_tally(session, plan, "UTC", state=state) == \
           plan_service.plan_tally(session, plan, "UTC")
    assert plan_service.member_ballot(session, plan, a, state=state).stage == \
           plan_service.member_ballot(session, plan, a).stage
