"""Capacity refusals must never acquire execution ownership inline."""
import json
import threading
import time
from typing import Any
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tools import async_delegation as ad
from tools import delegate_tool as dt
from tools.process_registry import process_registry


@pytest.fixture
def isolated(monkeypatch):
    ad._reset_for_tests()
    monkeypatch.setattr(ad, "_ensure_stale_monitor", lambda: None)
    monkeypatch.setattr("gateway.session_context.async_delivery_supported", lambda: True)
    monkeypatch.setattr(dt, "_get_max_async_children", lambda: 3)
    monkeypatch.setattr(dt, "_resolve_delegation_credentials", lambda *a, **k: dict(
        model="m", provider=None, base_url=None, api_key=None, api_mode=None))
    parent = MagicMock()
    parent._delegate_depth = 0
    parent.session_id = "capacity-owner"
    parent._interrupt_requested = False
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    built = []
    def build(**kw):
        child = SimpleNamespace(_delegate_role="leaf", close=MagicMock())
        built.append(child)
        parent._active_children.append(child)
        return child
    monkeypatch.setattr(dt, "_build_child_agent", build)
    calls = []
    def run(task_index, goal, child=None, parent_agent=None, **kw):
        calls.append(goal)
        return dict(task_index=task_index, status="completed", summary="done",
                    api_calls=0, duration_seconds=0, model="m")
    monkeypatch.setattr(dt, "_run_single_child", run)
    yield parent, built, calls
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()


def dispatch(runner, session="capacity-owner"):
    return ad.dispatch_async_delegation_batch(goals=["accepted work"], context=None,
        toolsets=None, role="leaf", model="m", session_key=session,
        runner=runner, max_async_children=3)


@pytest.mark.parametrize("batch", [False, True])
def test_fourth_dispatch_busy_not_inline_and_accepted_complete_once(isolated, batch):
    parent, built, calls = isolated
    gate = threading.Event()
    accepted = []
    def runner():
        gate.wait(5)
        return {"results": [{"status": "completed"}]}
    try:
        accepted = [dispatch(runner) for _ in range(3)]
        assert all(r["status"] == "dispatched" for r in accepted)
        kwargs: dict[str, Any] = ({"tasks": [{"goal": "first rejected independent task"},
                              {"goal": "second rejected independent task"}]} if batch
                  else {"goal": "rejected work"})
        start = time.monotonic()
        result = json.loads(dt.delegate_task(**kwargs, background=True, parent_agent=parent))
        assert not calls, "capacity rejection executed child work inline"
        assert time.monotonic() - start < 2
        assert result["status"] == "busy"
        assert result["accepted"] is False
        assert "action='list'" in result["control_hint"]
        assert ad.active_count() == 3
        assert not parent._active_children
        for child in built:
            child.close.assert_called_once()
    finally:
        gate.set()
        deadline = time.monotonic() + 5
        while ad.active_count() and time.monotonic() < deadline:
            time.sleep(.01)
    events = []
    while not process_registry.completion_queue.empty():
        events.append(process_registry.completion_queue.get_nowait())
    assert sorted(e["delegation_id"] for e in events) == sorted(r["delegation_id"] for r in accepted)
    assert all(e["session_key"] == "capacity-owner" for e in events)


def test_schedule_failure_retires_children_without_inline_retry(isolated, monkeypatch):
    parent, built, calls = isolated
    executor = MagicMock()
    executor.submit.side_effect = RuntimeError("executor unavailable")
    monkeypatch.setattr(ad, "_get_executor", lambda n: executor)
    result = json.loads(dt.delegate_task(goal="schedule failure", background=True, parent_agent=parent))
    assert not calls, "schedule rejection must not execute a fallback"
    assert result["status"] == "rejected"
    assert result["accepted"] is False
    assert ad.active_count() == 0
    assert not parent._active_children
    built[0].close.assert_called_once()
    assert not ad._records


def test_partial_admission_retires_only_rejected_units(isolated, monkeypatch):
    from tools import delegate_tool_dispatch as dd
    parent, built, calls = isolated
    monkeypatch.setattr("tools.delegate_tool_config._get_independent_completions", lambda: True)
    original = dd._dispatch_unit
    gate = threading.Event()
    entered = threading.Event()
    def run(task_index, goal, **kw):
        calls.append(goal)
        entered.set()
        gate.wait(5)
        return dict(task_index=task_index, status="completed", summary="done")
    monkeypatch.setattr(dt, "_run_single_child", run)
    count = 0
    def submit(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            return {"status": "rejected", "error": "executor unavailable"}
        return original(*args, **kwargs)
    monkeypatch.setattr(dd, "_dispatch_unit", submit)
    try:
        result = json.loads(dt.delegate_task(tasks=[{"goal": "accepted first"},
            {"goal": "rejected second"}], background=True, parent_agent=parent))
        assert entered.wait(2)
        assert calls == ["accepted first"]
        assert result["status"] == "dispatched"
        assert result["count"] == 1
        assert result["rejected_results"][0]["task_index"] == 1
        built[1].close.assert_called_once()
        built[0].close.assert_not_called()
    finally:
        gate.set()
        deadline = time.monotonic() + 5
        while ad.active_count() and time.monotonic() < deadline:
            time.sleep(.01)


def test_concurrent_admission_has_only_three_owners(isolated):
    gate = threading.Event()
    barrier = threading.Barrier(8)
    results = []
    def runner():
        gate.wait(5)
        return {"results": [{"status": "completed"}]}
    def submit():
        barrier.wait()
        results.append(dispatch(runner))
    threads = [threading.Thread(target=submit) for _ in range(8)]
    try:
        for t in threads: t.start()
        for t in threads: t.join(3)
        assert len(results) == 8
        assert sum(r["status"] == "dispatched" for r in results) == 3
        assert ad.active_count() == 3
    finally:
        gate.set()
        deadline = time.monotonic() + 5
        while ad.active_count() and time.monotonic() < deadline: time.sleep(.01)
