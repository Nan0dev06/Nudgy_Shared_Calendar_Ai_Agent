"""The Nudgy agentic loop — a real multi-step tool-using loop, not one LLM call.

Flow per user message:
  build system prompt (with current datetime injected)
    -> the model decides which tool to call
    -> we run the tool, append the result
    -> the model sees the result, calls another tool or writes its answer
    -> repeat until the model stops calling tools
    -> return the reply + a trace of every tool call (for the UI / debugging)

Provider is chosen by LLM_PROVIDER in .env (groq / ollama / openai) — all
speak the OpenAI chat-completions API, so there is exactly one code path.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from openai import OpenAI

from app.agent.fencing import fence, scrub
from app.agent.prompt import build_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ToolContext, run_tool
from app.core.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_FALLBACK_MODEL,
    LLM_MAX_INPUT_TOKENS,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
)

log = logging.getLogger("nudgy.agent")

# Safety bound on tool-call iterations per user message. Lowered 8 -> 5 on
# 2026-08-03: the free tier meters TOKENS PER MINUTE (12,000 on the 70b, 6,000
# on the 8b) and a step costs ~4,000, so a turn that ran to 8 steps could not
# physically finish inside one minute — it spent the last three sleeping on a
# 429. Five is above every turn the tools can actually need: members ->
# freebusy -> venues -> create, with one spare.
MAX_STEPS = 5


def _estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
    """Rough token count for the whole request. No tokenizer dependency — the
    ~4-chars-per-token heuristic is plenty for a size guard (we only need to
    know 'is this way too big', not an exact count). Counts message content,
    any tool-call arguments echoed back, and the tool schemas sent every call."""
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    chars += sum(len(str(tc)) for m in messages for tc in (m.get("tool_calls") or []))
    chars += len(str(tools))
    return chars // 4


# Keys in a tool result whose value is text WE wrote — guidance meant for the
# model ("do NOT invent a venue", "call the tool again with valid JSON"). They
# stay outside the fence so the model still follows them. Everything else a tool
# returns came from the outside world (a map API, someone's calendar, another
# member's typing) and gets fenced as data.
# Adding a key here is a trust decision: its text must never interpolate an
# untrusted value. tools/locations.py keeps that promise by having its notes
# point at a field name instead of inlining the value.
TRUSTED_RESULT_KEYS = ("note", "error", "search_failed")


def _tool_message(name: str, result) -> str:
    """What the model reads back from a tool call.

    Our guidance in the clear; everything the tool gathered from outside inside
    an <untrusted> block. Doing this HERE, in the one place tool output becomes
    a message, is deliberate — a tool added later is covered without its author
    having to remember anything.
    """
    if not isinstance(result, dict):
        return fence(json.dumps(scrub(result), ensure_ascii=False), f"tool.{name}")
    ours = {k: v for k, v in result.items()
            if k in TRUSTED_RESULT_KEYS and v is not None}
    data = {k: v for k, v in result.items() if k not in TRUSTED_RESULT_KEYS}
    if not data:
        return json.dumps(ours, ensure_ascii=False)
    body = fence(json.dumps(scrub(data), ensure_ascii=False), f"tool.{name}")
    if not ours:
        return body
    return f"{json.dumps(ours, ensure_ascii=False)}\n{body}"


@dataclass
class TraceStep:
    """One step of the loop, for display/debugging."""
    kind: str            # "tool_call" | "tool_result" | "text"
    name: str = ""
    detail: dict | str = ""


@dataclass
class AgentResult:
    reply: str
    trace: list[TraceStep] = field(default_factory=list)


# Host moves against an EXISTING poll. Advertising them when the group has no
# open poll costs tokens on every step and buys nothing: there is no round_id to
# read, nothing to spotlight, nothing to lock in. Worse than useless, in fact —
# a model offered lock_in_time with no poll has been seen to invent an id.
PLAN_TOOLS = frozenset({"get_plan_status", "spotlight_time", "lock_in_time"})


def _openai_tools(has_open_plan: bool = True) -> list[dict]:
    """Our tool schemas in OpenAI function-calling format.

    Schemas are the single largest fixed cost in the request — measured at
    roughly half of a 4,056-token call — and they are resent on EVERY step,
    because the chat-completions API is stateless and Groq's prompt caching does
    not apply to these models (verified 2026-08-03: three identical calls each
    charged the full prompt against the per-minute limit). The free tier meters
    TOKENS PER MINUTE, so what a steady-state step costs decides whether a
    multi-step turn finishes or 429s halfway through.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_SCHEMAS
        if has_open_plan or t["name"] not in PLAN_TOOLS
    ]


