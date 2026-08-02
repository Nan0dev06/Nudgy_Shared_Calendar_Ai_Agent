"""Inbound calendar sync: the providers' token loops and the mirror they feed.

Outbound sync (book a plan, it lands on Google) has always worked; the way back
was opaque busy time and nothing else. These tests cover the machinery that
changes that — Google's syncToken flow, Graph's delta flow, and the
`external_events` mirror both write into — with no network anywhere: the Google
API service and every httpx call are faked.

The bugs this file is aimed at are the ones incremental sync is famous for:
persisting a token that never arrived, losing a change because the token was
saved before the rows, resurrecting a deleted event, mirroring an event that
drifted outside the window, and — the one that actually matters here — reading a
title nobody opted into.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.calendars import google as gmod
from app.calendars import microsoft as msmod
from app.calendars.base import ExternalEventData
from app.calendars.google import GoogleCalendarProvider, _parse_event
from app.calendars.microsoft import MicrosoftCalendarProvider, _parse_delta_item
from app.db import repo
from app.db.models import Base, CalendarSyncState, ExternalEvent
from app.jobs import calendar_sync

NOW = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
WINDOW = (NOW - timedelta(days=7), NOW + timedelta(days=120))


@pytest.fixture
def Session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _account(session, email="ada@example.com", provider="google", read_titles=False):
    user = repo.login_with_google(session, email, '{"token":"t"}')
    account = repo.get_primary_calendar_account(session, user)
    account.provider = provider
    account.read_titles = read_titles
    session.commit()
    return user, account


def _data(eid, hour, *, title=None, busy=True, day=1):
    start = datetime(2026, 8, day, hour, tzinfo=timezone.utc)
    return ExternalEventData(
        external_id=eid, start=start, end=start + timedelta(hours=1),
        title=title, busy=busy,
    )


# ============================================================ Google syncToken

class _FakeExec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeEvents:
    """Replays a scripted list of events.list responses, recording each call."""

    def __init__(self, pages, calls):
        self._pages, self._calls = list(pages), calls

    def list(self, **kwargs):
        self._calls.append(kwargs)
        page = self._pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return _FakeExec(page)


class _FakeCalendarList:
    def __init__(self, resp):
        self._resp = resp

    def list(self, **kwargs):
        return _FakeExec(self._resp)


class _FakeService:
    def __init__(self, pages, calls, calendars=None):
        self._events = _FakeEvents(pages, calls)
        self._cal_list = _FakeCalendarList(calendars or {"items": []})

    def events(self):
        return self._events

    def calendarList(self):
        return self._cal_list


def _google(pages, calendars=None):
    calls: list[dict] = []
    provider = GoogleCalendarProvider(creds=None)
    provider._service = _FakeService(pages, calls, calendars)
    return provider, calls


def _http_error(status, reason):
    from googleapiclient.errors import HttpError

    class _Resp:
        def __init__(self, s):
            self.status = s
            self.reason = str(s)

    body = ('{"error":{"code":%d,"errors":[{"reason":"%s"}]}}' % (status, reason))
    return HttpError(_Resp(status), body.encode())


def test_google_full_sync_pages_and_only_then_keeps_the_token():
    """nextSyncToken arrives ONLY on the last page. A run that stopped early
    would have no bookmark, so the loop must walk every page before returning."""
    provider, calls = _google([
        {"items": [{"id": "a", "status": "confirmed",
                    "start": {"dateTime": "2026-08-03T09:00:00+00:00"},
                    "end": {"dateTime": "2026-08-03T10:00:00+00:00"}}],
         "nextPageToken": "p2"},
        {"items": [{"id": "b", "status": "confirmed",
                    "start": {"dateTime": "2026-08-04T09:00:00+00:00"},
                    "end": {"dateTime": "2026-08-04T10:00:00+00:00"}}],
         "nextSyncToken": "TOK1"},
    ])
    result = provider.sync_events(
        "primary", token=None, window_start=WINDOW[0], window_end=WINDOW[1],
        want_titles=False,
    )
    assert [e.external_id for e in result.changed] == ["a", "b"]
    assert result.next_token == "TOK1"
    assert result.full is True
    assert calls[1]["pageToken"] == "p2"


def test_google_empty_page_with_a_page_token_is_not_the_end():
    """Google documents that a page may come back empty while more pages exist.
    Terminating on an empty items[] would silently truncate the sync."""
    provider, _ = _google([
        {"items": [], "nextPageToken": "p2"},
        {"items": [{"id": "a", "status": "confirmed",
                    "start": {"dateTime": "2026-08-03T09:00:00+00:00"},
                    "end": {"dateTime": "2026-08-03T10:00:00+00:00"}}],
         "nextSyncToken": "TOK"},
    ])
    result = provider.sync_events(
        "primary", token=None, window_start=WINDOW[0], window_end=WINDOW[1],
        want_titles=False,
    )
    assert [e.external_id for e in result.changed] == ["a"]


def test_google_incremental_never_sends_timemin_and_sends_the_token():
    """timeMin/timeMax alongside syncToken is a 400, not a warning."""
    provider, calls = _google([{"items": [], "nextSyncToken": "TOK2"}])
    provider.sync_events(
        "primary", token="TOK1", window_start=WINDOW[0], window_end=WINDOW[1],
        want_titles=False,
    )
    assert calls[0]["syncToken"] == "TOK1"
    assert "timeMin" not in calls[0] and "timeMax" not in calls[0]
    # the frozen query the token was minted against
    assert calls[0]["showDeleted"] is True
    assert calls[0]["singleEvents"] is True


def test_google_cancelled_items_are_deletions_carrying_only_an_id():
    """A tombstone is guaranteed nothing but an id — parsing it as an event
    would throw away the deletion."""
    provider, _ = _google([
        {"items": [{"id": "gone", "status": "cancelled"}], "nextSyncToken": "T"},
    ])
    result = provider.sync_events(
        "primary", token="OLD", window_start=WINDOW[0], window_end=WINDOW[1],
        want_titles=False,
    )
    assert result.deleted_ids == ["gone"]
    assert result.changed == []


def test_google_410_falls_back_to_a_full_read():
    """Google publishes no token TTL and invalidates on unrelated ACL changes,
    so a 410 is routine. Raising here would mean the feature dies one day."""
    provider, calls = _google([
        _http_error(410, "fullSyncRequired"),
        {"items": [], "nextSyncToken": "FRESH"},
    ])
    result = provider.sync_events(
        "primary", token="STALE", window_start=WINDOW[0], window_end=WINDOW[1],
        want_titles=False,
    )
    assert result.full is True
    assert result.next_token == "FRESH"
    assert "syncToken" not in calls[1] and "timeMin" in calls[1]


def test_google_other_http_errors_still_raise():
    """Only staleness is recoverable here. Swallowing a 403 would let a broken
    calendar look like an empty one, which is worse than a recorded failure."""
    provider, _ = _google([_http_error(403, "rateLimitExceeded")])
    with pytest.raises(Exception):
        provider.sync_events(
            "primary", token="T", window_start=WINDOW[0], window_end=WINDOW[1],
            want_titles=False,
        )


def test_google_events_outside_the_window_are_dropped_as_deletions():
    """An incremental round ignores our window entirely. Something edited to a
    date beyond the horizon must leave the mirror, not sit in it forever."""
    provider, _ = _google([
        {"items": [{"id": "far", "status": "confirmed",
                    "start": {"dateTime": "2027-08-03T09:00:00+00:00"},
                    "end": {"dateTime": "2027-08-03T10:00:00+00:00"}}],
         "nextSyncToken": "T"},
    ])
    result = provider.sync_events(
        "primary", token="OLD", window_start=WINDOW[0], window_end=WINDOW[1],
        want_titles=False,
    )
    assert result.changed == []
    assert result.deleted_ids == ["far"]


def test_google_asks_for_no_summary_when_titles_are_off():
    """The strongest form of the opt-in: with titles off the request does not
    ASK for them, so there is nothing to discard and nothing to leak into a log
    or a crash report."""
    provider, calls = _google([{"items": [], "nextSyncToken": "T"}])
    provider.sync_events("primary", token=None, window_start=WINDOW[0],
                         window_end=WINDOW[1], want_titles=False)
    assert "summary" not in calls[0]["fields"]

    provider, calls = _google([{"items": [], "nextSyncToken": "T"}])
    provider.sync_events("primary", token=None, window_start=WINDOW[0],
                         window_end=WINDOW[1], want_titles=True)
    assert "summary" in calls[0]["fields"]


def test_google_sync_calendars_skip_subscribed_ones():
    """Mirrored calendars must be the same set that makes you busy — freebusy
    skips reader/freeBusyReader, so this does too."""
    provider, _ = _google([], calendars={"items": [
        {"id": "me@x.com", "summary": "Personal", "accessRole": "owner"},
        {"id": "uni@x.com", "summary": "Uni", "accessRole": "writer"},
        {"id": "hols", "summary": "Holidays", "accessRole": "reader"},
    ]})
    cals = provider.list_sync_calendars()
    assert [(c.id, c.name) for c in cals] == [
        ("me@x.com", "Personal"), ("uni@x.com", "Uni"),
    ]


def test_google_parses_all_day_and_transparent_events():
    allday = _parse_event(
        {"id": "x", "start": {"date": "2026-08-03"}, "end": {"date": "2026-08-04"}},
        want_titles=False,
    )
    assert allday.all_day is True
    assert allday.start == datetime(2026, 8, 3, tzinfo=timezone.utc)

    free = _parse_event(
        {"id": "y", "transparency": "transparent",
         "start": {"dateTime": "2026-08-03T09:00:00+00:00"},
         "end": {"dateTime": "2026-08-03T10:00:00+00:00"}},
        want_titles=False,
    )
    assert free.busy is False, "a transparent event is on the calendar, not in it"


def test_google_skips_events_it_cannot_time():
    """endTimeUnspecified and malformed payloads. Mirroring a block at a time
    nobody agreed to is worse than skipping it."""
    assert _parse_event({"id": "x", "start": {}, "end": {}}, want_titles=False) is None
    assert _parse_event(
        {"id": "x", "start": {"dateTime": "2026-08-03T10:00:00+00:00"},
         "end": {"dateTime": "2026-08-03T09:00:00+00:00"}},
        want_titles=False,
    ) is None


# ============================================================== Graph delta

class _FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status_code = payload, status
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _graph(monkeypatch, responses):
    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return responses.pop(0)

    monkeypatch.setattr(msmod.httpx, "get", fake_get)
    provider = MicrosoftCalendarProvider.__new__(MicrosoftCalendarProvider)
    provider._token = "tok"
    return provider, calls


def test_graph_walks_nextlink_until_the_deltalink(monkeypatch):
    """Exactly one of the two links is present per page; only the deltaLink is
    a bookmark worth keeping."""
    provider, calls = _graph(monkeypatch, [
        _FakeResponse({"value": [], "@odata.nextLink": "https://graph/next"}),
        _FakeResponse({
            "value": [{"id": "e1", "subject": "Standup",
                       "start": {"dateTime": "2026-08-03T09:00:00.0000000", "timeZone": "UTC"},
                       "end": {"dateTime": "2026-08-03T09:15:00.0000000", "timeZone": "UTC"},
                       "showAs": "busy"}],
            "@odata.deltaLink": "https://graph/delta?$deltatoken=D1",
        }),
    ])
    result = provider.sync_events(
        "", token=None, window_start=WINDOW[0], window_end=WINDOW[1], want_titles=True,
    )
    assert result.next_token == "https://graph/delta?$deltatoken=D1"
    assert [e.title for e in result.changed] == ["Standup"]
    assert calls[1]["url"] == "https://graph/next"
    # immutable ids are non-negotiable for a mirror keyed on the provider's id
    assert 'IdType="ImmutableId"' in calls[0]["headers"]["Prefer"]


def test_graph_initial_call_carries_the_window_and_the_token_call_does_not(monkeypatch):
    """Graph encodes start/endDateTime INTO the token; re-sending them is wrong."""
    provider, calls = _graph(monkeypatch, [
        _FakeResponse({"value": [], "@odata.deltaLink": "D"}),
    ])
    provider.sync_events("", token=None, window_start=WINDOW[0],
                         window_end=WINDOW[1], want_titles=False)
    assert "startDateTime=" in calls[0]["url"] and "endDateTime=" in calls[0]["url"]

    provider, calls = _graph(monkeypatch, [
        _FakeResponse({"value": [], "@odata.deltaLink": "D2"}),
    ])
    provider.sync_events("", token="https://graph/delta?$deltatoken=D",
                         window_start=WINDOW[0], window_end=WINDOW[1], want_titles=False)
    assert calls[0]["url"] == "https://graph/delta?$deltatoken=D"


def test_graph_removed_and_cancelled_both_leave_the_mirror(monkeypatch):
    """@removed also covers "edited out of the window", and isCancelled is a
    third, different thing. All three mean the same locally: it's not on."""
    provider, _ = _graph(monkeypatch, [
        _FakeResponse({"value": [
            {"id": "gone", "@removed": {"reason": "deleted"}},
            {"id": "called-off", "isCancelled": True,
             "start": {"dateTime": "2026-08-03T09:00:00.0000000"},
             "end": {"dateTime": "2026-08-03T10:00:00.0000000"}},
        ], "@odata.deltaLink": "D"}),
    ])
    result = provider.sync_events("", token="T", window_start=WINDOW[0],
                                  window_end=WINDOW[1], want_titles=False)
    assert sorted(result.deleted_ids) == ["called-off", "gone"]


