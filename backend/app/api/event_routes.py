"""Group events & tasks created in-app (distinct from poll bookings).

GET    /groups/{group_id}/events   -> events + tasks for the group
POST   /groups/{group_id}/events   -> create; optionally sync to Google Calendar
PATCH  /events/{event_id}          {"done": true}  -> check a task off
DELETE /events/{event_id}          -> remove (and delete the Google event too)

Google sync uses the same pattern as booking.py: one event on the creator's
primary calendar with chosen members as attendees, sendUpdates="all" — Google
mirrors it onto everyone's calendar and sends invite emails. The inbound half
of "two-way" is the freebusy-based availability endpoint: whatever people do
in Google Calendar shows up as busy blocks here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import GroupEvent, User
from app.db import repo
from app.db.session import get_session

log = logging.getLogger("orbi.api")

router = APIRouter(tags=["events"])


class CreateEventBody(BaseModel):
    kind: str = Field(default="event", pattern="^(event|task)$")
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="Event", max_length=30)
    location: str | None = Field(default=None, max_length=200)
    start_iso: str | None = None  # events: required; tasks: optional due date
    end_iso: str | None = None
    invite_emails: list[str] = Field(default_factory=list)  # default: everyone
    sync_google: bool = False
    # personal=True: my own thing — groupmates see only busy time; anonymous
    # decides whether they see the title/place (anonymous is the default)
    personal: bool = False
    anonymous: bool = True


class PatchEventBody(BaseModel):
    done: bool


def _require_membership(session: Session, user: User, group_id: int) -> None:
    if group_id not in {g.id for g in repo.get_user_groups(session, user)}:
        raise HTTPException(status_code=403, detail="You are not in this group.")


def _parse_iso(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad {name}: not ISO 8601.")
    if dt.tzinfo is None:
        raise HTTPException(status_code=400, detail=f"Bad {name}: must include a timezone offset.")
    return dt.astimezone(timezone.utc)


def _event_json(
    event: GroupEvent, tz_name: str,
    viewer_id: int | None = None, creator_email: str | None = None,
) -> dict:
    tz = ZoneInfo(tz_name)
    iso = lambda dt: dt.astimezone(tz).isoformat() if dt else None  # noqa: E731
    # someone else's ANONYMOUS personal event: the busy range is all they get
    masked = (
        event.personal and event.anonymous
        and viewer_id is not None and viewer_id != event.created_by
    )
    return {
        "id": event.id,
        "kind": event.kind,
        "title": "Busy" if masked else event.title,
        "category": "Busy" if masked else event.category,
        "location": None if masked else event.location,
        "start_iso": iso(event.start),
        "end_iso": iso(event.end),
        "done": event.done,
        "personal": event.personal,
        "anonymous": event.anonymous,
        "synced": event.synced,
        "gcal_link": None if masked else event.gcal_link,
        "created_by": event.created_by,
        "creator_email": creator_email,
    }


@router.get("/groups/{group_id}/events")
def group_events(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _require_membership(session, user, group_id)
    members = repo.get_group_members(session, group_id)
    email_of = {m.id: m.email for m in members}
    events = repo.get_group_events(session, group_id, member_ids=list(email_of))
    return [
        _event_json(e, user.timezone, viewer_id=user.id,
                    creator_email=email_of.get(e.created_by))
        for e in events
    ]


@router.post("/groups/{group_id}/events")
def create_event(
    group_id: int,
    body: CreateEventBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _require_membership(session, user, group_id)
    start = _parse_iso(body.start_iso, "start_iso")
    end = _parse_iso(body.end_iso, "end_iso")
    if body.kind == "event":
        if start is None or end is None:
            raise HTTPException(status_code=400, detail="Events need start_iso and end_iso.")
        if end <= start:
            raise HTTPException(status_code=400, detail="Event end must be after start.")
    elif start is not None and end is None:
        end = start  # task with a due date

    event = repo.create_event(
        session,
        group_id=group_id, created_by=user.id, kind=body.kind, title=body.title,
        category=body.category, location=body.location,
        start_utc=start, end_utc=end,
        personal=body.personal, anonymous=body.anonymous,
    )

    out = _event_json(event, user.timezone, viewer_id=user.id, creator_email=user.email)
    if body.sync_google and body.kind == "event" and not body.personal:
        out["sync"] = _sync_to_google(session, event, user, body.invite_emails, group_id)
        out["synced"] = event.synced
        out["gcal_link"] = event.gcal_link
    return out


def _sync_to_google(
    session: Session, event: GroupEvent, creator: User,
    invite_emails: list[str], group_id: int,
) -> dict:
    """Best effort: a sync failure never loses the in-app event."""
    if not creator.calendar_connected:
        return {"ok": False, "reason": "Your Google Calendar isn't connected."}
    from googleapiclient.discovery import build

    from app.auth.google import credentials_from_json

    member_emails = {m.email for m in repo.get_group_members(session, group_id)}
    attendees = [e for e in invite_emails if e in member_emails] or sorted(member_emails)
    try:
        creds, refreshed = credentials_from_json(creator.token_json)
        if refreshed:
            repo.set_user_token(session, creator, refreshed)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        gcal_body = {
            "summary": event.title,
            "start": {"dateTime": event.start.astimezone(timezone.utc).isoformat()},
            "end": {"dateTime": event.end.astimezone(timezone.utc).isoformat()},
            "attendees": [{"email": e} for e in attendees],
            "description": "Created in Overlap.",
        }
        if event.location:
            gcal_body["location"] = event.location
        created = service.events().insert(
            calendarId="primary", body=gcal_body, sendUpdates="all"
        ).execute()
        repo.set_event_gcal(session, event, created.get("id"), created.get("htmlLink"))
        log.info("[events] %d synced to Google -> %s", event.id, created.get("htmlLink"))
        return {"ok": True, "event_link": created.get("htmlLink")}
    except Exception as exc:
        log.exception("[events] Google sync failed for event %d", event.id)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


@router.patch("/events/{event_id}")
def patch_event(
    event_id: int,
    body: PatchEventBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    event = repo.get_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="No such event.")
    _require_membership(session, user, event.group_id)
    repo.set_event_done(session, event, body.done)
    return _event_json(event, user.timezone, viewer_id=user.id)


@router.delete("/events/{event_id}")
def delete_event(
    event_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    event = repo.get_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="No such event.")
    _require_membership(session, user, event.group_id)

    gcal_result = None
    if event.synced and event.gcal_event_id:
        gcal_result = _delete_from_google(session, event)
    repo.delete_event(session, event)
    return {"ok": True, "gcal": gcal_result}


def _delete_from_google(session: Session, event: GroupEvent) -> dict:
    """Best effort — the Google copy lives on the creator's calendar, so we
    need the creator's token regardless of who deletes in-app."""
    creator = session.get(User, event.created_by)
    if creator is None or not creator.calendar_connected:
        return {"ok": False, "reason": "Creator's calendar not connected."}
    from googleapiclient.discovery import build

    from app.auth.google import credentials_from_json

    try:
        creds, refreshed = credentials_from_json(creator.token_json)
        if refreshed:
            repo.set_user_token(session, creator, refreshed)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        service.events().delete(
            calendarId="primary", eventId=event.gcal_event_id, sendUpdates="all"
        ).execute()
        return {"ok": True}
    except Exception as exc:
        log.exception("[events] Google delete failed for event %d", event.id)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
