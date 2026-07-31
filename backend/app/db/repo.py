"""Repository layer: every DB query the app needs, in one file.

Keeping SQLAlchemy calls here (instead of scattered through the API and agent)
means there is a single place to read to understand what data operations
exist, and a single place to debug when a query misbehaves.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    CalendarAccount, EventRsvp, Group, GroupEvent, GuestInterestVote,
    GuestTimeVote, InterestVote, Membership, Plan, PlaceReview, PlanGuest,
    TimeRound, TimeVote, User,
)


# ----------------------------------------------------------------- users

def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


# ------------------------------------------------------------- calendar accounts
# Identity is decoupled from calendars (Phase 1): OAuth tokens live on
# CalendarAccount, one per connected calendar, not on the User.

def get_calendar_accounts(session: Session, user: User) -> list[CalendarAccount]:
    """A user's connected calendar accounts — those actually holding a token.

    Availability reads union across ALL of these (one person = one free/busy
    truth). A row with no token is a stub and is skipped."""
    return [a for a in user.calendar_accounts if a.token_json]


def get_primary_calendar_account(session: Session, user: User) -> CalendarAccount | None:
    """The user's default WRITABLE calendar — where bookings and synced events
    land. The account flagged primary, else the earliest-connected one."""
    accounts = get_calendar_accounts(session, user)
    if not accounts:
        return None
    for a in accounts:
        if a.is_primary:
            return a
    return accounts[0]  # calendar_accounts is ordered by created_at


def account_syncs_out(account: CalendarAccount) -> bool:
    """Whether Nudgy should WRITE in-app events / bookings to this calendar.

    sync_setting "none" opts the calendar out of outbound sync; "one_way" and
    "two_way" both write out. (The inbound half of two_way is the freebusy
    availability read, which is independent of this and always happens.) The write
    paths — event_routes._sync_to_google and tools.booking — consult this before
    creating a calendar event."""
    return account.sync_setting != "none"


def upsert_calendar_account(
    session: Session, user: User, provider: str, external_email: str, token_json: str,
) -> CalendarAccount:
    """Create or refresh a user's connection for (provider, external_email).

    The user's first connected calendar becomes the primary (default writable)
    one; reconnecting an existing calendar just refreshes its token.

    Mutates through the `calendar_accounts` relationship (not a bare add) so the
    user's in-memory collection stays in sync — with expire_on_commit=False a
    freshly-created user would otherwise keep a cached-empty collection and reads
    right after the write would miss the new account."""
    for account in user.calendar_accounts:  # loads the collection if needed
        if account.provider == provider and account.external_email == external_email:
            account.token_json = token_json
            session.commit()
            return account
    account = CalendarAccount(
        provider=provider, external_email=external_email, token_json=token_json,
        is_primary=not user.calendar_accounts,  # first one connected = default
    )
    user.calendar_accounts.append(account)  # back_populates sets user_id on flush
    session.commit()
    return account


def set_account_token(session: Session, account: CalendarAccount, token_json: str) -> None:
    """Persist a refreshed OAuth token back to the account (after a silent
    refresh), so a token expired mid-session never goes stale on disk."""
    account.token_json = token_json
    session.commit()


def get_calendar_account(
    session: Session, user: User, account_id: int,
) -> CalendarAccount | None:
    """One of the user's OWN connected calendars by id — the ownership check for
    the management endpoints. Scans the relationship (not a global lookup) so one
    user can never read or mutate another's account. Stubs (no token) are skipped,
    matching get_calendar_accounts."""
    for a in user.calendar_accounts:
        if a.id == account_id and a.token_json:
            return a
    return None


def set_account_color(session: Session, account: CalendarAccount, color: str | None) -> None:
    """Set (or clear, with None) the display color that tells this calendar's
    events apart from another's in the multi-calendar UI."""
    account.color = color
    session.commit()


