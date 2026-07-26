"""scrypt password hashing (app/core/passwords.py): round-trips, rejects wrong
passwords, salts each hash uniquely, and never raises on malformed input."""
from app.core.passwords import hash_password, verify_password


def test_hash_is_not_the_plaintext_and_verifies():
    h = hash_password("correct horse battery staple")
    assert h.startswith("scrypt$")
    assert "correct horse" not in h
    assert verify_password("correct horse battery staple", h) is True


def test_wrong_password_fails():
    h = hash_password("s3cret-pw")
    assert verify_password("s3cret-pX", h) is False


def test_each_hash_uses_a_fresh_salt():
    a = hash_password("same-pw")
    b = hash_password("same-pw")
    assert a != b  # different salts -> different encodings
    assert verify_password("same-pw", a)
    assert verify_password("same-pw", b)


def test_none_and_malformed_stored_values_return_false_not_raise():
    assert verify_password("x", None) is False
    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-valid-hash") is False
    assert verify_password("x", "scrypt$bad$fields") is False
    assert verify_password("x", "bcrypt$16384$8$1$aa$bb") is False  # wrong scheme
