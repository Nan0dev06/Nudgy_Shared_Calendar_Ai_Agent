"""Custom SQLAlchemy column types.

EncryptedString transparently encrypts on write and decrypts on read, so the
rest of the app keeps handling plaintext and never has to remember to encrypt.
Underlying storage is still a plain String/VARCHAR, so applying it to an
existing column needs no schema migration.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.core.crypto import decrypt, encrypt


class EncryptedString(TypeDecorator):
    """Store a string encrypted at rest (see core/crypto.py)."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # Python -> DB
        return encrypt(value)

    def process_result_value(self, value, dialect):  # DB -> Python
        return decrypt(value)
