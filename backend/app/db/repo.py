"""Repository layer: every DB query the app needs, in one file.

Keeping SQLAlchemy calls here (instead of scattered through the API and agent)
means there is a single place to read to understand what data operations
exist, and a single place to debug when a query misbehaves.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    CalendarAccount, CalendarSyncState, EventRsvp, ExternalEvent, Group,
    GroupEvent, GuestInterestVote, GuestTimeVote, InterestVote, Membership,
    Plan, PlaceReview, PlanGuest, TimeRound, TimeVote, User,
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
    session: Session, user: User, account: CalendarAccount, keep_events: bool = False,
) -> None:
    """Remove a connected calendar. If it was primary and other connected calendars
    remain, promote the earliest-connected survivor so writes still have a target
    (calendar_accounts is ordered by created_at, so survivors[0] is earliest).

    `keep_events` decides the fate of whatever inbound sync mirrored from this
    calendar, and the caller is expected to have ASKED. Deleting somebody's
    schedule because they unlinked an account is destructive and surprising;
    silently keeping a copy of a calendar they just disconnected is worse. So
    neither is a default the app picks on its own — the API requires the choice
    and passes it through.

    Keeping detaches rather than copies: the rows stay under the user with
    account_id NULL, a frozen record that no longer syncs. Reconnecting the same
    calendar re-adopts them, because the mirror's uniqueness key is the user's
    (see ExternalEvent), so nothing duplicates.
    """
    if keep_events:
        detach_external_events(session, account)
    else:
        delete_external_events_for_account(session, account)
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


# ------------------------------------------------------------- inbound sync
# The mirror of external events (docs/inbound-sync.md). Reads here are always
# scoped to ONE user: an external event is the property of whoever's calendar it
# came from, and groupmates only ever see it as anonymous busy time via
# availability. Nothing in this section is group-scoped, on purpose.

def set_account_read_titles(
    session: Session, account: CalendarAccount, read_titles: bool,
    keep_titles: bool = False,
) -> None:
    """Flip the per-calendar title opt-in (v1-decisions.md #5).

    Two side effects, both required rather than tidy-up:

    1. Every sync token on the account is cleared. Google's field mask is part
       of the query frozen into a token, so a token minted while titles were off
       can never start returning them — the next round has to be a full read
       with the new mask. (Graph ignores the distinction, but one rule beats a
       per-provider special case.)
    2. Turning titles OFF purges the text already pulled in, unless the owner
       said to keep it. That choice belongs to them: the titles are theirs, and
       the app should neither hoard text somebody just withdrew consent for nor
       quietly bin a schedule they may still be using. The times always stay —
       they are what make the block a block.
    """
    account.read_titles = read_titles
    clear_sync_tokens(session, account)
    if not read_titles and not keep_titles:
        purge_external_titles(session, account)
    session.commit()


def clear_sync_tokens(session: Session, account: CalendarAccount) -> None:
    """Force every calendar on this account to re-read in full next tick.

    Flushed, not committed — callers fold this into their own transaction.
    """
    session.execute(
        update(CalendarSyncState)
        .where(CalendarSyncState.account_id == account.id)
        .values(sync_token=None)
    )
    session.flush()


def purge_external_titles(session: Session, account: CalendarAccount) -> int:
    """Strip titles/locations from this calendar's mirror, keeping the times."""
    result = session.execute(
        update(ExternalEvent)
        .where(ExternalEvent.account_id == account.id)
        .values(title=None, location=None)
    )
    session.flush()
    return result.rowcount or 0


def delete_external_events_for_account(session: Session, account: CalendarAccount) -> int:
    result = session.execute(
        delete(ExternalEvent).where(ExternalEvent.account_id == account.id)
    )
    session.flush()
    return result.rowcount or 0


def detach_external_events(session: Session, account: CalendarAccount) -> int:
    """Keep the mirror but cut it loose from the connection being removed.

    account_id NULL is what "the calendar this came from is gone" looks like:
    the rows stay visible to their owner and stop being synced. They keep their
    calendar_id, so reconnecting the same calendar re-adopts them on the next
    round rather than inserting a second copy of everything.
    """
    result = session.execute(
        update(ExternalEvent)
        .where(ExternalEvent.account_id == account.id)
        .values(account_id=None)
    )
    session.flush()
    return result.rowcount or 0


def get_sync_states(session: Session, account: CalendarAccount) -> list[CalendarSyncState]:
    return list(session.scalars(
        select(CalendarSyncState)
        .where(CalendarSyncState.account_id == account.id)
        .order_by(CalendarSyncState.id)
    ))


def upsert_sync_state(
    session: Session, account: CalendarAccount, calendar_id: str,
    name: str | None = None,
) -> CalendarSyncState:
    """The bookmark row for one calendar, created on first sight.

    The name is refreshed every round so renaming a calendar in Google shows up
    here, but never overwritten with None — a provider that declines to give a
    display name should not erase one we already have.
    """
    state = session.scalar(
        select(CalendarSyncState).where(
            CalendarSyncState.account_id == account.id,
            CalendarSyncState.calendar_id == calendar_id,
        )
    )
    if state is None:
        state = CalendarSyncState(account_id=account.id, calendar_id=calendar_id, name=name)
        session.add(state)
    elif name:
        state.name = name
    session.commit()
    return state


def accounts_with_tokens(session: Session) -> list[CalendarAccount]:
    """Every connected calendar the sync job could poll (i.e. holding a token)."""
    return list(session.scalars(
        select(CalendarAccount).where(CalendarAccount.token_json.is_not(None))
        .order_by(CalendarAccount.id)
    ))


def save_sync_round(
    session: Session,
    state: CalendarSyncState,
    *,
    changed: list,
    deleted_ids: list[str],
    next_token: str | None,
    full: bool,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    store_titles: bool,
) -> dict:
    """Apply one completed sync round and advance the bookmark, atomically.

    ORDER MATTERS AND IT IS NOT SYMMETRIC. Committing the token before the rows
    loses those changes forever — neither provider will ever re-send them.
    Committing the rows before the token merely means the next round replays work
    already done, which is harmless because every write here is an idempotent
    upsert keyed on (user, calendar, external id). So: one transaction, and if it
    has to break, it breaks on the safe side.

    `full` rounds additionally reconcile BY ABSENCE — anything mirrored for this
    calendar that the provider did not just hand back is gone. That is the only
    way to catch deletions that happened while we were holding a token the
    provider had already forgotten about.
    """
    account = state.account
    seen: set[str] = set()
    counts = {"added": 0, "updated": 0, "deleted": 0}

    existing = {
        e.external_id: e
        for e in session.scalars(
            select(ExternalEvent).where(
                ExternalEvent.user_id == account.user_id,
                ExternalEvent.calendar_id == state.calendar_id,
            )
        )
    }

    for item in changed:
        seen.add(item.external_id)
        # store_titles is read from the account at round time, so an opt-in
        # switched off mid-round can never write a title back in.
        title = item.title if store_titles else None
        location = item.location if store_titles else None
        row = existing.get(item.external_id)
        if row is None:
            session.add(ExternalEvent(
                user_id=account.user_id, account_id=account.id,
                calendar_id=state.calendar_id, external_id=item.external_id,
                title=title, location=location,
                start_utc=item.start, end_utc=item.end,
                all_day=item.all_day, busy=item.busy, updated_at=now,
            ))
            counts["added"] += 1
        else:
            # Re-adopt a row detached by an earlier disconnect: the same calendar
            # is connected again, so it is live again.
            row.account_id = account.id
            row.title, row.location = title, location
            row.start_utc, row.end_utc = item.start, item.end
            row.all_day, row.busy = item.all_day, item.busy
            row.updated_at = now
            counts["updated"] += 1

    drop = {eid for eid in deleted_ids if eid in existing}
    if full:
        drop |= {eid for eid in existing if eid not in seen}
    for eid in drop:
        session.delete(existing[eid])
    counts["deleted"] = len(drop)

    state.sync_token = next_token
    state.window_start_utc, state.window_end_utc = window_start, window_end
    state.synced_at = now
    state.error = None
    session.commit()
    return counts


def record_sync_error(session: Session, state: CalendarSyncState, message: str) -> None:
    """Remember why a calendar stopped syncing, so Settings can say so.

    The token is left alone: most failures are transient (an expired access
    token, a 429, a network blip) and throwing away a valid bookmark would turn
    every hiccup into a full re-read.
    """
    state.error = message[:500]
    session.commit()


def calendar_labels_for_user(session: Session, user_id: int) -> dict[tuple[int, str], str]:
    """{(account_id, calendar_id): display name} for one user's calendars.

    Built in one query so rendering N mirrored events doesn't cost N lookups.
    Events detached by a disconnect (account_id NULL) are absent by construction
    — there is no connection left to name them after.
    """
    rows = session.execute(
        select(CalendarSyncState.account_id, CalendarSyncState.calendar_id,
               CalendarSyncState.name)
        .join(CalendarAccount, CalendarAccount.id == CalendarSyncState.account_id)
        .where(CalendarAccount.user_id == user_id)
    ).all()
    return {(acc, cal): name for acc, cal, name in rows if name}


def get_external_events(
    session: Session, user_id: int, window_start: datetime, window_end: datetime,
) -> list[ExternalEvent]:
    """One person's mirrored external events overlapping a window.

    Overlap, not containment — something that started before the window and runs
    into it is still on their plate inside it.
    """
    return list(session.scalars(
        select(ExternalEvent).where(
            ExternalEvent.user_id == user_id,
            ExternalEvent.start_utc < window_end,
            ExternalEvent.end_utc > window_start,
        ).order_by(ExternalEvent.start_utc)
    ))


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
) -> Plan:
    """Create a plan with all its candidate times immediately votable.

    No time is "activated" — every candidate is open from the start (the queue
    is gone; see docs/poll-edit-redesign.md §1.2).

    A plan created with NO times asks the interest question first — that is
    Float-an-idea, and `asks_interest` is stored rather than derived so adding
    times later doesn't silently change what the plan asked. A plan created WITH
    times never asks it: a yes on any time is the interest signal.

    The host suggested the plan, so their interest is recorded as yes up front
    when there is an interest stage at all. They still vote on the times.
    """
    asks_interest = not slots
    plan = Plan(group_id=group.id, created_by=host.id, title=title,
                location=location, expected_count=expected_count,
                deadline_utc=deadline, asks_interest=asks_interest)
    session.add(plan)
    session.flush()  # assign plan.id
    for i, (start, end) in enumerate(slots):
        session.add(TimeRound(
            plan_id=plan.id, ordinal=i, slot_start_utc=start, slot_end_utc=end,
            created_by=host.id,
        ))
    if asks_interest:
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