def test_graph_410_restarts_from_a_full_window_read(monkeypatch):
    provider, calls = _graph(monkeypatch, [
        _FakeResponse({}, status=410, headers={"Location": "https://graph/fresh"}),
        _FakeResponse({"value": [], "@odata.deltaLink": "D2"}),
    ])
    result = provider.sync_events("", token="https://graph/stale",
                                  window_start=WINDOW[0], window_end=WINDOW[1],
                                  want_titles=False)
    assert result.full is True
    assert calls[1]["url"] == "https://graph/fresh"


def test_graph_syncstatenotfound_is_also_a_resync(monkeypatch):
    """Microsoft only commits to "a 40X-series error with codes such as
    syncStateNotFound", so the code is checked as well as the status."""
    provider, _ = _graph(monkeypatch, [
        _FakeResponse({"error": {"code": "syncStateNotFound"}}, status=400),
        _FakeResponse({"value": [], "@odata.deltaLink": "D2"}),
    ])
    result = provider.sync_events("", token="stale", window_start=WINDOW[0],
                                  window_end=WINDOW[1], want_titles=False)
    assert result.full is True


def test_graph_drops_the_title_it_could_not_avoid_receiving(monkeypatch):
    """$select is unsupported on a delta call, so the subject arrives whatever
    the user chose. The opt-in is enforced here, at ingestion."""
    item = {"id": "e1", "subject": "Therapy", "bodyPreview": "private",
            "location": {"displayName": "Clinic"},
            "start": {"dateTime": "2026-08-03T09:00:00.0000000"},
            "end": {"dateTime": "2026-08-03T10:00:00.0000000"},
            "showAs": "busy"}
    assert _parse_delta_item(item, want_titles=False).title is None
    assert _parse_delta_item(item, want_titles=False).location is None
    assert _parse_delta_item(item, want_titles=True).title == "Therapy"


