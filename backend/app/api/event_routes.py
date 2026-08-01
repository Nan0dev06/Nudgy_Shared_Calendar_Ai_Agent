"""Group events & tasks created in-app (distinct from poll bookings).

GET    /groups/{group_id}/events   -> events + tasks for the group
POST   /groups/{group_id}/events   -> create; optionally sync to Google Calendar
PATCH  /events/{event_id}          -> check a task off, and/or edit its fields
DELETE /events/{event_id}          -> remove (and delete the Google event too)

Who may do what lives in tools/event_rules.py, not here: personal events belong
to their owner, shared events can't be edited by any one person (changes go to
a group vote — not built yet), and only the creator deletes.

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
from app.calendars import provider_for_account
from app.db.models import GroupEvent, User
from app.db import repo
from app.db.session import get_session
from app.realtime import events_changed
from app.tools import event_rules

log = logging.getLogger("nudgy.api")

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
    """Tick a task off, and/or edit an event's fields.

    Every field is optional and "was it sent?" is read from `model_fields_set`,
    not from the value — otherwise clearing a location (`null`) would be
    indistinguishable from not mentioning it. `kind` and `personal` are absent
    on purpose: see repo.update_event.
    """
    done: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=30)
    location: str | None = Field(default=None, max_length=200)
    start_iso: str | None = None
    end_iso: str | None = None
    # the owner revealing (or re-hiding) their own personal event's details
    anonymous: bool | None = None


# Everything here is a change to what the event IS, so all of it goes through
# can_edit. `done` is the exception and is handled separately.
EDIT_FIELDS = frozenset(
    {"title", "category", "location", "start_iso", "end_iso", "anonymous"}
)


class RsvpBody(BaseModel):
    status: str = Field(pattern="^(going|maybe|cant)$")


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
    rsvps: dict[str, str] | None = None,
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
        # {email: going|maybe|cant} — masked personal events never expose these
        "rsvps": {} if masked else (rsvps or {}),
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
        _event_json(
            e, user.timezone, viewer_id=user.id,
            creator_email=email_of.get(e.created_by),
            rsvps={email_of[r.user_id]: r.status
                   for r in e.rsvps if r.user_id in email_of},
        )
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
    _announce(event)
    return out


def _announce(event: GroupEvent) -> None:
    """Poke the group's live feed (app/realtime) so everyone's calendar catches
    up without a refresh. Personal events are skipped on purpose — nobody else
    can see them, so a poke would only cost every member a pointless refetch."""
    if not event.personal:
        events_changed(event.group_id)


def _sync_to_google(
    session: Session, event: GroupEvent, creator: User,
    invite_emails: list[str], group_id: int,
) -> dict:
    """Best effort: a sync failure never loses the in-app event."""
    account = repo.get_primary_calendar_account(session, creator)
    if account is None:
        return {"ok": False, "reason": "Your Google Calendar isn't connected."}
    # Respect the calendar's sync setting: "none" opts it out of outbound writes.
    if not repo.account_syncs_out(account):
        return {"ok": False, "reason": "Sync is off for your primary calendar (change it in Settings → Calendars)."}

    member_emails = {m.email for m in repo.get_group_members(session, group_id)}
    attendees = [e for e in invite_emails if e in member_emails] or sorted(member_emails)
    try:
        provider = provider_for_account(session, account)
        created = provider.create_event(
            summary=event.title,
            start=event.start,
            end=event.end,
            attendee_emails=attendees,
            location=event.location,
            description="Created in Nudgy.",
        )
        repo.set_event_gcal(session, event, created.id, created.link)
        log.info("[events] %d synced to Google -> %s", event.id, created.link)
        return {"ok": True, "event_link": created.link}
    except Exception as exc:
        log.exception("[events] Google sync failed for event %d", event.id)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _check(decision) -> None:
    """Turn an event_rules.Decision into a 403 carrying its own explanation."""
    if not decision:
        raise HTTPException(status_code=403, detail=decision.reason)


@router.patch("/events/{event_id}")
def patch_event(
    event_id: int,
    body: PatchEventBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Tick a task off, and/or edit the event itself.

    Two different permissions, checked separately: finishing shared work is
    collaborative, changing what the thing IS is not. See tools/event_rules.py.
    """
    event = repo.get_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="No such event.")
    _require_membership(session, user, event.group_id)

    sent = body.model_fields_set
    if not sent:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    edits = sent & EDIT_FIELDS

    creator = session.get(User, event.created_by)
    creator_email = creator.email if creator else None
    if "done" in sent:
        _check(event_rules.can_toggle_done(event, user.id, creator_email))
    if edits:
        _check(event_rules.can_edit(event, user.id, creator_email))
        # Only personal events are editable today, and those never sync out
        # (see create_event), so this is unreachable — it exists so that if the
        # group-vote path ever reaches here before the provider write is wired,
        # it fails loudly instead of letting Nudgy and Google quietly disagree.
        if event.synced and event.gcal_event_id:
            raise HTTPException(
                status_code=409,
                detail="This event is on a real calendar and can't be edited here yet.",
            )

    fields: dict = {}
    if "done" in sent:
        fields["done"] = body.done
    for name in ("title", "category", "location", "anonymous"):
        if name in sent:
            fields[name] = getattr(body, name)

    if "start_iso" in sent or "end_iso" in sent:
        start = _parse_iso(body.start_iso, "start_iso") if "start_iso" in sent else event.start
        end = _parse_iso(body.end_iso, "end_iso") if "end_iso" in sent else event.end
        if event.kind == "event":
            if start is None or end is None:
                raise HTTPException(status_code=400, detail="Events need a start and an end.")
            if end <= start:
                raise HTTPException(status_code=400, detail="Event end must be after start.")
        else:
            # A task's due date is start_utc and end_utc mirrors it — the same
            # shape create_event builds (see the GroupEvent docstring).
            end = start
        fields["start_utc"] = start
        fields["end_utc"] = end

    repo.update_event(session, event, **fields)
    _announce(event)
    return _event_json(
        event, user.timezone, viewer_id=user.id, creator_email=creator_email,
    )


