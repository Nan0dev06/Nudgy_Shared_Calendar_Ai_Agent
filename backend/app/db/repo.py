"""Repository layer: every DB query the app needs, in one file.

Keeping SQLAlchemy calls here (instead of scattered through the API and agent)
means there is a single place to read to understand what data operations
exist, and a single place to debug when a query misbehaves.
"""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Group, GroupEvent, InterestVote, Membership, Plan, PlaceReview, TimeRound,
    TimeVote, User,
)


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


# ----------------------------------------------------------------- plans

def create_plan(
    session: Session,
    group: Group,
    host: User,
    title: str,
    slots: list[tuple],          # ordered [(start_utc, end_utc), ...] candidate times
    location: str | None = None,
    expected_count: int | None = None,
) -> Plan:
    """Create a plan with its candidate times queued in order.

    The first time is activated immediately, so the moment a member says yes to
    the interest question they have a time to answer. The host suggested the
    plan, so their interest is recorded as yes up front — they still vote on
    the times themselves.
    """
    plan = Plan(group_id=group.id, created_by=host.id, title=title,
                location=location, expected_count=expected_count)
    session.add(plan)
    session.flush()  # assign plan.id
    for i, (start, end) in enumerate(slots):
        session.add(TimeRound(
            plan_id=plan.id, ordinal=i, slot_start_utc=start, slot_end_utc=end,
            status="active" if i == 0 else "queued",
        ))
    session.add(InterestVote(plan_id=plan.id, user_id=host.id, yes=True))
    session.commit()
    return plan


def append_rounds(session: Session, plan: Plan, slots: list[tuple]) -> list[TimeRound]:
    """Append candidate times to an existing plan (host adding times later).

    Ordinals continue after the current queue. If no round is active or queued
    (a timeless "who's in?" plan, or every earlier time was skipped), the first
    appended time becomes active immediately so the interested cohort has a
    question to answer.
    """
    existing = list(plan.rounds)
    next_ordinal = max((r.ordinal for r in existing), default=-1) + 1
    has_live = any(r.status in ("active", "queued") for r in existing)
    made: list[TimeRound] = []
    for i, (start, end) in enumerate(slots):
        r = TimeRound(
            plan_id=plan.id, ordinal=next_ordinal + i,
            slot_start_utc=start, slot_end_utc=end,
            status="active" if (not has_live and i == 0) else "queued",
        )
        session.add(r)
        made.append(r)
    session.commit()
    session.refresh(plan)
    return made


def get_plan(session: Session, plan_id: int) -> Plan | None:
    return session.get(Plan, plan_id)


def get_group_plans(session: Session, group_id: int, only_open: bool = False) -> list[Plan]:
    q = select(Plan).where(Plan.group_id == group_id).order_by(Plan.created_at.desc())
    if only_open:
        q = q.where(Plan.status == "open")
    return list(session.scalars(q))


def get_active_round(session: Session, plan: Plan) -> TimeRound | None:
    return session.scalar(
        select(TimeRound).where(TimeRound.plan_id == plan.id, TimeRound.status == "active")
    )


def get_next_queued_round(session: Session, plan: Plan) -> TimeRound | None:
    return session.scalar(
        select(TimeRound)
        .where(TimeRound.plan_id == plan.id, TimeRound.status == "queued")
        .order_by(TimeRound.ordinal)
    )


def count_queued_rounds(session: Session, plan: Plan) -> int:
    return len(list(session.scalars(
        select(TimeRound).where(TimeRound.plan_id == plan.id, TimeRound.status == "queued")
    )))


def get_round(session: Session, round_id: int) -> TimeRound | None:
    return session.get(TimeRound, round_id)


def cast_interest(session: Session, plan: Plan, user: User, yes: bool) -> InterestVote:
    """Stage 1 vote; voting again replaces the previous answer."""
    vote = session.scalar(
        select(InterestVote).where(
            InterestVote.plan_id == plan.id, InterestVote.user_id == user.id
        )
    )
    if vote is None:
        vote = InterestVote(plan_id=plan.id, user_id=user.id, yes=yes)
        session.add(vote)
    else:
        vote.yes = yes
    session.commit()
    return vote


