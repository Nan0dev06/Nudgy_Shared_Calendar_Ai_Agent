"""Chat endpoint: one turn of the Nudgy agent.

POST /chat  {"group_id": 1, "message": "...", "history": [...]}
  -> {"reply": "...", "trace": [...]}

Stateless by design: the frontend holds the conversation history and sends it
back each turn (simplest thing that works; the API itself stays sessionless).
`trace` lists every tool call the agent made this turn so the UI can render
the reasoning steps.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.loop import run_agent
from app.agent.tools import ToolContext
from app.api.deps import get_current_user
from app.db.models import User
from app.db import repo

log = logging.getLogger("nudgy.api")


def _friendly_error(exc: Exception) -> str:
    """Turn an unexpected agent/LLM/DB failure into a readable orb reply so the
    frontend never chokes on a raw 500. Names the error type so we can tell
    a rate limit from a DB error from a bug when debugging."""
    name = type(exc).__name__
    text = str(exc).lower()
    # Groq's free tier signals rate limits two ways: a 429 RateLimitError, and a
    # 413 on the per-minute token cap (code "rate_limit_exceeded", "tokens per
    # minute") when a heavy multi-tool turn is too big for the TPM window. Both
    # mean "wait a minute", so catch the 413 form too — not just 429.
    if ("ratelimit" in name.lower() or "429" in text or "rate limit" in text
            or "rate_limit" in text or "tokens per minute" in text):
        return ("I'm being rate-limited by the model service right now "
                "(free tier). Give it a minute and try again.")
    return f"Sorry — I hit a problem finishing that ({name}). Please try again."
from app.db.session import get_session

router = APIRouter(tags=["chat"])


class HistoryItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatBody(BaseModel):
    group_id: int | None = None
    message: str = Field(min_length=1, max_length=2000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=40)


@router.post("/chat")
def chat(
    body: ChatBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = None
    if body.group_id is not None:
        my_groups = {g.id: g for g in repo.get_user_groups(session, user)}
        group = my_groups.get(body.group_id)
        if group is None:
            raise HTTPException(status_code=403, detail="You are not in this group.")

    ctx = ToolContext(
        session=session,
        user=user,
        group=group,
        now_utc=datetime.now(timezone.utc),  # fresh "now" injected every turn
        tz_name=user.timezone,
    )
    try:
        result = run_agent(
            ctx,
            history=[h.model_dump() for h in body.history],
            user_message=body.message,
        )
    except Exception as exc:  # LLM API error, DB error, anything unexpected
        log.exception("chat turn failed for %s", user.email)
        return {"reply": _friendly_error(exc), "trace": []}
    return {"reply": result.reply, "trace": [asdict(s) for s in result.trace]}
