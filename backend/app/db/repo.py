"""Repository layer: every DB query the app needs, in one file.

Keeping SQLAlchemy calls here (instead of scattered through the API and agent)
means there is a single place to read to understand what data operations
exist, and a single place to debug when a query misbehaves.
"""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Group, Membership, Poll, User, Vote


# ----------------------------------------------------------------- users

def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def upsert_user_token(session: Session, email: str, token_json: str) -> User:
    """Create the user if new, and store/refresh their OAuth token."""
    user = get_user_by_email(session, email)
    if user is None:
        user = User(email=email, token_json=token_json)
        session.add(user)
    else:
        user.token_json = token_json
    session.commit()
    return user


def set_user_token(session: Session, user: User, token_json: str) -> None:
    """Persist a refreshed token back to the DB (used after a silent refresh)."""
    user.token_json = token_json
    session.commit()


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


# ----------------------------------------------------------------- groups

def _new_invite_code() -> str:
    # 6 chars, unambiguous uppercase+digits, easy to type/share
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def create_group(session: Session, name: str, creator: User) -> Group:
    """Create a group with a unique invite code and add creator as first member."""
    code = _new_invite_code()
    while session.scalar(select(Group).where(Group.invite_code == code)):
        code = _new_invite_code()
    group = Group(name=name, invite_code=code, created_by=creator.id)
    session.add(group)
    session.flush()  # assign group.id
    session.add(Membership(user_id=creator.id, group_id=group.id))
    session.commit()
    return group


def get_group_by_code(session: Session, code: str) -> Group | None:
    return session.scalar(select(Group).where(Group.invite_code == code.upper()))


def add_member(session: Session, group: Group, user: User) -> Membership:
    """Add user to group; idempotent (returns existing membership if present)."""
    existing = session.scalar(
        select(Membership).where(
            Membership.group_id == group.id, Membership.user_id == user.id
        )
    )
    if existing:
        return existing
    membership = Membership(user_id=user.id, group_id=group.id)
    session.add(membership)
    session.commit()
    return membership


def get_group_members(session: Session, group_id: int) -> list[User]:
    """All users in a group, creator first then join order."""
    rows = session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.group_id == group_id)
        .order_by(Membership.joined_at)
    )
    return list(rows)


def get_user_groups(session: Session, user: User) -> list[Group]:
    rows = session.scalars(
        select(Group)
        .join(Membership, Membership.group_id == Group.id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.joined_at)
    )
    return list(rows)


# ----------------------------------------------------------------- polls

def create_poll(
    session: Session,
    group: Group,
    creator: User,
    title: str,
    slot_start_utc,
    slot_end_utc,
    min_yes: int,
    location: str | None = None,
) -> Poll:
    poll = Poll(
        group_id=group.id, created_by=creator.id, title=title, location=location,
        slot_start_utc=slot_start_utc, slot_end_utc=slot_end_utc, min_yes=min_yes,
    )
    session.add(poll)
    session.commit()
    return poll


def get_poll(session: Session, poll_id: int) -> Poll | None:
    return session.get(Poll, poll_id)


def get_group_polls(session: Session, group_id: int, only_open: bool = False) -> list[Poll]:
    q = select(Poll).where(Poll.group_id == group_id).order_by(Poll.created_at.desc())
    if only_open:
        q = q.where(Poll.status == "open")
    return list(session.scalars(q))


def cast_vote(session: Session, poll: Poll, user: User, yes: bool) -> Vote:
    """Record a vote; voting again replaces the previous vote."""
    vote = session.scalar(
        select(Vote).where(Vote.poll_id == poll.id, Vote.user_id == user.id)
    )
    if vote is None:
        vote = Vote(poll_id=poll.id, user_id=user.id, yes=yes)
        session.add(vote)
    else:
        vote.yes = yes
    session.commit()
    return vote


def get_poll_votes(session: Session, poll: Poll) -> dict[str, bool]:
    """email -> yes/no for everyone who has voted on this poll."""
    rows = session.scalars(select(Vote).where(Vote.poll_id == poll.id))
    return {v.user.email: v.yes for v in rows}


def set_poll_status(session: Session, poll: Poll, status: str) -> None:
    poll.status = status
    session.commit()


def mark_poll_booked(session: Session, poll: Poll, event_link: str | None) -> None:
    poll.booked = True
    poll.event_link = event_link
    session.commit()