def cast_time_vote(session: Session, round_: TimeRound, user: User, yes: bool) -> TimeVote:
    """Stage 2 vote on one candidate time; voting again replaces the previous."""
    vote = session.scalar(
        select(TimeVote).where(TimeVote.round_id == round_.id, TimeVote.user_id == user.id)
    )
    if vote is None:
        vote = TimeVote(round_id=round_.id, user_id=user.id, yes=yes)
        session.add(vote)
    else:
        vote.yes = yes
    session.commit()
    return vote


def get_interest_votes(session: Session, plan: Plan) -> dict[str, bool]:
    """email -> yes/no for everyone who answered the plan's interest question."""
    rows = session.scalars(select(InterestVote).where(InterestVote.plan_id == plan.id))
    return {v.user.email: v.yes for v in rows}


def get_time_votes(session: Session, round_: TimeRound | None) -> dict[str, bool]:
    """email -> yes/no for everyone who voted on this candidate time."""
    if round_ is None:
        return {}
    rows = session.scalars(select(TimeVote).where(TimeVote.round_id == round_.id))
    return {v.user.email: v.yes for v in rows}


def set_plan_status(session: Session, plan: Plan, status: str) -> None:
    plan.status = status
    session.commit()


def set_round_status(session: Session, round_: TimeRound, status: str) -> None:
    round_.status = status
    session.commit()


def mark_round_booked(session: Session, round_: TimeRound, event_link: str | None) -> None:
    round_.booked = True
    round_.event_link = event_link
    session.commit()


# ----------------------------------------------------------------- events

def create_event(session: Session, **kwargs) -> GroupEvent:
    event = GroupEvent(**kwargs)
    session.add(event)
    session.commit()
    return event


def get_event(session: Session, event_id: int) -> GroupEvent | None:
    return session.get(GroupEvent, event_id)


def get_group_events(
    session: Session, group_id: int, member_ids: list[int] | None = None,
) -> list[GroupEvent]:
    """All events + tasks for a group, chronological (undated tasks last).

    When member_ids is given, members' PERSONAL events are included too — from
    ANY group they're in — so their busy time shows on this group's calendar
    (the anonymity masking happens at the API layer, not here).
    """
    rows = list(session.scalars(
        select(GroupEvent).where(
            GroupEvent.group_id == group_id, GroupEvent.personal.is_(False)
        )
    ))
    if member_ids:
        rows += list(session.scalars(
            select(GroupEvent).where(
                GroupEvent.personal.is_(True),
                GroupEvent.created_by.in_(member_ids),
            )
        ))
    return sorted(rows, key=lambda e: (e.start is None, e.start or e.created_at))


def set_event_done(session: Session, event: GroupEvent, done: bool) -> None:
    event.done = done
    session.commit()


def delete_event(session: Session, event: GroupEvent) -> None:
    session.delete(event)
    session.commit()


def set_event_gcal(session: Session, event: GroupEvent, gcal_id: str | None, link: str | None) -> None:
    event.synced = gcal_id is not None
    event.gcal_event_id = gcal_id
    event.gcal_link = link
    session.commit()


# ----------------------------------------------------------------- reviews

def upsert_review(
    session: Session, user: User, place: str, stars: int, text: str | None,
) -> PlaceReview:
    """One review per (user, place); reviewing again replaces the old one."""
    review = session.scalar(
        select(PlaceReview).where(
            PlaceReview.user_id == user.id, PlaceReview.place == place
        )
    )
    if review is None:
        review = PlaceReview(user_id=user.id, place=place, stars=stars, text=text)
        session.add(review)
    else:
        review.stars = stars
        review.text = text
    session.commit()
    return review


def get_review(session: Session, review_id: int) -> PlaceReview | None:
    return session.get(PlaceReview, review_id)


def get_user_reviews(session: Session, user: User) -> list[PlaceReview]:
    return list(session.scalars(
        select(PlaceReview).where(PlaceReview.user_id == user.id)
        .order_by(PlaceReview.created_at.desc())
    ))


def get_reviews_for_users(session: Session, user_ids: list[int]) -> list[PlaceReview]:
    """All reviews by this set of users (the Places page's friends view)."""
    if not user_ids:
        return []
    return list(session.scalars(
        select(PlaceReview).where(PlaceReview.user_id.in_(user_ids))
        .order_by(PlaceReview.created_at.desc())
    ))


def delete_review(session: Session, review: PlaceReview) -> None:
    session.delete(review)
    session.commit()
