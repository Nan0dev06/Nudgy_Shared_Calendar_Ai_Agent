"""Per-user timezone: auto-detected by default, manual once you say so.

Every account used to be stamped "Asia/Beirut", which is a correctness bug the
moment a group spans two zones — a plan shown as 8pm to one member is a
different hour for the other, and the reminder mail says the wrong time. The
browser is the only party that actually knows, so it reports on every boot; the
server's job is to accept that WITHOUT ever overwriting a choice someone made
by hand.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth_routes import router
from app.api.deps import COOKIE_NAME, make_session_cookie
from app.db import repo
from app.db.models import Base, User
from app.db.session import get_session


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    client = TestClient(app, follow_redirects=False)
    with Session() as s:
        user = repo.login_with_google(s, "sam@x.com", '{"token": "t"}')
        uid = user.id
    client.cookies.set(COOKIE_NAME, make_session_cookie(uid))
    return client, Session, uid


def _user(Session, uid) -> User:
    with Session() as s:
        return s.get(User, uid)


def test_a_new_account_is_in_auto_mode(ctx):
    client, *_ = ctx
    me = client.get("/auth/me").json()
    assert me["timezone_auto"] is True
    assert me["timezone"] == "Asia/Beirut"  # the placeholder until a browser reports


def test_the_browser_report_sets_the_timezone(ctx):
    client, Session, uid = ctx
    me = client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Berlin"}).json()
    assert me["timezone"] == "Europe/Berlin"
    assert me["timezone_auto"] is True
    assert _user(Session, uid).timezone == "Europe/Berlin"


def test_reporting_the_same_zone_again_is_a_no_op(ctx):
    """This fires on every page load — it must not write on every page load."""
    client, *_ = ctx
    client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Berlin"})
    r = client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Berlin"})
    assert r.status_code == 200
    assert r.json()["timezone"] == "Europe/Berlin"


def test_travel_moves_an_auto_timezone(ctx):
    client, *_ = ctx
    client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Berlin"})
    me = client.post("/auth/me/detected-timezone", json={"timezone": "America/New_York"}).json()
    assert me["timezone"] == "America/New_York"


def test_choosing_one_by_hand_turns_detection_off(ctx):
    client, Session, uid = ctx
    me = client.patch("/auth/me", json={"timezone": "Asia/Tokyo"}).json()
    assert me["timezone"] == "Asia/Tokyo"
    assert me["timezone_auto"] is False
    assert _user(Session, uid).timezone_auto is False


def test_a_manual_timezone_survives_the_browser_report(ctx):
    """The whole point: a laptop set to UTC must not undo what you typed."""
    client, Session, uid = ctx
    client.patch("/auth/me", json={"timezone": "Asia/Tokyo"})
    me = client.post("/auth/me/detected-timezone", json={"timezone": "UTC"}).json()
    assert me["timezone"] == "Asia/Tokyo"
    assert _user(Session, uid).timezone == "Asia/Tokyo"


def test_detection_can_be_turned_back_on(ctx):
    client, *_ = ctx
    client.patch("/auth/me", json={"timezone": "Asia/Tokyo"})
    me = client.patch("/auth/me", json={"timezone_auto": True}).json()
    assert me["timezone_auto"] is True
    me = client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Lisbon"}).json()
    assert me["timezone"] == "Europe/Lisbon"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "", "../../etc/passwd", "/etc/localtime"])
def test_a_garbage_report_is_ignored_not_an_error(ctx, bad):
    """An old browser sending nonsense shouldn't turn every boot into a 400 —
    and shouldn't be able to reach outside the zone database either."""
    client, Session, uid = ctx
    r = client.post("/auth/me/detected-timezone", json={"timezone": bad})
    assert r.status_code == 200
    assert r.json()["timezone"] == "Asia/Beirut"
    assert _user(Session, uid).timezone == "Asia/Beirut"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "../../etc/passwd"])
def test_a_garbage_manual_choice_is_rejected(ctx, bad):
    """Typed by a person, so say so instead of silently keeping the old one."""
    client, *_ = ctx
    assert client.patch("/auth/me", json={"timezone": bad}).status_code == 400


def test_detection_requires_a_session(ctx):
    client, *_ = ctx
    client.cookies.clear()
    r = client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Berlin"})
    assert r.status_code == 401


def test_display_name_edits_do_not_disturb_the_timezone(ctx):
    client, Session, uid = ctx
    client.post("/auth/me/detected-timezone", json={"timezone": "Europe/Berlin"})
    me = client.patch("/auth/me", json={"display_name": "Sam"}).json()
    assert me["timezone"] == "Europe/Berlin"
    assert me["timezone_auto"] is True
