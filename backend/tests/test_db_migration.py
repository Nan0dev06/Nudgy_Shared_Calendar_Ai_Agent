"""Upgrading an EXISTING database, not building a fresh one.

Every other test in this suite starts from `create_all` on an empty file, which
means the whole class of "works on a new DB, fatal on an old one" bug is
invisible to them. That is not hypothetical: the 2026-08-01 poll redesign
shipped with three columns that made an existing database unusable —

  time_votes.yes / guest_time_votes.yes   NOT NULL, no default, replaced by the
      three-state `answer`. Reading a vote raised "no such column: answer";
      casting one raised NOT NULL on a column nothing writes any more.
  time_rounds.status                      NOT NULL, no default, replaced by
      nothing — every candidate time is live at once now. Inserting a new time
      into an existing DB failed outright.

So these tests build the PRE-redesign schema by hand, put real rows in it, run
init_db() against it, and assert the data survived and the app can write again.

Extended for §2 (poll/event unification), which adds `events.plan_id`. The rule
this file exists to enforce: **every schema change ships its migration and its
case here together**, not afterwards. A green suite otherwise reads as "safe to
deploy" while saying nothing at all about existing data.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text

# The schema as it stood before docs/poll-edit-redesign.md §1. Only the tables
# the migration touches — enough to be the thing that used to break.
LEGACY_SCHEMA = """
CREATE TABLE plans (
    id INTEGER NOT NULL PRIMARY KEY,
    group_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    title VARCHAR NOT NULL,
    location VARCHAR,
    status VARCHAR NOT NULL,
    expected_count INTEGER,
    created_at DATETIME NOT NULL,
    auto_book BOOLEAN DEFAULT FALSE NOT NULL
);
CREATE TABLE time_rounds (
    id INTEGER NOT NULL PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    slot_start_utc DATETIME NOT NULL,
    slot_end_utc DATETIME NOT NULL,
    status VARCHAR NOT NULL,
    booked BOOLEAN NOT NULL,
    event_link VARCHAR
);
CREATE TABLE time_votes (
    id INTEGER NOT NULL PRIMARY KEY,
    round_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    yes BOOLEAN NOT NULL,
    voted_at DATETIME NOT NULL
);
CREATE TABLE guest_time_votes (
    id INTEGER NOT NULL PRIMARY KEY,
    round_id INTEGER NOT NULL,
    guest_id INTEGER NOT NULL,
    yes BOOLEAN NOT NULL,
    voted_at DATETIME NOT NULL
);
-- pre-§2: events had no idea a poll existed
CREATE TABLE events (
    id INTEGER NOT NULL PRIMARY KEY,
    group_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    kind VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    location VARCHAR,
    start_utc DATETIME,
    end_utc DATETIME,
    done BOOLEAN NOT NULL,
    personal BOOLEAN DEFAULT FALSE NOT NULL,
    anonymous BOOLEAN DEFAULT TRUE NOT NULL,
    synced BOOLEAN NOT NULL,
    gcal_event_id VARCHAR,
    gcal_link VARCHAR,
    created_at DATETIME NOT NULL
);
CREATE TABLE event_rsvps (
    id INTEGER NOT NULL PRIMARY KEY,
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    updated_at DATETIME NOT NULL
);
-- pre-inbound-sync: a connected calendar had no title opt-in and no bookmark
CREATE TABLE calendar_accounts (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    provider VARCHAR NOT NULL,
    external_email VARCHAR NOT NULL,
    token_json VARCHAR,
    color VARCHAR,
    sync_setting VARCHAR NOT NULL,
    is_primary BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
);
INSERT INTO calendar_accounts VALUES
    (1, 1, 'google', 'ada@example.com', 'tok', NULL, 'two_way', 1,
     '2026-07-01 10:00:00');
INSERT INTO events VALUES
    (1, 1, 1, 'event', 'Picnic', 'Event', 'Park', '2026-07-20 14:00:00',
     '2026-07-20 16:00:00', 0, 0, 1, 0, NULL, NULL, '2026-07-01 10:00:00');
