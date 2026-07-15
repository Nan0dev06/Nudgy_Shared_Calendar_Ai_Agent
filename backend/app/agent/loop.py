"""The Orbi agentic loop — a real multi-step tool-using loop, not one LLM call.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone

from openai import OpenAI

from app.agent.prompt import build_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ToolContext, run_tool
from app.core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER

log = logging.getLogger("orbi.agent")

MAX_STEPS = 8  # safety bound on tool-call iterations per user message


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


def run_agent(ctx: ToolContext, history: list[dict], user_message: str) -> AgentResult:
    """Run one turn of Orbi. `history` is prior [{"role","content"}] messages
    (plain strings; not mutated — the caller decides what to persist)."""
    now = ctx.now_utc or datetime.now(timezone.utc)
    system = build_system_prompt(
        user_email=ctx.user.email,
        tz_name=ctx.tz_name,
        now_utc=now,
        group_name=ctx.group.name if ctx.group else None,
        group_id=ctx.group.id if ctx.group else None,
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

    for step in range(MAX_STEPS):
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=_openai_tools(),
            max_tokens=1024,
        )
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
            args = json.loads(tc.function.arguments or "{}")
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
