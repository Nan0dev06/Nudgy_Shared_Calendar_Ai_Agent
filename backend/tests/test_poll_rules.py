"""Tests for the exact poll decision rules from the spec."""
from app.tools.poll_rules import APPROVED, PENDING, REJECTED, evaluate_poll

MEMBERS = ["a@x.com", "b@x.com", "c@x.com"]


def test_single_no_rejects_even_with_enough_yes():
    d = evaluate_poll({"a@x.com": True, "b@x.com": True, "c@x.com": False}, MEMBERS, 2)
    assert d.outcome == REJECTED
    assert "c@x.com" in d.reason


def test_no_vote_rejects_immediately_even_before_others_vote():
    d = evaluate_poll({"b@x.com": False}, MEMBERS, 1)
    assert d.outcome == REJECTED


def test_threshold_met_with_zero_no_approves():
    d = evaluate_poll({"a@x.com": True, "b@x.com": True}, MEMBERS, 2)
    assert d.outcome == APPROVED
    assert d.missing_voters == ["c@x.com"]


def test_unanimous_required_and_reached():
    votes = {e: True for e in MEMBERS}
    d = evaluate_poll(votes, MEMBERS, 3)
    assert d.outcome == APPROVED
    assert d.missing_voters == []


def test_not_enough_yes_yet_is_pending_not_rejected():
    d = evaluate_poll({"a@x.com": True}, MEMBERS, 2)
    assert d.outcome == PENDING
    assert set(d.missing_voters) == {"b@x.com", "c@x.com"}
    assert "proceed" in d.reason  # agent must ask, never auto-book


def test_nobody_voted_is_pending():
    d = evaluate_poll({}, MEMBERS, 1)
    assert d.outcome == PENDING
    assert d.missing_voters == MEMBERS


def test_silence_never_books_threshold_above_votes():
    # 1 yes, threshold 2, others silent -> pending forever, never approved
    d = evaluate_poll({"a@x.com": True}, MEMBERS, 2)
    assert d.outcome == PENDING
