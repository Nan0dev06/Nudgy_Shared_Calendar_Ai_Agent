"""Password hashing with scrypt — a memory-hard KDF from the standard library.

No third-party dependency (no bcrypt/argon2/passlib): hashlib.scrypt ships with
Python and OpenSSL. Each hash carries its own random salt and the parameters it
was made with, so params can be raised later without breaking old hashes. The
stored form is a self-describing string:

    scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>

Verification is constant-time (hmac.compare_digest) and never raises on a
malformed stored value — it just returns False.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

# Cost parameters. n is the CPU/memory cost (must be a power of two); these use
# ~16 MB per hash — comfortably interactive while being expensive to brute-force.
_N = 2 ** 14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
# scrypt needs ~128*n*r bytes; give OpenSSL generous headroom over that.
_MAXMEM = 128 * _N * _R * 3


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode())


def hash_password(password: str) -> str:
    """Hash a plaintext password into the self-describing stored form."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    """True iff `password` matches the stored hash. False on any malformed or
    missing stored value — never raises."""
    if not stored:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p,
            dklen=len(expected), maxmem=128 * n * r * 3,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