def test_graph_showas_free_is_mirrored_but_not_busy():
    item = {"id": "e1", "showAs": "free",
            "start": {"dateTime": "2026-08-03T09:00:00.0000000"},
            "end": {"dateTime": "2026-08-03T10:00:00.0000000"}}
    assert _parse_delta_item(item, want_titles=False).busy is False


# ======================================================= the mirror (repo)

def _state(session, account, calendar_id="primary"):
    return repo.upsert_sync_state(session, account, calendar_id, "Personal")


def test_save_sync_round_upserts_and_advances_the_bookmark(Session):
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        counts = repo.save_sync_round(
            s, state, changed=[_data("a", 9), _data("b", 11)], deleted_ids=[],
            next_token="T1", full=True, window_start=WINDOW[0],
            window_end=WINDOW[1], now=NOW, store_titles=False,
        )
        assert counts["added"] == 2
        assert state.sync_token == "T1"
        assert state.synced == NOW
        assert len(repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])) == 2


def test_replaying_a_round_changes_nothing(Session):
    """A crash between applying rows and saving the token replays the round.
    That is the safe failure direction only because writes are idempotent."""
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        for _ in range(2):
            repo.save_sync_round(
                s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T",
                full=False, window_start=WINDOW[0], window_end=WINDOW[1],
                now=NOW, store_titles=False,
            )
        rows = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])
        assert len(rows) == 1


