"""Mirrored event titles on the calendar — and who is allowed to see them.

Inbound sync exists so a busy block can say what it actually is. The rule that
makes that safe is narrow and worth pinning down hard: a title is visible to the
person whose calendar it came from AND NOBODY ELSE. Groupmates keep getting the
opaque busy ranges they always got, which is what leaves the freebusy-only
promise in v1-decisions.md #5 intact.

The second rule these cover is that labelling is a DISPLAY concern. It must not
move a single free slot, invent busy time the live read didn't find, or draw the
same hour twice.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent import availability
from app.db.models import (
    Base, CalendarAccount, CalendarSyncState, ExternalEvent, Group, GroupEvent,
    Membership, User,
)

DAY = datetime(2026, 3, 9, tzinfo=timezone.utc)   # a Monday
NOW = DAY.replace(hour=8)


@pytest.fixture
def Session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _group(session, *emails):
    users = [User(email=e) for e in emails]
    session.add_all(users)
    session.commit()
    group = Group(name="crew", invite_code="abc123", created_by=users[0].id)
    session.add(group)
    session.commit()
    session.add_all([Membership(user_id=u.id, group_id=group.id) for u in users])
    session.commit()
    return group, users


def _in_app_event(session, group, owner, hour, minutes=60):
    """A Nudgy event, which is busy time we can create without any network."""
    start = DAY.replace(hour=hour)
    ev = GroupEvent(
        group_id=group.id, created_by=owner.id, kind="event", title="thing",
        start_utc=start, end_utc=start + timedelta(minutes=minutes), personal=True,
    )
    session.add(ev)
    session.commit()
    return ev


def _mirror(session, user, hour, *, title="Dentist", where=None, minutes=60,
            busy=True, account_id=None, calendar_id="primary"):
    start = DAY.replace(hour=hour)
    row = ExternalEvent(
        user_id=user.id, account_id=account_id, calendar_id=calendar_id,
        external_id=f"ext-{hour}", title=title, location=where,
        start_utc=start, end_utc=start + timedelta(minutes=minutes),
        busy=busy, updated_at=NOW,
    )
    session.add(row)
    session.commit()
    return row


def _members_busy(session, group, viewer):
    result = availability.compute_availability(
        session, group, NOW, days_ahead=1, duration_minutes=60,
        tz_name="UTC", include_member_busy=True, viewer_id=viewer.id,
    )
    return {m["email"]: m["busy"] for m in result["members_busy"]}


# ------------------------------------------------------------------ the rule

def test_you_see_your_own_titles(Session):
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _in_app_event(s, group, ada, 15)
        _mirror(s, ada, 15, title="Dentist", where="Clinic")

        rows = _members_busy(s, group, ada)["ada@x.com"]
        titled = [r for r in rows if r.get("title")]
        assert len(titled) == 1
        assert titled[0]["title"] == "Dentist"
        assert titled[0]["where"] == "Clinic"


def test_a_groupmate_sees_the_same_block_with_no_title(Session):
    """The whole privacy decision in one assertion. Ada's dentist appointment
    still blocks Ben's planning; Ben must not learn it is a dentist."""
    with Session() as s:
        group, (ada, ben) = _group(s, "ada@x.com", "ben@x.com")
        _in_app_event(s, group, ada, 15)
        _mirror(s, ada, 15, title="Dentist")

        as_ben = _members_busy(s, group, ben)
        ada_rows = as_ben["ada@x.com"]
        assert ada_rows, "Ada's busy time vanished for Ben"
        assert all("title" not in r for r in ada_rows), "a title leaked to a groupmate"


def test_the_same_group_renders_differently_per_viewer(Session):
    """Two people loading the same group get two different payloads — each sees
    their own detail and the other's opaque time."""
    with Session() as s:
        group, (ada, ben) = _group(s, "ada@x.com", "ben@x.com")
        _in_app_event(s, group, ada, 15)
        _in_app_event(s, group, ben, 17)
        _mirror(s, ada, 15, title="Dentist")
        _mirror(s, ben, 17, title="Gym")

        as_ada = _members_busy(s, group, ada)
        as_ben = _members_busy(s, group, ben)

        assert any(r.get("title") == "Dentist" for r in as_ada["ada@x.com"])
        assert all("title" not in r for r in as_ada["ben@x.com"])
        assert any(r.get("title") == "Gym" for r in as_ben["ben@x.com"])
        assert all("title" not in r for r in as_ben["ada@x.com"])


