"""Engine + session factory + schema creation.

Two backends, chosen by whether DATABASE_URL is set:
- set  -> Postgres (Render deployment; survives restarts)
- unset-> a local SQLite file at repo-root/nudgy.db (gitignored), for dev

For a hackathon we create tables on startup with create_all — no migrations.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DATABASE_URL, ROOT_DIR
from app.db.models import Base

if DATABASE_URL:
    # Render's Postgres URL sometimes uses the legacy "postgres://" scheme,
    # which SQLAlchemy rejects — normalize it. pool_pre_ping avoids errors
    # from connections the free DB has idled out.
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(url, pool_pre_ping=True)
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
    ("plans", "expected_count", "INTEGER"),
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
    ("ix_events_group_id", "events", "group_id"),
    ("ix_events_created_by", "events", "created_by"),
    ("ix_memberships_group_id", "memberships", "group_id"),
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


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
