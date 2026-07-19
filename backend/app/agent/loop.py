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

from app.agent.prompt import build_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ToolContext, run_tool
from app.core.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_INPUT_TOKENS,
    LLM_MODEL,
    LLM_PROVIDER,
)

log = logging.getLogger("nudgy.agent")

MAX_STEPS = 8  # safety bound on tool-call iterations per user message


def _estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
    """Rough token count for the whole request. No tokenizer dependency — the
    ~4-chars-per-token heuristic is plenty for a size guard (we only need to
    know 'is this way too big', not an exact count). Counts message content,
    any tool-call arguments echoed back, and the tool schemas sent every call."""
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    chars += sum(len(str(tc)) for m in messages for tc in (m.get("tool_calls") or []))
    chars += len(str(tools))
    return chars // 4


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


def _openai_tools() -> list[dict]:
    """Our tool schemas in OpenAI function-calling format."""
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
    ]


def _create_with_retry(client: OpenAI, messages: list[dict], attempts: int = 5):
    """One chat-completion call, retried on Groq's two stochastic failures:

      tool_use_failed — Llama occasionally emits malformed function-call syntax
        and Groq 400s. The next sample is independent, so retry immediately.
      429 rate limit — free-tier bursts. Needs a wait, not a fresh sample.

    Anything else propagates; the caller turns it into a friendly reply."""
    from openai import BadRequestError, RateLimitError

    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(attempts):
        try:
            return client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=_openai_tools(),
                max_tokens=1024,
            )
        except BadRequestError as exc:
            if "tool_use_failed" not in str(exc):
                raise
            last_exc = exc
            log.warning("[loop] malformed tool call from model (attempt %d/%d) — retrying",
                        attempt + 1, attempts)
        except RateLimitError as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                # Free-tier limits reset on a per-minute window, so a real wait
                # (not a token-shaving 1-2s) is what actually clears them.
                delay = min(4 * 2 ** attempt, 30)  # 4s, 8, 16, 30 — capped
                log.warning("[loop] rate-limited (attempt %d/%d) — waiting %ds",
                            attempt + 1, attempts, delay)
                time.sleep(delay)
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


def run_agent(ctx: ToolContext, history: list[dict], user_message: str) -> AgentResult:
    """Run one turn of Nudgy. `history` is prior [{"role","content"}] messages
    (plain strings; not mutated — the caller decides what to persist)."""
    now = ctx.now_utc or datetime.now(timezone.utc)
    system = build_system_prompt(
        user_email=ctx.user.email,
        tz_name=ctx.tz_name,
        now_utc=now,
        group_name=ctx.group.name if ctx.group else None,
        group_id=ctx.group.id if ctx.group else None,
        taste_notes=_taste_notes(ctx),
        memory_notes=_memory_notes(ctx),
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
    tools = _openai_tools()

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

        resp = _create_with_retry(client, messages)
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
                    "content": json.dumps(err),
                })
                continue
            trace.append(TraceStep(kind="tool_call", name=tc.function.name, detail=args))
            result = run_tool(ctx, tc.function.name, args)
            trace.append(TraceStep(kind="tool_result", name=tc.function.name, detail=result))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    log.warning("[loop] hit MAX_STEPS without finishing")
    return AgentResult(
        reply="I got stuck working that out — could you rephrase or narrow it down?",
        trace=trace,
    )
