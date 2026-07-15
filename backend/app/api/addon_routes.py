"""Google Calendar Add-on endpoint — a thin, secret-authenticated door into
the SAME agent loop the web app uses. No cookie session here: the Apps
Script add-on calls this server-to-server (from Google's infrastructure, not
the user's browser), so it authenticates with a shared secret header instead.
That secret lives only in the Add-on's Script Properties, never in a browser.

POST /addon/chat
  headers: X-Orbi-Addon-Secret: <ADDON_SHARED_SECRET>
  body:    {"email": "...", "message": "...", "history": [...]}

The add-on never manages groups — that stays the web app's job. If the email
isn't a connected Orbi user yet, or has no group, Orbi just says so and points
back to the web app rather than trying to replicate that UI in a sidebar.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.loop import run_agent
from app.agent.tools import ToolContext
from app.core.config import ADDON_SHARED_SECRET
from app.db import repo
from app.db.session import get_session

router = APIRouter(prefix="/addon", tags=["addon"])


class AddonHistoryItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class AddonChatBody(BaseModel):
    email: str
    message: str = Field(min_length=1, max_length=2000)
    history: list[AddonHistoryItem] = Field(default_factory=list, max_length=40)


def _check_secret(x_orbi_addon_secret: str | None = Header(default=None)) -> None:
    if not ADDON_SHARED_SECRET or x_orbi_addon_secret != ADDON_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Bad or missing add-on secret.")


@router.post("/chat")
def addon_chat(
    body: AddonChatBody,
    session: Session = Depends(get_session),
    _: None = Depends(_check_secret),
):
    user = repo.get_user_by_email(session, body.email)
    if user is None:
        return {
            "reply": (
                f"I don't recognize {body.email} yet — connect your Google "
                "Calendar in the Orbi web app first, then come back here."
            )
        }

    groups = repo.get_user_groups(session, user)
    group = groups[0] if groups else None
    if group is None:
        return {
            "reply": (
                "You're not in a group yet — create or join one in the Orbi "
                "web app, then I can help you schedule with them here."
            )
        }

    ctx = ToolContext(
        session=session, user=user, group=group,
        now_utc=datetime.now(timezone.utc), tz_name=user.timezone,
    )
    result = run_agent(ctx, [h.model_dump() for h in body.history], body.message)
    return {"reply": result.reply}
