"""Agent tools: JSON schemas the model sees + the Python that runs them.

The agent's full toolbox:
- get_group_members  — resolve the group and who has connected a calendar
- find_meeting_slots — live freebusy + intersection + reasonable-hours filter
- create_poll        — propose a confirmed slot to the group (Phase 3)
- get_poll_status    — tallies + the decision rule's verdict (Phase 3)
- book_meeting       — write an APPROVED poll to everyone's calendar (Phase 3)

Each tool logs its invocation so the agent loop is visible in the server log
(the "show the judges the loop" requirement).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agent.availability import compute_availability
from app.db.models import Group, User
from app.db import repo

log = logging.getLogger("orbi.agent")


@dataclass
class ToolContext:
    """Everything the tools need that doesn't come from the model's arguments."""
    session: Session
    user: User
    group: Group | None
    now_utc: datetime
    tz_name: str


# --- Schemas advertised to the model (converted per provider in loop.py) -----

TOOL_SCHEMAS = [
    {
        "name": "get_group_members",
        "description": (
            "List the members of the user's group and whether each has connected "
            "a Google Calendar. Call this first to understand the group before "
            "checking availability. Takes no arguments."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "create_poll",
        "description": (
            "Propose a specific meeting slot to the group as a yes/no poll. Only "
            "call this AFTER the user has confirmed the slot you proposed. Use the "
            "start_iso/end_iso values returned by find_meeting_slots verbatim. "
            "min_yes defaults to the full member count (unanimous) unless the user "
            "asked for a lower threshold."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short event title, e.g. 'Coffee catch-up'."},
                "start_iso": {"type": "string", "description": "Slot start, ISO 8601 with offset."},
                "end_iso": {"type": "string", "description": "Slot end, ISO 8601 with offset."},
                "location": {"type": "string", "description": "Optional meeting place."},
                "min_yes": {"type": "integer", "minimum": 1,
                            "description": "Minimum YES votes required (default: all members)."},
            },
            "required": ["title", "start_iso", "end_iso"],
        },
    },
    {
        "name": "get_poll_status",
        "description": (
            "Check the group's polls and their vote tallies. Re-evaluates the "
            "decision rule (any NO = rejected; enough YES and zero NO = approved; "
            "otherwise pending). Call this when the user asks how a poll is going, "
            "or before booking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "poll_id": {"type": "integer", "description": "Specific poll; omit for all recent polls."},
            },
            "required": [],
        },
    },
    {
        "name": "book_meeting",
        "description": (
            "Write an APPROVED poll's event to Google Calendar: every member gets "
            "the event + Google's invite email. Only works when the poll is "
            "approved — never call it for pending or rejected polls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "poll_id": {"type": "integer"},
            },
            "required": ["poll_id"],
        },
    },
    {
        "name": "find_meeting_slots",
        "description": (
            "Compute, LIVE, the time windows when all calendar-connected members "
            "of the group are free. Fetches fresh free/busy data (busy ranges "
            "only — never event details) and returns common slots within "
            "reasonable local hours, plus, when no slot works for everyone, the "
            "closest partial windows (where most members are free)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days forward from now to search (e.g. 7 for 'this week').",
                    "minimum": 1,
                    "maximum": 30,
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Required meeting length in minutes (default 60).",
                    "minimum": 15,
                    "maximum": 480,
                },
                "earliest_hour": {
                    "type": "integer",
                    "description": "Earliest acceptable local hour, 0-23 (default 9). Use to honor 'evenings' etc.",
                    "minimum": 0,
                    "maximum": 23,
                },
                "latest_hour": {
                    "type": "integer",
                    "description": "Latest acceptable local hour, 0-23 (default 22).",
                    "minimum": 0,
                    "maximum": 23,
                },
            },
            "required": ["days_ahead"],
        },
    },
]


# --- Implementations ---------------------------------------------------------

def _get_group_members(ctx: ToolContext, _args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    members = repo.get_group_members(ctx.session, ctx.group.id)
    return {
        "group_name": ctx.group.name,
        "member_count": len(members),
        "members": [
            {"email": m.email, "calendar_connected": m.calendar_connected}
            for m in members
        ],
    }


def _find_meeting_slots(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    return compute_availability(
        session=ctx.session,
        group=ctx.group,
        now=ctx.now_utc,
        days_ahead=args["days_ahead"],
        duration_minutes=args.get("duration_minutes", 60),
        tz_name=ctx.tz_name,
        earliest_hour=args.get("earliest_hour", 9),
        latest_hour=args.get("latest_hour", 22),
    )


def _create_poll(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    try:
        start = datetime.fromisoformat(args["start_iso"])
        end = datetime.fromisoformat(args["end_iso"])
    except ValueError as e:
        return {"error": f"Bad ISO datetime: {e}"}
    if start.tzinfo is None or end.tzinfo is None:
        return {"error": "Datetimes must include a timezone offset (use start_iso/end_iso from find_meeting_slots)."}
    if end <= start:
        return {"error": "Slot end must be after start."}

    members = repo.get_group_members(ctx.session, ctx.group.id)
    min_yes = args.get("min_yes") or len(members)
    poll = repo.create_poll(
        ctx.session, ctx.group, ctx.user,
        title=args["title"],
        slot_start_utc=start.astimezone(timezone.utc),
        slot_end_utc=end.astimezone(timezone.utc),
        min_yes=min(min_yes, len(members)),
        location=args.get("location"),
    )
    return {
        "poll_id": poll.id, "title": poll.title, "status": poll.status,
        "min_yes": poll.min_yes, "members_to_vote": [m.email for m in members],
        "note": "Poll created. Members vote yes/no in the app; check with get_poll_status.",
    }


def _poll_json(ctx: ToolContext, poll) -> dict:
    from app.tools.poll_service import refresh_poll_status
    from zoneinfo import ZoneInfo

    decision = refresh_poll_status(ctx.session, poll)
    tz = ZoneInfo(ctx.tz_name)
    return {
        "poll_id": poll.id,
        "title": poll.title,
        "slot": f"{poll.start.astimezone(tz):%a %d %b %H:%M} - {poll.end.astimezone(tz):%H:%M} ({ctx.tz_name})",
        "status": poll.status,
        "booked": poll.booked,
        "event_link": poll.event_link,
        "yes": decision.yes_count, "no": decision.no_count,
        "min_yes": poll.min_yes,
        "waiting_on": decision.missing_voters,
        "rule_says": decision.reason,
    }


def _get_poll_status(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    if args.get("poll_id"):
        poll = repo.get_poll(ctx.session, args["poll_id"])
        if poll is None or poll.group_id != ctx.group.id:
            return {"error": f"No poll {args['poll_id']} in this group."}
        return _poll_json(ctx, poll)
    polls = repo.get_group_polls(ctx.session, ctx.group.id)[:5]
    return {"polls": [_poll_json(ctx, p) for p in polls]} if polls else {
        "polls": [], "note": "No polls yet in this group."}


def _book_meeting(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    from app.tools.booking import book_poll_event
    from app.tools.poll_service import refresh_poll_status

    poll = repo.get_poll(ctx.session, args["poll_id"])
    if poll is None or poll.group_id != ctx.group.id:
        return {"error": f"No poll {args['poll_id']} in this group."}
    refresh_poll_status(ctx.session, poll)  # make status current before booking
    members = repo.get_group_members(ctx.session, ctx.group.id)
    return book_poll_event(ctx.session, poll, ctx.user, [m.email for m in members])


_DISPATCH = {
    "get_group_members": _get_group_members,
    "find_meeting_slots": _find_meeting_slots,
    "create_poll": _create_poll,
    "get_poll_status": _get_poll_status,
    "book_meeting": _book_meeting,
}


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """Execute a tool by name. Logs the call (name + args) for live debugging."""
    log.info("[tool] %s(%s)", name, args)
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(ctx, args)
    except Exception as exc:  # surface errors to the model instead of crashing
        log.exception("[tool] %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}