def set_account_sync_setting(
    session: Session, account: CalendarAccount, sync_setting: str,
) -> None:
    """Store how in-app events flow to this calendar (none|one_way|two_way).
    Stored only for now — the sync path doesn't yet read it (v1 decision)."""
    account.sync_setting = sync_setting
    session.commit()


def set_primary_calendar_account(
    session: Session, user: User, account: CalendarAccount,
) -> None:
    """Make `account` the user's primary (default writable) calendar, clearing the
    flag on every other so exactly one stays primary — writes (bookings, synced
    events) always have a single unambiguous target."""
    for a in user.calendar_accounts:
        a.is_primary = a.id == account.id
    session.commit()


def disconnect_calendar_account(
    session: Session, user: User, account: CalendarAccount,
) -> None:
    """Remove a connected calendar. If it was primary and other connected calendars
    remain, promote the earliest-connected survivor so writes still have a target
    (calendar_accounts is ordered by created_at, so survivors[0] is earliest)."""
    was_primary = account.is_primary
    user.calendar_accounts.remove(account)  # delete-orphan cascade deletes the row
    session.flush()
    if was_primary:
        survivors = [a for a in user.calendar_accounts if a.token_json]
        if survivors:
            for a in survivors:
                a.is_primary = False
            survivors[0].is_primary = True
    session.commit()


def _login_with_provider(
    session: Session, provider: str, email: str, token_json: str,
) -> User:
    """Social sign-in (Google/Microsoft): find-or-create the identity, then
    attach or refresh its calendar account for this provider. Identity
    (User.email) is decoupled from calendars; a social login also grants a
    calendar in one step. The provider already verified the address, so the
    identity counts as email-verified. If the same email already exists (e.g. a
    Google user now adding Microsoft), both calendars hang off the one identity."""
    user = get_user_by_email(session, email)
    if user is None:
        user = User(email=email, email_verified=True)
        session.add(user)
        session.commit()
    elif not user.email_verified:
        # The email owner is proving control now (the provider verified the
        # address). Any password sitting on this NOT-yet-verified row was set by
        # someone who never confirmed the address — possibly an attacker who
        # pre-registered the email to hijack it later. Discard that unproven
        # credential; the real owner can set a fresh password via reset.
        user.password_hash = None
        user.email_verified = True
        session.commit()
    upsert_calendar_account(session, user, provider, email, token_json)
    return user


def login_with_google(session: Session, email: str, token_json: str) -> User:
    return _login_with_provider(session, "google", email, token_json)


def login_with_microsoft(session: Session, email: str, token_json: str) -> User:
    return _login_with_provider(session, "microsoft", email, token_json)


# ------------------------------------------------------------- email/password

def create_password_user(session: Session, email: str, password_hash: str) -> User:
    """A new email/password identity — no calendar, unverified until they click
    the verification link."""
    user = User(email=email, password_hash=password_hash, email_verified=False)
    session.add(user)
    session.commit()
    return user


def set_password(session: Session, user: User, password_hash: str) -> None:
    user.password_hash = password_hash
    session.commit()


def mark_email_verified(session: Session, user: User) -> None:
    user.email_verified = True
    session.commit()


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


def get_group(session: Session, group_id: int) -> Group | None:
    return session.get(Group, group_id)


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


# --------------------------------------------------------- group lifecycle ops
# Ownership = Group.created_by. The API layer gates owner-only ops (rename,
# regenerate code, kick, delete); any member may leave.

def get_membership(session: Session, group_id: int, user_id: int) -> Membership | None:
    return session.scalar(
        select(Membership).where(
            Membership.group_id == group_id, Membership.user_id == user_id
        )
    )


def rename_group(session: Session, group: Group, name: str) -> Group:
    group.name = name
    session.commit()
    return group


def regenerate_invite_code(session: Session, group: Group) -> Group:
    """Roll a fresh unique invite code — invalidates the old shared link."""
    code = _new_invite_code()
    while session.scalar(select(Group).where(Group.invite_code == code)):
        code = _new_invite_code()
    group.invite_code = code
    session.commit()
    return group


