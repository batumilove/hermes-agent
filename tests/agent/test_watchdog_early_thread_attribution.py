"""Watchdog diagnostics attribute preparation before interrupt-scope binding."""
import logging
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("prior_thread_id", [None, -1])
def test_preparation_stack_uses_scheduling_owner_without_resetting_interrupts(monkeypatch, caplog, prior_thread_id):
    from agent import periodic_scheduler
    from agent.turn_liveness import TurnLivenessWatchdog

    agent = SimpleNamespace(
        _execution_thread_id=prior_thread_id,
        _interrupt_requested=True,
        _interrupt_message="pending user stop",
        _interrupt_thread_signal_pending=True,
    )
    before = vars(agent).copy()
    handles = []

    def schedule(callback, interval):
        handle = object()
        handles.append(handle)
        return handle

    monkeypatch.setattr(periodic_scheduler, "schedule", schedule)
    watchdog = TurnLivenessWatchdog(
        agent, session_id="isolated", timeout_s=30, poll_s=5,
        stop_event=threading.Event(), activity_lock=threading.Lock(),
        is_turn_active=lambda: True, commit_abort=lambda *_: False,
        deactivate_turn=lambda: None,
    )
    ready = threading.Event()
    release = threading.Event()
    failures = []
    owner_ids = []

    def preparation_owner():
        try:
            owner_ids.append(threading.get_ident())
            assert watchdog.schedule() is handles[0]
            ready.set()
            assert release.wait(5), "diagnostic observer never released owner"
        except BaseException as exc:
            failures.append(exc)
            ready.set()

    owner = threading.Thread(target=preparation_owner, name="early-preparation-owner")
    owner.start()
    try:
        assert ready.wait(5)
        assert not failures
        with caplog.at_level(logging.ERROR, logger="agent.turn_liveness"):
            watchdog._dump_turn_thread_stack()
        assert f"tid={owner_ids[0]} <- turn thread" in caplog.text
        assert "preparation_owner" in caplog.text
        assert vars(agent) == before
    finally:
        release.set()
        owner.join(5)
    assert not owner.is_alive()
    assert not failures


def test_unscheduled_watchdog_retains_existing_execution_thread_fallback(caplog):
    from agent.turn_liveness import TurnLivenessWatchdog

    watchdog = TurnLivenessWatchdog(
        SimpleNamespace(_execution_thread_id=threading.get_ident()),
        session_id="isolated", timeout_s=30, poll_s=5,
        stop_event=threading.Event(), activity_lock=threading.Lock(),
        is_turn_active=lambda: True, commit_abort=lambda *_: False,
        deactivate_turn=lambda: None,
    )
    with caplog.at_level(logging.ERROR):
        watchdog._dump_turn_thread_stack()
    assert f"tid={threading.get_ident()} <- turn thread" in caplog.text
