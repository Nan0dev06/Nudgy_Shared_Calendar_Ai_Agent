"""Engine + session factory + schema creation.

Two backends, chosen by whether DATABASE_URL is set:
- set  -> Postgres (the deployment; survives restarts)
- unset-> a local SQLite file at repo-root/nudgy.db (gitignored), for dev

For a hackathon we create tables on startup with create_all — no migrations.
"""
import logging
from collections.abc import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DATABASE_URL, DB_POOL_SIZE, ROOT_DIR
from app.db.models import Base

log = logging.getLogger("nudgy.db")


def normalize_db_url(raw: str) -> str:
    """Make a pasted Postgres URL safe and SQLAlchemy-shaped.

    Two fixes, both from real paste-the-connection-string mistakes:
    - Heroku/Render hand out the legacy `postgres://` scheme, which SQLAlchemy
      rejects outright.
    - libpq's default sslmode is `prefer`, which silently falls back to an
      UNENCRYPTED connection if the TLS handshake fails. Every managed provider
      (Neon, Supabase, Render) requires TLS anyway, so a URL that arrives
      without an explicit sslmode gets `require` rather than a mode that can
      quietly downgrade. An explicit sslmode in the URL is left alone.
    """
    url = raw.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.startswith("postgresql"):
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    return urlunsplit(parts._replace(query=urlencode(query)))


if DATABASE_URL:
    # Serverless Postgres (Neon) parks idle connections and can drop them, so:
    # pool_pre_ping discards a dead connection instead of failing the request,
    # and pool_recycle retires one before the provider's idle window closes.
    # The pool is deliberately small — free tiers cap total connections, and a
    # sleepy web service holding them open helps nobody.
    engine = create_engine(
        normalize_db_url(DATABASE_URL),
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_POOL_SIZE,
        connect_args={"connect_timeout": 10},
    )
else:
    DB_PATH = ROOT_DIR / "nudgy.db"
    # check_same_thread=False so FastAPI's threadpool can share the engine
    engine = create_engine(
        f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
    )

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# columns added after a DB was first created: (table, column, DDL type+default)
_LATE_COLUMNS = [
    ("users", "display_name", "VARCHAR"),
    ("users", "drafts_json", "VARCHAR"),
    ("users", "memory_json", "VARCHAR"),
    ("users", "tier", "VARCHAR DEFAULT 'free' NOT NULL"),
    ("users", "password_hash", "VARCHAR"),
    ("users", "email_verified", "BOOLEAN DEFAULT FALSE NOT NULL"),
    # Existing rows default to TRUE (auto): every timezone in the DB before this
    # column existed was the unasked-for "Asia/Beirut" default, so letting the
    # browser correct it is the point. Anyone who then types one in Settings
    # flips it to FALSE and is never touched again.
    ("users", "timezone_auto", "BOOLEAN DEFAULT TRUE NOT NULL"),
    ("plans", "expected_count", "INTEGER"),
    # WITH TIME ZONE matches DateTime(timezone=True) on Postgres; SQLite treats
    # any type name as an affinity hint, so the same DDL is portable
    ("plans", "deadline_utc", "TIMESTAMP WITH TIME ZONE"),
    ("plans", "reminder_sent_at", "TIMESTAMP WITH TIME ZONE"),
    # poll redesign (docs/poll-edit-redesign.md §1). `auto_book` was dropped from
    # the model here — convergence replaced it — but the column is deliberately
    # NOT removed: dropping one is a table rebuild on SQLite, and an unread
    # column costs nothing. Existing rows keep whatever they had.
    ("plans", "asks_interest", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("plans", "spotlight_round_id", "INTEGER"),
    ("time_rounds", "created_by", "INTEGER"),
    # Votes went from a boolean to three states. The old `yes` column is dropped
    # further down, AFTER _backfill_vote_answers copies it across — order
    # matters, which is why the drop is a separate pass and not a _LATE_COLUMNS
    # entry.
    ("time_votes", "answer", "VARCHAR"),
    ("guest_time_votes", "answer", "VARCHAR"),
    ("plans", "share_token", "VARCHAR"),
    # TRUE/FALSE literals work on both SQLite (>=3.23) and Postgres
    ("events", "personal", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("events", "anonymous", "BOOLEAN DEFAULT TRUE NOT NULL"),
    # poll/event unification (docs/poll-edit-redesign.md §2): the poll a booked
    # event came from. Nullable — every event that predates this was created
    # directly, and NULL is exactly what that means.
    ("events", "plan_id", "INTEGER"),
]

# Columns the model no longer has, which an EXISTING database still carries as
# NOT NULL with no default — so leaving them in place makes every INSERT fail.
# create_all never touches an existing table, so nothing else would catch this;
# the test suite can't either, because tests always build a fresh schema.
#
#   time_votes.yes / guest_time_votes.yes
#       replaced by the three-state `answer`. Backfilled first (see
#       _backfill_vote_answers), then dropped — otherwise casting a vote raises
#       NOT NULL, and reading one raises "no such column: answer".
#   time_rounds.status
#       the queued/active/skipped machine. Nothing replaces it: every candidate
#       time is live at once, so the concept is gone rather than renamed.
#       Dropping it is what lets a new candidate time be inserted at all.
#
# `plans.auto_book` is deliberately NOT here: it is NOT NULL but DEFAULT FALSE,
# so it accepts inserts and simply sits unread. Dropping a harmless column is
# risk without benefit.
_DROPPED_COLUMNS = [
    ("time_votes", "yes"),
    ("guest_time_votes", "yes"),
    ("time_rounds", "status"),
]

# indexes on hot foreign keys, added after a DB existed. create_all only builds
# indexes for tables it CREATES, not existing ones, so these need an explicit
# pass. CREATE INDEX IF NOT EXISTS is idempotent on both SQLite and Postgres, so
# it's safe to run on every startup (and a no-op once the index is there).
_LATE_INDEXES = [
    ("ix_plans_group_id", "plans", "group_id"),
    ("ix_plan_guests_plan_id", "plan_guests", "plan_id"),
    ("ix_events_group_id", "events", "group_id"),
    ("ix_events_created_by", "events", "created_by"),
    ("ix_memberships_group_id", "memberships", "group_id"),
]


# Same idea, but UNIQUE. Both SQLite and Postgres treat NULLs as distinct in a
# unique index, so a nullable column (share_token: most plans have none) is fine
# here — it enforces "no two plans share a link" without blocking the NULLs.
_LATE_UNIQUE_INDEXES = [
    ("ux_plans_share_token", "plans", "share_token"),
]


def init_db() -> None:
    Base.metadata.create_all(engine)
    # create_all never ALTERs existing tables, so columns added after a DB was
    # first created need this tiny in-place migration (works on SQLite + PG).
    insp = inspect(engine)
    for table, column, ddl in _LATE_COLUMNS:
        cols = {c["name"] for c in insp.get_columns(table)}
        if column not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    for name, table, column in _LATE_INDEXES:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))
    for name, table, column in _LATE_UNIQUE_INDEXES:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({column})"
            ))
    _backfill_calendar_accounts(engine)
    # Copy the old boolean votes across BEFORE the column carrying them goes.
    _backfill_vote_answers(engine)
    _drop_dead_columns(engine)