def test_a_full_round_reconciles_by_absence(Session):
    """The only way to catch a deletion that happened while we held a token the
    provider had already forgotten about."""
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9), _data("b", 11)], deleted_ids=[],
            next_token="T", full=True, window_start=WINDOW[0],
            window_end=WINDOW[1], now=NOW, store_titles=False,
        )
        repo.save_sync_round(
            s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T2",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1],
            now=NOW, store_titles=False,
        )
        left = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])
        assert [e.external_id for e in left] == ["a"]


def test_an_incremental_round_does_not_reconcile_by_absence(Session):
    """An incremental round only reports CHANGES — treating everything it didn't
    mention as deleted would wipe the whole mirror on the first quiet tick."""
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9), _data("b", 11)], deleted_ids=[],
            next_token="T", full=True, window_start=WINDOW[0],
            window_end=WINDOW[1], now=NOW, store_titles=False,
        )
        repo.save_sync_round(
            s, state, changed=[], deleted_ids=[], next_token="T2", full=False,
            window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        assert len(repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])) == 2


def test_titles_are_not_stored_when_the_opt_in_is_off(Session):
    with Session() as s:
        user, account = _account(s, read_titles=False)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9, title="Therapy")], deleted_ids=[],
            next_token="T", full=True, window_start=WINDOW[0],
            window_end=WINDOW[1], now=NOW, store_titles=False,
        )
        row = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])[0]
        assert row.title is None