def transfer_ownership(session: Session, group: Group, new_owner_id: int) -> None:
    group.created_by = new_owner_id
    session.commit()


def remove_membership(session: Session, group_id: int, user_id: int) -> bool:
    """Drop a user's membership (leave or kick). Returns False if they weren't a
    member. The caller handles owner-departure (transfer or delete) separately."""
    m = get_membership(session, group_id, user_id)
    if m is None:
        return False
    session.delete(m)
    session.commit()
    return True


def delete_group(session: Session, group: Group) -> None:
    """Delete a group and everything scoped to it: plans (with their rounds +
    votes, via cascade), the group's own events (with their RSVPs, via cascade),
    and memberships (Group.memberships delete-orphan cascade). Any Google Calendar
    events already booked stay on attendees' calendars — same as delete_plan."""
    for plan in get_group_plans(session, group.id):
        session.delete(plan)
    events = session.scalars(select(GroupEvent).where(GroupEvent.group_id == group.id))
    for event in events:
        session.delete(event)
    session.delete(group)  # memberships cascade with the group
    session.commit()


# ----------------------------------------------------------------- plans

def create_plan(
    session: Session,
    group: Group,
    host: User,
    title: str,
    slots: list[tuple],          # ordered [(start_utc, end_utc), ...] candidate times
    location: str | None = None,
    expected_count: int | None = None,
    deadline: datetime | None = None,
    auto_book: bool = False,
) -> Plan:
    """Create a plan with its candidate times queued in order.

    The first time is activated immediately, so the moment a member says yes to
    the interest question they have a time to answer. The host suggested the
    plan, so their interest is recorded as yes up front — they still vote on
    the times themselves.

    `deadline` (UTC) closes voting at a fixed instant and `auto_book` lets a
    unanimous plan book itself — see tools/plan_deadlines.py.
    """
    plan = Plan(group_id=group.id, created_by=host.id, title=title,
                location=location, expected_count=expected_count,
                deadline_utc=deadline, auto_book=auto_book)
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


def find_duplicate_open_plan(
    session: Session, group: Group, location: str | None, slots: list[tuple]
) -> Plan | None:
    """An already-open plan in this group that is effectively the same proposal
    — same place and the same set of candidate times — so neither the agent nor
    the UI spawns a second card for it.

    Title is deliberately ignored: the whole point is that "Hang out at Blend
    Cafe" and "hang out" for the same place and time ARE the same plan. Timeless
    "who's in?" checks (no slots) are never deduped — they carry no time to
    compare and are cheap to re-ask.
    """
    if not slots:
        return None
    loc_key = (location or "").strip().casefold()
    want = frozenset(
        (s.astimezone(timezone.utc), e.astimezone(timezone.utc)) for s, e in slots
    )
    for p in get_group_plans(session, group.id, only_open=True):
        if (p.location or "").strip().casefold() != loc_key:
            continue
        have = frozenset(
            (r.start.astimezone(timezone.utc), r.end.astimezone(timezone.utc))
            for r in p.rounds
        )
        if have == want:
            return p
    return None


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


def delete_plan(session: Session, plan: Plan) -> None:
    """Remove a plan and everything hanging off it — its candidate times and
    both kinds of vote — via the ORM delete-orphan cascades on Plan/TimeRound.
    Host-gated at the API layer. Does NOT touch any Google Calendar event a
    booked round already created; that stays on the calendar."""
    session.delete(plan)
    session.commit()


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
    """email -> yes/no for everyone who answered the plan's interest question.

    joinedload pulls each voter's User in the same query — without it, reading
    v.user.email lazy-loads one row per vote (N+1), and this runs on the poll
    page's 5s refresh for every open plan."""
    rows = session.scalars(
        select(InterestVote)
        .where(InterestVote.plan_id == plan.id)
        .options(joinedload(InterestVote.user))
    )
    return {v.user.email: v.yes for v in rows}