def append_rounds(session: Session, plan: Plan, slots: list[tuple],
                  suggested_by: User | None = None) -> list[TimeRound]:
    """Add candidate times to an existing plan — ANY member may do this.

    `suggested_by` is stored so the card can say who put a time up, and so that
    person (and only that person) can take it back down again.

    Ordinals continue after the existing ones, which keeps display order stable
    and gives the convergence rule its final tiebreak. New times are votable
    immediately; no existing vote is disturbed.
    """
    next_ordinal = max((r.ordinal for r in plan.rounds), default=-1) + 1
    made: list[TimeRound] = []
    for i, (start, end) in enumerate(slots):
        r = TimeRound(
            plan_id=plan.id, ordinal=next_ordinal + i,
            slot_start_utc=start, slot_end_utc=end,
            created_by=suggested_by.id if suggested_by else None,
        )
        session.add(r)
        made.append(r)
    session.commit()
    session.refresh(plan)
    return made


def delete_round(session: Session, plan: Plan, round_: TimeRound) -> None:
    """Remove one candidate time, and its votes with it (delete-orphan cascade).

    Clears the spotlight if it pointed here — a spotlight on a time that no
    longer exists would read as "no spotlight" everywhere anyway, and leaving a
    dangling id behind invites a stale match against a future round's id.
    """
    if plan.spotlight_round_id == round_.id:
        plan.spotlight_round_id = None
    session.delete(round_)
    session.commit()
    session.refresh(plan)


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


