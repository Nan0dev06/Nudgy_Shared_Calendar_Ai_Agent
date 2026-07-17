"""Auth endpoints: Google OAuth web flow = the app's login.

GET  /auth/google/login     -> 302 to Google's consent screen
GET  /auth/google/callback  -> exchanges code, upserts user + token, sets cookie
GET   /auth/me              -> who am I (or 401)
PATCH /auth/me              -> update display name / timezone
POST  /auth/logout          -> clears the cookie
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, get_current_user, make_session_cookie
from app.auth.google import build_web_flow, get_account_email
from app.core.config import GOOGLE_REDIRECT_URI
from app.db.models import User
from app.db import repo
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/login")
def google_login():
    flow = build_web_flow(GOOGLE_REDIRECT_URI)
    url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(url)


@router.get("/google/callback")
def google_callback(request: Request, session: Session = Depends(get_session)):
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
    response.set_cookie(COOKIE_NAME, make_session_cookie(user.id), httponly=True)
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