INSERT INTO event_rsvps VALUES (1, 1, 2, 'going', '2026-07-01 11:00:00');
INSERT INTO plans VALUES
    (1, 1, 1, 'Karaoke', 'Cheers', 'open', NULL, '2026-07-01 10:00:00', 0);
INSERT INTO time_rounds VALUES
    (1, 1, 0, '2026-07-05 17:00:00', '2026-07-05 20:00:00', 'active', 0, NULL),
    (2, 1, 1, '2026-07-06 17:00:00', '2026-07-06 20:00:00', 'queued', 0, NULL);
INSERT INTO time_votes VALUES
    (1, 1, 1, 1, '2026-07-01 11:00:00'),
    (2, 1, 2, 0, '2026-07-01 11:05:00');
INSERT INTO guest_time_votes VALUES
    (1, 1, 1, 1, '2026-07-01 12:00:00');
"""


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    """A SQLite file carrying the pre-redesign schema, with init_db pointed at
    it. Returns the path so a test can inspect it with raw sqlite3 too."""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    con.commit()
    con.close()

    from app.db import session as sess

    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(sess, "engine", engine)
    return path, engine


def _columns(engine, table) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_init_db_upgrades_a_pre_redesign_database(legacy_db):
    """The whole point: init_db() on an old DB leaves it usable."""
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    # the columns that used to make every write fail are gone...
    assert "yes" not in _columns(engine, "time_votes")
    assert "yes" not in _columns(engine, "guest_time_votes")
    assert "status" not in _columns(engine, "time_rounds")
    # ...and the ones the new engine reads are there
    assert "answer" in _columns(engine, "time_votes")
    assert "answer" in _columns(engine, "guest_time_votes")
    assert "created_by" in _columns(engine, "time_rounds")
    assert "asks_interest" in _columns(engine, "plans")
    assert "spotlight_round_id" in _columns(engine, "plans")


def test_existing_votes_are_preserved_not_dropped(legacy_db):
    """A boolean vote becomes the equivalent three-state answer.

    Nothing becomes `if_needed` — nobody was ever able to say it, so inventing
    it would be putting words in a real person's mouth.
    """
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    with engine.begin() as conn:
        rows = dict(conn.execute(text("SELECT id, answer FROM time_votes")).all())
        guest = dict(conn.execute(text("SELECT id, answer FROM guest_time_votes")).all())

    assert rows == {1: "yes", 2: "no"}
    assert guest == {1: "yes"}
    assert "if_needed" not in set(rows.values()) | set(guest.values())


def test_can_write_after_the_upgrade(legacy_db):
    """The failure that would have hit a real user: putting a new time up, and
    voting on it, against a database that predates the redesign."""
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    with engine.begin() as conn:
        # a new candidate time — used to fail on time_rounds.status NOT NULL
        conn.execute(text(
            "INSERT INTO time_rounds (plan_id, ordinal, slot_start_utc, "
            "  slot_end_utc, booked, created_by) "
            "VALUES (1, 2, '2026-07-07 17:00:00', '2026-07-07 20:00:00', 0, 3)"
        ))
        # a three-state vote — used to fail on time_votes.yes NOT NULL
        conn.execute(text(
            "INSERT INTO time_votes (round_id, user_id, answer, voted_at) "
            "VALUES (3, 3, 'if_needed', '2026-07-02 09:00:00')"
        ))
        answers = conn.execute(
            text("SELECT answer FROM time_votes ORDER BY id")
        ).scalars().all()

    assert answers == ["yes", "no", "if_needed"]


def test_upgrade_is_idempotent(legacy_db):
    """init_db runs on every boot, so running it twice must be a no-op — and
    must not re-run the backfill over answers people have since changed."""
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()
    with engine.begin() as conn:
        conn.execute(text("UPDATE time_votes SET answer = 'if_needed' WHERE id = 1"))

    init_db()  # second boot

    with engine.begin() as conn:
        again = dict(conn.execute(text("SELECT id, answer FROM time_votes")).all())
    assert again[1] == "if_needed", "the backfill overwrote a real answer"
    assert again[2] == "no"


# ------------------------------- §2 poll/event unification + §3 needs_reconfirm

def test_events_gain_plan_id_and_keep_their_rows(legacy_db):
    """§2 adds `events.plan_id`. It must be NULLABLE: every event that predates
    the change was created directly, and NULL is exactly what that means — a
    NOT NULL column here would make the upgrade fail on real data."""
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    assert "plan_id" in _columns(engine, "events")
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, title, plan_id FROM events")).all()
    assert rows == [(1, "Picnic", None)]


def test_needs_reconfirm_needs_no_schema_change(legacy_db):
    """§3's new attendance state is a VALUE in an existing VARCHAR column, not a
    new column — so an old database can store it the moment the code ships.
    Worth pinning: if `status` ever gains a CHECK constraint, this breaks first.
    """
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE event_rsvps SET status = 'needs_reconfirm' WHERE id = 1"
        ))
        got = conn.execute(text("SELECT status FROM event_rsvps WHERE id = 1")).scalar()
    assert got == "needs_reconfirm"


def test_a_pre_existing_rsvp_is_untouched_by_the_upgrade(legacy_db):
    """Upgrading must not invent attendance state. Somebody who said `going`
    before §2 existed is still going afterwards — only a real edit resets."""
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    with engine.begin() as conn:
        got = conn.execute(text("SELECT status FROM event_rsvps WHERE id = 1")).scalar()
    assert got == "going"


# ------------------------------------------------------------- inbound sync

def test_existing_calendars_gain_read_titles_switched_OFF(legacy_db):
    """The opt-in must arrive off for calendars connected before it existed.

    This is the whole substance of the privacy decision (v1-decisions.md #5): a
    consent switch that upgrades itself into the "on" position for people who
    never saw it is not consent. A DEFAULT TRUE here would start reading titles
    off every already-connected calendar on the next deploy.
    """
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    assert "read_titles" in _columns(engine, "calendar_accounts")
    with engine.begin() as conn:
        got = conn.execute(
            text("SELECT read_titles FROM calendar_accounts WHERE id = 1")
        ).scalar()
    assert not got, "an existing calendar came back with titles already enabled"


def test_connecting_a_calendar_still_works_after_the_upgrade(legacy_db):
    """The failure mode a NOT NULL column with no default would cause.

    An old `calendar_accounts` table gains `read_titles`; if the added column
    were NOT NULL without DEFAULT FALSE, every subsequent INSERT that didn't
    name it — including db/session.py's own backfill — would fail, and the app
    would refuse to connect a calendar at all.
    """
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO calendar_accounts "
            "  (user_id, provider, external_email, token_json, sync_setting, "
            "   is_primary, created_at) "
            "VALUES (1, 'microsoft', 'ada@outlook.com', 'tok2', 'two_way', 0, "
            "        '2026-08-01 10:00:00')"
        ))
        got = conn.execute(text(
            "SELECT read_titles FROM calendar_accounts WHERE provider = 'microsoft'"
        )).scalar()
    assert not got


def test_the_mirror_tables_are_created_on_an_existing_database(legacy_db):
    """`calendar_sync_states` and `external_events` are brand-new TABLES, which
    create_all builds even on an old database — no _LATE_COLUMNS entry needed.
    Pinned because "new table" and "new column" have genuinely different
    migration paths here, and getting them the wrong way round fails silently
    (create_all never ALTERs an existing table)."""
    path, engine = legacy_db
    from app.db.session import init_db

    init_db()

    tables = set(inspect(engine).get_table_names())
    assert {"calendar_sync_states", "external_events"} <= tables

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO calendar_sync_states "
            "  (account_id, calendar_id, name, sync_token) "
            "VALUES (1, 'ada@example.com', 'Personal', 'tok-abc')"
        ))
        conn.execute(text(
            "INSERT INTO external_events "
            "  (user_id, account_id, calendar_id, external_id, start_utc, "
            "   end_utc, all_day, busy, updated_at) "
            "VALUES (1, 1, 'ada@example.com', 'evt-1', '2026-08-03 09:00:00', "
            "        '2026-08-03 10:00:00', 0, 1, '2026-08-01 10:00:00')"
        ))
        title = conn.execute(
            text("SELECT title FROM external_events WHERE external_id = 'evt-1'")
        ).scalar()
    assert title is None, "an untitled mirror row must be storable"