def _backfill_vote_answers(engine) -> None:
    """Three-state votes (docs/poll-edit-redesign.md §1.3): turn every stored
    `yes` boolean into the new `answer` string.

    A pre-redesign vote only ever meant "works for me" or "doesn't", so it maps
    onto exactly two of the three states — nothing becomes `if_needed`, because
    nobody was ever able to say it. Guarded on `answer IS NULL` so it is a no-op
    on every boot after the first, and skipped entirely once `yes` is gone.
    """
    insp = inspect(engine)
    for table in ("time_votes", "guest_time_votes"):
        cols = {c["name"] for c in insp.get_columns(table)}
        if "yes" not in cols or "answer" not in cols:
            continue  # already migrated, or a fresh DB that never had `yes`
        with engine.begin() as conn:
            conn.execute(text(
                f"UPDATE {table} SET answer = CASE WHEN yes THEN 'yes' ELSE 'no' END "
                " WHERE answer IS NULL"
            ))


def _drop_dead_columns(engine) -> None:
    """Remove columns the model no longer writes but an existing DB still
    requires (see _DROPPED_COLUMNS for why each one is fatal if left).

    ALTER TABLE ... DROP COLUMN is supported by SQLite >= 3.35 and every
    Postgres we target. On an older SQLite the drop fails; that is logged and
    tolerated rather than raised, because the alternative — refusing to boot —
    is worse than a clear error at the point somebody votes.

    Re-inspects rather than reusing init_db's Inspector: that one was built
    before the ALTERs above ran and its cached column lists are stale by now.
    """
    insp = inspect(engine)
    for table, column in _DROPPED_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
            log.info("dropped dead column %s.%s", table, column)
        except Exception:
            log.exception(
                "could not drop %s.%s — polls will fail until it is removed "
                "by hand or the DB is recreated", table, column,
            )


def _backfill_calendar_accounts(engine) -> None:
    """Identity/calendar split (Phase 1): move each pre-split user's Google token
    into a CalendarAccount row. Idempotent — the NOT EXISTS guard makes it a
    no-op once a user's account exists, so it's safe on every startup.

    The token value is copied VERBATIM (still ciphertext, same key), so it reads
    back through EncryptedString exactly as before — no re-encryption, no
    decrypt/re-encrypt round-trip. TRUE / CURRENT_TIMESTAMP are portable across
    SQLite and Postgres.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO calendar_accounts "
            "  (user_id, provider, external_email, token_json, color, "
            "   sync_setting, is_primary, created_at) "
            "SELECT u.id, 'google', u.email, u.token_json, NULL, 'two_way', "
            "       TRUE, CURRENT_TIMESTAMP "
            "  FROM users u "
            " WHERE u.token_json IS NOT NULL "
            "   AND NOT EXISTS (SELECT 1 FROM calendar_accounts ca "
            "                    WHERE ca.user_id = u.id AND ca.provider = 'google')"
        ))


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
