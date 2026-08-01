"""Agent tools: JSON schemas the model sees + the Python that runs them.

The agent's full toolbox:
- get_group_members  — resolve the group and who has connected a calendar
- find_meeting_slots — live freebusy + intersection + reasonable-hours filter
- suggest_venues     — location-anchored REAL venue search
- create_plan        — put a poll (place + candidate times + minimum) to the group
- get_plan_status    — the host's decision box: every candidate time's standing
- spotlight_time     — host move: lean toward one time (resets no votes)
- lock_in_time       — host move: book a named time for whoever can make it

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
from app.realtime import events_changed, plans_changed
from app.tools.plan_service import day_label, time_label

log = logging.getLogger("nudgy.agent")


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
            "Suggest REAL venues for a candidate slot, anchored one of two ways. "
            "By default it anchors on WHERE THE GROUP ALREADY IS: it reads only "
            "the location fields members typed into their own adjacent events "
            "(never titles) and takes the midpoint. Pass `near` instead to search "
            "an area the USER named. Venues you mention MUST come from this "
            "tool's results — if it returns none, say so honestly. Call it AFTER "
            "you have a candidate slot the user likes, and after you know which "
            "of the two anchors they want."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string", "description": "Candidate slot start, ISO 8601 with offset (from find_meeting_slots)."},
                "end_iso": {"type": "string", "description": "Candidate slot end, ISO 8601 with offset."},
                "kind": {"type": "string", "enum": ["cafe", "restaurant", "bar", "fast_food"],
                         "description": "What kind of place (default cafe)."},
                "near": {
                    "type": "string",
                    "description": (
                        "An area to search in, e.g. 'Hamra, Beirut' — ONLY when the user "
                        "named one. Anchors there instead of on the group's own locations, "
                        "and no calendar is read. Omit it to anchor on where the group "
                        "already is. Never invent this from a guess about where they live."
                    ),
                },
            },
            "required": ["start_iso", "end_iso"],
        },
    },
    {
        "name": "create_plan",
        "description": (
            "Put a poll to the group: one place and a list of candidate times. "
            "Everyone votes on EVERY time at once (yes / no / if needed) — there "
            "is no ordering and no fallback, so list the times chronologically. "
            "Only call this AFTER the user has confirmed the place, the times, "
            "AND the minimum with you — never guess them. Use start_iso/end_iso "
            "values from find_meeting_slots verbatim."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short event title, e.g. 'Coffee catch-up'."},
                "location": {"type": "string", "description": "Where the hangout is, e.g. 'Cafe Younes, Hamra'."},
                "minimum": {
                    "type": "integer",
                    "description": (
                        "How many people must be able to make a time before it books "
                        "itself WITHOUT anyone pressing a button. OMIT IT to use the "
                        "default, which is that every member of the group has to be "
                        "able to make it. Only pass a number when the user actually "
                        "says a smaller group is fine ('4 of us is enough') or their "
                        "stored preferences set one — ASK rather than guessing. A "
                        "number also lets share-link guests count toward it; the "
                        "default never does."
                    ),
                },
                "times": {
                    "type": "array",
                    "description": (
                        "Candidate times, chronological, all votable at once. There is "
                        "no preference order — the group's votes decide which wins."
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
            "The host's decision box for a poll: who is in, who is out, who hasn't "
            "answered, and EVERY candidate time's standing (yes / if-needed / no / "
            "silent, plus whether it clears the minimum). Nothing here decides "
            "anything: relay it and let the HOST choose. Call it when the user asks "
            "how the poll is going, and to get the exact round_id for a host move."
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
        "name": "spotlight_time",
        "description": (
            "HOST MOVE — mark one candidate time as the one the group is leaning "
            "toward. This does NOT skip, close or reset anything: every vote "
            "already cast still counts, and the spotlight can be moved back. It "
            "only highlights the time, sharpens the reminders, and breaks a tie. "
            "Use it when the host says they prefer a time but isn't committing "
            "yet — for committing, use lock_in_time. Say plainly that no votes "
            "were lost, because people expect otherwise."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "round_id": {
                    "type": "integer",
                    "description": "The time to highlight, from get_plan_status. "
                                   "Omit to clear the spotlight.",
                },
                "plan_id": {
                    "type": "integer",
                    "description": "OMIT this when the group has one open plan — it will "
                                   "be used automatically. NEVER guess a number: if you "
                                   "need an id, take the exact one from get_plan_status.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "lock_in_time",
        "description": (
            "HOST MOVE — commit ONE named candidate time and put it on the "
            "calendar. ONLY the people who said that time works (yes or if-needed) "
            "get the event and the invite email. Take round_id from "
            "get_plan_status — never guess it. Only call this when the host has "
            "explicitly said to go ahead with that time: never on your own "
            "judgement, never because the numbers look good, and never while "
            "people are still silent unless the host says so anyway."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {
                "type": "integer",
                "description": "OMIT this when the group has one open plan — it will be "
                               "used automatically. NEVER guess a number: booking the "
                               "wrong plan puts a real event on real calendars. If you "
                               "need an id, take the exact one from get_plan_status.",
            },
                "round_id": {
                    "type": "integer",
                    "description": "REQUIRED — which candidate time to book, from "
                                   "get_plan_status. There is no 'current' time any "
                                   "more; the host names the one they mean.",
                },
            },
            "required": ["round_id"],
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
                    "description": "How many days forward from now to search (e.g. 7 for 'this week'). Use 0 for 'today only'.",
                    "minimum": 0,
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
    from app.calendars import provider_for_account
    from app.tools.locations import suggest_venues_for_slot

    slot = _parse_slot(args)
    if isinstance(slot, dict):
        return slot
    start, end = slot

    # A user-named area needs nobody's calendar, so don't touch their tokens.
    # Location anchoring reads each member's PRIMARY calendar (one "where is this
    # person" signal per member); availability is the place that unions all their
    # calendars, not this.
    members_with_providers = []
    if not args.get("near"):
        for m in repo.get_group_members(ctx.session, ctx.group.id):
            account = repo.get_primary_calendar_account(ctx.session, m)
            if account is None:
                continue
            members_with_providers.append(
                (m.email, provider_for_account(ctx.session, account))
            )

    return suggest_venues_for_slot(
        members_with_providers, start, end,
        kind=args.get("kind", "cafe"),
        near=args.get("near"),
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

    # Don't spawn a second card for a plan the group already has open at the same
    # place and time — the model will re-call create_plan across turns, and the
    # title alone ("hang out" vs "Hang out at Blend Cafe") won't stop it.
    dup = repo.find_duplicate_open_plan(ctx.session, ctx.group, args.get("location"), slots)
    if dup is not None:
        log.info("[plan %d] create_plan skipped — duplicate of an open plan", dup.id)
        return {
            "plan_id": dup.id,
            "title": dup.title,
            "location": dup.location,
            "duplicate": True,
            "note": ("An open poll for this place and these times already exists, so "
                     "a second one was NOT created. Tell the user it's already up — "
                     "call get_plan_status for its standing rather than proposing again."),
        }

    members = repo.get_group_members(ctx.session, ctx.group.id)
    # None means the default rule (every member must be able to make it), which
    # is stored as NULL rather than as len(members) — a number could be reached
    # by guests, and the rule is about these specific people.
    minimum = args.get("minimum")
    plan = repo.create_plan(
        ctx.session, ctx.group, ctx.user,
        title=args["title"],
        slots=slots,
        location=args.get("location"),
        expected_count=minimum,
    )
    log.info("[plan %d] created by %s: %d candidate time(s), bar %s, asked %d member(s)",
             plan.id, ctx.user.email, len(slots), minimum or "all members", len(members))
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, ctx.tz_name),
        "asked": [m.email for m in members],
        "minimum": minimum or f"all {len(members)} members",
        "times": [{"round_id": r.id, "label": time_label(r, ctx.tz_name)}
                  for r in plan.rounds],
        "note": ("Everyone can vote on every time straight away (yes / no / if "
                 "needed). It books itself only once the bar above is met AND "
                 "nobody is left to answer; otherwise the host locks one in. Tell "
                 "the user which bar is in force — they need to know whether it "
                 "waits for everybody."),
    }


def _plan_json(ctx: ToolContext, plan) -> dict:
    from app.tools.plan_service import plan_tally

    from app.tools.plan_service import minimum_for, requires_all_members

    t = plan_tally(ctx.session, plan, ctx.tz_name)
    labels = {r.id: time_label(r, ctx.tz_name) for r in plan.rounds}
    minimum = minimum_for(ctx.session, plan)
    all_members = requires_all_members(plan)
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "location": plan.location,
        "day": day_label(plan, ctx.tz_name),
        "status": plan.status,
        "you_are_host": ctx.user.id == plan.created_by,
        "minimum_to_book_itself": (f"all {minimum} members" if all_members
                                   else minimum),
        "in_for_the_plan": t.interested,
        "out_of_the_plan": t.not_interested,
        "no_answer_on_the_plan": t.no_interest_answer,
        # every candidate at once — round_id is what a host move needs
        "times": [
            {
                "round_id": r.key,
                "label": labels.get(r.key),
                "can_make_it": r.yes,
                "if_needed": r.if_needed,
                "cannot_make_it": r.no,
                "silent": r.waiting,
                "guests_coming": len(r.guest_yes) + len(r.guest_if_needed),
                "clears_the_minimum": r.qualifies(
                    minimum=plan.expected_count, member_total=t.member_total),
                "spotlit": r.key == plan.spotlight_round_id,
            }
            for r in t.times
        ],
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


def _spotlight_time(ctx: ToolContext, args: dict) -> dict:
    from app.tools.plan_service import set_spotlight

    plan = _resolve_plan(ctx, args)
    if isinstance(plan, dict):
        return plan
    return set_spotlight(ctx.session, plan, ctx.user, args.get("round_id"), ctx.tz_name)


def _lock_in_time(ctx: ToolContext, args: dict) -> dict:
    from app.tools.plan_service import confirm_time

    plan = _resolve_plan(ctx, args)
    if isinstance(plan, dict):
        return plan
    round_id = args.get("round_id")
    if round_id is None:
        # No "current" time exists to fall back on, and guessing here books a
        # real event at a time nobody chose.
        return {"error": "Which time? Call get_plan_status and use the exact "
                         "round_id of the time the host named — never guess it."}
    return confirm_time(ctx.session, plan, ctx.user, round_id, ctx.tz_name)


_DISPATCH = {
    "get_group_members": _get_group_members,
    "find_meeting_slots": _find_meeting_slots,
    "suggest_venues": _suggest_venues,
    "create_plan": _create_plan,
    "get_plan_status": _get_plan_status,
    "spotlight_time": _spotlight_time,
    "lock_in_time": _lock_in_time,
}


# Tools that change what other members see. The agent runs inside one member's
# chat request, so without this the group finds out about a plan the agent
# started only on their next refresh — a poke here covers every mutating path
# through the model in one place, rather than inside each tool.
_MUTATING = {"create_plan", "spotlight_time", "lock_in_time"}


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """Execute a tool by name. Logs the call (name + args) for live debugging."""
    log.info("[tool] %s(%s)", name, args)
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        out = fn(ctx, args)
    except Exception as exc:  # surface errors to the model instead of crashing
        log.exception("[tool] %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}
    if name in _MUTATING and ctx.group is not None and not out.get("error"):
        plans_changed(ctx.group.id)
        if out.get("action") == "booked":
            events_changed(ctx.group.id)
    return out
