"""Voting without an account (app/api/share_routes.py).

Covers the three things that make this safe to expose unauthenticated: the link
is scoped and revocable, the public view leaks no addresses, and a guest's vote
is a real vote — counted in the host's tally and in the unanimity check.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import plan_routes, share_routes
from app.api.deps import COOKIE_NAME, get_current_user, make_session_cookie
from app.db.models import Base, User
from app.db import repo
from app.db.session import get_session
from app.tools.plan_service import load_plan_state, plan_tally

SLOT = (datetime(2026, 8, 1, 17, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 18, tzinfo=timezone.utc))


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, expire_on_commit=False)

    s = TS()
    host, amy = User(email="host@x.com", timezone="UTC"), User(email="amy@x.com", timezone="UTC")
    s.add_all([host, amy])
    s.commit()
    group = repo.create_group(s, "Crew", host)
    repo.add_member(s, group, amy)
    plan = repo.create_plan(s, group, host, title="Coffee", location="Cafe", slots=[SLOT])
    ids = {"host": host.id, "amy": amy.id, "group": group.id, "plan": plan.id}
    s.close()

    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda *a, **k: {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
    )

    app = FastAPI()
    app.include_router(plan_routes.router)
    app.include_router(share_routes.router)
    current = {"id": ids["host"]}

    def override_user():
        return TS().get(User, current["id"])

    def override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_session] = override_session
    return TestClient(app), current, ids, TS


def _token(client, plan_id, regenerate=False):
    r = client.post(f"/plans/{plan_id}/share", params={"regenerate": regenerate})
    assert r.status_code == 200, r.text
    return r.json()["share_token"]


def _guest(app_client, token, name="Sam", email=None):
    """A visitor with their own cookie jar — each guest is a separate browser."""
    c = TestClient(app_client.app)
    r = c.post(f"/share/{token}/join", json={"name": name, "email": email})
    assert r.status_code == 200, r.text
    return c


# ----------------------------------------------------------------- the link

def test_the_host_gets_a_link_and_it_is_stable(ctx):
    client, _, ids, _ = ctx
    first = client.post(f"/plans/{ids['plan']}/share").json()
    second = client.post(f"/plans/{ids['plan']}/share").json()
    assert first["share_token"] == second["share_token"]
    assert first["share_url"].endswith(f"?share={first['share_token']}")


def test_only_the_host_can_get_the_link(ctx):
    client, current, ids, _ = ctx
    current["id"] = ids["amy"]
    assert client.post(f"/plans/{ids['plan']}/share").status_code == 403


def test_a_member_never_sees_the_link_in_the_plan_feed(ctx):
    client, current, ids, _ = ctx
    _token(client, ids["plan"])
    current["id"] = ids["amy"]
    plan = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert "share_url" not in plan


def test_regenerating_kills_the_old_link(ctx):
    client, _, ids, _ = ctx
    old = _token(client, ids["plan"])
    new = _token(client, ids["plan"], regenerate=True)
    assert old != new
    assert client.get(f"/share/{old}").status_code == 404
    assert client.get(f"/share/{new}").status_code == 200


def test_revoking_turns_the_link_off(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    assert client.delete(f"/plans/{ids['plan']}/share").status_code == 200
    assert client.get(f"/share/{token}").status_code == 404


def test_a_made_up_token_reveals_nothing(ctx):
    client, _, _, _ = ctx
    r = client.get("/share/not-a-real-token")
    assert r.status_code == 404
    assert "isn't active" in r.json()["detail"]


# ----------------------------------------------------------------- privacy

def test_the_public_view_carries_no_email_addresses(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    body = client.get(f"/share/{token}").text
    assert "@x.com" not in body


def test_the_public_view_shows_the_host_by_first_name_only(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    view = client.get(f"/share/{token}").json()
    assert view["host_name"] == "Host"
    assert view["title"] == "Coffee"
    assert view["group_name"] == "Crew"


# ----------------------------------------------------------------- voting

def test_a_visitor_must_name_themselves_before_voting(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    r = client.post(f"/share/{token}/interest", json={"yes": True})
    assert r.status_code == 401


def test_a_guest_votes_and_the_cascade_opens_the_time_question(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)

    view = guest.post(f"/share/{token}/interest", json={"yes": True}).json()
    assert view["me"]["stage"] == "time"

    round_id = view["active_round_id"]
    view = guest.post(f"/share/{token}/time-vote",
                      json={"yes": True, "round_id": round_id}).json()
    assert view["me"]["stage"] == "waiting"


def test_a_guest_cannot_answer_a_time_without_being_in(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    view = client.get(f"/share/{token}").json()
    r = guest.post(f"/share/{token}/time-vote",
                   json={"yes": True, "round_id": view["active_round_id"]})
    assert r.status_code == 403


def test_a_guest_can_change_their_mind(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    guest.post(f"/share/{token}/interest", json={"yes": True})
    guest.post(f"/share/{token}/interest", json={"yes": False})

    s = TS()
    votes = repo.get_guest_interest_votes(s, repo.get_plan(s, ids["plan"]))
    assert votes == {"Sam (guest)": False}   # replaced, not doubled
    s.close()


def test_guest_votes_land_in_the_hosts_tally(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam").post(f"/share/{token}/interest", json={"yes": True})

    plan = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert "Sam (guest)" in plan["host_box"]["interested"]


def test_a_guest_who_joined_but_went_quiet_shows_as_waiting(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam")

    plan = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert "Sam (guest)" in plan["host_box"]["no_answer"]


# ----------------------------------------------------------------- identity

def test_two_browsers_are_two_different_guests(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam")
    _guest(client, token, "Ali")

    s = TS()
    assert len(repo.get_plan_guests(s, repo.get_plan(s, ids["plan"]))) == 2
    s.close()


def test_a_second_person_cannot_take_a_name_already_in_use(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam")

    other = TestClient(client.app)
    r = other.post(f"/share/{token}/join", json={"name": "sam"})
    assert r.status_code == 409
    assert "last initial" in r.json()["detail"]


def test_rejoining_from_the_same_browser_keeps_the_same_identity(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam")
    guest.post(f"/share/{token}/interest", json={"yes": True})
    guest.post(f"/share/{token}/join", json={"name": "Sam", "email": "sam@out.com"})

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    guests = repo.get_plan_guests(s, plan)
    assert len(guests) == 1
    assert guests[0].email == "sam@out.com"
    # the vote they'd already cast survived the re-join
    assert repo.get_guest_interest_votes(s, plan) == {"Sam (guest)": True}
    s.close()


def test_a_cookie_from_another_plan_does_not_carry_over(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam")

    s = TS()
    group = repo.get_group(s, ids["group"])
    host = s.get(User, ids["host"])
    other = repo.create_plan(s, group, host, title="Lunch", slots=[SLOT])
    other_token = repo.ensure_share_token(s, other)
    s.close()

    # same browser, different plan: the cookie names a guest of the FIRST plan
    r = guest.post(f"/share/{other_token}/interest", json={"yes": True})
    assert r.status_code == 401


def test_a_member_of_the_group_cannot_take_a_second_ballot_as_a_guest(ctx):
    """Otherwise opening your own group's link would give you two votes and
    quietly skew the tally you're reading."""
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])

    # a REAL session cookie, not the dependency override — the guard reads the
    # cookie itself, since the share routes have no auth dependency to override
    amy = TestClient(client.app)
    amy.cookies.set(COOKIE_NAME, make_session_cookie(ids["amy"]))

    r = amy.post(f"/share/{token}/join", json={"name": "Amy"})
    assert r.status_code == 409
    assert "already" in r.json()["detail"]


