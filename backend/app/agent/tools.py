"""Agent tools: JSON schemas the model sees + the Python that runs them.

The agent's full toolbox:
- get_group_members  — resolve the group and who has connected a calendar
- find_meeting_slots — live freebusy + intersection + reasonable-hours filter
- suggest_venues     — location-anchored REAL venue search
- create_plan        — put a plan (place + day + candidate times) to the group
- get_plan_status    — the host's decision box: who's in, out, and in/out on the time
- use_next_time      — host move: this time didn't work, ask the cohort the next one
- lock_in_time       — host move: commit the active time, booking its yes-voters

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
from app.tools.plan_service import day_label, time_label

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
        "name": "suggest_venues",
        "description": (
            "Suggest REAL venues near where the group will actually be around a "
            "candidate slot. Reads only the location fields members typed into "
            "their own adjacent events (never titles), anchors a midpoint, and "
            "searches OpenStreetMap for real named places. Venues you mention "
            "MUST come from this tool's results — if it returns none, say so "
            "honestly. Call it AFTER you have a candidate slot the user likes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string", "description": "Candidate slot start, ISO 8601 with offset (from find_meeting_slots)."},
                "end_iso": {"type": "string", "description": "Candidate slot end, ISO 8601 with offset."},
                "kind": {"type": "string", "enum": ["cafe", "restaurant", "bar", "fast_food"],
                         "description": "What kind of place (default cafe)."},
            },
            "required": ["start_iso", "end_iso"],
        },
    },
    {
        "name": "create_plan",
        "description": (
            "Put a plan to the group: one place, one day, and an ORDERED list of "
            "candidate times to try (e.g. 5 PM first, then 7 PM as the fallback). "
            "This starts a two-stage cascade: every member is first asked whether "
            "they're in for the plan at all, and only the people who say yes are "
            "then asked about the first time. Only call this AFTER the user has "
            "confirmed the place, the day, and the times with you — never guess "
            "them. Use start_iso/end_iso values from find_meeting_slots verbatim."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short event title, e.g. 'Coffee catch-up'."},
                "location": {"type": "string", "description": "Where the hangout is, e.g. 'Cafe Younes, Hamra'."},
                "times": {
                    "type": "array",
                    "description": (
                        "Candidate times IN PREFERENCE ORDER, all on the same day. "
                        "The first is asked immediately; the rest are held back and "
                        "only used if the host decides to move on from the first."
                    ),
                    "minItems": 1,
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_iso": {"type": "string", "description": "Slot start, ISO 8601 with offset."},
                            "end_iso": {"type": "string", "description": "Slot end, ISO 8601 with offset."},
                        },
                        "required": ["start_iso", "end_iso"],
                    },
                },
            },
            "required": ["title", "times"],
        },
    },
    {
        "name": "get_plan_status",
        "description": (
            "The host's decision box for a plan: who is in for the plan, who is "
            "out, who hasn't answered, and — for the time currently being asked — "
            "who can make it, who can't, and who is silent. Nothing here decides "
            "anything: relay it to the host and let THEM choose between locking "
            "the time in and moving to the next one. Call it when the user asks "
            "how the plan is going."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "description": "Specific plan; omit for all recent plans."},
            },
            "required": [],
        },
    },
    {
        "name": "use_next_time",
        "description": (
            "HOST MOVE — the current time didn't work out, so drop it and ask the "
            "next candidate time instead. Everyone who said they're in for the "
            "plan gets asked the new time, including people who were fine with the "
            "old one (a different hour is a different question). Only call this "
            "when the host has explicitly said to move on. If no candidate times "
            "are left the plan closes and you should search for fresh ones. This "
            "is about the times ALREADY queued on the plan — it does not search "
            "for new ones, so do not call find_meeting_slots for it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {
                "type": "integer",
                "description": "OMIT this when the group has one open plan — it will be "
                               "used automatically. NEVER guess a number: if you need an "
                               "id, take the exact one from get_plan_status.",
            }},
            "required": [],
        },
    },
    {
        "name": "lock_in_time",
        "description": (
            "HOST MOVE — commit the time currently being asked and put it on the "
            "calendar. ONLY the members who said that time works get the event and "
            "Google's invite email. Only call this when the host has explicitly "
            "said to go ahead with this time — never on your own judgement, never "
            "because the numbers look good, and never while people are still "
            "silent unless the host says so anyway."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {
                "type": "integer",
                "description": "OMIT this when the group has one open plan — it will be "
                               "used automatically. NEVER guess a number: booking the "
                               "wrong plan puts a real event on real calendars. If you "
                               "need an id, take the exact one from get_plan_status.",
            }},
            "required": [],
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


def _parse_slot(args: dict) -> tuple[datetime, datetime] | dict:
    """Shared ISO parsing for tools that take start_iso/end_iso."""
    try:
        start = datetime.fromisoformat(args["start_iso"])
        end = datetime.fromisoformat(args["end_iso"])
    except (KeyError, ValueError) as e:
        return {"error": f"Bad ISO datetime: {e}"}
    if start.tzinfo is None or end.tzinfo is None:
        return {"error": "Datetimes must include a timezone offset (use start_iso/end_iso from find_meeting_slots)."}
    if end <= start:
        return {"error": "Slot end must be after start."}
    return start, end


def _suggest_venues(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    from app.auth.google import credentials_from_json
    from app.tools.locations import suggest_venues_for_slot

    slot = _parse_slot(args)
    if isinstance(slot, dict):
        return slot
    start, end = slot

    members_with_creds = []
    for m in repo.get_group_members(ctx.session, ctx.group.id):
        if not m.calendar_connected:
            continue
        creds, refreshed = credentials_from_json(m.token_json)
        if refreshed:
            repo.set_user_token(ctx.session, m, refreshed)
        members_with_creds.append((m.email, creds))

    return suggest_venues_for_slot(
        members_with_creds, start, end, kind=args.get("kind", "cafe")
    )


def _create_plan(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    raw_times = args.get("times") or []
    if not raw_times:
        return {"error": "A plan needs at least one candidate time."}

    slots = []
    for t in raw_times:
        parsed = _parse_slot(t)
        if isinstance(parsed, dict):
            return parsed
        start, end = parsed
        slots.append((start.astimezone(timezone.utc), end.astimezone(timezone.utc)))
    slots.sort(key=lambda s: s[0])  # the queue is walked in chronological order

    plan = repo.create_plan(
        ctx.session, ctx.group, ctx.user,
        title=args["title"],
        slots=slots,
        location=args.get("location"),
    )
    members = repo.get_group_members(ctx.session, ctx.group.id)
    active = repo.get_active_round(ctx.session, plan)
    log.info("[plan %d] created by %s: %d candidate time(s), asked %d member(s)",
             plan.id, ctx.user.email, len(slots), len(members))
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, ctx.tz_name),
        "asked": [m.email for m in members],
        "first_time": time_label(active, ctx.tz_name) if active else None,
        "times_held_back": [time_label(r, ctx.tz_name) for r in plan.rounds[1:]],
        "note": ("Everyone was asked if they're in for the plan itself. Whoever says "
                 "yes gets asked about the first time straight away. Nothing books "
                 "until the host says so — report back and let them decide."),
    }


def _plan_json(ctx: ToolContext, plan) -> dict:
    from app.tools.plan_service import plan_tally

    t = plan_tally(ctx.session, plan, ctx.tz_name)
    active = repo.get_active_round(ctx.session, plan)
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, ctx.tz_name),
        "status": plan.status,
        "you_are_host": ctx.user.id == plan.created_by,
        "in_for_the_plan": t.interested,
        "out_of_the_plan": t.not_interested,
        "no_answer_on_the_plan": t.no_interest_answer,
        "time_being_asked": time_label(active, ctx.tz_name) if active else None,
        "can_make_it": t.time_yes,
        "cannot_make_it": t.time_no,
        "silent_on_this_time": t.time_waiting,
        "times_left_to_try": [time_label(r, ctx.tz_name)
                              for r in plan.rounds if r.status == "queued"],
        "booked_link": next((r.event_link for r in plan.rounds if r.booked), None),
        "host_box": t.host_note,
    }


def _get_plan_status(ctx: ToolContext, args: dict) -> dict:
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    if args.get("plan_id"):
        plan = repo.get_plan(ctx.session, args["plan_id"])
        if plan is None or plan.group_id != ctx.group.id:
            return {"error": f"No plan {args['plan_id']} in this group."}
        return _plan_json(ctx, plan)
    plans = repo.get_group_plans(ctx.session, ctx.group.id)[:5]
    if not plans:
        return {"plans": [], "note": "No plans yet in this group."}
    return {"plans": [_plan_json(ctx, p) for p in plans]}


def _resolve_plan(ctx: ToolContext, args: dict):
    """Find the plan a host move refers to. Returns a Plan or an error dict.

    The model does not reliably know plan ids — it has been observed inventing
    one ("plan 123") when the host just said "lock it in". A wrong id that
    happens to exist would act on the WRONG plan and put it on real calendars,
    so ids are never trusted blind:
      - omitted    -> the group's open plan, when there is exactly one
      - ambiguous  -> refuse and list the open plans so the model can pick
      - unknown id -> refuse and list them, rather than failing bare (a bare
                      error sends the model wandering into other tools)
    """
    if ctx.group is None:
        return {"error": "The user is not in a group yet."}
    open_plans = repo.get_group_plans(ctx.session, ctx.group.id, only_open=True)
    choices = [{"plan_id": p.id, "title": p.title, "day": day_label(p, ctx.tz_name)}
               for p in open_plans]
    pid = args.get("plan_id")

    if pid is None:
        if len(open_plans) == 1:
            return open_plans[0]
        if not open_plans:
            return {"error": "This group has no open plan to act on."}
        return {"error": "Which plan? Ask the host — do not guess.", "open_plans": choices}

    plan = repo.get_plan(ctx.session, pid)
    if plan is None or plan.group_id != ctx.group.id:
        return {"error": f"There is no plan {pid} in this group. Never guess a plan_id: "
                         "omit it if there is only one open plan, or take the exact id "
                         "from get_plan_status.",
                "open_plans": choices}
    return plan


def _host_move(ctx: ToolContext, args: dict, fn) -> dict:
    """Shared guard for the two host-only moves."""
    plan = _resolve_plan(ctx, args)
    if isinstance(plan, dict):
        return plan
    return fn(ctx.session, plan, ctx.user, ctx.tz_name)


def _use_next_time(ctx: ToolContext, args: dict) -> dict:
    from app.tools.plan_service import advance_to_next_time

    return _host_move(ctx, args, advance_to_next_time)


def _lock_in_time(ctx: ToolContext, args: dict) -> dict:
    from app.tools.plan_service import confirm_active_time

    return _host_move(ctx, args, confirm_active_time)


_DISPATCH = {
    "get_group_members": _get_group_members,
    "find_meeting_slots": _find_meeting_slots,
    "suggest_venues": _suggest_venues,
    "create_plan": _create_plan,
    "get_plan_status": _get_plan_status,
    "use_next_time": _use_next_time,
    "lock_in_time": _lock_in_time,
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
