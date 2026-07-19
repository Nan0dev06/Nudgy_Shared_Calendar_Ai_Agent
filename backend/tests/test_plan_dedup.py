"""The group must not end up with two open plans for the same place and time.

Both the agent's create_plan tool and the UI's POST /plans go through
repo.find_duplicate_open_plan first. The real bug this guards: the model
re-proposes the same hangout across turns with a slightly different title
("hang out" vs "Hang out at Blend Cafe"), so a title-only guard misses it.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.tools import ToolContext, _create_plan
from app.db.models import Base, User
from app.db import repo

DAY = datetime(2026, 7, 20, tzinfo=timezone.utc)
FIVE_PM = (DAY.replace(hour=14), DAY.replace(hour=15))   # 17:00-18:00 Beirut
SEVEN_PM = (DAY.replace(hour=16), DAY.replace(hour=17))


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


def test_same_place_and_time_is_a_duplicate_even_with_a_different_title(ctx):
    repo.create_plan(ctx.session, ctx.group, ctx.user, "Hang out at Blend Cafe",
                     slots=[FIVE_PM], location="Blend Cafe")
    dup = repo.find_duplicate_open_plan(ctx.session, ctx.group, "Blend Cafe", [FIVE_PM])
    assert dup is not None and dup.title == "Hang out at Blend Cafe"


def test_location_match_is_case_and_space_insensitive(ctx):
    repo.create_plan(ctx.session, ctx.group, ctx.user, "Dinner", slots=[FIVE_PM],
                     location="Cafe Badaro")
    assert repo.find_duplicate_open_plan(ctx.session, ctx.group, "  cafe badaro ", [FIVE_PM])


def test_different_time_is_not_a_duplicate(ctx):
    repo.create_plan(ctx.session, ctx.group, ctx.user, "Hang out", slots=[FIVE_PM],
                     location="Blend Cafe")
    assert repo.find_duplicate_open_plan(ctx.session, ctx.group, "Blend Cafe", [SEVEN_PM]) is None


def test_different_place_is_not_a_duplicate(ctx):
    repo.create_plan(ctx.session, ctx.group, ctx.user, "Hang out", slots=[FIVE_PM],
                     location="Blend Cafe")
    assert repo.find_duplicate_open_plan(ctx.session, ctx.group, "Cafe Badaro", [FIVE_PM]) is None


def test_settled_plans_do_not_block_a_new_one(ctx):
    done = repo.create_plan(ctx.session, ctx.group, ctx.user, "Hang out", slots=[FIVE_PM],
                            location="Blend Cafe")
    repo.set_plan_status(ctx.session, done, "scheduled")
    assert repo.find_duplicate_open_plan(ctx.session, ctx.group, "Blend Cafe", [FIVE_PM]) is None


def test_timeless_who_is_in_checks_are_never_deduped(ctx):
    assert repo.find_duplicate_open_plan(ctx.session, ctx.group, "Blend Cafe", []) is None


def test_agent_create_plan_refuses_to_make_a_second_card(ctx):
    first = _create_plan(ctx, {
        "title": "Hang out at Blend Cafe", "location": "Blend Cafe",
        "times": [{"start_iso": FIVE_PM[0].isoformat(), "end_iso": FIVE_PM[1].isoformat()}],
    })
    assert "plan_id" in first and not first.get("duplicate")

    again = _create_plan(ctx, {
        "title": "hang out", "location": "Blend Cafe",   # casual re-ask, same place+time
        "times": [{"start_iso": FIVE_PM[0].isoformat(), "end_iso": FIVE_PM[1].isoformat()}],
    })
    assert again.get("duplicate") is True
    assert again["plan_id"] == first["plan_id"]          # points back at the original
    assert len(repo.get_group_plans(ctx.session, ctx.group.id)) == 1   # no second row