def test_no_viewer_means_no_titles_at_all(Session):
    """The agent calls compute_availability without a viewer. It gets ranges,
    exactly as it always has."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _in_app_event(s, group, ada, 15)
        _mirror(s, ada, 15, title="Dentist")

        result = availability.compute_availability(
            s, group, NOW, days_ahead=1, duration_minutes=60,
            tz_name="UTC", include_member_busy=True,
        )
        rows = result["members_busy"][0]["busy"]
        assert all("title" not in r for r in rows)


# ------------------------------------------------- labelling changes nothing

def test_an_untitled_mirror_row_changes_the_output_not_at_all(Session):
    """Until somebody opts in, the mirror stores times with no titles — and the
    availability payload must be byte-identical to what it was before this
    feature existed."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _in_app_event(s, group, ada, 15)
        before = _members_busy(s, group, ada)["ada@x.com"]

        _mirror(s, ada, 15, title=None)
        after = _members_busy(s, group, ada)["ada@x.com"]

        assert before == after


def test_labels_do_not_move_the_free_slots(Session):
    """Labelling is display only. If it touched the intersection, opting into
    titles would change when your group can meet — which would be absurd."""
    with Session() as s:
        group, (ada, ben) = _group(s, "ada@x.com", "ben@x.com")
        _in_app_event(s, group, ada, 15)

        def slots(with_mirror):
            return availability.compute_availability(
                s, group, NOW, days_ahead=1, duration_minutes=60,
                tz_name="UTC", viewer_id=ada.id,
            )["common_slots"]

        before = slots(False)
        _mirror(s, ada, 15, title="Dentist")
        assert slots(True) == before


def test_the_same_hour_is_never_drawn_twice(Session):
    """A labelled block and the merged range that produced it are the same hour.
    Emitting both would show every opted-in event twice."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _in_app_event(s, group, ada, 15, minutes=120)   # 15:00-17:00 busy
        _mirror(s, ada, 15, title="Dentist", minutes=60)  # explains 15:00-16:00

        rows = _members_busy(s, group, ada)["ada@x.com"]
        assert len(rows) == 2
        titled, plain = rows[0], rows[1]
        assert titled["title"] == "Dentist"
        assert titled["start_iso"].startswith("2026-03-09T15:00")
        assert titled["end_iso"].startswith("2026-03-09T16:00")
        # the unexplained remainder survives as an anonymous block
        assert "title" not in plain
        assert plain["start_iso"].startswith("2026-03-09T16:00")
        assert plain["end_iso"].startswith("2026-03-09T17:00")


def test_a_stale_mirror_cannot_invent_busy_time(Session):
    """The mirror is minutes behind the live read by design. An event deleted in
    Google a minute ago is still sitting in it — and must NOT be drawn over an
    hour the slot math is simultaneously offering as free."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _mirror(s, ada, 15, title="Cancelled thing")   # no matching busy time

        rows = _members_busy(s, group, ada)["ada@x.com"]
        assert rows == [], "a stale mirror row became a busy block"


def test_a_free_marked_event_is_never_a_label(Session):
    """showAs=free / transparency=transparent: on the calendar, not in it. There
    is no busy block for it to explain."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _in_app_event(s, group, ada, 15)
        _mirror(s, ada, 15, title="Someone's birthday", busy=False)

        rows = _members_busy(s, group, ada)["ada@x.com"]
        assert all("title" not in r for r in rows)


def test_a_label_names_the_calendar_it_came_from(Session):
    """Which calendar tells you whether a block is movable — the reason
    CalendarAccount carries a colour in the first place."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        # No token on purpose: this test is about naming the calendar, and a real
        # token would send fetch_busy_for_group off to build a Google provider.
        account = CalendarAccount(
            user_id=ada.id, provider="google", external_email="ada@x.com",
        )
        s.add(account)
        s.commit()
        s.add(CalendarSyncState(
            account_id=account.id, calendar_id="uni@x.com", name="AUB timetable",
        ))
        s.commit()
        _in_app_event(s, group, ada, 15)
        _mirror(s, ada, 15, title="Lecture",
                account_id=account.id, calendar_id="uni@x.com")

        titled = [r for r in _members_busy(s, group, ada)["ada@x.com"] if r.get("title")]
        assert titled[0]["calendar"] == "AUB timetable"


def test_a_kept_copy_from_a_disconnected_calendar_has_no_calendar_name(Session):
    """Detached rows (account_id NULL) outlive the connection. There is no
    connection left to name them after, and that is not an error."""
    with Session() as s:
        group, (ada,) = _group(s, "ada@x.com")
        _in_app_event(s, group, ada, 15)
        _mirror(s, ada, 15, title="Old lecture", account_id=None)

        titled = [r for r in _members_busy(s, group, ada)["ada@x.com"] if r.get("title")]
        assert titled[0]["calendar"] is None
