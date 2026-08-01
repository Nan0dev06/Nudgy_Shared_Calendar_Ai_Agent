"""Availability service — the bridge between the DB and the Phase 1 slot math.

Given a group, this builds each member's busy time, then reuses app/tools/slots.py
to compute common free windows and a partial-availability breakdown for graceful
degradation when no window works for everyone.

BUSY HAS TWO SOURCES, unioned (see docs/poll-edit-redesign.md §4):
  1. LIVE freebusy from every calendar the member connected (fresh at call time,
     per the real-time requirement).
  2. Events the member keeps IN NUDGY — their own, plus shared events they said
     they're coming to (repo.get_busy_events_for_users).

Both, not either. A member who connected nothing still has real busy time, which
is what makes "usable with no external calendar" true rather than nominal; a
member who connected two calendars and also uses Nudgy gets all three merged.
Union is the right operator because it is idempotent — an in-app event synced out
to Google arrives from both sources and merging it twice changes nothing.

Privacy: only busy time RANGES cross this boundary, with exactly one exception.
External calendars are read via freebusy, which has no titles to leak, and in-app
events are reduced to their start/end here. The exception is the VIEWER's own
blocks: if they switched titles on for a calendar, inbound sync
(docs/inbound-sync.md) has a mirror of what is actually in those hours, and
`members_busy` labels their own rows with it. That never applies to anybody
else's row, so what a groupmate can see is unchanged.

Note the mirror is NOT a third source of busy time. Availability stays live —
freebusy at call time — because the mirror is minutes stale by design; it only
explains blocks the live read already found.
Token refresh: if a member's OAuth token was refreshed during load, the new
token is written back to the DB immediately so it never silently goes stale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.calendars import provider_for_account
from app.calendars.cache import freebusy_cache
from app.db.models import Group, User
from app.db import repo
from app.tools.slots import (
    Interval,
    complement,
    find_common_slots,
    intersect,
    merge_intervals,
    reasonable_hours,
)

log = logging.getLogger("nudgy.agent")


@dataclass
class LabeledBusy:
    """A busy block the VIEWER is allowed to see the contents of.

    Only ever built for the person asking (see `label_for_user_id`), and only
    from calendars whose owner switched titles on. Groupmates' busy time never
    becomes one of these — it stays an anonymous Interval, which is what keeps
    the freebusy-only promise made to everyone else intact.
    """
    start: datetime
    end: datetime
    title: str
    where: str | None = None
    calendar: str | None = None      # which connected calendar it came from


@dataclass
class MemberBusy:
    email: str
    connected: bool                  # has at least one external calendar attached
    busy: list[Interval] = field(default_factory=list)   # merged, both sources
    in_app_blocks: int = 0           # how many of `busy` came from Nudgy events
    # Populated for the viewer alone; empty for every other member.
    labeled: list[LabeledBusy] = field(default_factory=list)

    @property
    def has_source(self) -> bool:
        """Do we know anything at all about this person's time?

        The distinction slots.py warns about: an empty busy list means "free all
        week" for somebody we can actually see, and "no idea" for somebody we
        can't. A connected calendar is a source even when it returns nothing;
        so is a Nudgy event. Only somebody with neither is unknown.
        """
        return self.connected or self.in_app_blocks > 0


@dataclass
class Slot:
    start: datetime  # UTC
    end: datetime    # UTC


@dataclass
class PartialWindow:
    """A window where SOME (not all) members are free — used to answer
    'no time works for everyone' with the closest alternatives."""
    start: datetime
    end: datetime
    free_emails: list[str]
    busy_emails: list[str]


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%a %d %b %H:%M")


def _labels_for_viewer(
    session: Session, user_id: int, now: datetime, window_end: datetime,
    busy: list[Interval],
) -> list[LabeledBusy]:
    """The viewer's own mirrored external events, as labels ON their busy time.

    Requires a TITLE to qualify. An untitled mirror row (the calendar never opted
    in) carries nothing a plain busy block doesn't, and emitting it would only
    fragment the merged blocks for nothing — which is why availability output is
    byte-identical to before until somebody opts in.

    CLIPPED TO `busy`, WHICH IS THE POINT. These labels EXPLAIN blocks the live
    read already found; they never assert new ones. The mirror is minutes stale
    by design, so a meeting deleted a minute ago is still sitting in it — and
    without clipping, the calendar would draw "Dentist, 15:00" over an hour the
    slot math is simultaneously offering as free. Intersecting means the worst a
    stale mirror can do is fail to label a block, which is just the old
    behaviour.

    `busy=False` rows are skipped for the same reason: an event marked
    free/transparent is on the calendar without occupying it, so there is no
    block for it to explain.
    """
    events = repo.get_external_events(session, user_id, now, window_end)
    if not events or not busy:
        return []
    names = repo.calendar_labels_for_user(session, user_id)
    labeled: list[LabeledBusy] = []
    for ev in events:
        if not ev.title or not ev.busy:
            continue
        for start, end in intersect([(ev.start, ev.end)], busy):
            labeled.append(LabeledBusy(
                start=start, end=end, title=ev.title, where=ev.location,
                calendar=names.get((ev.account_id, ev.calendar_id)),
            ))
    return sorted(labeled, key=lambda lb: lb.start)


def _split_busy(
    busy: list[Interval], labeled: list[LabeledBusy],
    now: datetime, window_end: datetime,
) -> list[Interval]:
    """The part of `busy` that no label already accounts for.

    Without this the viewer would see the same hour twice: once as a titled
    block from the mirror and once inside the merged free/busy range that
    produced it. Subtracting is the right operator rather than replacing,
    because the mirror is a few minutes stale by design while free/busy is live
    — anything live-but-unmirrored has to survive as an anonymous block.
    """
    if not labeled:
        return busy
    covered = merge_intervals([(lb.start, lb.end) for lb in labeled])
    return intersect(busy, complement(covered, now, window_end))


def fetch_busy_for_group(
    session: Session, group: Group, now: datetime, days_ahead: int,
    label_for_user_id: int | None = None,
) -> list[MemberBusy]:
    """Every member's busy time: connected calendars UNION their Nudgy events.

    `label_for_user_id` opts ONE member — always the person making the request —
    into seeing what their own busy blocks actually are, drawn from the inbound
    mirror (docs/inbound-sync.md). Nobody else's blocks are ever labelled, and
    the parameter defaults to None so every existing caller (the agent included)
    keeps getting pure ranges.

    Logs each member so the loop is visible.
    """
    window_end = now + timedelta(days=days_ahead or 1)  # 0 means "today" — still a 1-day window
    members = repo.get_group_members(session, group.id)
    # One batched query for everyone's in-app events rather than a round-trip per
    # member — this runs on every availability call, including the agent's.
    in_app = repo.get_busy_events_for_users(
        session, [m.id for m in members], now, window_end
    )
    results: list[MemberBusy] = []
    for user in members:
        nudgy = in_app.get(user.id, [])
        accounts = repo.get_calendar_accounts(session, user)
        # Union busy across EVERY calendar this person connected — being busy on
        # any one of them (personal, work, …) makes them busy. This is what keeps
        # one user = one free/busy truth across all their calendars. Reads go
        # through the short-TTL cache; the provider (and its silent token refresh)
        # is built only on a cache miss.
        external: list[Interval] = []
        for account in accounts:
            external += freebusy_cache.get_busy(
                account.id,
                lambda tmin, tmax, acct=account: (
                    provider_for_account(session, acct).get_busy(tmin, tmax)
                ),
                now, window_end,
            )
        busy = merge_intervals(external + nudgy)
        labeled = (
            _labels_for_viewer(session, user.id, now, window_end, busy)
            if user.id == label_for_user_id else []
        )
        log.info("[freebusy] %s — %d busy block(s) from %d calendar(s) + %d Nudgy event(s)",
                 user.email, len(busy), len(accounts), len(nudgy))
        results.append(MemberBusy(
            email=user.email, connected=bool(accounts),
            # The merged list stays WHOLE — it is what the slot math runs on, and
            # labelling must never change who is free. Only the display split
            # below (members_busy) knows about labels.
            busy=busy, in_app_blocks=len(nudgy), labeled=labeled,
        ))
    return results


def compute_availability(
    session: Session,
    group: Group,
    now: datetime,
    days_ahead: int,
    duration_minutes: int,
    tz_name: str,
    earliest_hour: int = 9,
    latest_hour: int = 22,
    include_member_busy: bool = False,
    viewer_id: int | None = None,
) -> dict:
    """Full availability picture for the agent: common slots + partial windows.

    Returns a plain dict (JSON-serializable) — this is exactly what the agent
    tool hands back to the model, so keep it readable and free of raw datetimes
    the model would have to parse. All times are pre-formatted in tz_name.

    `viewer_id` is who is looking, and it only ever affects `members_busy`: that
    one person's own blocks may come back carrying the title of the external
    event behind them. It does NOT reach the slot math, and it is not the agent's
    to pass — the agent gets ranges, as it always has.
    """
    tz = ZoneInfo(tz_name)
    window_end = now + timedelta(days=days_ahead or 1)  # 0 means "today" — still a 1-day window
    members = fetch_busy_for_group(
        session, group, now, days_ahead, label_for_user_id=viewer_id,
    )

    # Anyone we know something about takes part in the intersection — a connected
    # calendar OR Nudgy events both count. Before in-app events fed this, only
    # connected members did, which silently ignored everyone using Nudgy as their
    # calendar. Members with neither source are left out rather than counted as
    # free all week (see MemberBusy.has_source).
    sourced = [m for m in members if m.has_source]
    connected = [m for m in members if m.connected]
    not_connected = [m.email for m in members if not m.connected]

    busy_by_member = {m.email: m.busy for m in sourced}
    slots = find_common_slots(
        busy_by_member, now, window_end,
        duration_minutes=duration_minutes, tz_name=tz_name,
        earliest_hour=earliest_hour, latest_hour=latest_hour,
    )
    log.info("[intersect] %d common slot(s) across %d member(s) with a source "
             "(%d connected)", len(slots), len(sourced), len(connected))

    result = {
        "now_local": _fmt(now, tz),
        "timezone": tz_name,
        "window_days": days_ahead,
        "duration_minutes": duration_minutes,
        "reasonable_hours": f"{earliest_hour:02d}:00-{latest_hour:02d}:00",
        "members_total": len(members),
        "members_connected": len(connected),
        # who the intersection actually covers — connected OR keeping events in
        # Nudgy. `members_connected` alone understates this now.
        "members_with_source": len(sourced),
        "members_not_connected": not_connected,
        # The list the AGENT must surface: people we know NOTHING about, so the
        # slots below may hide a conflict. Not the same as members_not_connected
        # — somebody who keeps their events in Nudgy is fully accounted for
        # without ever attaching Google.
        "members_unknown": [m.email for m in members if not m.has_source],
        "common_slots": [
            {
                "start": _fmt(s, tz),
                "end": _fmt(e, tz),
                # ISO forms are for tool calls (create_plan) — copy verbatim
                "start_iso": s.astimezone(tz).isoformat(),
                "end_iso": e.astimezone(tz).isoformat(),
                "duration_minutes": int((e - s).total_seconds() // 60),
            }
            for s, e in slots
        ],
    }

    # For the calendar UI: busy ranges per member. Anonymous for everyone except
    # the viewer, whose own blocks may carry the title of the external event
    # behind them (docs/inbound-sync.md). Groupmates' entries are exactly what
    # they always were — freebusy has no titles to leak, and the mirror's are
    # never read for anybody but their owner.
    if include_member_busy:
        result["members_busy"] = [
            {
                "email": m.email,
                "connected": m.connected,
                "busy": _busy_json(m, now, window_end, tz),
            }
            for m in members
        ]

    # Graceful degradation: if nobody-can-all-meet, compute windows where the
    # MOST members overlap, so the model can offer the closest alternatives.
    if not slots and len(sourced) >= 2:
        result["partial_windows"] = _best_partial_windows(
            sourced, now, window_end, duration_minutes, tz_name,
            earliest_hour, latest_hour, tz,
        )
    return result


def _busy_json(
    m: MemberBusy, now: datetime, window_end: datetime, tz: ZoneInfo,
) -> list[dict]:
    """One member's busy blocks for the calendar UI.

    Labelled blocks and the leftover anonymous ones are emitted as one
    chronological list of the same shape, so the client renders a single kind of
    thing and simply shows a title when there is one. `title`/`where`/`calendar`
    are absent on every entry that isn't the viewer's own.
    """
    iso = lambda d: d.astimezone(tz).isoformat()  # noqa: E731
    rows = [
        {
            "start_iso": iso(lb.start), "end_iso": iso(lb.end),
            "title": lb.title, "where": lb.where, "calendar": lb.calendar,
        }
        for lb in m.labeled
    ]
    rows += [
        {"start_iso": iso(s), "end_iso": iso(e)}
        for s, e in _split_busy(m.busy, m.labeled, now, window_end)
    ]
    return sorted(rows, key=lambda r: r["start_iso"])


def _best_partial_windows(
    sourced: list[MemberBusy],
    now: datetime,
    window_end: datetime,
    duration_minutes: int,
    tz_name: str,
    earliest_hour: int,
    latest_hour: int,
    tz: ZoneInfo,
    max_windows: int = 3,
) -> list[dict]:
    """Windows (>= duration, in reasonable hours) ranked by how many members
    are free. Lets Nudgy say '4 of 5 are free Thursday 5pm'."""
    hours = reasonable_hours(now, window_end, tz_name, earliest_hour, latest_hour)
    need = timedelta(minutes=duration_minutes)

    # free intervals per member (within reasonable hours)
    free_per_member: dict[str, list[Interval]] = {}
    for m in sourced:
        member_free = complement(merge_intervals(m.busy), now, window_end)
        free_per_member[m.email] = intersect(member_free, hours)

    # candidate boundaries: every free-interval edge
    edges = sorted({t for ivs in free_per_member.values() for iv in ivs for t in iv})
    windows: list[PartialWindow] = []
    for a, b in zip(edges, edges[1:]):
        if b - a < need:
            continue
        free = [email for email, ivs in free_per_member.items()
                if any(s <= a and b <= e for s, e in ivs)]
        if 0 < len(free) < len(sourced):
            busy = [m.email for m in sourced if m.email not in free]
            windows.append(PartialWindow(a, b, free, busy))

    windows.sort(key=lambda w: (-len(w.free_emails), w.start))
    return [
        {
            "start": _fmt(w.start, tz),
            "end": _fmt(w.end, tz),
            "free_count": len(w.free_emails),
            "free_members": w.free_emails,
            "busy_members": w.busy_emails,
        }
        for w in windows[:max_windows]
    ]