def _log_usage(resp, model: str) -> None:
    """Record what the call actually cost.

    The free tiers meter a DAILY token budget shared by every beta tester on one
    API key, and nothing here knew how much a turn spent — the ceiling was only
    ever hit, never seen coming. `usage` is on every OpenAI-compatible response;
    a missing one is not worth a line of noise, so it is ignored silently.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    log.info("[loop] usage model=%s prompt=%s completion=%s total=%s",
             model, getattr(usage, "prompt_tokens", "?"),
             getattr(usage, "completion_tokens", "?"),
             getattr(usage, "total_tokens", "?"))


def _create_with_retry(client: OpenAI, messages: list[dict], attempts: int = 5,
                       tools: list[dict] | None = None):
    """One chat-completion call, retried on Groq's two stochastic failures:

      tool_use_failed — Llama occasionally emits malformed function-call syntax
        and Groq 400s. The next sample is independent, so retry immediately.
      429 rate limit — free-tier bursts. Needs a wait, not a fresh sample.

    Then, if the rate limit never cleared, ONE attempt on LLM_FALLBACK_MODEL.
    The daily budgets are per-model, so a main model that has run dry says
    nothing about the smaller one — and a weaker answer beats making someone
    who is mid-plan wait a minute for an error. Not a router: this only runs
    after the real model has genuinely failed.

    Anything else propagates; the caller turns it into a friendly reply."""
    from openai import BadRequestError, RateLimitError

    # Built once by the caller so the advertised set matches the one the token
    # estimate was computed against; the default keeps direct callers working.
    call_tools = _openai_tools() if tools is None else tools

    def _call(model: str):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=call_tools,
            max_tokens=1024,
            temperature=LLM_TEMPERATURE,
        )
        _log_usage(resp, model)
        return resp

    last_exc: Exception = RuntimeError("no attempts made")
    rate_limited = False
    for attempt in range(attempts):
        try:
            return _call(LLM_MODEL)
        except BadRequestError as exc:
            if "tool_use_failed" not in str(exc):
                raise
            last_exc = exc
            log.warning("[loop] malformed tool call from model (attempt %d/%d) — retrying",
                        attempt + 1, attempts)
        except RateLimitError as exc:
            last_exc = exc
            rate_limited = True
            if attempt + 1 < attempts:
                # Free-tier limits reset on a per-minute window, so a real wait
                # (not a token-shaving 1-2s) is what actually clears them.
                delay = min(4 * 2 ** attempt, 30)  # 4s, 8, 16, 30 — capped
                log.warning("[loop] rate-limited (attempt %d/%d) — waiting %ds",
                            attempt + 1, attempts, delay)
                time.sleep(delay)

    # Only for rate limits. A model that kept emitting malformed tool calls is
    # not a quota problem, and a smaller model would do it more, not less.
    if rate_limited and LLM_FALLBACK_MODEL and LLM_FALLBACK_MODEL != LLM_MODEL:
        log.warning("[loop] %s still rate-limited — falling back to %s",
                    LLM_MODEL, LLM_FALLBACK_MODEL)
        try:
            return _call(LLM_FALLBACK_MODEL)
        except Exception as exc:
            log.warning("[loop] fallback model %s failed too: %s",
                        LLM_FALLBACK_MODEL, exc)
    raise last_exc


def _taste_notes(ctx: ToolContext) -> str | None:
    """Members' own place reviews, best-rated first, as prompt lines.

    Capped hard (5 per member / 40 lines total) so a review-happy group can't
    blow up the prompt. Returns None when nobody has reviewed anything."""
    if ctx.group is None or ctx.session is None:
        return None
    try:
        from app.db import repo

        members = repo.get_group_members(ctx.session, ctx.group.id)
        name_of = {m.id: (m.display_name or m.email.split("@")[0]) for m in members}
        reviews = repo.get_reviews_for_users(ctx.session, list(name_of))
    except Exception:  # taste is a nice-to-have; never break the turn over it
        log.exception("[loop] failed to load taste notes")
        return None
    if not reviews:
        return None
    per_member: dict[int, int] = {}
    lines = []
    for r in sorted(reviews, key=lambda r: -r.stars):
        if per_member.get(r.user_id, 0) >= 5 or len(lines) >= 40:
            continue
        per_member[r.user_id] = per_member.get(r.user_id, 0) + 1
        note = f' — "{r.text}"' if r.text else ""
        lines.append(f"- {name_of[r.user_id]}: {r.place} {r.stars}/5{note}")
    return "\n".join(lines)


def _memory_notes(ctx: ToolContext) -> str | None:
    """The user's own agent-memory notes, as prompt bullet lines.

    Stored on the user (memory_json) and owned by the frontend Settings page;
    injected here so the notes the user teaches Nudgy actually reach the model."""
    raw = getattr(ctx.user, "memory_json", None)
    if not raw:
        return None
    try:
        notes = json.loads(raw)
    except (ValueError, TypeError):
        return None
    lines = [f"- {n.strip()}" for n in notes if isinstance(n, str) and n.strip()]
    return "\n".join(lines[:50]) or None


def _has_open_plan(ctx: ToolContext) -> bool:
    """Does this group have a poll the host could actually act on?

    Decides both which tools are advertised and whether the host-moves block is
    in the prompt. Fails OPEN — if the lookup breaks we advertise everything,
    because a missing tool is a broken turn while an extra one is only tokens.
    """
    if ctx.group is None or ctx.session is None:
        return False
    try:
        from app.db import repo

        return bool(repo.get_group_plans(ctx.session, ctx.group.id, only_open=True))
    except Exception:
        log.exception("[loop] open-plan lookup failed — advertising every tool")
        return True


def run_agent(ctx: ToolContext, history: list[dict], user_message: str) -> AgentResult:
    """Run one turn of Nudgy. `history` is prior [{"role","content"}] messages
    (plain strings; not mutated — the caller decides what to persist)."""
    now = ctx.now_utc or datetime.now(timezone.utc)
    has_open_plan = _has_open_plan(ctx)
    system = build_system_prompt(
        user_email=ctx.user.email,
        tz_name=ctx.tz_name,
        now_utc=now,
        group_name=ctx.group.name if ctx.group else None,
        group_id=ctx.group.id if ctx.group else None,
        taste_notes=_taste_notes(ctx),
        memory_notes=_memory_notes(ctx),
        has_open_plan=has_open_plan,
    )

    if not LLM_API_KEY:
        raise RuntimeError(
            f"No API key for LLM_PROVIDER={LLM_PROVIDER}. "
            "Set GROQ_API_KEY (or LLM_API_KEY) in .env."
        )
    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

    messages = (
        [{"role": "system", "content": system}]
        + list(history)
        + [{"role": "user", "content": user_message}]
    )
    trace: list[TraceStep] = []
    tools = _openai_tools(has_open_plan)

    for step in range(MAX_STEPS):
        # Big-intake guard: notice an over-large request BEFORE the model
        # rejects it, and ask the user to narrow rather than surfacing a raw
        # 413. Checked every step because tool results grow the message list.
        if LLM_MAX_INPUT_TOKENS:
            est = _estimate_tokens(messages, tools)
            if est > LLM_MAX_INPUT_TOKENS:
                log.warning("[loop] request too large (~%d tokens > %d) — asking to narrow",
                            est, LLM_MAX_INPUT_TOKENS)
                return AgentResult(
                    reply="This request is large enough that I might hit the model's "
                          "limit. Could you narrow it down — a shorter message, or a "
                          "fresh chat — so I can answer it cleanly?",
                    trace=trace,
                )

        resp = _create_with_retry(client, messages, tools=tools)
        msg = resp.choices[0].message

        if not msg.tool_calls:
            reply = (msg.content or "").strip()
            if reply:
                trace.append(TraceStep(kind="text", detail=reply))
            log.info("[loop] done in %d step(s)", step + 1)
            return AgentResult(reply=reply, trace=trace)

        # echo the assistant's tool-call turn back verbatim
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                # Llama sometimes sends `null` (or a bare array) as the whole
                # argument object; json.loads then yields None/list and every
                # tool that does args.get(...) would crash. Treat any non-object
                # as "no arguments" — the optional-arg tools handle {} fine.
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                # Same root cause as tool_use_failed, but Groq let this one
                # through. Hand the error back as the tool result so the model
                # can re-issue the call, instead of killing the whole turn.
                log.warning("[loop] unparseable arguments for %s: %r",
                            tc.function.name, tc.function.arguments)
                err = {"error": "Your arguments were not valid JSON. "
                                "Call the tool again with a valid JSON object."}
                trace.append(TraceStep(kind="tool_result", name=tc.function.name, detail=err))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _tool_message(tc.function.name, err),
                })
                continue
            trace.append(TraceStep(kind="tool_call", name=tc.function.name, detail=args))
            result = run_tool(ctx, tc.function.name, args)
            trace.append(TraceStep(kind="tool_result", name=tc.function.name, detail=result))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _tool_message(tc.function.name, result),
            })

    log.warning("[loop] hit MAX_STEPS without finishing")
    return AgentResult(
        reply="I got stuck working that out — could you rephrase or narrow it down?",
        trace=trace,
    )
