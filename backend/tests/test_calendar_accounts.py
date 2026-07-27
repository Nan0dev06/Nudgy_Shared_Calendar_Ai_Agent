"""Identity/calendar split (Phase 1): OAuth tokens live on CalendarAccount, one
per connected calendar, and availability unions busy across ALL of a user's
calendars (one person = one free/busy truth).

Covers: token encryption at rest on the new table, login_with_google identity
find-or-create, primary-account selection, the calendar_connected property, the
one-time legacy-token backfill (idempotent), and the union of busy time across a
member's multiple calendars.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.agent import availability
from app.db import repo
from app.db.models import Base, CalendarAccount, User
from app.db.session import _backfill_calendar_accounts

SECRET = '{"refresh_token": "super-secret-value", "token": "abc"}'


@pytest.fixture
def Session():
    """Fresh in-memory DB per test, schema created (mirrors test_crypto)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# --------------------------------------------------------------- encryption

def test_account_token_encrypted_at_rest():
    """The new token column gets the same at-rest protection User.token_json had."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        u = User(email="sam@x.com")
        s.add(u)
        s.commit()
        acct = CalendarAccount(
            user_id=u.id, provider="google", external_email="sam@x.com",
            token_json=SECRET,
        )
        s.add(acct)
        s.commit()
        aid = acct.id

        raw = s.execute(
            text("SELECT token_json FROM calendar_accounts WHERE id=:i"), {"i": aid}
        ).scalar()
        assert raw.startswith("enc:v1:")
        assert "super-secret-value" not in raw

        s.expire(acct)
        assert acct.token_json == SECRET


# ----------------------------------------------------------- login_with_google

def test_login_with_google_creates_identity_and_primary_account(Session):
    with Session() as s:
        user = repo.login_with_google(s, "amir@x.com", SECRET)
        assert user.id is not None
        accounts = repo.get_calendar_accounts(s, user)
        assert len(accounts) == 1
        acct = accounts[0]
        assert acct.provider == "google"
        assert acct.external_email == "amir@x.com"
        assert acct.token_json == SECRET
        assert acct.is_primary is True
        assert acct.sync_setting == "two_way"  # v1 default
        assert user.calendar_connected is True


def test_social_login_clears_an_unverified_password(Session):
    """Account pre-hijacking defense: if an attacker pre-registered the email with
    a password but never verified it, a later social login by the real owner wipes
    that unproven password so it can't be used to log in."""
    from app.core.passwords import hash_password, verify_password
    with Session() as s:
        victim = repo.create_password_user(s, "victim@x.com", hash_password("attacker-knows-this"))
        assert victim.email_verified is False
        # the real owner signs in with Google (proving they control the address)
        repo.login_with_google(s, "victim@x.com", '{"token":"g"}')
        s.refresh(victim)
        assert victim.email_verified is True
        assert victim.password_hash is None
        assert verify_password("attacker-knows-this", victim.password_hash) is False


def test_social_login_keeps_an_already_verified_password(Session):
    """A password the owner already verified is genuine — a later social login
    must NOT wipe it."""
    from app.core.passwords import hash_password, verify_password
    with Session() as s:
        user = repo.create_password_user(s, "sam@x.com", hash_password("my-real-pw"))
        repo.mark_email_verified(s, user)  # sam confirmed their email
        repo.login_with_microsoft(s, "sam@x.com", SECRET)
        s.refresh(user)
        assert verify_password("my-real-pw", user.password_hash) is True


def test_login_with_google_is_idempotent_and_refreshes(Session):
    with Session() as s:
        u1 = repo.login_with_google(s, "amir@x.com", SECRET)
        u2 = repo.login_with_google(s, "amir@x.com", '{"token": "REFRESHED"}')
        assert u1.id == u2.id  # same identity, not a second user
        accounts = repo.get_calendar_accounts(s, u2)
        assert len(accounts) == 1  # same calendar, token replaced not duplicated
        assert accounts[0].token_json == '{"token": "REFRESHED"}'
        assert s.query(User).count() == 1


# --------------------------------------------------------- account selection

def test_calendar_connected_is_false_without_a_token(Session):
    with Session() as s:
        u = User(email="sam@x.com")
        s.add(u)
        s.commit()
        assert u.calendar_connected is False
        # a stub account with no token still doesn't count as connected
        s.add(CalendarAccount(user_id=u.id, provider="google",
                              external_email="sam@x.com", token_json=None))
        s.commit()
        s.refresh(u)
        assert u.calendar_connected is False
        assert repo.get_calendar_accounts(s, u) == []