def get_time_votes(session: Session, round_: TimeRound | None) -> dict[str, bool]:
    """email -> yes/no for everyone who voted on this candidate time."""
    if round_ is None:
        return {}
    rows = session.scalars(
        select(TimeVote)
        .where(TimeVote.round_id == round_.id)
        .options(joinedload(TimeVote.user))
    )
    return {v.user.email: v.yes for v in rows}


def set_plan_status(session: Session, plan: Plan, status: str) -> None:
    plan.status = status
    session.commit()


# ------------------------------------------------------- plan deadlines / async

def set_plan_deadline(session: Session, plan: Plan, deadline: datetime | None) -> None:
    """Set or clear a plan's vote deadline (UTC).

    Giving an expired plan a fresh deadline REOPENS it: the host is saying "I'm
    giving you more time", and a closed plan nobody can vote in would make that
    a lie. The reminder stamp resets with it so the extra window gets its own
    nudge instead of inheriting a spent one.
    """
    plan.deadline_utc = deadline
    if deadline is not None and plan.status == "expired":
        plan.status = "open"
        plan.reminder_sent_at = None
    session.commit()


def set_plan_auto_book(session: Session, plan: Plan, auto_book: bool) -> None:
    plan.auto_book = auto_book
    session.commit()


def mark_plan_reminded(session: Session, plan: Plan, when: datetime) -> None:
    plan.reminder_sent_at = when
    session.commit()


# ------------------------------------------------- share links & guest voters

# A hard ceiling on how many people can pile in through a link. The token is a
# bearer credential: whoever holds it can add voters, and a plan with 200 "yes"
# from strangers is worse than useless to the host. High enough that no real
# friend group hits it, low enough that a leaked link can't drown the group.
MAX_GUESTS_PER_PLAN = 25


def ensure_share_token(session: Session, plan: Plan) -> str:
    """The plan's public vote link token, minting one on first use."""
    if not plan.share_token:
        plan.share_token = secrets.token_urlsafe(16)
        session.commit()
    return plan.share_token


def regenerate_share_token(session: Session, plan: Plan) -> str:
    """Mint a new token, which instantly kills every copy of the old link.

    Guests who already voted keep their votes — they were invited in good faith.
    Revoking a link is about who can join from here on, not about erasing people.
    """
    plan.share_token = secrets.token_urlsafe(16)
    session.commit()
    return plan.share_token


def revoke_share_token(session: Session, plan: Plan) -> None:
    plan.share_token = None
    session.commit()


def get_plan_by_share_token(session: Session, token: str) -> Plan | None:
    if not token:
        return None
    return session.scalar(select(Plan).where(Plan.share_token == token))


def get_plan_guests(session: Session, plan: Plan) -> list[PlanGuest]:
    return list(session.scalars(
        select(PlanGuest).where(PlanGuest.plan_id == plan.id)
        .order_by(PlanGuest.created_at)
    ))


def get_guest(session: Session, guest_id: int | None) -> PlanGuest | None:
    return session.get(PlanGuest, guest_id) if guest_id else None


def find_guest_by_name(session: Session, plan: Plan, name: str) -> PlanGuest | None:
    """Case-insensitive, because "sam" and "Sam" are the same person to everyone
    reading the tally."""
    key = name.strip().casefold()
    return next((g for g in get_plan_guests(session, plan)
                 if g.name.casefold() == key), None)


def create_guest(session: Session, plan: Plan, name: str,
                 email: str | None = None) -> PlanGuest:
    guest = PlanGuest(plan_id=plan.id, name=name.strip(), email=email)
    session.add(guest)
    session.commit()
    return guest


def cast_guest_interest(session: Session, guest: PlanGuest, yes: bool) -> GuestInterestVote:
    vote = session.scalar(
        select(GuestInterestVote).where(GuestInterestVote.guest_id == guest.id)
    )
    if vote is None:
        vote = GuestInterestVote(guest_id=guest.id, yes=yes)
        session.add(vote)
    else:
        vote.yes = yes
    session.commit()
    return vote


