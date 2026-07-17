"""SQLite schema (SQLAlchemy 2.0). Deliberately tiny — 3 tables for Phase 2
(polls + votes get added in Phase 3).

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


class Poll(Base):
    """A proposed meeting slot the group votes on.

    Times are stored in UTC (tz handling happens at the edges, as everywhere).
    min_yes is fixed at creation (decide-once rule): book only if there are
    zero NO votes AND at least min_yes YES votes.
    Status: open -> approved | rejected | cancelled. `booked` flips to True
    once the calendar event is actually written (approved != booked, so we
    can never double-book).
    """
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String, default="Group meetup")
    location: Mapped[str | None] = mapped_column(String, default=None)
    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    min_yes: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String, default="open")
    booked: Mapped[bool] = mapped_column(default=False)
    event_link: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    votes: Mapped[list["Vote"]] = relationship(
        back_populates="poll", cascade="all, delete-orphan"
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


class Vote(Base):
    """One member's yes/no on a poll; voting again replaces the old vote."""
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="uq_poll_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    yes: Mapped[bool] = mapped_column()
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    poll: Mapped["Poll"] = relationship(back_populates="votes")
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


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="memberships")
    group: Mapped["Group"] = relationship(back_populates="memberships")
