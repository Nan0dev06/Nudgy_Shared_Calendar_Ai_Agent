"""Auth endpoints: Google OAuth web flow = the app's login.

GET  /auth/google/login     -> 302 to Google's consent screen
GET  /auth/google/callback  -> exchanges code, upserts user + token, sets cookie
GET   /auth/me              -> who am I (or 401)
PATCH /auth/me              -> update display name / timezone
POST  /auth/me/detected-timezone -> the browser reports where it is
POST  /auth/logout          -> clears the cookie
"""
from __future__ import annotations

import logging
import re
import secrets
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    COOKIE_KWARGS, COOKIE_NAME, SESSION_TTL_SECONDS, get_current_user,
    make_session_cookie, resolve_session_user,
)
from app.auth import tokens
from app.auth.google import build_web_flow, get_account_email
from app.auth.microsoft import (
    build_authorize_url,
    exchange_code,
    get_account_email as ms_get_account_email,
)
from app.core.config import APP_BASE_URL, GOOGLE_REDIRECT_URI
from app.core.passwords import hash_password, verify_password
from app.db.models import CalendarAccount, User
from app.db import repo
from app.db.session import get_session
from app.mailer import send_email_safe

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("nudgy.auth")

# Ties a callback back to the /login that started it. Without this, anyone can
# feed a victim's browser an authorization code of their choosing and silently
# log that victim into an ACCOUNT THEY CONTROL — the victim then plans hangouts
# inside the attacker's account. Google hands `state` back untouched, so a value
# only we could have set proves the callback answers our own login.
STATE_COOKIE = "nudgy_oauth_state"
STATE_TTL_SECONDS = 600

# Marks an OAuth round-trip started from "Connect another calendar" (a logged-in
# user adding a calendar) rather than "Sign in". The callback reuses the SAME
# provider redirect URI for both — the URI is fixed in the provider console — so
# this cookie is how the one callback tells the two flows apart. In connect mode
# the callback attaches the calendar to the CURRENT session user and does NOT
# issue a new session (no identity switch).
CONNECT_COOKIE = "nudgy_oauth_connect"


@router.get("/google/login")
def google_login():
    flow = build_web_flow(GOOGLE_REDIRECT_URI)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    response = RedirectResponse(url)
    # SameSite=Lax still sends this on Google's top-level redirect back to us.
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
    return response