def test_turning_titles_off_purges_them_and_resets_the_tokens(Session):
    """A field mask is part of the query frozen into a Google token, so the next
    round has to be a full read. And text somebody just withdrew consent for
    should not sit in the database."""
    with Session() as s:
        user, account = _account(s, read_titles=True)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9, title="Therapy")], deleted_ids=[],
            next_token="T1", full=True, window_start=WINDOW[0],
            window_end=WINDOW[1], now=NOW, store_titles=True,
        )
        repo.set_account_read_titles(s, account, False)

        row = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])[0]
        assert row.title is None, "the title survived the opt-out"
        assert row.start is not None, "the time should NOT have been deleted"
        assert s.get(CalendarSyncState, state.id).sync_token is None


def test_turning_titles_off_can_keep_what_was_already_pulled_in(Session):
    """The user is asked, and "keep" has to actually keep."""
    with Session() as s:
        user, account = _account(s, read_titles=True)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9, title="Therapy")], deleted_ids=[],
            next_token="T1", full=True, window_start=WINDOW[0],
            window_end=WINDOW[1], now=NOW, store_titles=True,
        )
        repo.set_account_read_titles(s, account, False, keep_titles=True)
        row = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])[0]
        assert row.title == "Therapy"


def test_disconnecting_deletes_the_mirror_by_default(Session):
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        repo.disconnect_calendar_account(s, user, account)
        assert repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1]) == []


