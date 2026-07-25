"""OAuth tokens are encrypted at rest: round-trips cleanly, legacy plaintext
reads through, and the ORM column stores ciphertext while returning plaintext
(app/core/crypto.py + app/db/types.py)."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core import crypto
from app.db.models import Base, User

SECRET = '{"refresh_token": "super-secret-value", "token": "abc"}'


def test_encrypt_then_decrypt_round_trips():
    enc = crypto.encrypt(SECRET)
    assert enc != SECRET
    assert enc.startswith("enc:v1:")
    assert "super-secret-value" not in enc
    assert crypto.decrypt(enc) == SECRET


def test_none_stays_none():
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None


def test_legacy_plaintext_reads_through():
    # a value written before encryption existed has no prefix -> returned as-is
    assert crypto.decrypt(SECRET) == SECRET


def test_undecryptable_value_returns_none():
    # right prefix, garbage body (e.g. key rotated) -> None, never a crash
    assert crypto.decrypt("enc:v1:not-a-real-token") is None


def test_orm_stores_ciphertext_but_returns_plaintext():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        u = User(email="sam@x.com", token_json=SECRET)
        s.add(u)
        s.commit()
        uid = u.id

        # the RAW column (same connection/in-memory DB) holds ciphertext
        raw = s.execute(text("SELECT token_json FROM users WHERE id=:i"), {"i": uid}).scalar()
        assert raw.startswith("enc:v1:")
        assert "super-secret-value" not in raw

        # a fresh load through the ORM decrypts transparently
        s.expire(u)
        assert u.token_json == SECRET