@router.get("/google/connect")
def google_connect(user: User = Depends(get_current_user)):
    """Add a Google calendar to the ALREADY-logged-in user (not a login). Same
    consent screen as sign-in; the CONNECT_COOKIE tells the shared callback to
    attach it to this user instead of switching identity."""
    flow = build_web_flow(GOOGLE_REDIRECT_URI)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    response = RedirectResponse(url)
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
    response.set_cookie(CONNECT_COOKIE, "1", max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
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
    try:
        flow = build_web_flow(GOOGLE_REDIRECT_URI)
        flow.fetch_token(code=code)
        creds = flow.credentials
        email = get_account_email(creds)

        # Connect mode: attach this calendar to the current user and keep their
        # session as-is — never find-or-create by the provider's email (that's
        # login, and here it would silently switch identity).
        if request.cookies.get(CONNECT_COOKIE):
            current = resolve_session_user(request.cookies.get(COOKIE_NAME), session)
            if current is not None:
                repo.upsert_calendar_account(session, current, "google", email, creds.to_json())
                return _finish_connect("google")

        user = repo.login_with_google(session, email, creds.to_json())
    except Exception:
        # e.g. Google Calendar API not enabled on the Cloud project, a reused
        # code (invalid_grant), or a bad client secret — a clear retry beats a 500.
        log.exception("Google sign-in callback failed")
        return _auth_error_redirect("google")

    # logged in — back to the app with the signed cookie set. max_age makes the
    # browser drop it at the same TTL the server enforces (see deps.SESSION_TTL_SECONDS).
    response = RedirectResponse("/")
    response.set_cookie(
        COOKIE_NAME, make_session_cookie(user.id),
        max_age=SESSION_TTL_SECONDS, **COOKIE_KWARGS,
    )
    response.delete_cookie(STATE_COOKIE)  # single use
    response.delete_cookie(CONNECT_COOKIE)  # clean up if it lingered
    return response


# ------------------------------------------------------------- microsoft oauth
# Same shape as Google, and it REUSES the STATE_COOKIE CSRF machinery above — one
# cookie name, one check. Microsoft has no OAuth library generating `state`, so
# we mint it ourselves and thread it into the authorize URL.

@router.get("/microsoft/login")
def microsoft_login():
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(build_authorize_url(state))
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
    return response


@router.get("/microsoft/connect")
def microsoft_connect(user: User = Depends(get_current_user)):
    """Add a Microsoft/Outlook calendar to the already-logged-in user (see
    google_connect — same connect-vs-login machinery)."""
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(build_authorize_url(state))
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
    response.set_cookie(CONNECT_COOKIE, "1", max_age=STATE_TTL_SECONDS, **COOKIE_KWARGS)
    return response


@router.get("/microsoft/callback")
def microsoft_callback(request: Request, session: Session = Depends(get_session)):
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
        raise HTTPException(status_code=400, detail="Missing ?code from Microsoft.")
    try:
        token_json = exchange_code(code)
        email = ms_get_account_email(token_json)

        if request.cookies.get(CONNECT_COOKIE):
            current = resolve_session_user(request.cookies.get(COOKIE_NAME), session)
            if current is not None:
                repo.upsert_calendar_account(session, current, "microsoft", email, token_json)
                return _finish_connect("microsoft")

        user = repo.login_with_microsoft(session, email, token_json)
    except Exception:
        # e.g. a bad/expired MS_CLIENT_SECRET (401 from the token endpoint) or a
        # Graph hiccup — recoverable retry beats a 500.
        log.exception("Microsoft sign-in callback failed")
        return _auth_error_redirect("microsoft")

    response = _issue_session(user)          # signed session cookie -> "/"
    response.delete_cookie(STATE_COOKIE)     # single use
    response.delete_cookie(CONNECT_COOKIE)
    return response


def _me_json(user: User) -> dict:
    return {
        "email": user.email,
        "timezone": user.timezone,
        "timezone_auto": user.timezone_auto,
        "display_name": user.display_name,
        "calendar_connected": user.calendar_connected,
        "email_verified": user.email_verified,
    }


def _valid_timezone(name: str) -> bool:
    """A real IANA zone name. ZoneInfo also accepts paths like '../etc/passwd'
    on some systems, so the shape is checked before the lookup."""
    if not name or len(name) > 60 or ".." in name or name.startswith("/"):
        return False
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _me_json(user)


class PatchMeBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=60)
    # send true to hand the timezone back to auto-detection
    timezone_auto: bool | None = None


