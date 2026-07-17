"""A host move must never act on a plan the model merely guessed.

Regression test for an observed failure: asked to "lock it in", the model
invented `plan_id: 123`. Nothing in the message carried an id, so it made one
up. Had 123 existed, the wrong hangout would have been booked onto real
calendars — so resolution is tested here rather than trusted to the prompt.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.tools import ToolContext, _resolve_plan
from app.db.models import Base, Plan, User
from app.db import repo

DAY = datetime(2026, 7, 20, tzinfo=timezone.utc)


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    host = User(email="sam@x.com")
    session.add(host)
    session.commit()
    group = repo.create_group(session, "The usual", host)
    return ToolContext(session=session, user=host, group=group,
                       now_utc=DAY, tz_name="Asia/Beirut")


def _plan(ctx, title="Coffee at the usual"):
    return repo.create_plan(ctx.session, ctx.group, ctx.user, title,
                            slots=[(DAY.replace(hour=14), DAY.replace(hour=15))])


def test_omitted_id_resolves_to_the_only_open_plan(ctx):
    plan = _plan(ctx)
    assert _resolve_plan(ctx, {}).id == plan.id


def test_guessed_id_that_does_not_exist_is_refused(ctx):
    _plan(ctx)
    out = _resolve_plan(ctx, {"plan_id": 123})
    assert "error" in out
    assert "never guess" in out["error"].lower()
    # steer the model back instead of failing bare, or it wanders to other tools
    assert out["open_plans"][0]["title"] == "Coffee at the usual"


def test_id_from_another_group_is_refused(ctx):
    _plan(ctx)
    other_host = User(email="zed@x.com")
    ctx.session.add(other_host)
    ctx.session.commit()
    other_group = repo.create_group(ctx.session, "Other", other_host)
    theirs = repo.create_plan(ctx.session, other_group, other_host, "Not yours",
                              slots=[(DAY.replace(hour=14), DAY.replace(hour=15))])
    assert "error" in _resolve_plan(ctx, {"plan_id": theirs.id})


def test_ambiguous_plans_refuse_rather_than_pick_one(ctx):
    _plan(ctx, "Coffee")
    _plan(ctx, "Dinner")
    out = _resolve_plan(ctx, {})
    assert "error" in out
    assert len(out["open_plans"]) == 2   # let the model ask, don't gamble


def test_settled_plans_are_not_candidates(ctx):
    done = _plan(ctx, "Already booked")
    repo.set_plan_status(ctx.session, done, "scheduled")
    live = _plan(ctx, "Still open")
    assert _resolve_plan(ctx, {}).id == live.id


def test_no_open_plan_says_so(ctx):
    assert "error" in _resolve_plan(ctx, {})
