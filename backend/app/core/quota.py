"""Per-user daily agent-turn quota — the meter and the gate.

The agent is the only part of Nudgy that costs real money, and on a shared free
LLM key one heavy user could otherwise drain the whole day's budget for everyone.
This enforces each tier's `agent_turns_per_day` (see core/entitlements.py),
counted in the USER'S local day so the allowance resets at their midnight.

Only a real agent turn calls this — manual actions (polls/events/tasks) never do.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entitlements import entitlements_for
from app.db.models import AgentUsage, User


@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool   # is at least one more turn permitted right now?
    used: int       # turns used so far today
    limit: int      # this user's tier allowance

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def _local_today(user: User) -> date:
    """Today's date in the user's timezone (falls back to UTC on an unknown tz)."""
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).date()


def _usage_row(session: Session, user: User, day: date) -> AgentUsage | None:
    return session.scalar(
        select(AgentUsage).where(
            AgentUsage.user_id == user.id, AgentUsage.day == day
        )
    )


def check_quota(session: Session, user: User) -> QuotaStatus:
    """Read-only: how many turns used today, and whether one more is allowed.
    Call this BEFORE running the agent; only run if `allowed`."""
    limit = entitlements_for(user.tier).agent_turns_per_day
    row = _usage_row(session, user, _local_today(user))
    used = row.turns if row else 0
    return QuotaStatus(allowed=used < limit, used=used, limit=limit)


def record_turn(session: Session, user: User) -> QuotaStatus:
    """Count one agent turn against today's allowance (upsert). Call this only
    after a turn actually ran, so an outage on our side never burns the user's
    quota. Returns the status AFTER incrementing."""
    day = _local_today(user)
    row = _usage_row(session, user, day)
    if row is None:
        row = AgentUsage(user_id=user.id, day=day, turns=1)
        session.add(row)
    else:
        row.turns += 1
    session.commit()
    limit = entitlements_for(user.tier).agent_turns_per_day
    return QuotaStatus(allowed=row.turns < limit, used=row.turns, limit=limit)