def get_round(session: Session, round_id: int) -> TimeRound | None:
    return session.get(TimeRound, round_id)


def get_spotlight_round(session: Session, plan: Plan) -> TimeRound | None:
    """The time the host is leaning toward, if it still exists.

    Resolved through plan.rounds rather than a join: spotlight_round_id is a
    plain integer (see the Plan model), and a stale id — the time it pointed at
    was removed — must read as "no spotlight", not raise.
    """
    if plan.spotlight_round_id is None:
        return None
    return next((r for r in plan.rounds if r.id == plan.spotlight_round_id), None)


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


def cast_time_vote(session: Session, round_: TimeRound, user: User, answer: str) -> TimeVote:
    """Answer one candidate time (yes / no / if_needed); re-voting replaces.

    Each candidate time is answered independently — voting on one says nothing
    about the others.
    """
    vote = session.scalar(
        select(TimeVote).where(TimeVote.round_id == round_.id, TimeVote.user_id == user.id)
    )
    if vote is None:
        vote = TimeVote(round_id=round_.id, user_id=user.id, answer=answer)
        session.add(vote)
    else:
        vote.answer = answer
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


def get_time_votes(session: Session, round_: TimeRound | None) -> dict[str, str]:
    """email -> answer for everyone who voted on ONE candidate time."""
    if round_ is None:
        return {}
    rows = session.scalars(
        select(TimeVote)
        .where(TimeVote.round_id == round_.id)
        .options(joinedload(TimeVote.user))
    )
    return {v.user.email: v.answer for v in rows}


