"""PLAN CASCADE PROOF (no LLM needed): interest -> time -> host decides -> booking.

Exercises the exact code paths the agent tools use, for a plan with two
candidate times tomorrow (17:00 and 19:00 Beirut):
  1. create the plan  -> everyone asked the PLAN; 17:00 is the active time
  2. a member's ballot is the INTEREST question, not a time
  3. that member says yes -> their ballot becomes the TIME question instantly
     (the cascade: one yes opens the second question, no waiting on the group)
  4. they say NO to 17:00 -> still IN for the plan, and nothing auto-rejects
  5. the host cannot lock in 17:00: nobody can make it
  6. host moves on -> 19:00 goes live and the SAME member is asked again
  7. both say yes -> host locks it in -> REAL Google Calendar event
  8. cleanup: delete the test event + the plan

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
from app.tools.plan_rules import INTEREST, TIME
from app.tools.plan_service import advance_to_next_time, confirm_active_time, member_ballot

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

        plan = repo.create_plan(session, group, host, "Nudgy cascade smoke test",
                                slots=[slot(17), slot(19)], location="Test cafe")
        active = repo.get_active_round(session, plan)
        print(f"[1] plan {plan.id} created — 2 candidate times, active is {active.ordinal} (17:00)")

        b = member_ballot(session, plan, friend)
        assert b.stage == INTEREST, b
        print(f"[2] {friend.email} is asked the PLAN first: {b.stage}")

        repo.cast_interest(session, plan, friend, True)
        b = member_ballot(session, plan, friend)
        assert b.stage == TIME, b
        print(f"[3] their yes opened the TIME question on the spot: {b.stage}")

        repo.cast_time_vote(session, active, friend, False)
        assert repo.get_interest_votes(session, plan)[friend.email] is True
        print("[4] they said no to 17:00 — still IN for the plan, plan still open")

        blocked = confirm_active_time(session, plan, host, TZ)
        assert "error" in blocked, blocked
        print(f"[5] host cannot lock in 17:00: {blocked['error']}")

        moved = advance_to_next_time(session, plan, host, TZ)
        assert moved["action"] == "next_time", moved
        b = member_ballot(session, plan, friend)
        assert b.stage == TIME, b
        print(f"[6] host moved on -> {moved['time']} asked again to: {moved['asked']}")

        active2 = repo.get_active_round(session, plan)
        repo.cast_time_vote(session, active2, friend, True)
        repo.cast_time_vote(session, active2, host, True)
        result = confirm_active_time(session, plan, host, TZ)
        assert result.get("action") == "booked", result
        print(f"[7] host locked it in -> BOOKED for {result['attendees']}\n    {result['event_link']}")

        creds, _ = credentials_from_json(host.token_json)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        service.events().delete(calendarId="primary", eventId=result["event_id"],
                                sendUpdates="all").execute()
        session.delete(plan); session.commit()
        print("[8] cleanup done (test event deleted, plan removed)")
        print("\nCASCADE VERIFIED: interest -> time -> host decides -> book/refuse all work.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
