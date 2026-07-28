"""SQLite schema (SQLAlchemy 2.0). Deliberately tiny.

Design notes:
- A User's Google OAuth token is stored as the raw JSON string Google's
  library produces (token_json). We never parse it here; auth/google.py owns
  that. One place to serialize, one place to read.
- timezone is an IANA name ("Asia/Beirut"). Everything time-related in the
  app is UTC internally and converted to this for display. Defaults to Beirut
  for the hackathon; real users would set it at connect time.
- A Group has a short invite_code; joining is "know the code" — no roles,
  no hierarchy (per spec).
- Membership is the user<->group join table. A user can be in several groups.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.types import EncryptedString


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Re-attach UTC to a timestamp SQLite handed back naive."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    # DEPRECATED — identity/calendar split (Phase 1): a user's OAuth tokens now
    # live on CalendarAccount, one per connected calendar. This column is kept
    # only so the one-time backfill in db/session.py can copy the pre-split
    # token into a CalendarAccount; nothing writes it after that. A later
    # migration drops it. Read calendars via `calendar_accounts`, never here.
    token_json: Mapped[str | None] = mapped_column(EncryptedString, default=None)
    # Email/password login (Phase 1): scrypt-encoded hash (see core/passwords.py).
    # None for identities that only sign in via Google/Microsoft — a user can have
    # both. email_verified gates password login (a social login sets it True, since
    # the provider already verified the address).
    password_hash: Mapped[str | None] = mapped_column(String, default=None)
    email_verified: Mapped[bool] = mapped_column(default=False)
    timezone: Mapped[str] = mapped_column(String, default="Asia/Beirut")
    # optional user-chosen name; UI falls back to deriving one from the email
    display_name: Mapped[str | None] = mapped_column(String, default=None)
    # unfinished things (event/poll started without a time) — a JSON array the
    # frontend owns entirely; stored server-side so drafts survive across devices
    drafts_json: Mapped[str | None] = mapped_column(String, default=None)
    # freeform notes the user teaches the agent ("Aya can't do Fridays") — a
    # JSON array of strings. Persisted here so it survives across devices AND so
    # the agent prompt can actually read it (see agent/prompt.py memory block).
    memory_json: Mapped[str | None] = mapped_column(String, default=None)
    # subscription tier (see core/entitlements.py). Everyone is "free" until a
    # payment processor exists; this is what the agent quota reads.
    tier: Mapped[str] = mapped_column(String, default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # Identity is decoupled from calendars: one person can connect several
    # (Google, Outlook, …), each with its own encrypted token. Availability
    # unions across ALL of them (one person = one free/busy truth); writes
    # target the primary one. Ordered by connect time so "first = default".
    calendar_accounts: Mapped[list["CalendarAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        order_by="CalendarAccount.created_at",
    )

    @property
    def calendar_connected(self) -> bool:
        """True if the user has at least one calendar account holding a token."""
        return any(a.token_json for a in self.calendar_accounts)


class CalendarAccount(Base):
    """One external calendar a User has connected (the identity/calendar split).

    Identity (User) and calendars are separate concerns: a single person can
    connect a Google account, an Outlook account, more than one of each — each
    row here is one such connection with its own OAuth token. This is what makes
    "unified availability" and multi-provider real:
      - availability unions busy time across ALL of a user's accounts, so Nudgy
        never double-books someone across their personal + work calendars;
      - writes (booking a plan, syncing an in-app event) target the PRIMARY one.

    token_json is the provider's raw OAuth credentials JSON, encrypted at rest
    (EncryptedString) — same protection the legacy User.token_json had.
    external_email is the connected calendar's address and need NOT equal the
    user's identity email (User.email): today a Google login makes them coincide,
    but a magic-link user could later attach a differently-addressed calendar.
    """
    __tablename__ = "calendar_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_email",
                         name="uq_user_provider_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String)  # "google" | "microsoft"
    external_email: Mapped[str] = mapped_column(String)
    token_json: Mapped[str | None] = mapped_column(EncryptedString, default=None)
    # multi-calendar UI: a color so this account's events are told apart from
    # another calendar's at a glance (v1 decision). None until the user picks one.
    color: Mapped[str | None] = mapped_column(String, default=None)
    # how in-app events flow to this calendar: none | one_way | two_way. Defaults
    # to two_way per the v1 decision (applied after the user consents at connect).
    sync_setting: Mapped[str] = mapped_column(String, default="two_way")
    # the default WRITABLE calendar — where bookings and synced events land. The
    # first calendar a user connects becomes primary; they can move it later.
    is_primary: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="calendar_accounts")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    invite_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class Plan(Base):
    """A proposed hangout: a place, a day, and an ordered queue of candidate times.

    Voting is a two-stage cascade, evaluated per person (see tools/plan_rules.py):
      stage 1 INTEREST — every member: "coming to the coffee shop Monday?"
                         no  -> out of the plan entirely, never asked a time
                         yes -> immediately handed the active time question
      stage 2 TIME     — the interested cohort only: "does 5 PM work?"
                         no  -> out of THAT time, still in the plan

    Exactly one TimeRound is "active" at a time. Nothing is ever booked by a
    rule — no majority, no unanimity, no auto-booking on silence. The HOST
    (created_by) reads the tally and either confirms the active time or moves
    to the next one, which re-asks the whole interested cohort.
    Status: open -> scheduled | dead (all candidate times used up) | expired
    (the vote deadline passed — see below).

    ASYNC CONVERGENCE. Groups are not all in the app at once, so a plan can
    carry a `deadline_utc`: after it, voting closes and the plan goes `expired`
    — the host can still lock in whatever came in (or push the deadline out to
    reopen it), but the plan stops hanging around silently forever. Until then a
    background ticker nudges the people who haven't answered (`reminder_sent_at`
    rate-limits that). `auto_book` is the opt-in that lets a plan converge with
    NO host present: the moment every member has answered and every interested
    member said yes to the active time, it books itself. It is opt-in precisely
    because the default rule of this app is "a human decides before anything
    reaches a calendar".

    The plan's DAY is not stored — it is derived from the rounds' times in the
    viewer's timezone, so everyone reads the day in their own zone.
    """
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String, default="Group hangout")
    location: Mapped[str | None] = mapped_column(String, default=None)
    status: Mapped[str] = mapped_column(String, default="open")
    # optional "aiming for N people" — lets the host (and the agent) see when
    # enough of the group has said yes; None means no target
    expected_count: Mapped[int | None] = mapped_column(default=None)
    # when voting closes (UTC). None = no deadline, the plan stays open until
    # the host acts. Read via the `deadline` accessor, never raw.
    deadline_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # opt-in: book the active time by itself once everybody said yes to it
    auto_book: Mapped[bool] = mapped_column(default=False)
    # last non-voter nudge, so reminders are rate-limited instead of spammed
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # bearer token for the public vote link (see PlanGuest). None = not shared.
    # Revoking is setting it back to None; regenerating mints a new one, which
    # kills every copy of the old link.
    share_token: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    rounds: Mapped[list["TimeRound"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="TimeRound.ordinal"
    )
    interest_votes: Mapped[list["InterestVote"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    guests: Mapped[list["PlanGuest"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan",
        order_by="PlanGuest.created_at",
    )

    # SQLite drops tzinfo on read (same guard as TimeRound.start/end). The
    # deadline math compares these against an aware "now", and naive-vs-aware
    # comparison raises — so every read of a plan timestamp goes through here.
    @property
    def deadline(self) -> datetime | None:
        return _as_utc(self.deadline_utc)

    @property
    def reminded_at(self) -> datetime | None:
        return _as_utc(self.reminder_sent_at)

    @property
    def created(self) -> datetime:
        return _as_utc(self.created_at)


class InterestVote(Base):
    """Stage 1: one member's yes/no on the plan itself. Re-voting replaces."""
    __tablename__ = "interest_votes"
    __table_args__ = (UniqueConstraint("plan_id", "user_id", name="uq_plan_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    yes: Mapped[bool] = mapped_column()
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    plan: Mapped["Plan"] = relationship(back_populates="interest_votes")
    user: Mapped["User"] = relationship()


class TimeRound(Base):
    """Stage 2: one candidate time for a plan (5 PM, then 7 PM, ...).

    Times are stored in UTC (tz handling happens at the edges, as everywhere).
    ordinal fixes the queue order the host walks through.
    Status: queued -> active -> confirmed | skipped. `booked` flips to True
    once the calendar event is actually written (confirmed != booked, so we
    can never double-book).
    """
    __tablename__ = "time_rounds"
    __table_args__ = (UniqueConstraint("plan_id", "ordinal", name="uq_plan_ordinal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    ordinal: Mapped[int] = mapped_column()
    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, default="queued")
    booked: Mapped[bool] = mapped_column(default=False)
    event_link: Mapped[str | None] = mapped_column(String, default=None)

    plan: Mapped["Plan"] = relationship(back_populates="rounds")
    votes: Mapped[list["TimeVote"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )
    guest_votes: Mapped[list["GuestTimeVote"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )

    # SQLite drops tzinfo on read — these accessors re-attach UTC so no naive
    # datetime ever leaves the model. Always use these, never the raw columns.
    @property
    def start(self) -> datetime:
        dt = self.slot_start_utc
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @property
    def end(self) -> datetime:
        dt = self.slot_end_utc
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class TimeVote(Base):
    """One member's yes/no on ONE candidate time; re-voting replaces the old vote.

    A no here only removes them from THIS time — they stay in the interested
    cohort and are asked again if the host moves to the next time.
    """
    __tablename__ = "time_votes"
    __table_args__ = (UniqueConstraint("round_id", "user_id", name="uq_round_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("time_rounds.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    yes: Mapped[bool] = mapped_column()
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    round: Mapped["TimeRound"] = relationship(back_populates="votes")
    user: Mapped["User"] = relationship()


class PlanGuest(Base):
    """Somebody voting on a plan through its share link, with no Nudgy account.

    The friction that kills group scheduling is "everyone install the app first".
    A share link removes it: one person in the group shares the link, anyone can
    open it, give a name, and answer. Guests are attached to ONE plan — this is
    not a shadow account, it grants nothing beyond that plan's two questions,
    and it disappears with the plan.

    They are pinned to a browser by a signed cookie (auth/guest_tokens.py), which
    is what lets someone change their mind later instead of voting twice. A lost
    cookie means a new guest row, so names are unique per plan — a second "Sam"
    is asked to distinguish themselves rather than silently landing on the first
    Sam's ballot.

    `email` is optional and used for exactly one thing: sending the calendar
    invite if the plan gets booked. No email, no invite — but the vote still
    counts, because requiring an address would rebuild the friction this removes.
    """
    __tablename__ = "plan_guests"
    __table_args__ = (UniqueConstraint("plan_id", "name", name="uq_plan_guest_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    plan: Mapped["Plan"] = relationship(back_populates="guests")
    # so deleting a plan takes its guests' votes with it, the same way a member's
    # votes go with the plan
    interest_votes: Mapped[list["GuestInterestVote"]] = relationship(
        back_populates="guest", cascade="all, delete-orphan"
    )
    time_votes: Mapped[list["GuestTimeVote"]] = relationship(
        back_populates="guest", cascade="all, delete-orphan"
    )

    @property
    def label(self) -> str:
        """How this guest appears in a tally, next to members' emails.

        Unique inside a plan (names are), and can never collide with an email —
        it has a space in it. The "(guest)" suffix is not decoration: the host
        reading the box needs to know which of these people they can't chase in
        the app."""
        return f"{self.name} (guest)"


class GuestInterestVote(Base):
    """A guest's stage-1 answer. Separate table from InterestVote rather than a
    nullable user_id on it: a guest is a different kind of participant, and this
    keeps the members' vote tables exactly as they were — no migration that has
    to relax a NOT NULL on live data."""
    __tablename__ = "guest_interest_votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # one plan per guest, so the guest alone identifies the ballot
    guest_id: Mapped[int] = mapped_column(ForeignKey("plan_guests.id"), unique=True)
    yes: Mapped[bool] = mapped_column()
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    guest: Mapped["PlanGuest"] = relationship(back_populates="interest_votes")


class GuestTimeVote(Base):
    """A guest's stage-2 answer on one candidate time. Re-voting replaces."""
    __tablename__ = "guest_time_votes"
    __table_args__ = (UniqueConstraint("round_id", "guest_id", name="uq_round_guest"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("time_rounds.id"), index=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("plan_guests.id"), index=True)
    yes: Mapped[bool] = mapped_column()
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    guest: Mapped["PlanGuest"] = relationship(back_populates="time_votes")
    round: Mapped["TimeRound"] = relationship(back_populates="guest_votes")


class GroupEvent(Base):
    """An event or task a member created in-app (distinct from poll bookings,
    which live on Poll). kind: 'event' has start/end; 'task' uses start_utc as
    its due date (end_utc mirrors it) and can be checked off via `done`.

    If the creator opted into Google sync, gcal_event_id/gcal_link map to the
    Google Calendar event on the creator's primary calendar (members get it
    via invites, same pattern as booking.py) so deletes can propagate.
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String, default="event")  # event | task
    title: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="Event")
    location: Mapped[str | None] = mapped_column(String, default=None)
    start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    done: Mapped[bool] = mapped_column(default=False)
    # personal=True: this is one member's own thing, not a group outing. It
    # shows on groupmates' calendars only as busy time; anonymous decides
    # whether the title/place are revealed to them (anonymous is the default).
    personal: Mapped[bool] = mapped_column(default=False)
    anonymous: Mapped[bool] = mapped_column(default=True)
    synced: Mapped[bool] = mapped_column(default=False)
    gcal_event_id: Mapped[str | None] = mapped_column(String, default=None)
    gcal_link: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    rsvps: Mapped[list["EventRsvp"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    # same SQLite-drops-tzinfo guard as Poll — always read via these
    @property
    def start(self) -> datetime | None:
        dt = self.start_utc
        return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @property
    def end(self) -> datetime | None:
        dt = self.end_utc
        return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PlaceReview(Base):
    """One member's rating of a place — the agent's taste memory.

    One review per (user, place); re-reviewing replaces. Reviews are shared
    with groupmates (the Places page shows friends' reviews) and injected into
    the agent's prompt so venue suggestions can lean on what people liked.
    """
    __tablename__ = "place_reviews"
    __table_args__ = (UniqueConstraint("user_id", "place", name="uq_user_place"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    place: Mapped[str] = mapped_column(String)
    stars: Mapped[int] = mapped_column()  # 1..5
    text: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship()


class EventRsvp(Base):
    """One member's RSVP to a group event (going | maybe | cant).

    RSVP only applies to in-app group events (GroupEvent), never poll bookings
    — a booked poll round already collected everyone's yes through the vote
    cascade. One RSVP per (event, user); answering again replaces the old one.
    Cascades away with its event (delete-orphan on GroupEvent.rsvps).
    """
    __tablename__ = "event_rsvps"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String)  # going | maybe | cant
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    event: Mapped["GroupEvent"] = relationship(back_populates="rsvps")
    user: Mapped["User"] = relationship()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="memberships")
    group: Mapped["Group"] = relationship(back_populates="memberships")


class AgentUsage(Base):
    """Per-user, per-day count of agent turns — the meter behind the free-tier
    quota. One row per (user, day), where `day` is the user's LOCAL date so the
    allowance resets at their midnight, not UTC's. A "turn" is one user->agent
    message; manual actions never touch this table (see core/quota.py)."""
    __tablename__ = "agent_usage"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date)  # the user's local calendar date
    turns: Mapped[int] = mapped_column(default=0)
