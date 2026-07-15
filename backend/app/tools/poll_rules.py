"""The poll decision rule — implemented exactly once, as a pure function.

Spec (decide-once, implement exactly):
- Book only if NO member has voted NO and at least `min_yes` members voted YES.
- If someone votes NO -> do not book; offer an alternative slot.
- If not everyone has voted (and nobody said NO, threshold not met yet) ->
  the poll stays PENDING; the agent asks the requester whether to proceed,
  nudge, or re-plan. This function only *reports* that state — it never
  auto-books on silence, and it never books over a decline.
"""
from __future__ import annotations

from dataclasses import dataclass

APPROVED = "approved"
REJECTED = "rejected"
PENDING = "pending"


@dataclass
class PollDecision:
    outcome: str                 # APPROVED | REJECTED | PENDING
    yes_count: int
    no_count: int
    missing_voters: list[str]    # emails of members who haven't voted
    reason: str                  # human-readable, for the agent to relay


def evaluate_poll(
    votes: dict[str, bool],      # email -> yes/no, only members who voted
    member_emails: list[str],
    min_yes: int,
) -> PollDecision:
    yes_count = sum(1 for v in votes.values() if v)
    no_count = sum(1 for v in votes.values() if not v)
    missing = [e for e in member_emails if e not in votes]

    if no_count > 0:
        decliners = sorted(e for e, v in votes.items() if not v)
        return PollDecision(
            REJECTED, yes_count, no_count, missing,
            f"{', '.join(decliners)} voted no — this slot must not be booked. "
            "Offer to find an alternative.",
        )
    if yes_count >= min_yes:
        return PollDecision(
            APPROVED, yes_count, no_count, missing,
            f"{yes_count} yes, 0 no (needed {min_yes}) — approved for booking.",
        )
    return PollDecision(
        PENDING, yes_count, no_count, missing,
        f"{yes_count}/{min_yes} yes so far, no declines. "
        + (f"Still waiting on: {', '.join(missing)}. " if missing else "")
        + "Ask the requester whether to proceed, nudge the others, or re-plan.",
    )
