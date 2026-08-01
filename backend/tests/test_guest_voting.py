"""Voting without an account (app/api/share_routes.py).

Covers the things that make this safe to expose unauthenticated: the link is
scoped and revocable, the public view leaks no addresses, a guest's vote is a
real vote, and one person cannot quietly become two.

Rewritten for the 2026-08-01 engine. Guests answer every candidate time in
parallel with the same three states members use. Their yes counts toward a
minimum the CREATOR typed, but never toward the default rule ("every
account-holding member"), because people who joined through a link are not the
group.
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
    ids = {"host": host.id, "amy": amy.id, "group": group.id, "plan": plan.id,
           "round": plan.rounds[0].id}
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


def _guest_votes(guest, token, round_id, answer="yes"):
    return guest.post(f"/share/{token}/time-vote",
                      json={"round_id": round_id, "answer": answer})


def _member_votes(client, plan_id, round_id, answer="yes"):
    return client.post(f"/plans/{plan_id}/time-vote",
                       json={"round_id": round_id, "answer": answer})


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
    assert "@x.com" not in client.get(f"/share/{token}").text


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
    r = _guest_votes(client, token, ids["round"])
    assert r.status_code == 401


def test_a_guest_answers_the_times_directly(ctx):
    """This poll was created WITH a time, so nobody is asked "are you in?" —
    a yes on a time IS that answer."""
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)

    view = client.get(f"/share/{token}").json()
    assert view["asks_interest"] is False
    round_id = view["times"][0]["round_id"]

    view = _guest_votes(guest, token, round_id).json()
    assert view["me"]["stage"] == "waiting"
    assert view["times"][0]["my_answer"] == "yes"


def test_a_guest_can_answer_if_needed(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    view = _guest_votes(guest, token, ids["round"], "if_needed").json()
    assert view["times"][0]["my_answer"] == "if_needed"


def test_a_guest_cannot_send_a_made_up_answer(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    assert _guest_votes(guest, token, ids["round"], "sure-why-not").status_code == 400


def test_the_interest_question_is_refused_on_a_timed_poll(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    r = guest.post(f"/share/{token}/interest", json={"yes": True})
    assert r.status_code == 400


def test_on_a_float_poll_a_guest_must_say_they_are_in_first(ctx):
    client, _, ids, TS = ctx
    s = TS()
    group = repo.get_group(s, ids["group"])
    host = s.get(User, ids["host"])
    float_plan = repo.create_plan(s, group, host, title="Idea", slots=[])
    repo.append_rounds(s, float_plan, [SLOT])
    token = repo.ensure_share_token(s, float_plan)
    round_id = float_plan.rounds[0].id
    s.close()

    guest = _guest(client, token)
    assert _guest_votes(guest, token, round_id).status_code == 403

    guest.post(f"/share/{token}/interest", json={"yes": True})
    assert _guest_votes(guest, token, round_id).status_code == 200


def test_a_guest_can_change_their_mind(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    _guest_votes(guest, token, ids["round"], "yes")
    _guest_votes(guest, token, ids["round"], "no")

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    votes = repo.get_guest_votes_by_time(s, plan)
    assert votes[ids["round"]] == {"Sam (guest)": "no"}   # replaced, not doubled
    s.close()


def test_guest_votes_land_in_the_hosts_counts(ctx):
    client, _, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam")
    _guest_votes(guest, token, ids["round"])

    plan = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert plan["times"][0]["guest_yes"] == 1
    assert plan["guest_count"] == 1


# ----------------------------------------------------------------- the bar

def test_a_guest_cannot_stand_in_for_a_member_under_the_default_rule(ctx):
    """Otherwise sharing a link would let a plan book without the group."""
    client, current, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam")

    _member_votes(client, ids["plan"], ids["round"], "yes")      # host in
    current["id"] = ids["amy"]
    _member_votes(client, ids["plan"], ids["round"], "no")       # amy out
    body = _guest_votes(guest, token, ids["round"], "yes").json()

    assert body["status"] == "open"


def test_a_guest_does_count_toward_a_minimum_the_creator_typed(ctx):
    """The host said two people is enough, and a guest who said yes is one."""
    client, current, ids, _ = ctx
    client.patch(f"/plans/{ids['plan']}", json={"minimum": 2})
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam")

    _member_votes(client, ids["plan"], ids["round"], "yes")      # host in
    current["id"] = ids["amy"]
    _member_votes(client, ids["plan"], ids["round"], "no")       # amy out
    body = _guest_votes(guest, token, ids["round"], "yes").json()

    assert body["status"] == "booked"


def test_a_silent_guest_blocks_the_poll_from_finishing(ctx):
    """A guest is a participant from the moment they JOIN, so the poll is still
    waiting on them — otherwise sharing a link would make plans book EASIER."""
    client, current, ids, _ = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam")           # joins, says nothing

    _member_votes(client, ids["plan"], ids["round"], "yes")
    current["id"] = ids["amy"]
    body = _member_votes(client, ids["plan"], ids["round"], "yes").json()

    assert body["status"] == "open"


def test_a_guest_yes_can_be_the_answer_that_finishes_it(ctx):
    client, current, ids, _ = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token, "Sam", email="sam@out.com")

    _member_votes(client, ids["plan"], ids["round"], "yes")
    current["id"] = ids["amy"]
    _member_votes(client, ids["plan"], ids["round"], "yes")
    r = _guest_votes(guest, token, ids["round"], "yes")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "booked"


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
    _guest_votes(guest, token, ids["round"])
    guest.post(f"/share/{token}/join", json={"name": "Sam", "email": "sam@out.com"})

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    guests = repo.get_plan_guests(s, plan)
    assert len(guests) == 1
    assert guests[0].email == "sam@out.com"
    # the vote they'd already cast survived the re-join
    assert repo.get_guest_votes_by_time(s, plan)[ids["round"]] == {"Sam (guest)": "yes"}
    s.close()


def test_a_lost_cookie_is_reclaimed_by_email_instead_of_making_a_second_guest(ctx):
    """A guest's vote counts toward a typed minimum, so one person appearing
    twice inflates the numbers the bar is measured against. A new browser with
    the same address is the same person."""
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    first = _guest(client, token, "Sam", email="sam@out.com")
    _guest_votes(first, token, ids["round"], "yes")

    # same person, new browser (no cookie), same address
    again = TestClient(client.app)
    r = again.post(f"/share/{token}/join", json={"name": "Sam", "email": "sam@out.com"})
    assert r.status_code == 200

    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    assert len(repo.get_plan_guests(s, plan)) == 1
    # and they still hold the vote they cast from the other browser
    assert repo.get_guest_votes_by_time(s, plan)[ids["round"]] == {"Sam (guest)": "yes"}
    s.close()


def test_a_different_address_is_a_different_guest(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest(client, token, "Sam", email="sam@out.com")
    _guest(client, token, "Ali", email="ali@out.com")

    s = TS()
    assert len(repo.get_plan_guests(s, repo.get_plan(s, ids["plan"]))) == 2
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
    other_round = other.rounds[0].id
    s.close()

    # same browser, different plan: the cookie names a guest of the FIRST plan
    assert _guest_votes(guest, other_token, other_round).status_code == 401


def test_a_member_of_the_group_cannot_take_a_second_ballot_as_a_guest(ctx):
    """Otherwise opening your own group's link would give you two votes and
    quietly skew the counts you're reading."""
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
    assert third.post(f"/share/{token}/join", json={"name": "Three"}).status_code == 403


