"""POLL ENGINE PROOF (no LLM needed) — against a REAL Google Calendar.

Rewritten for the 2026-08-01 engine (docs/poll-edit-redesign.md §1). The old
script walked a queue: one time was "active", the host advanced, and advancing
re-asked everybody. None of that exists now, so what this proves changed too.

Exercises the exact code paths the agent tools and the API use, for a poll with
two candidate times tomorrow (17:00 and 19:00 Beirut):
  1. create the poll -> BOTH times are live at once; there is no active one
  2. a member's ballot asks about TIMES directly (times were given, so no
     interest question — that is Pick-a-time, not Float-an-idea)
  3. they say NO to 17:00 and IF_NEEDED to 19:00 — one answer per time, and a
     no on one time does not take them out of the poll
  4. the host spotlights 17:00, then moves it to 19:00 — and NO VOTE CHANGES.
     This is the property the whole redesign turns on.
  5. nothing books on its own: the host hasn't answered, so somebody still owes
     an answer and `converge` must decline
  6. the host says yes to 19:00 -> everyone has now answered, 19:00 clears the
     all-members bar on yes+if_needed -> it books ITSELF, no lock-in needed
  7. cleanup: delete the test event + the poll

Needs a host with a connected Google calendar (it writes a real event and then
deletes it).

    python backend/scripts/check_plan_cascade.py
"""
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> backend/

from googleapiclient.discovery import build
from sqlalchemy import select

from app.auth.google import credentials_from_json
from app.db.models import Group
from app.db import repo
from app.db.session import SessionLocal, init_db
from app.tools.plan_rules import IF_NEEDED, NO, TIME, YES
from app.tools.plan_service import (
    converge, load_plan_state, member_ballot, set_spotlight,
)

BEIRUT = ZoneInfo("Asia/Beirut")
TZ = "Asia/Beirut"


def main() -> None:
    init_db()
    session = SessionLocal()
    try:
        group = session.scalar(select(Group))
        users = repo.get_group_members(session, group.id)
        if len(users) < 2:
            sys.exit("Need >= 2 users in the demo group. Run chat_cli.py --tools first.")
        host, friend = users[0], users[1]
        print(f"Group: {group.name} | host: {host.email} | friend: {friend.email}\n")

        tomorrow = datetime.now(BEIRUT).date() + timedelta(days=1)

        def slot(hour: int) -> tuple:
            return (
                datetime.combine(tomorrow, time(hour), tzinfo=BEIRUT).astimezone(timezone.utc),
                datetime.combine(tomorrow, time(hour + 1), tzinfo=BEIRUT).astimezone(timezone.utc),
            )

        # expected_count omitted -> the DEFAULT RULE: every member must be able
        # to make the time before anything books without a human.
        plan = repo.create_plan(session, group, host, "Nudgy poll smoke test",
                                slots=[slot(17), slot(19)], location="Test cafe")
        five, seven = plan.rounds[0], plan.rounds[1]
        assert not plan.asks_interest, "times were given, so it must not ask interest"
        print(f"[1] poll {plan.id} created — 2 candidate times, BOTH votable now "
              f"(no active round)")

        b = member_ballot(session, plan, friend)
        assert b.stage == TIME, b
        assert b.unanswered == 2, b
        print(f"[2] {friend.email} is asked about times directly: "
              f"{b.unanswered} still to answer")

        repo.cast_time_vote(session, five, friend, NO)
        repo.cast_time_vote(session, seven, friend, IF_NEEDED)
        b = member_ballot(session, plan, friend)
        assert b.unanswered == 0, b
        print("[3] they said NO to 17:00 and IF NEEDED to 19:00 — still in the "
              "poll, both answers stand on their own")

        # The headline property: moving the spotlight resets NOTHING. The old
        # advance_to_next_time made every prior vote irrelevant by design.
        before = repo.get_time_votes(session, seven)
        set_spotlight(session, plan, host, five.id, TZ)
        set_spotlight(session, plan, host, seven.id, TZ)
        after = repo.get_time_votes(session, seven)
        assert before == after, (before, after)
        print(f"[4] spotlight moved 17:00 -> 19:00 and every vote survived: {after}")

        # Nobody may be booked over: the host still owes two answers, so no
        # automatic path is allowed to fire yet.
        assert converge(session, plan, TZ) is None
        st = load_plan_state(session, plan)
        print("[5] nothing booked itself — the host hasn't answered, so an "
              "answer could still change the outcome")

        repo.cast_time_vote(session, five, host, NO)
        repo.cast_time_vote(session, seven, host, YES)
        result = converge(session, plan, TZ)
        assert result is not None and result.get("action") == "booked", result
        assert result["round_id"] == seven.id, result
        print(f"[6] last answer landed and 19:00 clears the bar -> it BOOKED "
              f"ITSELF for {result['attendees']}\n    {result['event_link']}")

        account = repo.get_primary_calendar_account(session, host)
        creds, _ = credentials_from_json(account.token_json)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        service.events().delete(calendarId="primary", eventId=result["event_id"],
                                sendUpdates="all").execute()
        session.delete(plan); session.commit()
        print("[7] cleanup done (test event deleted, poll removed)")
        print("\nENGINE VERIFIED: parallel voting, three states, a spotlight that "
              "resets nothing, and convergence that waits for everyone.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
