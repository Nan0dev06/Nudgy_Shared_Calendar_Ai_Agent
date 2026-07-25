"""Session cookies expire: a valid cookie authenticates, but a missing,
tampered, or too-old one is refused (app/api/deps.py)."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import deps
from app.db.models import Base, User


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _user(session):
    u = User(email="sam@x.com")
    session.add(u)
    session.commit()
    return u


def test_valid_cookie_authenticates(session):
    user = _user(session)
    cookie = deps.make_session_cookie(user.id)
    got = deps.get_current_user(nudgy_session=cookie, session=session)
    assert got.id == user.id


def test_missing_cookie_is_401(session):
    with pytest.raises(HTTPException) as exc:
        deps.get_current_user(nudgy_session=None, session=session)
    assert exc.value.status_code == 401


def test_tampered_cookie_is_401(session):
    _user(session)
    with pytest.raises(HTTPException) as exc:
        deps.get_current_user(nudgy_session="not-a-real-cookie", session=session)
    assert exc.value.status_code == 401
    assert "invalid" in exc.value.detail.lower()


def test_expired_cookie_is_rejected_distinctly(session, monkeypatch):
    user = _user(session)
    cookie = deps.make_session_cookie(user.id)
    # negative TTL makes even a fresh cookie read as too old, without sleeping
    monkeypatch.setattr(deps, "SESSION_TTL_SECONDS", -1)
    with pytest.raises(HTTPException) as exc:
        deps.get_current_user(nudgy_session=cookie, session=session)
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()