def get_votes_by_time(session: Session, plan: Plan) -> dict[int, dict[str, str]]:
    """round id -> {email -> answer}, for every candidate time in one query.

    Times are voted on in parallel now, so the poll page needs all of them at
    once. Fetching per round would be a query per candidate on every refresh of
    every open plan — the N+1 this layer exists to prevent.
    """
    if not plan.rounds:
        return {}
    rows = session.scalars(
        select(TimeVote)
        .where(TimeVote.round_id.in_([r.id for r in plan.rounds]))
        .options(joinedload(TimeVote.user))
    )
    out: dict[int, dict[str, str]] = {r.id: {} for r in plan.rounds}
    for v in rows:
        out[v.round_id][v.user.email] = v.answer
    return out


def get_guest_votes_by_time(session: Session, plan: Plan) -> dict[int, dict[str, str]]:
    """The same, for share-link guests, keyed by their display label."""
    if not plan.rounds:
        return {}
    rows = session.scalars(
        select(GuestTimeVote)
        .where(GuestTimeVote.round_id.in_([r.id for r in plan.rounds]))
        .options(joinedload(GuestTimeVote.guest))
    )
    out: dict[int, dict[str, str]] = {r.id: {} for r in plan.rounds}
    for v in rows:
        out[v.round_id][v.guest.label] = v.answer
    return out


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


def set_plan_spotlight(session: Session, plan: Plan, round_id: int | None) -> None:
    """Point the spotlight at a candidate time, or clear it.

    Deliberately destructive of NOTHING — no vote, no status, no other time is
    touched. That is what makes the move reversible, and it is the whole
    difference from the advance_to_next_time it replaces.
    """
    plan.spotlight_round_id = round_id
    session.commit()


def set_plan_minimum(session: Session, plan: Plan, minimum: int) -> None:
    """Change how many members must be able to make a time before it books
    without a human. Host-gated at the API layer."""
    plan.expected_count = minimum
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


def find_guest_by_email(session: Session, plan: Plan, email: str) -> PlanGuest | None:
    """The strongest identity signal a guest ever gives us.

    Used to hand a returning guest their EXISTING ballot when their cookie is
    gone (different device, cleared browser) instead of minting a second row.
    That matters beyond tidiness: a guest's vote counts toward a creator-typed
    minimum, so duplicates would let one person raise a plan over its own bar.
    """
    key = email.strip().casefold()
    if not key:
        return None
    return next((g for g in get_plan_guests(session, plan)
                 if (g.email or "").casefold() == key), None)