def test_a_signed_in_outsider_can_still_vote_as_a_guest(ctx):
    """Having an account isn't the same as being in this group — someone from a
    different group who was sent the link is exactly who this is for."""
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    s = TS()
    outsider = User(email="zoe@elsewhere.com")
    s.add(outsider)
    s.commit()
    outsider_id = outsider.id
    s.close()

    zoe = TestClient(client.app)
    zoe.cookies.set(COOKIE_NAME, make_session_cookie(outsider_id))
    assert zoe.post(f"/share/{token}/join", json={"name": "Zoe"}).status_code == 200


def test_the_guest_cap_holds(ctx, monkeypatch):
    client, _, ids, _ = ctx
    monkeypatch.setattr(repo, "MAX_GUESTS_PER_PLAN", 2)
    token = _token(client, ids["plan"])
    _guest(client, token, "One")
    _guest(client, token, "Two")

    third = TestClient(client.app)
    r = third.post(f"/share/{token}/join", json={"name": "Three"})
    assert r.status_code == 403


# ----------------------------------------------------------------- closing

def test_the_deadline_closes_the_link_too(ctx, monkeypatch):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    plan.deadline_utc = datetime.now(timezone.utc) - timedelta(minutes=1)
    s.commit()
    s.close()

    r = guest.post(f"/share/{token}/interest", json={"yes": True})
    assert r.status_code == 400
    assert "deadline" in r.json()["detail"]


