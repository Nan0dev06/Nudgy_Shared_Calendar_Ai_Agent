"""Stateless, signed, time-limited tokens for the email auth flows.

One serializer per PURPOSE (verify-email, magic-login, password-reset), each
salted differently so a token minted for one flow can never be replayed in
another. Tokens are signed with SECRET_KEY (itsdangerous), carry a small JSON
payload, and expire via max_age at read time.

These are stateless — not stored server-side. Reset tokens additionally embed a
`stamp` derived from the user's current password hash, so completing a reset (or
any password change) invalidates every outstanding reset token for that user:
the stamp no longer matches. Verify/magic tokens rely on their short TTL; reusing
a verify token is idempotent (the account is already verified).
"""
from __future__ import annotations

import hashlib

from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import SECRET_KEY

# Purpose -> its own serializer (distinct salt = no cross-flow replay).
VERIFY_EMAIL = "verify-email"
MAGIC_LOGIN = "magic-login"
PASSWORD_RESET = "password-reset"

_serializers = {
    purpose: URLSafeTimedSerializer(SECRET_KEY, salt=f"nudgy-{purpose}")
    for purpose in (VERIFY_EMAIL, MAGIC_LOGIN, PASSWORD_RESET)
}

# Default lifetimes (seconds).
TTL = {
    VERIFY_EMAIL: 24 * 3600,
    MAGIC_LOGIN: 15 * 60,
    PASSWORD_RESET: 60 * 60,
}


def password_stamp(password_hash: str | None) -> str:
    """A short fingerprint of the current password hash. Embedded in reset tokens
    so they stop working the moment the password changes."""
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def make_token(purpose: str, payload: dict) -> str:
    return _serializers[purpose].dumps(payload)


def read_token(purpose: str, token: str, max_age: int | None = None) -> dict | None:
    """Verify signature + expiry and return the payload, or None if the token is
    forged, tampered, expired, or for the wrong purpose."""
    try:
        return _serializers[purpose].loads(token, max_age=max_age or TTL[purpose])
    except BadData:
        return None
