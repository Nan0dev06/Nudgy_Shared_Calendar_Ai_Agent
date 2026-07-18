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

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    # raw Google Credentials JSON; None until the user connects a calendar
    token_json: Mapped[str | None] = mapped_column(String, default=None)
    timezone: Mapped[str] = mapped_column(String, default="Asia/Beirut")
    # optional user-chosen name; UI falls back to deriving one from the email
    display_name: Mapped[str | None] = mapped_column(String, default=None)
    # unfinished things (event/poll started without a time) — a JSON array the
    # frontend owns entirely; stored server-side so drafts survive across devices
    drafts_json: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def calendar_connected(self) -> bool:
        return self.token_json is not None


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
    Status: open -> scheduled | dead (all candidate times used up).

    The plan's DAY is not stored — it is derived from the rounds' times in the
    viewer's timezone, so everyone reads the day in their own zone.
    """
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String, default="Group hangout")
    location: Mapped[str | None] = mapped_column(String, default=None)
    status: Mapped[str] = mapped_column(String, default="open")
    # optional "aiming for N people" — lets the host (and the agent) see when
    # enough of the group has said yes; None means no target
    expected_count: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    rounds: Mapped[list["TimeRound"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="TimeRound.ordinal"
    )
    interest_votes: Mapped[list["InterestVote"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


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
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
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


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="memberships")
    group: Mapped["Group"] = relationship(back_populates="memberships")
