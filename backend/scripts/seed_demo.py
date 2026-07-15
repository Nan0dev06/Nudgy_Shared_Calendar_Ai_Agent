"""Demo safety: seed every connected test calendar with realistic Beirut events.

Creates staggered events (with locations) over the next few days so the demo
shows real busy blocks being intersected — no dependence on live third-party
state. Each seeded event carries a private marker so re-seeding is clean:

    python backend/scripts/seed_demo.py          # wipe old demo events, seed fresh
    python backend/scripts/seed_demo.py --wipe   # remove demo events only

Only touches events THIS script created (never the account's own events).
"""
import argparse
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> backend/

from googleapiclient.discovery import build

from app.auth.google import list_connected_accounts, load_credentials

BEIRUT = ZoneInfo("Asia/Beirut")
MARKER = {"private": {"orbi_demo": "1"}}

# Per-account weekly plan: (day_offset, start_hour, end_hour, title, location)
# Deliberately overlapping/complementary so intersection produces interesting
# results: e.g. everyone ends up free Thu evening but not Wed.
PLANS = [
    [   # account 1
        (1, 9, 12, "Lectures", "AUB, Bliss Street, Beirut"),
        (1, 15, 17, "Study group", "Jafet Library, AUB, Beirut"),
        (2, 9, 13, "Lectures", "AUB, Bliss Street, Beirut"),
        (2, 18, 20, "Football", "Horsh Beirut"),
        (3, 10, 12, "Doctor", "Clemenceau Medical Center, Beirut"),
        (4, 9, 14, "Volunteering", "Mar Mikhael, Beirut"),
    ],
    [   # account 2
        (1, 10, 13, "Work", "Beirut Digital District"),
        (1, 17, 19, "Gym", "Fitness Zone, Hamra, Beirut"),
        (2, 10, 16, "Work", "Beirut Digital District"),
        (3, 9, 12, "Client meeting", "Downtown, Beirut"),
        (3, 19, 21, "Dinner with family", "Achrafieh, Beirut"),
        (4, 10, 15, "Work", "Beirut Digital District"),
    ],
    [   # account 3 (if connected)
        (1, 9, 11, "Arabic class", "Saifi Institute, Beirut"),
        (2, 14, 18, "Shift", "Cafe Younes, Hamra, Beirut"),
        (3, 9, 13, "Shift", "Cafe Younes, Hamra, Beirut"),
        (4, 11, 13, "Errands", "Verdun, Beirut"),
    ],
]


def _service(creds):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def wipe_demo_events(service, email: str) -> int:
    """Delete events previously created by this script (marker match only)."""
    deleted = 0
    page_token = None
    while True:
        resp = service.events().list(
            calendarId="primary",
            privateExtendedProperty="orbi_demo=1",
            pageToken=page_token,
            maxResults=100,
        ).execute()
        for ev in resp.get("items", []):
            service.events().delete(calendarId="primary", eventId=ev["id"]).execute()
            deleted += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    if deleted:
        print(f"  {email}: removed {deleted} old demo event(s)")
    return deleted


def seed_account(service, email: str, plan: list) -> None:
    today = datetime.now(BEIRUT).date()
    for day_offset, start_h, end_h, title, location in plan:
        day = today + timedelta(days=day_offset)
        start = datetime.combine(day, time(start_h), tzinfo=BEIRUT)
        end = datetime.combine(day, time(end_h), tzinfo=BEIRUT)
        service.events().insert(calendarId="primary", body={
            "summary": title,
            "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Beirut"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Beirut"},
            "extendedProperties": MARKER,
        }).execute()
    print(f"  {email}: created {len(plan)} event(s)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wipe", action="store_true", help="only remove demo events")
    args = p.parse_args()

    token_files = list_connected_accounts()
    if not token_files:
        sys.exit("No connected accounts. Run connect_account.py first.")

    print(f"{'Wiping' if args.wipe else 'Seeding'} {len(token_files)} account(s)...")
    for i, path in enumerate(token_files):
        email = path.stem
        creds = load_credentials(path)
        service = _service(creds)
        wipe_demo_events(service, email)
        if not args.wipe:
            seed_account(service, email, PLANS[i % len(PLANS)])
    print("Done.")


if __name__ == "__main__":
    main()
