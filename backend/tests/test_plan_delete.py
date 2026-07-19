"""Deleting a poll removes it and everything hanging off it — no orphan rounds
or votes left behind. The delete-orphan cascades live on the ORM (not the DB),
so this must go through session.delete(plan), which repo.delete_plan does.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, InterestVote, TimeRound, TimeVote, User
from app.db import repo

DAY = datetime(2026, 7, 20, tzinfo=timezone.utc)
FIVE_PM = (DAY.replace(hour=14), DAY.replace(hour=15))
SEVEN_PM = (DAY.replace(hour=16), DAY.replace(hour=17))


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_delete_plan_takes_its_rounds_and_votes_with_it(session):
    host = User(email="sam@x.com")
    other = User(email="mo@x.com")
    session.add_all([host, other])
    session.commit()
    group = repo.create_group(session, "The usual", host)
    plan = repo.create_plan(session, group, host, "Coffee",
                            slots=[FIVE_PM, SEVEN_PM], location="Blend Cafe")
    repo.cast_interest(session, plan, other, True)
    repo.cast_time_vote(session, repo.get_active_round(session, plan), other, True)
    pid = plan.id

    repo.delete_plan(session, plan)

    assert repo.get_plan(session, pid) is None
    assert session.scalars(select(TimeRound).where(TimeRound.plan_id == pid)).all() == []
    assert session.scalars(select(InterestVote).where(InterestVote.plan_id == pid)).all() == []
    assert session.scalars(select(TimeVote)).all() == []   # cascaded through the round


def test_delete_one_plan_leaves_the_others_untouched(session):
    host = User(email="sam@x.com")
    session.add(host)
    session.commit()
    group = repo.create_group(session, "The usual", host)
    keep = repo.create_plan(session, group, host, "Keep me", slots=[FIVE_PM], location="A")
    drop = repo.create_plan(session, group, host, "Drop me", slots=[FIVE_PM], location="B")

    repo.delete_plan(session, drop)

    remaining = [p.id for p in repo.get_group_plans(session, group.id)]
    assert remaining == [keep.id]
