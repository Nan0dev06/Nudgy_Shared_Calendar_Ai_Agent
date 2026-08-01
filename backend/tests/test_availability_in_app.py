"""In-app events feed availability (docs/poll-edit-redesign.md §4).

Before this, `fetch_busy_for_group` read ONLY external calendar freebusy: events
created in Nudgy blocked nothing, RSVPs affected nothing, and a member with no
connected calendar was dropped from the intersection entirely — while
"usable with no external calendar" is a [LOCKED] v1 promise.

Covers: whose time an event occupies (owner / creator / RSVP status), that tasks
never block, that events count across groups (one user = one availability), that
the two sources union without double-counting, and that a member known only
through Nudgy takes part in the intersection instead of being ignored.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent import availability
from app.db import repo
from app.db.models import (
    Base, CalendarAccount, EventRsvp, Group, GroupEvent, Membership, User,
)

DAY = datetime(2026, 3, 9, tzinfo=timezone.utc)   # a Monday


@pytest.fixture
def Session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _hour(h: int, minutes: int = 60) -> tuple[datetime, datetime]:
    start = DAY.replace(hour=h)
    return start, start + timedelta(minutes=minutes)


def _group(session, *emails: str) -> tuple[Group, list[User]]:
    users = [User(email=e) for e in emails]
    session.add_all(users)
    session.commit()
    group = Group(name="crew", invite_code=f"c{users[0].id}", created_by=users[0].id)
    session.add(group)
    session.commit()
    session.add_all([Membership(user_id=u.id, group_id=group.id) for u in users])
    session.commit()
    return group, users


def _event(session, group, creator, hour, *, personal=False, kind="event", minutes=60):
    start, end = _hour(hour, minutes)
    e = GroupEvent(
        group_id=group.id, created_by=creator.id, kind=kind, title="thing",
        start_utc=start, end_utc=end if kind == "event" else start,
        personal=personal,
    )
    session.add(e)
    session.commit()
    return e


def _busy(session, users, hours=24):
    return repo.get_busy_events_for_users(
        session, [u.id for u in users], DAY, DAY + timedelta(hours=hours)
    )


# ----------------------------------------------------------- whose time it is

def test_personal_event_blocks_only_its_owner(Session):
    with Session() as s:
        g, (amir, sam) = _group(s, "amir@x.com", "sam@x.com")
        _event(s, g, amir, 14, personal=True)

        busy = _busy(s, [amir, sam])
        assert busy[amir.id] == [_hour(14)]
        # a personal event is nobody else's business AND nobody else's busy time
        assert busy[sam.id] == []


def test_shared_event_blocks_creator_and_rsvp_going_only(Session):
    """The consent rule: a shared event does not own your time until you say
    you're coming. Silence is not attendance."""
    with Session() as s:
        g, (amir, sam, lea, ziad) = _group(
            s, "amir@x.com", "sam@x.com", "lea@x.com", "ziad@x.com"
        )
        e = _event(s, g, amir, 18)
        s.add_all([
            EventRsvp(event_id=e.id, user_id=sam.id, status="going"),
            EventRsvp(event_id=e.id, user_id=lea.id, status="cant"),
            # ziad never answered
        ])
        s.commit()

        busy = _busy(s, [amir, sam, lea, ziad])
        assert busy[amir.id] == [_hour(18)]   # creator
        assert busy[sam.id] == [_hour(18)]    # going
        assert busy[lea.id] == []             # cant -> time stays open
        assert busy[ziad.id] == []            # no answer -> not attending


def test_rsvp_maybe_counts_as_busy(Session):
    """Tentatively attending still means the time is spoken for — proposing a
    competing plan on top of it is worse than losing a slot that frees up."""
    with Session() as s:
        g, (amir, sam) = _group(s, "amir@x.com", "sam@x.com")
        e = _event(s, g, amir, 11)
        s.add(EventRsvp(event_id=e.id, user_id=sam.id, status="maybe"))
        s.commit()

        assert _busy(s, [amir, sam])[sam.id] == [_hour(11)]


def test_tasks_never_block(Session):
    """A due date is not an appointment."""
    with Session() as s:
        g, (amir,) = _group(s, "amir@x.com")
        _event(s, g, amir, 9, kind="task")

        assert _busy(s, [amir])[amir.id] == []


def test_events_count_across_groups(Session):
    """One user = one unified availability: a group is a planning context, not a
    separate time universe. Amir's Tuesday is busy here because of something in
    a group this query never mentions."""
    with Session() as s:
        g1, (amir, sam) = _group(s, "amir@x.com", "sam@x.com")
        other = Group(name="work", invite_code="w1", created_by=amir.id)
        s.add(other)
        s.commit()
        s.add(Membership(user_id=amir.id, group_id=other.id))
        s.commit()
        _event(s, other, amir, 15)

        # queried only for the members of g1, yet the work event still lands
        assert _busy(s, [amir, sam])[amir.id] == [_hour(15)]