# ----------------------------------------------------------------- closing

def test_the_deadline_closes_the_link_too(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    s = TS()
    plan = repo.get_plan(s, ids["plan"])
    plan.deadline_utc = datetime.now(timezone.utc) - timedelta(minutes=1)
    s.commit()
    s.close()

    r = _guest_votes(guest, token, ids["round"])
    assert r.status_code == 400
    assert "deadline" in r.json()["detail"]


def test_a_booked_poll_stops_taking_link_votes(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    guest = _guest(client, token)
    s = TS()
    repo.set_plan_status(s, repo.get_plan(s, ids["plan"]), "booked")
    s.close()

    assert _guest_votes(guest, token, ids["round"]).status_code == 400


# ----------------------------------------------------------------- booking

def test_a_guest_label_is_never_handed_to_the_calendar(ctx, monkeypatch):
    """The tally key is "Sam (guest)"; only a real address may be invited."""
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest_votes(_guest(client, token, "Sam", email="sam@out.com"), token, ids["round"])
    _guest_votes(_guest(client, token, "Nomail"), token, ids["round"])

    # both guests are counted as coming, before anything is booked
    before = client.get(f"/groups/{ids['group']}/plans").json()[0]
    assert before["times"][0]["guest_yes"] == 2

    seen = {}
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda session, plan_, round_, organizer, emails: (
            seen.update(emails=emails),
            {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
        )[1],
    )
    body = client.post(f"/plans/{ids['plan']}/lock-in",
                       json={"round_id": ids["round"]}).json()

    # the email-less guest still counted as coming (asserted above); what they
    # can't get is an invite, and their label must never be handed over as one
    assert body["action"] == "booked"
    assert seen["emails"] == ["sam@out.com"]


def test_only_email_less_guests_still_books_on_the_hosts_calendar(ctx, monkeypatch):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest_votes(_guest(client, token, "Nomail"), token, ids["round"])

    seen = {}
    monkeypatch.setattr(
        "app.tools.booking.book_round_event",
        lambda session, plan_, round_, organizer, emails: (
            seen.update(emails=emails),
            {"booked": True, "event_link": "http://cal/x", "event_id": "e1"},
        )[1],
    )
    r = client.post(f"/plans/{ids['plan']}/lock-in", json={"round_id": ids["round"]})

    assert r.status_code == 200
    assert seen["emails"] == ["host@x.com"]


# ----------------------------------------------------------------- cleanup

def test_deleting_a_poll_takes_its_guests_and_their_votes(ctx):
    client, _, ids, TS = ctx
    token = _token(client, ids["plan"])
    _guest_votes(_guest(client, token, "Sam"), token, ids["round"])

    assert client.delete(f"/plans/{ids['plan']}").status_code == 200
    s = TS()
    from app.db.models import GuestTimeVote, PlanGuest
    assert s.query(PlanGuest).count() == 0
    assert s.query(GuestTimeVote).count() == 0
    s.close()