@router.post("/events/{event_id}/rsvp")
def rsvp_event(
    event_id: int,
    body: RsvpBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Record the caller's RSVP (going/maybe/cant) to a group event.

    Only real group events take an RSVP: personal events are one person's own
    thing, and poll bookings settled attendance through the vote cascade."""
    event = repo.get_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="No such event.")
    _require_membership(session, user, event.group_id)
    if event.personal:
        raise HTTPException(status_code=400, detail="Personal events don't take RSVPs.")
    if event.kind != "event":
        raise HTTPException(status_code=400, detail="Only events take RSVPs.")
    repo.upsert_rsvp(session, event, user, body.status)
    _announce(event)

    members = repo.get_group_members(session, event.group_id)
    email_of = {m.id: m.email for m in members}
    return _event_json(
        event, user.timezone, viewer_id=user.id,
        creator_email=email_of.get(event.created_by),
        rsvps={email_of[r.user_id]: r.status
               for r in event.rsvps if r.user_id in email_of},
    )


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
    creator = session.get(User, event.created_by)
    _check(event_rules.can_delete(event, user.id, creator.email if creator else None))

    gcal_result = None
    if event.synced and event.gcal_event_id:
        gcal_result = _delete_from_google(session, event)
    personal, group_id = event.personal, event.group_id
    repo.delete_event(session, event)
    if not personal:  # the row is gone — read what _announce needs before that
        events_changed(group_id)
    return {"ok": True, "gcal": gcal_result}


def _delete_from_google(session: Session, event: GroupEvent) -> dict:
    """Best effort — the Google copy lives on the creator's calendar, so we
    need the creator's token regardless of who deletes in-app."""
    creator = session.get(User, event.created_by)
    account = repo.get_primary_calendar_account(session, creator) if creator else None
    if account is None:
        return {"ok": False, "reason": "Creator's calendar not connected."}
    try:
        provider = provider_for_account(session, account)
        provider.delete_event(event.gcal_event_id)
        return {"ok": True}
    except Exception as exc:
        log.exception("[events] Google delete failed for event %d", event.id)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
