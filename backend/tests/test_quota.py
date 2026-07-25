"""Per-user daily agent-turn quota: turns are counted per local day, reset
across days, and respect the tier's allowance (core/entitlements.py + core/quota.py).
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core import quota
from app.core.entitlements import FREE, PRO, entitlements_for
from app.db.models import AgentUsage, Base, User


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _user(session, tier="free"):
    u = User(email="sam@x.com", tier=tier)
    session.add(u)
    session.commit()
    return u


def test_free_tier_allows_then_blocks_at_limit(session):
    user = _user(session)
    limit = entitlements_for(FREE).agent_turns_per_day

    for i in range(limit):
        status = quota.check_quota(session, user)
        assert status.allowed
        assert status.used == i
        assert status.limit == limit
        quota.record_turn(session, user)

    blocked = quota.check_quota(session, user)
    assert not blocked.allowed
    assert blocked.used == limit
    assert blocked.remaining == 0


def test_record_turn_upserts_one_row_per_day(session):
    user = _user(session)
    quota.record_turn(session, user)
    after = quota.record_turn(session, user)

    rows = session.scalars(select(AgentUsage).where(AgentUsage.user_id == user.id)).all()
    assert len(rows) == 1          # one row per (user, day), not one per turn
    assert rows[0].turns == 2
    assert after.used == 2


def test_usage_is_scoped_to_the_local_day(session):
    user = _user(session)
    # a big count on an OLD day must not touch today's allowance
    session.add(AgentUsage(user_id=user.id, day=date(2000, 1, 1), turns=999))
    session.commit()

    status = quota.check_quota(session, user)
    assert status.used == 0
    assert status.allowed


def test_tier_changes_the_limit(session):
    user = _user(session, tier=PRO)
    status = quota.check_quota(session, user)
    assert status.limit == entitlements_for(PRO).agent_turns_per_day
    assert status.limit > entitlements_for(FREE).agent_turns_per_day


def test_unknown_tier_falls_back_to_free(session):
    user = _user(session, tier="platinum-unknown")
    status = quota.check_quota(session, user)
    assert status.limit == entitlements_for(FREE).agent_turns_per_day
