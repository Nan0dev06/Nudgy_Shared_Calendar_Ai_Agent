"""PHASE 3 PROOF (no LLM needed): poll -> votes -> threshold rule -> booking.

Exercises the exact code paths the agent tools use:
  1. create a poll for tomorrow 19:00-20:00 Beirut (min_yes = all members)
  2. one YES  -> rule says PENDING (silence never books)
  3. all YES  -> rule says APPROVED
  4. book     -> REAL Google Calendar event, all members invited
  5. a second poll gets one NO -> REJECTED, booking refused
  6. cleanup: delete the test event + both polls

    python backend/scripts/check_phase3.py
"""
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> backend/

from googleapiclient.discovery import build
from sqlalchemy import select

from app.auth.google import credentials_from_json
from app.db.models import Group, User
from app.db import repo
from app.db.session import SessionLocal, init_db
from app.tools.booking import book_poll_event
from app.tools.poll_service import refresh_poll_status

BEIRUT = ZoneInfo("Asia/Beirut")


def main() -> None:
    init_db()
    session = SessionLocal()
    try:
        group = session.scalar(select(Group))
        users = repo.get_group_members(session, group.id)
        if len(users) < 2:
            sys.exit("Need >= 2 users in the demo group. Run chat_cli.py --tools first.")
        u1, u2 = users[0], users[1]
        print(f"Group: {group.name} | members: {[u.email for u in users]}\n")

        tomorrow = datetime.now(BEIRUT).date() + timedelta(days=1)
        start = datetime.combine(tomorrow, time(19), tzinfo=BEIRUT).astimezone(timezone.utc)
        end = datetime.combine(tomorrow, time(20), tzinfo=BEIRUT).astimezone(timezone.utc)

        # --- happy path -----------------------------------------------------
        poll = repo.create_poll(session, group, u1, "Orbi phase-3 smoke test",
                                start, end, min_yes=len(users))
        print(f"[1] poll {poll.id} created (min_yes={poll.min_yes})")

        repo.cast_vote(session, poll, u1, True)
        d = refresh_poll_status(session, poll)
        assert d.outcome == "pending" and poll.status == "open", d
        print(f"[2] after 1 yes: {d.outcome} — waiting on {d.missing_voters}")

        repo.cast_vote(session, poll, u2, True)
        d = refresh_poll_status(session, poll)
        assert poll.status == "approved", (d, poll.status)
        print(f"[3] after all yes: {poll.status}")

        result = book_poll_event(session, poll, u1, [u.email for u in users])
        assert result.get("booked"), result
        print(f"[4] BOOKED -> {result['event_link']}")

        # --- rejection path ---------------------------------------------------
        poll2 = repo.create_poll(session, group, u1, "Should never be booked",
                                 start, end, min_yes=len(users))
        repo.cast_vote(session, poll2, u1, True)
        repo.cast_vote(session, poll2, u2, False)
        d2 = refresh_poll_status(session, poll2)
        assert poll2.status == "rejected", (d2, poll2.status)
        blocked = book_poll_event(session, poll2, u1, [u.email for u in users])
        assert "error" in blocked, blocked
        print(f"[5] NO vote -> {poll2.status}; booking refused: {blocked['error']}")

        # --- cleanup ----------------------------------------------------------
        creds, _ = credentials_from_json(u1.token_json)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        service.events().delete(calendarId="primary", eventId=result["event_id"],
                                sendUpdates="all").execute()
        session.delete(poll); session.delete(poll2); session.commit()
        print("[6] cleanup done (test event deleted, polls removed)")
        print("\nPHASE 3 VERIFIED: poll -> votes -> rule -> book/refuse all work.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
