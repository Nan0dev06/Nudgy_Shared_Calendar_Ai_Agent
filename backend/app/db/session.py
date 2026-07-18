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


def init_db() -> None:
    Base.metadata.create_all(engine)
    # create_all never ALTERs existing tables, so columns added after a DB was
    # first created need this tiny in-place migration (works on SQLite + PG).
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    if "display_name" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR"))


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
