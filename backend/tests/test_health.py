"""Liveness/readiness probes.

The split matters for cost, not just style: /healthz is what an uptime pinger
hits every few minutes, and it must never touch the database, or the pings
alone would keep serverless Postgres awake and burn the free compute budget.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.health_routes import router
from app.db.models import Base
from app.db.session import get_session


@pytest.fixture
def app_and_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    return app, Session


@pytest.fixture
def client(app_and_session):
    app, Session = app_and_session

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_healthz_is_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_never_opens_a_session(app_and_session):
    """No dependency override at all: if /healthz needed a database it would
    fall through to the real engine, and this would not be a 200."""
    app, _ = app_and_session
    opened = []

    def tripwire():
        opened.append(True)
        yield None

    app.dependency_overrides[get_session] = tripwire
    assert TestClient(app).get("/healthz").status_code == 200
    assert opened == []


def test_readyz_reports_a_working_database(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_readyz_503s_when_the_database_is_unreachable(app_and_session):
    app, _ = app_and_session

    class Broken:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("could not connect to server: host=db.neon.tech")

    def broken_session():
        yield Broken()

    app.dependency_overrides[get_session] = broken_session
    r = TestClient(app).get("/readyz")
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"
    # the driver error quotes the connection string — it stays in the log
    assert "neon.tech" not in r.text
