"""Place reviews — the agent's (and the Places page's) taste memory.

GET    /reviews                    -> my reviews
POST   /reviews                    {"place": "...", "stars": 4, "text": "..."}
                                   -> upsert (one review per place per user)
DELETE /reviews/{review_id}        -> remove one of MY reviews
GET    /groups/{group_id}/reviews  -> every member's reviews (friends' taste),
                                      so a place can show a full profile
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import PlaceReview, User
from app.db import repo
from app.db.session import get_session

log = logging.getLogger("orbi.api")

router = APIRouter(tags=["reviews"])


class ReviewBody(BaseModel):
    place: str = Field(min_length=1, max_length=200)
    stars: int = Field(ge=1, le=5)
    text: str | None = Field(default=None, max_length=500)


def _review_json(r: PlaceReview, email: str | None = None) -> dict:
    out = {
        "id": r.id,
        "place": r.place,
        "stars": r.stars,
        "text": r.text,
        "ts": r.created_at.isoformat() if r.created_at else None,
    }
    if email is not None:
        out["email"] = email
    return out


@router.get("/reviews")
def my_reviews(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return [_review_json(r) for r in repo.get_user_reviews(session, user)]


@router.post("/reviews")
def upsert_review(
    body: ReviewBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    r = repo.upsert_review(
        session, user, body.place.strip(), body.stars,
        (body.text or "").strip() or None,
    )
    log.info("[reviews] %s rated %r %d★", user.email, r.place, r.stars)
    return _review_json(r)


@router.delete("/reviews/{review_id}")
def delete_review(
    review_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    r = repo.get_review(session, review_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such review of yours.")
    repo.delete_review(session, r)
    return {"ok": True}


@router.get("/groups/{group_id}/reviews")
def group_reviews(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if group_id not in {g.id for g in repo.get_user_groups(session, user)}:
        raise HTTPException(status_code=403, detail="You are not in this group.")
    members = repo.get_group_members(session, group_id)
    email_of = {m.id: m.email for m in members}
    rows = repo.get_reviews_for_users(session, list(email_of))
    return [_review_json(r, email_of[r.user_id]) for r in rows]