def test_disconnecting_can_keep_the_mirror_and_reconnecting_re_adopts_it(Session):
    """Keeping detaches rather than copies, so reconnecting the same calendar
    picks the rows back up instead of inserting a second set of everything."""
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        repo.disconnect_calendar_account(s, user, account, keep_events=True)

        kept = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])
        assert len(kept) == 1
        assert kept[0].account_id is None, "a kept copy no longer belongs to a connection"

        # reconnect the same calendar and sync again
        again = repo.upsert_calendar_account(
            s, user, "google", "ada@example.com", '{"token":"t2"}',
        )
        state2 = _state(s, again)
        repo.save_sync_round(
            s, state2, changed=[_data("a", 9)], deleted_ids=[], next_token="T",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        rows = repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])
        assert len(rows) == 1, "reconnecting duplicated the mirror"
        assert rows[0].account_id == again.id


# ==================================================== the job's own decisions

def test_a_fresh_calendar_syncs_immediately_then_waits():
    state = CalendarSyncState(account_id=1, calendar_id="primary")
    assert calendar_sync.due(state, NOW, timedelta(minutes=5)) is True
    state.synced_at = NOW
    assert calendar_sync.due(state, NOW + timedelta(minutes=1), timedelta(minutes=5)) is False
    assert calendar_sync.due(state, NOW + timedelta(minutes=6), timedelta(minutes=5)) is True


def test_a_live_token_keeps_its_original_window():
    """The window is part of the token's identity — recomputing it every tick
    would invalidate Graph's token on every single round."""
    state = CalendarSyncState(
        account_id=1, calendar_id="primary", sync_token="T",
        window_start_utc=WINDOW[0], window_end_utc=WINDOW[1],
    )
    start, end, token = calendar_sync.plan_round(state, NOW)
    assert (start, end, token) == (WINDOW[0], WINDOW[1], "T")


def test_the_window_is_re_based_before_the_horizon_runs_out():
    """Neither provider rolls the horizon forward on its own, so without this
    the mirror would quietly stop covering the future."""
    state = CalendarSyncState(
        account_id=1, calendar_id="primary", sync_token="T",
        window_start_utc=WINDOW[0], window_end_utc=NOW + timedelta(days=5),
    )
    start, end, token = calendar_sync.plan_round(state, NOW)
    assert token is None, "a nearly-expired horizon must force a fresh full read"
    assert end > NOW + timedelta(days=100)


def test_a_cleared_token_forces_a_full_read():
    """What set_account_read_titles does — the next round must re-read."""
    state = CalendarSyncState(
        account_id=1, calendar_id="primary", sync_token=None,
        window_start_utc=WINDOW[0], window_end_utc=WINDOW[1],
    )
    _, _, token = calendar_sync.plan_round(state, NOW)
    assert token is None


def test_one_broken_calendar_does_not_stop_the_others(Session, monkeypatch):
    """A dead token on one person's calendar must not cost everybody else their
    sync — and the failure is recorded rather than swallowed."""
    class _Boom:
        def list_sync_calendars(self):
            from app.calendars.base import SyncCalendar
            return [SyncCalendar(id="bad", name="Bad"), SyncCalendar(id="ok", name="OK")]

        def sync_events(self, calendar_id, **kwargs):
            from app.calendars.base import SyncResult
            if calendar_id == "bad":
                raise RuntimeError("token exploded")
            return SyncResult(changed=[_data("a", 9)], next_token="T", full=True)

    with Session() as s:
        user, account = _account(s)
        monkeypatch.setattr(calendar_sync, "provider_for_account", lambda *a: _Boom())
        counts = calendar_sync.run_sync(s, NOW)

        assert counts["failed"] == 1
        assert counts["added"] == 1, "the healthy calendar still synced"
        states = {st.calendar_id: st for st in repo.get_sync_states(s, account)}
        assert "token exploded" in states["bad"].error
        assert states["ok"].error is None


# ============================== sync_setting governs the direction (2026-08-02)
# Until inbound existed, "one_way" and "two_way" were indistinguishable — there
# was only one direction to have an opinion about. Now: none = neither,
# one_way = out only, two_way = both. See repo.account_syncs_in.

class _Recorder:
    """A provider that records whether it was asked to sync at all."""

    def __init__(self):
        self.rounds = 0

    def list_sync_calendars(self):
        from app.calendars.base import SyncCalendar
        return [SyncCalendar(id="primary", name="P")]

    def sync_events(self, calendar_id, **kwargs):
        from app.calendars.base import SyncResult
        self.rounds += 1
        return SyncResult(changed=[_data("a", 9)], next_token="T", full=True)


