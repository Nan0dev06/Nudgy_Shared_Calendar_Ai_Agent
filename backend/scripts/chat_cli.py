"""Phase 2 CLI: talk to Nudgy from the terminal, or smoke-test the tools.

Ensures a demo group exists containing all imported users, then:
  --tools     run the tools directly (no LLM, no API key needed) and print
              members + live availability. Proves the tool layer end to end.
  (default)   interactive chat with Nudgy (needs GROQ_API_KEY in .env).

    python backend/scripts/chat_cli.py --tools
    python backend/scripts/chat_cli.py
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> backend/

from sqlalchemy import select

from app.agent.tools import ToolContext, run_tool
from app.db.models import Group, User
from app.db import repo
from app.db.session import SessionLocal, init_db

DEMO_GROUP = "Beirut Crew"


def ensure_demo_group(session) -> Group:
    """Create the demo group (if missing) with every imported user as a member."""
    users = list(session.scalars(select(User)))
    if not users:
        sys.exit("No users. Run connect_account.py then import_tokens.py first.")
    group = session.scalar(select(Group).where(Group.name == DEMO_GROUP))
    if group is None:
        group = repo.create_group(session, DEMO_GROUP, users[0])
        print(f"Created group '{DEMO_GROUP}' (invite code {group.invite_code})")
    for u in users:
        repo.add_member(session, group, u)
    return group


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tools", action="store_true",
                   help="run tools directly without the LLM (no API key needed)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()
    session = SessionLocal()
    try:
        group = ensure_demo_group(session)
        user = repo.get_group_members(session, group.id)[0]
        ctx = ToolContext(
            session=session, user=user, group=group,
            now_utc=datetime.now(timezone.utc), tz_name=user.timezone,
        )

        if args.tools:
            print("\n=== get_group_members ===")
            print(run_tool(ctx, "get_group_members", {}))
            print("\n=== find_meeting_slots (7 days, 60 min) ===")
            print(run_tool(ctx, "find_meeting_slots",
                           {"days_ahead": 7, "duration_minutes": 60}))
            return

        # interactive chat
        from app.agent.loop import run_agent
        print(f"\nNudgy ready. Group: {group.name}, you are {user.email}.")
        print("Type your message (or 'quit').\n")
        history: list[dict] = []
        while True:
            try:
                msg = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if msg.lower() in {"quit", "exit"}:
                break
            if not msg:
                continue
            ctx.now_utc = datetime.now(timezone.utc)  # fresh "now" each turn
            result = run_agent(ctx, history, msg)
            print(f"\nnudgy> {result.reply}\n")
            history.append({"role": "user", "content": msg})
            history.append({"role": "assistant", "content": result.reply})
    finally:
        session.close()


if __name__ == "__main__":
    main()