def find_guest_by_email(session: Session, plan: Plan, email: str) -> PlanGuest | None:
    """The guest on this plan who already gave this address, if any.

    A guest's vote can count toward a creator-typed minimum, so the same person
    arriving twice doesn't merely look untidy — it inflates the numbers the bar
    is measured against. Cookies are lost routinely (another device, a cleared
    browser) and the old path minted a fresh guest row every time. An email is
    the only identity signal we ask for, so a match hands back the existing
    ballot instead. Optional by design: this protects the guests who give one.
    """
    key = email.strip().casefold()
    if not key:
        return None
    return next((g for g in get_plan_guests(session, plan)
                 if (g.email or "").casefold() == key), None)


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
                         answer: str) -> GuestTimeVote:
    vote = session.scalar(
        select(GuestTimeVote).where(
            GuestTimeVote.round_id == round_.id, GuestTimeVote.guest_id == guest.id
        )
    )
    if vote is None:
        vote = GuestTimeVote(round_id=round_.id, guest_id=guest.id, answer=answer)
        session.add(vote)
    else:
        vote.answer = answer
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


def get_guest_time_votes(session: Session, round_: TimeRound | None) -> dict[str, str]:
    if round_ is None:
        return {}
    rows = session.scalars(
        select(GuestTimeVote)
        .where(GuestTimeVote.round_id == round_.id)
        .options(joinedload(GuestTimeVote.guest))
    )
    return {v.guest.label: v.answer for v in rows}


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


def create_event_from_booking(
    session: Session,
    plan: Plan,
    round_: TimeRound,
    attendee_emails: list[str],
    *,
    gcal_event_id: str | None = None,
    gcal_link: str | None = None,
) -> GroupEvent:
    """Turn a booked poll time into the real group event (poll-edit-redesign §2).

    Before this, a booked `Plan` and a `GroupEvent` were separate worlds, which
    is why a booked poll had no edit path: there was no attendance record to
    reset and no row for `update_event` to act on. Locking in now produces an
    ordinary group event, so everything that works on an event — editing,
    RSVP-reset, availability, the calendar view — works on a booked poll too.

    Attendees are the yes / if-needed voters, written as `going` RSVPs: they
    already consented through the vote, so asking them to RSVP again would be
    asking the same question twice.

    GUESTS GET NO RSVP ROW. `EventRsvp.user_id` is a real foreign key and a
    guest has no user — they are a name attached to one poll. They still got
    their calendar invite from the booking itself (if they left an address); what
    they don't get is a seat in the group's attendance record, which is correct:
    they aren't in the group. The poll remains their record, via `plan_id`.

    Idempotent on the poll: a second call for the same plan returns the event
    that already exists rather than making a duplicate. Booking is retried after
    calendar failures, and two events for one poll would be worse than none.
    """
    existing = session.scalar(select(GroupEvent).where(GroupEvent.plan_id == plan.id))
    if existing is not None:
        return existing

    event = GroupEvent(
        group_id=plan.group_id,
        created_by=plan.created_by,
        plan_id=plan.id,
        kind="event",
        title=plan.title,
        category="Event",
        location=plan.location,
        start_utc=round_.start,
        end_utc=round_.end,
        # A poll booking is the group's business by definition — it is never
        # personal, and never anonymous: everyone who voted already knows what
        # it is and who is coming.
        personal=False,
        anonymous=False,
        synced=gcal_event_id is not None,
        gcal_event_id=gcal_event_id,
        gcal_link=gcal_link,
    )
    session.add(event)
    session.flush()

    members = {m.email: m for m in get_group_members(session, plan.group_id)}
    for email in attendee_emails:
        member = members.get(email)
        if member is None:
            continue  # a guest label, not a member address — see the docstring
        session.add(EventRsvp(event_id=event.id, user_id=member.id, status="going"))
    session.commit()
    return event


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