@router.patch("/me")
def patch_me(
    body: PatchMeBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    if body.timezone is not None:
        if not _valid_timezone(body.timezone):
            raise HTTPException(status_code=400, detail="Unknown timezone (use an IANA name like Asia/Beirut).")
        user.timezone = body.timezone
        # typing one in is an explicit choice: stop tracking the device unless
        # the same request also asks to go back to auto
        user.timezone_auto = False
    if body.timezone_auto is not None:
        user.timezone_auto = body.timezone_auto
    session.commit()
    return _me_json(user)


class DetectedTimezoneBody(BaseModel):
    timezone: str = Field(max_length=60)


@router.post("/me/detected-timezone")
def detected_timezone(
    body: DetectedTimezoneBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """"My browser thinks it is in this zone." Called on every app boot.

    The server can't infer a timezone on its own — a UTC timestamp and an IP
    are both bad guesses — so the one process that actually knows reports it.
    Ignored (200, unchanged) once the user has chosen a zone by hand, which is
    what makes this safe to call unconditionally: no confirmation prompt, and
    a stale zone fixes itself the next time that person opens the app.
    """
    if not _valid_timezone(body.timezone):
        # a browser reporting something we can't resolve isn't the user's
        # problem — keep whatever we had rather than 400ing on every boot
        log.warning("Ignoring unusable detected timezone %r", body.timezone[:60])
        return _me_json(user)
    if user.timezone_auto and user.timezone != body.timezone:
        log.info("User %s timezone %s -> %s (detected)", user.id, user.timezone, body.timezone)
        user.timezone = body.timezone
        session.commit()
    return _me_json(user)


@router.get("/me/drafts")
def get_drafts(user: User = Depends(get_current_user)):
    """Server-side draft storage so unfinished things survive across devices.
    The payload is an opaque JSON array the frontend owns."""
    import json
    try:
        return {"drafts": json.loads(user.drafts_json) if user.drafts_json else []}
    except ValueError:
        return {"drafts": []}


class DraftsBody(BaseModel):
    drafts: list[dict] = Field(max_length=30)


@router.put("/me/drafts")
def put_drafts(
    body: DraftsBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    import json
    raw = json.dumps(body.drafts)
    if len(raw) > 20_000:
        raise HTTPException(status_code=400, detail="Drafts too large.")
    user.drafts_json = raw
    session.commit()
    return {"ok": True, "drafts": body.drafts}


@router.get("/me/memory")
def get_memory(user: User = Depends(get_current_user)):
    """Freeform notes the user teaches the agent. Opaque JSON array of strings
    the frontend owns; the agent prompt reads it (agent/prompt.py)."""
    import json
    try:
        return {"memory": json.loads(user.memory_json) if user.memory_json else []}
    except ValueError:
        return {"memory": []}


class MemoryBody(BaseModel):
    memory: list[str] = Field(max_length=50)


@router.put("/me/memory")
def put_memory(
    body: MemoryBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    import json
    notes = [n.strip() for n in body.memory if n and n.strip()]
    raw = json.dumps(notes)
    if len(raw) > 20_000:
        raise HTTPException(status_code=400, detail="Memory too large.")
    user.memory_json = raw
    session.commit()
    return {"ok": True, "memory": notes}


@router.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


# ============================================================ calendar accounts
# Identity is decoupled from calendars: one User can have several CalendarAccounts
# (Google/Outlook, one per connection). These endpoints manage the connected set
# — colour, sync mode, which is primary (writable), and disconnect. Adding a new
# one goes through the OAuth *connect* flow above, not here.

_SYNC_SETTINGS = {"none", "one_way", "two_way"}


def _calendar_json(a: CalendarAccount) -> dict:
    return {
        "id": a.id,
        "provider": a.provider,
        "external_email": a.external_email,
        "color": a.color,
        "sync_setting": a.sync_setting,
        "is_primary": a.is_primary,
    }


@router.get("/me/calendars")
def list_calendars(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """The user's connected calendars (those holding a token)."""
    return [_calendar_json(a) for a in repo.get_calendar_accounts(session, user)]


class PatchCalendarBody(BaseModel):
    color: str | None = Field(default=None, max_length=32)
    sync_setting: str | None = Field(default=None, max_length=20)
    # only `true` is meaningful — you PROMOTE a calendar to primary; there's always
    # exactly one, so it isn't something you toggle off directly.
    is_primary: bool | None = None


@router.patch("/me/calendars/{account_id}")
def patch_calendar(
    account_id: int,
    body: PatchCalendarBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    account = repo.get_calendar_account(session, user, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="No such calendar.")
    if body.sync_setting is not None:
        if body.sync_setting not in _SYNC_SETTINGS:
            raise HTTPException(status_code=400, detail="sync_setting must be none, one_way, or two_way.")
        repo.set_account_sync_setting(session, account, body.sync_setting)
    if body.color is not None:
        repo.set_account_color(session, account, body.color or None)  # "" clears it
    if body.is_primary:
        repo.set_primary_calendar_account(session, user, account)
    return _calendar_json(account)


@router.delete("/me/calendars/{account_id}")
def disconnect_calendar(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    account = repo.get_calendar_account(session, user, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="No such calendar.")
    repo.disconnect_calendar_account(session, user, account)
    return {"ok": True}


# ============================================================ email / password
# Identity decoupled from calendars: a person can sign in with email + password
# (or a magic-link) and use Nudgy with NO external calendar connected. Google
# stays as social sign-in that also grants a calendar in one step.
#
# Enumeration safety: register / magic-link / reset-request always return the
# same generic message whether or not the email exists, so the API can't be used
# to probe who has an account. Password login uses one "wrong email or password"
# for both cases and burns equal time on a missing user.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# A throwaway hash so an unknown-user login still runs scrypt once — equalizing
# response time so timing can't reveal whether an email is registered.
_TIMING_EQUALIZER_HASH = hash_password(secrets.token_urlsafe(16))


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _issue_session(user: User, redirect_to: str = "/") -> RedirectResponse:
    """Log a user in (set the signed session cookie) and send them to the app."""
    response = RedirectResponse(redirect_to)
    response.set_cookie(
        COOKIE_NAME, make_session_cookie(user.id),
        max_age=SESSION_TTL_SECONDS, **COOKIE_KWARGS,
    )
    return response


def _finish_connect(provider: str) -> RedirectResponse:
    """End a successful *connect* round-trip: back to the app's Calendars settings
    (the SPA reads ?tab=calendars), single-use cookies cleared, session untouched."""
    response = RedirectResponse(f"/?tab=calendars&connected={provider}")
    response.delete_cookie(STATE_COOKIE)
    response.delete_cookie(CONNECT_COOKIE)
    return response


def _auth_error_redirect(provider: str) -> RedirectResponse:
    """A provider call blew up mid-callback (bad creds, an API not enabled on the
    Google Cloud/Azure side, a network hiccup). Send the user back to the sign-in
    screen with a clear, recoverable message instead of a raw 500. The real cause
    is logged server-side (log.exception) for the operator to read."""
    response = RedirectResponse(f"/?auth_error={provider}")
    response.delete_cookie(STATE_COOKIE)
    response.delete_cookie(CONNECT_COOKIE)
    return response


def _send_verification(user: User) -> None:
    token = tokens.make_token(tokens.VERIFY_EMAIL, {"uid": user.id})
    link = f"{APP_BASE_URL}/auth/verify?token={token}"
    send_email_safe(
        user.email, "Verify your Nudgy email",
        f"Welcome to Nudgy! Confirm your email to finish signing up:\n\n{link}\n\n"
        "This link expires in 24 hours. If you didn't sign up, ignore this.",
    )


class RegisterBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(min_length=8, max_length=200)


@router.post("/register")
def register(body: RegisterBody, session: Session = Depends(get_session)):
    """Create an email/password identity and email a verification link. Returns a
    generic message regardless of whether the email already exists."""
    email = _normalize_email(body.email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    generic = {"ok": True, "message": "If that email is new, check your inbox for a verification link."}
    user = repo.get_user_by_email(session, email)
    if user is None:
        user = repo.create_password_user(session, email, hash_password(body.password))
        _send_verification(user)
    elif (not user.email_verified and user.password_hash is not None
          and not user.calendar_accounts):
        # An unclaimed, unverified password account — safe to let them re-register
        # (set the new password, resend the link). Verified or social-linked
        # accounts are left untouched so this can't hijack a real account.
        repo.set_password(session, user, hash_password(body.password))
        _send_verification(user)
    return generic


@router.get("/verify")
def verify_email(token: str, session: Session = Depends(get_session)):
    """Consume an email-verification link, mark the address verified, and sign in."""
    data = tokens.read_token(tokens.VERIFY_EMAIL, token)
    if not data:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    user = repo.get_user(session, data.get("uid"))
    if user is None:
        raise HTTPException(status_code=400, detail="Unknown account.")
    if not user.email_verified:
        repo.mark_email_verified(session, user)
    return _issue_session(user)


class LoginBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


@router.post("/login")
def login(body: LoginBody, session: Session = Depends(get_session)):
    email = _normalize_email(body.email)
    user = repo.get_user_by_email(session, email)
    if user is None:
        verify_password(body.password, _TIMING_EQUALIZER_HASH)  # equalize timing
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Please verify your email first — check your inbox.")
    response = JSONResponse(_me_json(user))
    response.set_cookie(
        COOKIE_NAME, make_session_cookie(user.id),
        max_age=SESSION_TTL_SECONDS, **COOKIE_KWARGS,
    )
    return response


class EmailBody(BaseModel):
    email: str = Field(max_length=200)


@router.post("/magic-link")
def request_magic_link(body: EmailBody, session: Session = Depends(get_session)):
    """Email a one-click sign-in link. Generic response (no enumeration)."""
    email = _normalize_email(body.email)
    user = repo.get_user_by_email(session, email)
    if user is not None:
        token = tokens.make_token(tokens.MAGIC_LOGIN, {"uid": user.id})
        link = f"{APP_BASE_URL}/auth/magic?token={token}"
        send_email_safe(
            email, "Your Nudgy sign-in link",
            f"Click to sign in to Nudgy:\n\n{link}\n\nThis link expires in 15 minutes.",
        )
    return {"ok": True, "message": "If that email has an account, a sign-in link is on its way."}


@router.get("/magic")
def consume_magic_link(token: str, session: Session = Depends(get_session)):
    data = tokens.read_token(tokens.MAGIC_LOGIN, token)
    if not data:
        raise HTTPException(status_code=400, detail="This sign-in link is invalid or has expired.")
    user = repo.get_user(session, data.get("uid"))
    if user is None:
        raise HTTPException(status_code=400, detail="Unknown account.")
    # Clicking the emailed link proves control of the address, so it verifies too.
    if not user.email_verified:
        repo.mark_email_verified(session, user)
    return _issue_session(user)


@router.post("/password/reset-request")
def request_password_reset(body: EmailBody, session: Session = Depends(get_session)):
    email = _normalize_email(body.email)
    user = repo.get_user_by_email(session, email)
    if user is not None and user.password_hash is not None:
        token = tokens.make_token(
            tokens.PASSWORD_RESET,
            {"uid": user.id, "stamp": tokens.password_stamp(user.password_hash)},
        )
        # Land on the SPA (served at "/"), which reads ?mode=reset&token and shows
        # the reset form. `/auth/reset` has no route — it would 404 before the app
        # ever loaded. (verify/magic differ: those ARE backend GETs that redirect.)
        link = f"{APP_BASE_URL}/?mode=reset&token={token}"
        send_email_safe(
            email, "Reset your Nudgy password",
            f"Reset your password here:\n\n{link}\n\nExpires in 1 hour. "
            "If you didn't ask for this, you can ignore it.",
        )
    return {"ok": True, "message": "If that email has a password account, a reset link is on its way."}


class ResetBody(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=200)


@router.post("/password/reset")
def reset_password(body: ResetBody, session: Session = Depends(get_session)):
    """Set a new password from a reset link. The token embeds a fingerprint of the
    old password hash, so completing a reset invalidates every outstanding reset
    link for the account."""
    data = tokens.read_token(tokens.PASSWORD_RESET, body.token)
    if not data:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    user = repo.get_user(session, data.get("uid"))
    if user is None or data.get("stamp") != tokens.password_stamp(user.password_hash):
        raise HTTPException(status_code=400, detail="This reset link is no longer valid.")
    repo.set_password(session, user, hash_password(body.password))
    if not user.email_verified:
        repo.mark_email_verified(session, user)  # controlling the inbox verifies it
    return {"ok": True, "message": "Password updated — you can now sign in."}
