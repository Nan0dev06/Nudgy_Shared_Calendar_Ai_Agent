"""Microsoft identity platform OAuth 2.0 (v2.0) — sign-in that also grants
Outlook calendar access. The Microsoft analogue of auth/google.py.

No SDK: plain httpx against the v2.0 endpoints (we own the stored token shape).
Tenant "common" so both personal (Outlook.com) and work/school accounts work.

Token lifecycle mirrors google.credentials_from_json's contract: refresh_token()
returns (token_json, refreshed?) and the caller persists back ONLY when the bool
is True (see calendars/microsoft.py from_account). The stored token_json is a
small dict {access_token, refresh_token, expires_at} — encrypted at rest by
CalendarAccount.token_json (EncryptedString).

Two Microsoft-specific gotchas the flow handles:
- A refresh response may OMIT refresh_token (rotation) — we carry the previous
  one forward, else the account silently dies.
- offline_access MUST be in scope or no refresh_token is issued at all.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import httpx

from app.core.config import (
    MS_CLIENT_ID,
    MS_CLIENT_SECRET,
    MS_REDIRECT_URI,
    MS_SCOPES,
    MS_TENANT,
)

_AUTHORIZE = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/authorize"
_TOKEN = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/token"
_GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
_SKEW_SECONDS = 60  # treat a token as expired this early, to avoid racing expiry


def _require_config() -> None:
    if not MS_CLIENT_ID or not MS_CLIENT_SECRET:
        raise RuntimeError(
            "MS_CLIENT_ID / MS_CLIENT_SECRET missing. Add them to .env (from your "
            "Azure app registration) to enable Microsoft sign-in."
        )


def build_authorize_url(state: str) -> str:
    """The URL to redirect the browser to. `state` is the CSRF token, echoed back
    to the callback unchanged."""
    _require_config()
    params = {
        "client_id": MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": MS_REDIRECT_URI,
        "response_mode": "query",  # code arrives as a query param (server-readable)
        "scope": " ".join(MS_SCOPES),
        "state": state,
    }
    return f"{_AUTHORIZE}?{urlencode(params)}"


def _pack(resp: dict, prev_refresh: str | None = None) -> str:
    """Token-endpoint response -> the compact JSON we persist. Carries the prior
    refresh_token forward when Microsoft omits a new one (rotation)."""
    return json.dumps({
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token") or prev_refresh,
        "expires_at": time.time() + int(resp.get("expires_in", 3600)) - _SKEW_SECONDS,
    })


def exchange_code(code: str) -> str:
    """Trade an authorization code for tokens; return the stored token_json."""
    _require_config()
    r = httpx.post(_TOKEN, data={
        "client_id": MS_CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": MS_REDIRECT_URI,
        "client_secret": MS_CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    return _pack(r.json())


def refresh_token(token_json: str) -> tuple[str, bool]:
    """Return (token_json, refreshed?). If the access token is still valid,
    returns it unchanged with False. Otherwise refreshes via the refresh_token
    grant and returns the new token_json with True — mirroring the Google
    credentials_from_json (creds, refreshed_json) contract so the caller persists
    only on a real refresh."""
    data = json.loads(token_json)
    if time.time() < data.get("expires_at", 0):
        return token_json, False
    _require_config()
    r = httpx.post(_TOKEN, data={
        "client_id": MS_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": data["refresh_token"],
        "client_secret": MS_CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    return _pack(r.json(), prev_refresh=data.get("refresh_token")), True


def access_token(token_json: str) -> str:
    return json.loads(token_json)["access_token"]


def get_account_email(token_json: str) -> str:
    """The connected account's address via Graph /me. `mail` for work/school
    accounts (may be null), `userPrincipalName` for personal ones — read both."""
    r = httpx.get(
        _GRAPH_ME,
        params={"$select": "mail,userPrincipalName"},
        headers={"Authorization": f"Bearer {access_token(token_json)}"},
        timeout=30,
    )
    r.raise_for_status()
    me = r.json()
    email = me.get("mail") or me.get("userPrincipalName")
    if not email:
        raise RuntimeError("Could not determine the Microsoft account email.")
    return email.lower()
