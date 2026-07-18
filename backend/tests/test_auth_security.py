"""The OAuth callback must only answer a login this app started.

Without the state check, an attacker can obtain an authorization code for THEIR
Google account and hand a victim's browser a link to our callback carrying it.
The victim silently ends up signed into the attacker's account and plans their
hangouts there. Google echoes `state` back untouched, so a value only we could
have set is what proves the callback belongs to our own /login.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_routes import STATE_COOKIE, router

app = FastAPI()
app.include_router(router)
# follow_redirects=False so the 302 to Google is inspectable, not followed
client = TestClient(app, follow_redirects=False)


def _callback(params: dict, cookies: dict | None = None):
    client.cookies.clear()
    for k, v in (cookies or {}).items():
        client.cookies.set(k, v)
    return client.get("/auth/google/callback", params=params)


def test_callback_without_any_state_is_refused():
    # the bare attack: a code the victim never asked for, no state at all
    r = _callback({"code": "attacker-code"})
    assert r.status_code == 400
    assert "didn't come from here" in r.json()["detail"]


def test_callback_with_state_but_no_cookie_is_refused():
    # attacker supplies a state they invented; nothing proves we issued it
    r = _callback({"code": "attacker-code", "state": "made-up"})
    assert r.status_code == 400


def test_callback_with_mismatched_state_is_refused():
    r = _callback({"code": "attacker-code", "state": "made-up"},
                  cookies={STATE_COOKIE: "the-real-one"})
    assert r.status_code == 400


def test_matching_state_is_allowed_through(monkeypatch):
    """The gate must not be so strict it blocks the real flow.

    Everything past the state check is stubbed: a genuine callback would other-
    wise exchange the code with Google, and a unit test must not hit the network.
    """
    class FakeFlow:
        credentials = "fake-creds"
        def fetch_token(self, code): pass

    class FakeUser:
        id = 7

    monkeypatch.setattr(auth_routes, "build_web_flow", lambda uri: FakeFlow())
    monkeypatch.setattr(auth_routes, "get_account_email", lambda c: "sam@x.com")
    monkeypatch.setattr(auth_routes.repo, "upsert_user_token",
                        lambda s, e, t: FakeUser())
    monkeypatch.setattr(FakeFlow, "credentials",
                        type("C", (), {"to_json": lambda self: "{}"})())

    r = _callback({"code": "good", "state": "same"}, cookies={STATE_COOKIE: "same"})
    assert r.status_code == 307                    # logged in, redirected home
    assert "nudgy_session" in r.headers["set-cookie"]
    assert 'nudgy_oauth_state=""' in r.headers["set-cookie"]  # state burned after use


def test_login_issues_a_state_cookie_matching_the_google_url(monkeypatch):
    r = client.get("/auth/google/login")
    assert r.status_code == 307
    issued = r.cookies.get(STATE_COOKIE)
    assert issued, "login must pin a state cookie or the callback can't verify"
    # the same value must travel to Google, or it could never come back to match
    assert f"state={issued}" in r.headers["location"]


def test_state_cookie_is_httponly_and_short_lived():
    r = client.get("/auth/google/login")
    header = r.headers["set-cookie"]
    assert "HttpOnly" in header          # script must not be able to read it
    assert "Max-Age=600" in header       # a stale login link stops working
