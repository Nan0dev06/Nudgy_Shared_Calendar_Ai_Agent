"""Both background loops do their work FIRST, then sleep.

Sleeping first is what a loop naturally looks like, and it costs exactly the
window that matters. Free hosting spins the service down when it is idle, so
every wake is a fresh process; a leading sleep means nothing runs for a whole
interval after the service comes back — which is when a deadline that elapsed
during the sleep is waiting, and when the calendar mirror is at its stalest.

These tests pin the order rather than the behaviour of the jobs themselves
(`run_tick` and `run_sync` have their own suites), so what is asserted here is
just: ran, then slept — and that a crash inside the body still reaches the sleep,
because a body-first loop that skipped its sleep on error would spin hot.
"""
import asyncio

import pytest

from app.jobs import calendar_sync, plan_ticker


class _FakeSession:
    """SessionLocal() stand-in — the loop only opens and closes it."""

    def __init__(self, closed: list):
        self._closed = closed

    def close(self) -> None:
        self._closed.append(True)


# (module, name of the blocking function it calls, a counts dict it returns)
LOOPS = [
    pytest.param(plan_ticker, "run_tick",
                 {"booked": 0, "expired": 0, "reminded": 0}, id="plan_ticker"),
    pytest.param(calendar_sync, "run_sync",
                 {"added": 0, "updated": 0, "deleted": 0, "failed": 0},
                 id="calendar_sync"),
]


def _instrument(monkeypatch, module, runner, counts, body, sleeps_before_stop=1):
    """Wire fakes into `module` and return the recorded call order.

    The loop is infinite by design, so the fake sleep raises CancelledError once
    it has been reached `sleeps_before_stop` times — the same way `stop_ticker`
    ends it in production.
    """
    order: list = []
    closed: list = []

    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession(closed))

    def fake_runner(session):
        order.append("ran")
        return body()

    monkeypatch.setattr(module, runner, fake_runner)

    async def fake_sleep(seconds):
        order.append(("slept", seconds))
        if len([o for o in order if o != "ran"]) >= sleeps_before_stop:
            raise asyncio.CancelledError
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._loop(42))

    return order, closed


@pytest.mark.parametrize("module, runner, counts", LOOPS)
def test_loop_runs_once_before_its_first_sleep(monkeypatch, module, runner, counts):
    """The regression this file exists for: no dead first interval after a wake."""
    order, closed = _instrument(monkeypatch, module, runner, counts, lambda: counts)

    assert order == ["ran", ("slept", 42)]
    assert closed == [True], "the session must be closed even on the happy path"


@pytest.mark.parametrize("module, runner, counts", LOOPS)
def test_loop_keeps_running_on_the_interval(monkeypatch, module, runner, counts):
    """Body-first must not mean body-once."""
    order, _ = _instrument(monkeypatch, module, runner, counts, lambda: counts,
                           sleeps_before_stop=2)

    assert order == ["ran", ("slept", 42), "ran", ("slept", 42)]


@pytest.mark.parametrize("module, runner, counts", LOOPS)
def test_a_crash_still_sleeps(monkeypatch, module, runner, counts):
    """A raising body must not turn the loop into a busy wait."""
    def boom():
        raise RuntimeError("provider exploded")

    order, closed = _instrument(monkeypatch, module, runner, counts, boom)

    assert order == ["ran", ("slept", 42)]
    assert closed == [True], "the session must be closed on the failure path too"