def test_primary_is_the_first_connected_and_second_does_not_steal_it(Session):
    with Session() as s:
        user = repo.login_with_google(s, "amir@x.com", SECRET)
        # a second calendar on the same identity (e.g. a work account)
        repo.upsert_calendar_account(s, user, "google", "amir@work.com", '{"token":"w"}')
        accounts = repo.get_calendar_accounts(s, user)
        assert len(accounts) == 2
        primary = repo.get_primary_calendar_account(s, user)
        assert primary.external_email == "amir@x.com"  # first one stays default
        assert sum(a.is_primary for a in accounts) == 1


def test_primary_falls_back_to_earliest_when_no_flag(Session):
    with Session() as s:
        u = User(email="sam@x.com")
        s.add(u)
        s.commit()
        # two accounts, neither flagged primary (shouldn't happen via the repo,
        # but the selector must still be deterministic)
        s.add(CalendarAccount(user_id=u.id, provider="google",
                              external_email="a@x.com", token_json='{"t":1}'))
        s.add(CalendarAccount(user_id=u.id, provider="google",
                              external_email="b@x.com", token_json='{"t":2}'))
        s.commit()
        s.refresh(u)
        assert repo.get_primary_calendar_account(s, u).external_email == "a@x.com"


def test_no_accounts_means_no_primary(Session):
    with Session() as s:
        u = User(email="sam@x.com")
        s.add(u)
        s.commit()
        assert repo.get_primary_calendar_account(s, u) is None


# ------------------------------------------------------------------ backfill

def test_backfill_moves_legacy_token_and_is_idempotent(Session):
    with Session() as s:
        u = User(email="legacy@x.com", token_json=SECRET)  # pre-split token
        s.add(u)
        s.commit()
        uid = u.id
        # the raw (encrypted) column value the backfill will copy verbatim
        legacy_raw = s.execute(
            text("SELECT token_json FROM users WHERE id=:i"), {"i": uid}
        ).scalar()

    _backfill_calendar_accounts(Session().get_bind())

    with Session() as s:
        u = s.get(User, uid)
        accounts = repo.get_calendar_accounts(s, u)
        assert len(accounts) == 1
        acct = accounts[0]
        assert acct.provider == "google"
        assert acct.external_email == "legacy@x.com"
        assert acct.is_primary is True
        assert acct.token_json == SECRET  # decrypts back to the original
        # copied ciphertext verbatim — no decrypt/re-encrypt round-trip
        acct_raw = s.execute(
            text("SELECT token_json FROM calendar_accounts WHERE id=:i"), {"i": acct.id}
        ).scalar()
        assert acct_raw == legacy_raw

    # running it again is a no-op — no duplicate account
    _backfill_calendar_accounts(Session().get_bind())
    with Session() as s:
        assert s.query(CalendarAccount).count() == 1


def test_backfill_skips_users_without_a_token(Session):
    with Session() as s:
        s.add(User(email="nocal@x.com"))  # never connected a calendar
        s.commit()
    _backfill_calendar_accounts(Session().get_bind())
    with Session() as s:
        assert s.query(CalendarAccount).count() == 0


# --------------------------------------------------- unified availability

def test_busy_unions_across_a_members_calendars(Session, monkeypatch):
    """Being busy on ANY connected calendar makes the person busy: the two
    calendars' busy blocks are merged into one free/busy truth."""
    with Session() as s:
        user = repo.login_with_google(s, "amir@x.com", '{"token":"personal"}')
        repo.upsert_calendar_account(s, user, "google", "amir@work.com", '{"token":"work"}')
        group = repo.create_group(s, "Crew", user)

        # Stub the provider seam: one overlapping busy block per calendar, keyed
        # by that account's token, so no Google call happens.
        base = datetime(2026, 1, 5, tzinfo=timezone.utc)
        blocks = {
            '{"token":"personal"}': [(base.replace(hour=8, minute=30),
                                      base.replace(hour=9, minute=30))],
            '{"token":"work"}':     [(base.replace(hour=9),
                                      base.replace(hour=10))],
        }

        class FakeProvider:
            def __init__(self, token):
                self._token = token
            def get_busy(self, time_min, time_max):
                return blocks[self._token]

        monkeypatch.setattr(availability, "provider_for_account",
                            lambda session, account: FakeProvider(account.token_json))
        from app.calendars.cache import freebusy_cache
        freebusy_cache.clear()  # singleton persists across tests — isolate

        now = base.replace(hour=8)
        members = availability.fetch_busy_for_group(s, group, now, days_ahead=1)
        me = next(m for m in members if m.email == "amir@x.com")
        assert me.connected is True
        # 08:30–09:30 unioned with 09:00–10:00 => one block 08:30–10:00
        assert me.busy == [(base.replace(hour=8, minute=30), base.replace(hour=10))]
