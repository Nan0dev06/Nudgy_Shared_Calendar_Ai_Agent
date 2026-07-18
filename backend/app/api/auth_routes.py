"""Auth endpoints: Google OAuth web flow = the app's login.

GET  /auth/google/login     -> 302 to Google's consent screen
GET  /auth/google/callback  -> exchanges code, upserts user + token, sets cookie
GET   /auth/me              -> who am I (or 401)
PATCH /auth/me              -> update display name / timezone
POST  /auth/logout          -> clears the cookie
"""
from __future__ import annotations

import secrets
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    COOKIE_KWARGS, COOKIE_NAME, get_current_user, make_session_cookie,
)
from app.auth.google import build_web_flow, get_account_email
from app.core.config import GOOGLE_REDIRECT_URI
from app.db.models import User
from app.db import repo
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

# Ties a callback back to the /login that started it. Without this, anyone can
# feed a victim's browser an authorization code of their choosing and silently
# log that victim into an ACCOUNT THEY CONTROL — the victim then plans hangouts
# inside the attacker's account. Google hands `state` back untouched, so a value
# only we could have set proves the callback answers our own login.
STATE_COOKIE = "nudgy_oauth_state"
STATE_TTL_SECONDS = 600


@router.get("/google/login")
def google_login():
    flow = build_web_flow(GOOGLE_REDIRECT_URI)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    response = RedirectResponse(url)
    # SameSite=Lax still sends this on Google's top-level redirect back to us.
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
    return response


@router.get("/google/callback")
def google_callback(request: Request, session: Session = Depends(get_session)):
    expected = request.cookies.get(STATE_COOKIE)
    received = request.query_params.get("state")
    if not expected or not received or not secrets.compare_digest(expected, received):
        raise HTTPException(
            status_code=400,
            detail="This sign-in link didn't come from here, or it expired. "
                   "Start again from the app.",
        )
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing ?code from Google.")
    flow = build_web_flow(GOOGLE_REDIRECT_URI)
    flow.fetch_token(code=code)
    creds = flow.credentials
    email = get_account_email(creds)
    user = repo.upsert_user_token(session, email, creds.to_json())

    # logged in — back to the app with the signed cookie set
    response = RedirectResponse("/")
    response.set_cookie(COOKIE_NAME, make_session_cookie(user.id), **COOKIE_KWARGS)
    response.delete_cookie(STATE_COOKIE)  # single use
    return response


def _me_json(user: User) -> dict:
    return {
        "email": user.email,
        "timezone": user.timezone,
        "display_name": user.display_name,
        "calendar_connected": user.calendar_connected,
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _me_json(user)


class PatchMeBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=60)


@router.patch("/me")
def patch_me(
    body: PatchMeBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)  # validate it's a real IANA name
        except Exception:
            raise HTTPException(status_code=400, detail="Unknown timezone (use an IANA name like Asia/Beirut).")
        user.timezone = body.timezone
    session.commit()
    return _me_json(user)


@router.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response
