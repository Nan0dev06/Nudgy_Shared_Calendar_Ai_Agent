"""Shared FastAPI dependencies: DB session + current user from the session cookie.

Auth model (hackathon-simple, no passwords):
- Google OAuth IS the login. After the callback we set a signed cookie holding
  the user id. itsdangerous signs it with SECRET_KEY so it can't be forged.
- get_current_user reads and verifies that cookie on every request.
"""
from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.core.config import GOOGLE_REDIRECT_URI, SECRET_KEY
from app.db.models import User
from app.db import repo
from app.db.session import get_session

COOKIE_NAME = "nudgy_session"
# Sessions expire so a leaked/stolen cookie (or one signed with an old key) can't
# live forever. Timed signer stamps the issue time; get_current_user rejects
# anything older than this. 30 days keeps the "stay signed in" feel for a
# consumer app while still bounding a stolen cookie's usefulness.
SESSION_TTL_SECONDS = 30 * 24 * 3600
_signer = URLSafeTimedSerializer(SECRET_KEY, salt="nudgy-session")

# The cookie IS the login, so it must never cross the wire in clear text. It
# can't be Secure on http://localhost though — the browser would silently drop
# it and dev logins would just stop working. The redirect URI tells us which
# world we're in: https means deployed.
COOKIE_SECURE = GOOGLE_REDIRECT_URI.startswith("https://")

# Cookie args shared by every place that sets an auth cookie, so a flag can
# never be tightened in one spot and forgotten in another.
COOKIE_KWARGS = {"httponly": True, "secure": COOKIE_SECURE, "samesite": "lax"}


def make_session_cookie(user_id: int) -> str:
    return _signer.dumps({"user_id": user_id})


def get_current_user(
    nudgy_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    session: Session = Depends(get_session),
) -> User:
    if not nudgy_session:
        raise HTTPException(status_code=401, detail="Not logged in. Connect Google first.")
    try:
        data = _signer.loads(nudgy_session, max_age=SESSION_TTL_SECONDS)
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Your session expired. Please sign in again.")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid session cookie.")
    user = repo.get_user(session, data.get("user_id"))
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user.")
    return user
