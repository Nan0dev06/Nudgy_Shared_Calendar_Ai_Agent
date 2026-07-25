"""Subscription tiers and what each one is allowed — the entitlement skeleton.

Everyone is on FREE until a payment processor exists (see docs/v1-decisions.md).
The point of building this now is that every feature can be written *gate-aware*
from day one, so turning paid on later is config, not a refactor. It also doubles
as abuse/cost control immediately: the agent is the one resource that costs real
money, so it is quota'd per user per day right away.

Numbers here are PLACEHOLDERS pending a real cost model. Change them in one place.
A "turn" = one user message to the agent -> one complete answer (which may use
several internal LLM/tool steps). Manual actions (polls/events/tasks) cost nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tier identifiers (stored on User.tier). Keep these stable — they're persisted.
FREE = "free"
PRO = "pro"
TEAM = "team"
MAX = "max"

VALID_TIERS = frozenset({FREE, PRO, TEAM, MAX})


@dataclass(frozen=True)
class Entitlements:
    """What a tier may do. Extend with more gates (max_groups, calendars,
    advanced_venues, themes, …) as features land — one field per gate."""
    tier: str
    agent_turns_per_day: int   # user-facing agent messages allowed per local day
    max_persistent_chats: int  # saved/starred/incomplete chats kept server-side


# Placeholder allowances — revisit with a cost model before charging anyone.
_TIERS: dict[str, Entitlements] = {
    FREE: Entitlements(FREE, agent_turns_per_day=8,    max_persistent_chats=10),
    PRO:  Entitlements(PRO,  agent_turns_per_day=100,  max_persistent_chats=50),
    TEAM: Entitlements(TEAM, agent_turns_per_day=150,  max_persistent_chats=50),
    MAX:  Entitlements(MAX,  agent_turns_per_day=1000, max_persistent_chats=200),
}


def normalize_tier(tier: str | None) -> str:
    """Coerce a stored/None tier to a known one; unknown or missing -> free."""
    t = (tier or FREE).lower()
    return t if t in VALID_TIERS else FREE


def entitlements_for(tier: str | None) -> Entitlements:
    """The Entitlements for a tier string; always returns something (free default)."""
    return _TIERS[normalize_tier(tier)]
