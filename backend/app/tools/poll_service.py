"""Glue between poll storage and the decision rule.

refresh_poll_status() is THE single place a poll's status transitions based on
votes: it re-evaluates the rule and persists open -> approved/rejected.
Called after every vote (REST) and every agent status check, so the state in
the DB always matches the rule. Booking then only trusts poll.status.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Poll
from app.db import repo
from app.tools.poll_rules import APPROVED, REJECTED, PollDecision, evaluate_poll


def refresh_poll_status(session: Session, poll: Poll) -> PollDecision:
    votes = repo.get_poll_votes(session, poll)
    members = repo.get_group_members(session, poll.group_id)
    decision = evaluate_poll(votes, [m.email for m in members], poll.min_yes)

    if poll.status == "open":
        if decision.outcome == APPROVED:
            repo.set_poll_status(session, poll, "approved")
        elif decision.outcome == REJECTED:
            repo.set_poll_status(session, poll, "rejected")
    return decision