# RSVP statuses that make a member BUSY for a shared event. `maybe` counts:
# tentatively attending still means the time is spoken for, and proposing a
# competing plan on top of it is worse than losing a slot that frees up.
#
# `needs_reconfirm` counts too (docs/poll-edit-redesign.md §3-§4): it means the
# event was edited under someone who had already said yes. They WERE busy a
# moment ago and probably still are, so treating a pending re-confirm as free
# would invite double-booking somebody who is still coming. A soft-busy tier
# (avoid when possible, not disqualifying) is the better long-run answer, but
# slots.py deals in binary intervals; deferred.
#
# `cant` is the only status that leaves the time open.
BUSY_RSVP_STATUSES = ("going", "maybe", "needs_reconfirm")


def get_busy_events_for_users(
    session: Session,
    user_ids: list[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[tuple[datetime, datetime]]]:
    """In-app events as busy intervals, per user id, for a time window.

    The second half of "one user = one unified availability": external freebusy
    covers the calendars a person connected, and this covers what they keep in
    Nudgy. A user who never connects Google or Outlook still has real busy time,
    which is what makes the calendar-optional promise true rather than nominal.

    Whose time an event occupies:
      personal -> its owner, and nobody else.
      shared   -> its creator, plus members who RSVP'd going/maybe. NOT the
                  whole group: a shared event one member created does not get to
                  block another member's time until that member said they're
                  coming. Same consent rule the plan cascade runs on.

    Read ACROSS GROUPS on purpose. A member's Tuesday is equally busy whether the
    thing filling it belongs to this group or another one, and a group is a
    planning context, not a separate time universe.

    Tasks are excluded: a task's end mirrors its due date, so it is a zero-length
    instant, and a deadline is not an appointment — it should never eat a slot.
    """
    if not user_ids:
        return {}
    wanted = set(user_ids)
    busy: dict[int, list[tuple[datetime, datetime]]] = {uid: [] for uid in wanted}

    group_ids = list(session.scalars(
        select(Membership.group_id).where(Membership.user_id.in_(wanted)).distinct()
    ))
    events = list(session.scalars(
        select(GroupEvent).where(
            GroupEvent.kind == "event",
            GroupEvent.start_utc.is_not(None),
            GroupEvent.end_utc.is_not(None),
            # overlap, not containment: something that started before the window
            # and runs into it is still busy time inside it
            GroupEvent.start_utc < window_end,
            GroupEvent.end_utc > window_start,
            or_(
                and_(GroupEvent.personal.is_(True), GroupEvent.created_by.in_(wanted)),
                and_(GroupEvent.personal.is_(False), GroupEvent.group_id.in_(group_ids)),
            ),
        )
    ))
    if not events:
        return busy

    rsvps: dict[int, dict[int, str]] = {}
    for r in session.scalars(
        select(EventRsvp).where(EventRsvp.event_id.in_([e.id for e in events]))
    ):
        rsvps.setdefault(r.event_id, {})[r.user_id] = r.status

    for e in events:
        interval = (e.start, e.end)
        if e.created_by in wanted:
            busy[e.created_by].append(interval)
        if e.personal:
            continue
        for uid, status in rsvps.get(e.id, {}).items():
            if uid in wanted and uid != e.created_by and status in BUSY_RSVP_STATUSES:
                busy[uid].append(interval)
    return busy


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


def reset_attendance(session: Session, event: GroupEvent) -> list[str]:
    """A material edit landed — ask everyone who was coming to say so again.

    Returns the emails whose RSVP was reset, so the caller can notify exactly
    those people (docs/poll-edit-redesign.md §3).

    Only `going` and `maybe` are reset. Somebody who already said `cant` is not
    made to answer a second time about an event they had already declined, and
    an attendee sitting at `needs_reconfirm` from an EARLIER edit stays there
    with their original timestamp — re-stamping would restart the clock on a
    question they still haven't answered.

    The creator is skipped: they are the one who just made the change, so
    asking them to re-confirm their own edit is a question with no content.
    """
    from app.db.models import _utcnow

    reset: list[str] = []
    for rsvp in list(event.rsvps):
        if rsvp.user_id == event.created_by:
            continue
        if rsvp.status not in ("going", "maybe"):
            continue
        rsvp.status = "needs_reconfirm"
        rsvp.updated_at = _utcnow()
        user = session.get(User, rsvp.user_id)
        if user is not None:
            reset.append(user.email)
    session.commit()
    return reset


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
