"""Engine + session factory + schema creation for the SQLite DB.

The DB is a single file at repo-root/orbi.db (gitignored). For a hackathon we
create tables on startup with Base.metadata.create_all — no migrations.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import ROOT_DIR
from app.db.models import Base

DB_PATH = ROOT_DIR / "orbi.db"
# check_same_thread=False so FastAPI's threadpool can share the engine
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
