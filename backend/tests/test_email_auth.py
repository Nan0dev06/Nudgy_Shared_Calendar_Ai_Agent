"""Email/password + magic-link + reset flows end-to-end over HTTP.

Uses an isolated in-memory DB (StaticPool so one shared connection) and the
MemoryEmailSender so links are captured instead of sent. Enumeration-safety and
token single-use are asserted here.
"""
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth_routes import router
from app.db.models import Base
from app.db.session import get_session
from app import mailer
from app.mailer.memory import MemoryEmailSender

TOKEN_RE = re.compile(r"token=([^\s&]+)")


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override

    box = MemoryEmailSender()
    previous = mailer.get_email_sender()
    mailer.set_email_sender(box)
    client = TestClient(app, follow_redirects=False)
    try:
        yield client, box
    finally:
        mailer.set_email_sender(previous)


def _token_from(box) -> str:
    m = TOKEN_RE.search(box.last.text)
    assert m, f"no token found in email: {box.last.text!r}"
    return m.group(1)


def _register(client, email="sam@x.com", password="hunter2hunter"):
    return client.post("/auth/register", json={"email": email, "password": password})


# ------------------------------------------------------------- register/verify/login

def test_register_verify_then_login(ctx):
    client, box = ctx
    r = _register(client)
    assert r.status_code == 200
    assert len(box.outbox) == 1
    assert box.last.to == "sam@x.com"

    verify = client.get("/auth/verify", params={"token": _token_from(box)})
    assert verify.status_code == 307
    assert "nudgy_session" in verify.headers["set-cookie"]

    login = client.post("/auth/login", json={"email": "sam@x.com", "password": "hunter2hunter"})
    assert login.status_code == 200
    assert login.json()["email_verified"] is True
    assert "nudgy_session" in login.headers["set-cookie"]


def test_login_is_blocked_until_verified(ctx):
    client, _ = ctx
    _register(client)
    r = client.post("/auth/login", json={"email": "sam@x.com", "password": "hunter2hunter"})
    assert r.status_code == 403
    assert "verify" in r.json()["detail"].lower()


def test_wrong_password_and_unknown_email_are_indistinguishable(ctx):
    client, box = ctx
    _register(client)
    client.get("/auth/verify", params={"token": _token_from(box)})

    wrong = client.post("/auth/login", json={"email": "sam@x.com", "password": "nope"})
    unknown = client.post("/auth/login", json={"email": "ghost@x.com", "password": "whatever1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_register_rejects_bad_email_and_short_password(ctx):
    client, _ = ctx
    assert client.post("/auth/register", json={"email": "notanemail", "password": "longenough1"}).status_code == 400
    assert client.post("/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422  # Field min_length


def test_register_is_enumeration_and_takeover_safe(ctx):
    client, box = ctx
    _register(client, password="original-pw-1")
    client.get("/auth/verify", params={"token": _token_from(box)})  # now verified/claimed
    before = len(box.outbox)

    # re-registering a CLAIMED account: same generic response, no email, password unchanged
    r = _register(client, password="attacker-pw-2")
    assert r.status_code == 200
    assert len(box.outbox) == before  # nothing sent
    assert client.post("/auth/login", json={"email": "sam@x.com", "password": "attacker-pw-2"}).status_code == 401
    assert client.post("/auth/login", json={"email": "sam@x.com", "password": "original-pw-1"}).status_code == 200


def test_reregister_unclaimed_account_resends(ctx):
    client, box = ctx
    _register(client)                 # unverified, unclaimed
    assert len(box.outbox) == 1
    _register(client, password="new-unclaimed-pw")  # allowed to resend
    assert len(box.outbox) == 2


# ------------------------------------------------------------------- magic link

def test_magic_link_signs_in_and_verifies(ctx):
    client, box = ctx
    _register(client)  # unverified
    box.outbox.clear()

    req = client.post("/auth/magic-link", json={"email": "sam@x.com"})
    assert req.status_code == 200
    assert len(box.outbox) == 1

    consume = client.get("/auth/magic", params={"token": _token_from(box)})
    assert consume.status_code == 307
    assert "nudgy_session" in consume.headers["set-cookie"]
    # the session cookie now works, and clicking the link verified the email
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "sam@x.com"
    assert me.json()["email_verified"] is True


def test_magic_link_for_unknown_email_is_silent(ctx):
    client, box = ctx
    r = client.post("/auth/magic-link", json={"email": "ghost@x.com"})
    assert r.status_code == 200
    assert box.outbox == []


# ---------------------------------------------------------------- password reset

def test_password_reset_changes_password_and_invalidates_the_link(ctx):
    client, box = ctx
    _register(client, password="old-password-1")
    client.get("/auth/verify", params={"token": _token_from(box)})

    client.post("/auth/password/reset-request", json={"email": "sam@x.com"})
    reset_token = _token_from(box)
    r = client.post("/auth/password/reset", json={"token": reset_token, "password": "brand-new-pw-2"})
    assert r.status_code == 200

    # new password works, old one doesn't
    assert client.post("/auth/login", json={"email": "sam@x.com", "password": "brand-new-pw-2"}).status_code == 200
    assert client.post("/auth/login", json={"email": "sam@x.com", "password": "old-password-1"}).status_code == 401

    # the used reset link is now dead (stamp no longer matches)
    reused = client.post("/auth/password/reset", json={"token": reset_token, "password": "third-pw-3"})
    assert reused.status_code == 400


def test_reset_request_for_unknown_email_is_silent(ctx):
    client, box = ctx
    r = client.post("/auth/password/reset-request", json={"email": "ghost@x.com"})
    assert r.status_code == 200
    assert box.outbox == []


# ------------------------------------------------------------------- bad tokens

def test_garbage_tokens_are_rejected(ctx):
    client, _ = ctx
    assert client.get("/auth/verify", params={"token": "garbage"}).status_code == 400
    assert client.get("/auth/magic", params={"token": "garbage"}).status_code == 400
    assert client.post("/auth/password/reset", json={"token": "garbage", "password": "longenough1"}).status_code == 400


def test_verify_token_cannot_be_replayed_as_magic(ctx):
    """Per-purpose salts: a verification token must not work on the magic-link
    endpoint (or any other flow)."""
    client, box = ctx
    _register(client)
    verify_token = _token_from(box)
    assert client.get("/auth/magic", params={"token": verify_token}).status_code == 400