def test_window_uses_overlap_not_containment(Session):
    """Something that started before the window and runs into it is still busy
    time inside it."""
    with Session() as s:
        g, (amir,) = _group(s, "amir@x.com")
        start = DAY - timedelta(hours=2)
        s.add(GroupEvent(
            group_id=g.id, created_by=amir.id, kind="event", title="overnight",
            start_utc=start, end_utc=DAY + timedelta(hours=1), personal=True,
        ))
        s.commit()

        assert _busy(s, [amir])[amir.id] == [(start, DAY + timedelta(hours=1))]


# ------------------------------------------------- the two sources, unioned

def _stub_provider(monkeypatch, blocks):
    class FakeProvider:
        def __init__(self, token):
            self._token = token

        def get_busy(self, time_min, time_max):
            return blocks.get(self._token, [])

    monkeypatch.setattr(availability, "provider_for_account",
                        lambda session, account: FakeProvider(account.token_json))
    from app.calendars.cache import freebusy_cache
    freebusy_cache.clear()   # module-level singleton — isolate between tests


def test_external_and_in_app_busy_are_merged(Session, monkeypatch):
    with Session() as s:
        g, (amir,) = _group(s, "amir@x.com")
        s.add(CalendarAccount(
            user_id=amir.id, provider="google", external_email="amir@x.com",
            token_json='{"token":"g"}',
        ))
        s.commit()
        _event(s, g, amir, 10, personal=True)          # Nudgy: 10:00-11:00
        _stub_provider(monkeypatch, {'{"token":"g"}': [_hour(9)]})   # Google: 9-10

        members = availability.fetch_busy_for_group(s, g, DAY, days_ahead=1)
        me = next(m for m in members if m.email == "amir@x.com")
        # touching intervals merge into one block spanning both sources
        assert me.busy == [(DAY.replace(hour=9), DAY.replace(hour=11))]
        assert me.connected is True
        assert me.in_app_blocks == 1


def test_synced_event_is_not_double_counted(Session, monkeypatch):
    """An in-app event synced out to Google arrives from BOTH sources. Union is
    idempotent, so the same hour must not become two blocks."""
    with Session() as s:
        g, (amir,) = _group(s, "amir@x.com")
        s.add(CalendarAccount(
            user_id=amir.id, provider="google", external_email="amir@x.com",
            token_json='{"token":"g"}',
        ))
        s.commit()
        _event(s, g, amir, 16, personal=True)
        _stub_provider(monkeypatch, {'{"token":"g"}': [_hour(16)]})

        members = availability.fetch_busy_for_group(s, g, DAY, days_ahead=1)
        assert members[0].busy == [_hour(16)]


# ----------------------------------------- the headline fix: no Google at all

def test_member_without_a_calendar_still_shapes_the_intersection(Session, monkeypatch):
    """The bug this whole change exists for. Sam connected nothing but keeps his
    events in Nudgy; before, he was dropped from the intersection and the group
    was told 16:00 was free for everyone."""
    with Session() as s:
        g, (amir, sam) = _group(s, "amir@x.com", "sam@x.com")
        s.add(CalendarAccount(
            user_id=amir.id, provider="google", external_email="amir@x.com",
            token_json='{"token":"g"}',
        ))
        s.commit()
        _event(s, g, sam, 16, personal=True)
        _stub_provider(monkeypatch, {'{"token":"g"}': []})

        result = availability.compute_availability(
            s, g, DAY, days_ahead=1, duration_minutes=60,
            tz_name="UTC", earliest_hour=9, latest_hour=22,
        )
        assert result["members_with_source"] == 2      # Google + Nudgy
        assert result["members_connected"] == 1
        assert result["members_unknown"] == []
        # Sam's 16:00-17:00 splits the day in two. Without this fix the whole
        # 09:00-22:00 block comes back as one slot free for everybody.
        assert [(s_["start"], s_["end"]) for s_ in result["common_slots"]] == [
            ("Mon 09 Mar 09:00", "Mon 09 Mar 16:00"),
            ("Mon 09 Mar 17:00", "Mon 09 Mar 22:00"),
        ]


def test_member_with_no_source_is_reported_unknown_not_free(Session, monkeypatch):
    """No calendar and no Nudgy events = we know nothing. That must be surfaced,
    not silently treated as wide open."""
    with Session() as s:
        g, (amir, ghost) = _group(s, "amir@x.com", "ghost@x.com")
        s.add(CalendarAccount(
            user_id=amir.id, provider="google", external_email="amir@x.com",
            token_json='{"token":"g"}',
        ))
        s.commit()
        _stub_provider(monkeypatch, {'{"token":"g"}': []})

        result = availability.compute_availability(
            s, g, DAY, days_ahead=1, duration_minutes=60,
            tz_name="UTC", earliest_hour=9, latest_hour=22,
        )
        assert result["members_unknown"] == ["ghost@x.com"]
        assert result["members_with_source"] == 1


def test_group_with_nothing_known_reports_no_source(Session):
    """Nobody connected, nobody has events: the window is not "all free"."""
    with Session() as s:
        g, _users = _group(s, "a@x.com", "b@x.com")
        result = availability.compute_availability(
            s, g, DAY, days_ahead=1, duration_minutes=60,
            tz_name="UTC", earliest_hour=9, latest_hour=22,
        )
        # the API layer blanks common_slots on this signal (group_routes)
        assert result["members_with_source"] == 0
        assert sorted(result["members_unknown"]) == ["a@x.com", "b@x.com"]
