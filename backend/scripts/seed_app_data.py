"""Seed the LOCAL app database with demo people, groups, events, tasks and plans
so every feature has something to show — different creators, different groups.

Demo users all live under the @demo.nudgy domain so re-seeding is clean:

    python backend/scripts/seed_app_data.py                # wipe + seed fresh
    python backend/scripts/seed_app_data.py --wipe         # remove demo data only
    python backend/scripts/seed_app_data.py --me you@x.com # attach groups to you

This only writes app rows (no Google calls, no tokens) — demo users show up as
"no calendar yet", which is exactly the mixed-connection state worth testing.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.models import (
    Group, GroupEvent, InterestVote, Membership, Plan, TimeRound, TimeVote, User,
)
from app.db.session import SessionLocal, init_db

DEMO_DOMAIN = "@demo.nudgy"

DEMO_PEOPLE = [
    ("aya" + DEMO_DOMAIN, "Aya"),
    ("karim" + DEMO_DOMAIN, "Karim"),
    ("lina" + DEMO_DOMAIN, "Lina"),
    ("omar" + DEMO_DOMAIN, "Omar"),
    ("maya" + DEMO_DOMAIN, "Maya"),
]

def _at(days: int, hour: int, minute: int = 0) -> datetime:
    """`days` from today at `hour`:`minute` Beirut time, stored as UTC."""
    beirut = datetime.now(ZoneInfo("Asia/Beirut"))
    local = (beirut + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return local.astimezone(timezone.utc)


def wipe(session) -> None:
    demo_users = list(session.scalars(
        select(User).where(User.email.like("%" + DEMO_DOMAIN))
    ))
    demo_ids = {u.id for u in demo_users}
    demo_groups = [g for g in session.scalars(select(Group)) if g.created_by in demo_ids]
    for g in demo_groups:
        for p in session.scalars(select(Plan).where(Plan.group_id == g.id)):
            session.delete(p)
        for e in session.scalars(select(GroupEvent).where(GroupEvent.group_id == g.id)):
            session.delete(e)
        session.delete(g)
    # demo-authored rows in REAL groups
    for p in session.scalars(select(Plan)):
        if p.created_by in demo_ids:
            session.delete(p)
    for e in session.scalars(select(GroupEvent)):
        if e.created_by in demo_ids:
            session.delete(e)
    for v in session.scalars(select(InterestVote)):
        if v.user_id in demo_ids:
            session.delete(v)
    for v in session.scalars(select(TimeVote)):
        if v.user_id in demo_ids:
            session.delete(v)
    for u in demo_users:
        session.delete(u)
    session.commit()
    print(f"wiped {len(demo_users)} demo users, {len(demo_groups)} demo groups")


def seed(session, me_email: str | None) -> None:
    people = {}
    for email, name in DEMO_PEOPLE:
        u = User(email=email, display_name=name, timezone="Asia/Beirut")
        session.add(u)
        people[name] = u
    session.flush()

    me = None
    if me_email:
        me = session.scalar(select(User).where(User.email == me_email))
        if me is None:
            print(f"note: {me_email} not found in DB — seeding demo-only groups")

    def group(name, code, creator, members):
        g = Group(name=name, invite_code=code, created_by=creator.id)
        session.add(g)
        session.flush()
        for m in members:
            session.add(Membership(group_id=g.id, user_id=m.id))
        return g

    aya, karim, lina, omar, maya = (people[n] for n in ["Aya", "Karim", "Lina", "Omar", "Maya"])

    crew = [aya, karim, lina] + ([me] if me else [omar])
    g1 = group("Beirut Crew", "DEMO01", aya, crew)
    g2 = group("Uni Study Group", "DEMO02", karim, [karim, omar, maya] + ([me] if me else []))
    g3 = group("Weekend Hikers", "DEMO03", lina, [lina, maya, aya])

    def event(g, creator, kind, title, category, location, start, end=None, done=False):
        session.add(GroupEvent(
            group_id=g.id, created_by=creator.id, kind=kind, title=title,
            category=category, location=location, start_utc=start,
            end_utc=end or (start + timedelta(hours=2) if start else None), done=done,
        ))

    # ---- ~10 events/tasks PER USER, spread across their groups -------------
    # A couple land in the past (so review nudges fire); the rest spread over
    # the next three weeks so day/week/month views all have content.
    EVENT_IDEAS = [
        ("Dinner at Kalei", "Event", "Kalei Coffee Co."),
        ("Movie night", "Event", "ABC Verdun VOX"),
        ("Beach day", "Event", "Lazy B"),
        ("Coffee catch-up", "Meet", "BHive Cafe"),
        ("Physics review session", "Meet", "AUB Jafet Library"),
        ("Mock exam", "Meet", "Bliss Hall 204"),
        ("Tannourine cedars hike", "Event", "Tannourine Cedar Reserve"),
        ("Brunch", "Event", "Em Sherif Cafe"),
        ("Board games night", "Event", "Backburner Coffee"),
        ("Study sprint", "Meet", "AUB Jafet Library"),
        ("Sunset walk", "Event", "Corniche Beirut"),
        ("Karaoke warm-up", "Event", "Cheers Broumana"),
        ("Group call", "Call", None),
        ("Football five-a-side", "Event", "Beirut By Bike Pitch"),
    ]
    TASK_IDEAS = [
        ("Book the table for Friday", "Task", "Kalei Coffee Co."),
        ("Split the fuel money", "Errand", None),
        ("Bring the speakers", "Prep", None),
        ("Share the problem sets", "Task", None),
        ("Print past exams", "Errand", "Malik's Bookshop"),
        ("Check the trail conditions", "Prep", None),
        ("Buy sunscreen for the beach", "Errand", None),
        ("Reserve the karaoke room", "Task", "Cheers Broumana"),
        ("Charge the camera batteries", "Prep", None),
        ("Collect everyone's availability", "Task", None),
        ("Pick a birthday gift for Aya", "Errand", "ABC Verdun"),
        ("Upload the lecture notes", "Task", None),
        ("Pack the first-aid kit", "Prep", None),
        ("Confirm the minivan booking", "Task", None),
    ]

    groups_of = {}
    for g in (g1, g2, g3):
        for m in session.scalars(
            select(User).join(Membership, Membership.user_id == User.id)
            .where(Membership.group_id == g.id)
        ):
            groups_of.setdefault(m.id, []).append(g)
    users_by_id = {u.id: u for u in session.scalars(select(User))}

    made = 0
    for offset, (uid, ugroups) in enumerate(sorted(groups_of.items())):
        u = users_by_id[uid]
        for i in range(10):
            # i//2 so the event/task pair rotates groups together — plain
            # i % len would alias with the even/odd split below and dump
            # every task into the same group
            g = ugroups[(i // 2 + offset) % len(ugroups)]
            # days -2..+19: a few finished outings, the rest upcoming
            day = ((offset * 7 + i * 3) % 22) - 2
            if i % 2 == 0:  # event
                title, cat, loc = EVENT_IDEAS[(offset + i) % len(EVENT_IDEAS)]
                hour = 9 + ((offset * 5 + i * 2) % 11)  # 9:00–20:00 starts
                event(g, u, "event", title, cat, loc, _at(day, hour))
            else:  # task
                title, cat, loc = TASK_IDEAS[(offset + i) % len(TASK_IDEAS)]
                event(g, u, "task", title, cat, loc, _at(day, 12), done=(day < 0))
            made += 1

    # ---- Plans (two-stage polls) in the crew group --------------------------
    def plan(g, host, title, location, slot_pairs, interest=(), time_votes=(), status="open"):
        p = Plan(group_id=g.id, created_by=host.id, title=title,
                 location=location, status=status)
        session.add(p)
        session.flush()
        rounds = []
        for i, (s, e) in enumerate(slot_pairs):
            r = TimeRound(plan_id=p.id, ordinal=i, slot_start_utc=s, slot_end_utc=e,
                          status="active" if i == 0 else "queued")
            session.add(r)
            rounds.append(r)
        session.flush()
        session.add(InterestVote(plan_id=p.id, user_id=host.id, yes=True))
        for u, yes in interest:
            session.add(InterestVote(plan_id=p.id, user_id=u.id, yes=yes))
        for u, yes in time_votes:
            if rounds:
                session.add(TimeVote(round_id=rounds[0].id, user_id=u.id, yes=yes))
        return p

    plan(
        g1, aya, "Karaoke night", "Cheers Broumana",
        [(_at(5, 20), _at(5, 23))],
        interest=[(karim, True), (lina, False)],
        time_votes=[(karim, True)],
    )
    plan(g1, karim, "Paintball next weekend?", None, [],
         interest=[(aya, True)])  # pure interest check — no time yet
    plan(
        g2, omar, "Group lunch after the mock exam", "Socrate Hamra",
        [(_at(4, 13), _at(4, 14, 30))],
        interest=[(maya, True), (karim, False)],
    )

    session.commit()
    print(f"seeded: 5 demo users, 3 groups, {made} events/tasks, 3 plans"
          + (f" (you are in Beirut Crew + Uni Study Group as {me_email})" if me else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="remove demo data only")
    ap.add_argument("--me", default=None, help="your login email, to join the demo groups")
    args = ap.parse_args()

    init_db()
    session = SessionLocal()
    try:
        wipe(session)
        if not args.wipe:
            seed(session, args.me)
    finally:
        session.close()


if __name__ == "__main__":
    main()
