"""Engine + session factory + schema creation.

Two backends, chosen by whether DATABASE_URL is set:
- set  -> Postgres (the deployment; survives restarts)
- unset-> a local SQLite file at repo-root/nudgy.db (gitignored), for dev

For a hackathon we create tables on startup with create_all — no migrations.
"""
from collections.abc import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DATABASE_URL, DB_POOL_SIZE, ROOT_DIR
from app.db.models import Base


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
    ("plans", "expected_count", "INTEGER"),
    # WITH TIME ZONE matches DateTime(timezone=True) on Postgres; SQLite treats
    # any type name as an affinity hint, so the same DDL is portable
    ("plans", "deadline_utc", "TIMESTAMP WITH TIME ZONE"),
    ("plans", "auto_book", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("plans", "reminder_sent_at", "TIMESTAMP WITH TIME ZONE"),
    ("plans", "share_token", "VARCHAR"),
    # TRUE/FALSE literals work on both SQLite (>=3.23) and Postgres
    ("events", "personal", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("events", "anonymous", "BOOLEAN DEFAULT TRUE NOT NULL"),
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