def cast_guest_time_vote(session: Session, round_: TimeRound, guest: PlanGuest,
                         yes: bool) -> GuestTimeVote:
    vote = session.scalar(
        select(GuestTimeVote).where(
            GuestTimeVote.round_id == round_.id, GuestTimeVote.guest_id == guest.id
        )
    )
    if vote is None:
        vote = GuestTimeVote(round_id=round_.id, guest_id=guest.id, yes=yes)
        session.add(vote)
    else:
        vote.yes = yes
    session.commit()
    return vote


def get_guest_interest_votes(session: Session, plan: Plan) -> dict[str, bool]:
    """guest label -> yes/no, in the same shape as get_interest_votes so the
    cascade rules never have to know a guest from a member."""
    rows = session.scalars(
        select(GuestInterestVote)
        .join(PlanGuest, PlanGuest.id == GuestInterestVote.guest_id)
        .where(PlanGuest.plan_id == plan.id)
        .options(joinedload(GuestInterestVote.guest))
    )
    return {v.guest.label: v.yes for v in rows}


def get_guest_time_votes(session: Session, round_: TimeRound | None) -> dict[str, bool]:
    if round_ is None:
        return {}
    rows = session.scalars(
        select(GuestTimeVote)
        .where(GuestTimeVote.round_id == round_.id)
        .options(joinedload(GuestTimeVote.guest))
    )
    return {v.guest.label: v.yes for v in rows}


def get_open_plans(session: Session) -> list[Plan]:
    """Every open plan across every group — the ticker's work queue.

    Deliberately unfiltered by deadline: plans with no deadline still need
    reminders. The set is small (open plans only) and this runs once a minute in
    a background job, not on a request path. If it ever stops being small, the
    fix is to filter on `deadline_utc <= now OR reminder_sent_at IS NULL` here
    rather than to make the ticker smarter.
    """
    return list(session.scalars(
        select(Plan).where(Plan.status == "open").order_by(Plan.id)
    ))


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


def update_event(session: Session, event: GroupEvent, **fields) -> GroupEvent:
    """Apply the given fields to an event. Only keys actually passed are set,
    so a caller can clear `location` (pass None) without that being confused
    with "leave it alone" — the route decides which is which from the request
    body, not from the value.

    Deliberately does NOT touch `kind` or `personal`: those decide which rules
    govern the row (a task has no end time; a personal event is masked from
    groupmates and skips outbound sync). Changing one would silently move an
    event between mechanisms, so a conversion should be an explicit operation
    if it's ever wanted, not a field edit.
    """
    for key, value in fields.items():
        setattr(event, key, value)
    session.commit()
    session.refresh(event)
    return event


def delete_event(session: Session, event: GroupEvent) -> None:
    session.delete(event)
    session.commit()


def set_event_gcal(session: Session, event: GroupEvent, gcal_id: str | None, link: str | None) -> None:
    event.synced = gcal_id is not None
    event.gcal_event_id = gcal_id
    event.gcal_link = link
    session.commit()


def upsert_rsvp(session: Session, event: GroupEvent, user: User, status: str) -> EventRsvp:
    """One RSVP per (event, user); answering again replaces the previous one."""
    from app.db.models import _utcnow

    rsvp = session.scalar(
        select(EventRsvp).where(
            EventRsvp.event_id == event.id, EventRsvp.user_id == user.id
        )
    )
    if rsvp is None:
        rsvp = EventRsvp(event_id=event.id, user_id=user.id, status=status)
        session.add(rsvp)
    else:
        rsvp.status = status
        rsvp.updated_at = _utcnow()
    session.commit()
    return rsvp


def get_event_rsvps(session: Session, event: GroupEvent) -> dict[str, str]:
    """email -> status for everyone who RSVP'd to this event."""
    rows = session.scalars(select(EventRsvp).where(EventRsvp.event_id == event.id))
    return {r.user.email: r.status for r in rows}


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