def test_a_booked_plan_stops_taking_link_votes(ctx, monkeypatch):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    s = TS()
    repo.set_plan_status(s, repo.get_plan(s, ids["plan"]), "scheduled")
    s.close()

    assert guest.post(f"/share/{token}/interest", json={"yes": True}).status_code == 400


# ----------------------------------------------------------------- auto-book

def test_a_silent_guest_blocks_auto_book(ctx):
    """The guest is a participant from the moment they join, so unanimity has to
    include them — otherwise sharing a link would make plans book EASIER."""
    client, current, ids, _ = ctx
    client.patch(f"/plans/{ids['plan']}", json={"auto_book": True})
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam")

    plan = client.get(f"/groups/{ids['group']}/plans").json()[0]
    round_id = plan["times"][0]["round_id"]
    client.post(f"/plans/{ids['plan']}/time-vote", json={"yes": True, "round_id": round_id})
    current["id"] = ids["amy"]
    client.post(f"/plans/{ids['plan']}/interest", json={"yes": True})
    body = client.post(f"/plans/{ids['plan']}/time-vote",
                       json={"yes": True, "round_id": round_id}).json()

    assert body["status"] == "open"


def test_a_guest_yes_can_be_the_vote_that_books_it(ctx):
    client, current, ids, _ = ctx
    client.patch(f"/plans/{ids['plan']}", json={"auto_book": True})
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam", email="sam@out.com")

    plan = client.get(f"/groups/{ids['group']}/plans").json()[0]
    round_id = plan["times"][0]["round_id"]
    client.post(f"/plans/{ids['plan']}/time-vote", json={"yes": True, "round_id": round_id})
    current["id"] = ids["amy"]
    client.post(f"/plans/{ids['plan']}/interest", json={"yes": True})
    client.post(f"/plans/{ids['plan']}/time-vote", json={"yes": True, "round_id": round_id})

    guest.post(f"/share/{token}/interest", json={"yes": True})
    r = guest.post(f"/share/{token}/time-vote", json={"yes": True, "round_id": round_id})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"


# ----------------------------------------------------------------- booking

def test_a_guest_label_is_never_handed_to_the_calendar(ctx, monkeypatch):
    """The tally key is "Sam (guest)"; only a real address may be invited."""
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam", email="sam@out.com").post(
        f"/share/{token}/interest", json={"yes": True})
    _guest(client, token, "Nomail").post(f"/share/{token}/interest", json={"yes": True})

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    active = repo.get_active_round(s, plan)
    for g in repo.get_plan_guests(s, plan):
        repo.cast_guest_time_vote(s, active, g, True)
    s.close()

    # both guests are counted as coming, before anything is booked
    before = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert set(before["host_box"]["time_yes"]) == {"Sam (guest)", "Nomail (guest)"}

    seen = {}
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda session, plan_, round_, organizer, emails: (
            seen.update(emails=emails),
            {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
        )[1],
    )
    body = client.post(f"/plans/{ids['plan']}/lock-in").json()

    # the email-less guest still counted as coming (asserted above); what they
    # can't get is an invite, and their label must never be handed over as one
    assert body["action"] == "booked"
    assert seen["emails"] == ["sam@out.com"]


def test_only_email_less_guests_still_books_on_the_hosts_calendar(ctx, monkeypatch):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Nomail").post(f"/share/{token}/interest", json={"yes": True})

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    repo.cast_guest_time_vote(s, repo.get_active_round(s, plan),
                              repo.get_plan_guests(s, plan)[0], True)
    s.close()

    seen = {}
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda session, plan_, round_, organizer, emails: (
            seen.update(emails=emails),
            {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
        )[1],
    )
    r = client.post(f"/plans/{ids['plan']}/lock-in")

    assert r.status_code == 200
    assert seen["emails"] == ["host@x.com"]


# ----------------------------------------------------------------- cleanup

def test_deleting_a_plan_takes_its_guests_and_their_votes(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam").post(f"/share/{token}/interest", json={"yes": True})

    assert client.delete(f"/plans/{ids['plan']}").status_code == 200
    s = TS()
    from app.db.models import GuestInterestVote, PlanGuest
    assert s.query(PlanGuest).count() == 0
    assert s.query(GuestInterestVote).count() == 0
    s.close()
