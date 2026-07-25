"""Encrypt secrets (OAuth tokens) at rest with Fernet (AES-128-CBC + HMAC).

Why: the DB holds Google/Microsoft refresh tokens — long-lived keys to a user's
calendar. Stored plaintext, a DB leak hands an attacker everyone's calendars.
Encrypting at rest means the DB dump alone is useless without the key.

Key resolution (see core/config.py):
- TOKEN_ENCRYPTION_KEY (a urlsafe-base64 32-byte Fernet key) when set — the
  production path. Rotating it intentionally invalidates old ciphertext.
- else a STABLE key DERIVED from SECRET_KEY, so local dev needs no extra config.
  Acceptable in dev; production should set a dedicated TOKEN_ENCRYPTION_KEY.

Legacy plaintext: tokens written before encryption existed have no prefix, so
decrypt() returns them unchanged (read-through). They become ciphertext the
next time they're written. This makes turning encryption on a no-op migration.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import SECRET_KEY, TOKEN_ENCRYPTION_KEY

# Marks our ciphertext so decrypt() can tell it apart from legacy plaintext and
# from a future scheme (bump v1 -> v2 if the algorithm ever changes).
_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key = TOKEN_ENCRYPTION_KEY
    if not key:
        # Derive a valid 32-byte Fernet key from SECRET_KEY for zero-config dev.
        digest = hashlib.sha256(SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key)


def encrypt(plaintext: str | None) -> str | None:
    """Plaintext -> prefixed ciphertext. None stays None."""
    if plaintext is None:
        return None
    token = _fernet().encrypt(plaintext.encode()).decode()
    return _PREFIX + token


def decrypt(value: str | None) -> str | None:
    """Prefixed ciphertext -> plaintext. Legacy (unprefixed) values read through
    unchanged. None stays None. A value we can't decrypt (wrong/rotated key)
    returns None, so a stale token reads as 'not connected' rather than crashing.
    """
    if value is None:
        return None
    if not value.startswith(_PREFIX):
        return value  # legacy plaintext, written before encryption existed
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return None