@pytest.mark.parametrize("setting,should_sync", [
    ("two_way", True),
    ("one_way", False),
    ("none", False),
])
def test_only_a_two_way_calendar_is_mirrored(Session, monkeypatch, setting, should_sync):
    with Session() as s:
        user, account = _account(s)
        account.sync_setting = setting
        s.commit()
        rec = _Recorder()
        monkeypatch.setattr(calendar_sync, "provider_for_account", lambda *a: rec)
        counts = calendar_sync.run_sync(s, NOW)

        assert (rec.rounds > 0) is should_sync
        assert (counts["added"] > 0) is should_sync
        assert counts["skipped"] == (0 if should_sync else 1)


def test_sync_now_does_not_override_the_setting(Session, monkeypatch):
    """"Sync now" is impatience with the timer, not permission to ignore what
    the calendar was set to."""
    with Session() as s:
        user, account = _account(s)
        account.sync_setting = "one_way"
        s.commit()
        rec = _Recorder()
        monkeypatch.setattr(calendar_sync, "provider_for_account", lambda *a: rec)
        counts = calendar_sync.run_sync(s, NOW, account_id=account.id)
        assert rec.rounds == 0
        assert counts["skipped"] == 1


def test_leaving_two_way_stops_inbound_and_asks_about_the_mirror(Session):
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T1",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        assert repo.count_external_events(s, account) == 1

        repo.set_account_sync_setting(s, account, "one_way")

        assert repo.account_syncs_in(account) is False
        assert repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1]) == []
        # the bookmark goes too: resuming it later would skip everything that
        # changed while inbound was off
        assert s.get(CalendarSyncState, state.id).sync_token is None


def test_leaving_two_way_can_keep_the_mirror(Session):
    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        repo.save_sync_round(
            s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T1",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        repo.set_account_sync_setting(s, account, "none", keep_events=True)
        assert len(repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])) == 1


def test_moving_between_two_non_inbound_settings_touches_nothing(Session):
    """one_way -> none was never syncing inbound, so there is nothing to ask
    about and nothing to delete."""
    with Session() as s:
        user, account = _account(s)
        account.sync_setting = "one_way"
        s.commit()
        state = _state(s, account)
        # a mirror left over from when it WAS two-way and the user kept it
        repo.save_sync_round(
            s, state, changed=[_data("a", 9)], deleted_ids=[], next_token="T",
            full=True, window_start=WINDOW[0], window_end=WINDOW[1], now=NOW,
            store_titles=False,
        )
        repo.set_account_sync_setting(s, account, "none")
        assert len(repo.get_external_events(s, user.id, WINDOW[0], WINDOW[1])) == 1


def test_outbound_is_unchanged_by_all_of_this(Session):
    """The write paths still only care about "off". Regression guard: it would
    be easy to collapse the two predicates into one and silently stop one-way
    calendars receiving bookings."""
    with Session() as s:
        user, account = _account(s)
        for setting, out in [("two_way", True), ("one_way", True), ("none", False)]:
            account.sync_setting = setting
            assert repo.account_syncs_out(account) is out


def test_a_failure_keeps_the_existing_bookmark(Session, monkeypatch):
    """Most failures are transient. Discarding a valid token on a 429 would turn
    every blip into a full re-read of the whole window."""
    class _Flaky:
        def list_sync_calendars(self):
            from app.calendars.base import SyncCalendar
            return [SyncCalendar(id="primary", name="P")]

        def sync_events(self, calendar_id, **kwargs):
            raise RuntimeError("429 Too Many Requests")

    with Session() as s:
        user, account = _account(s)
        state = _state(s, account)
        state.sync_token = "KEEPME"
        s.commit()
        monkeypatch.setattr(calendar_sync, "provider_for_account", lambda *a: _Flaky())
        calendar_sync.run_sync(s, NOW)
        assert s.get(CalendarSyncState, state.id).sync_token == "KEEPME"
