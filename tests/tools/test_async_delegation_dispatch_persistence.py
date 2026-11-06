"""Dispatch refusal releases capacity; optional retention never vetoes a durable dispatch."""

import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest

from tools import async_delegation as ad
from tools.process_registry import process_registry


@pytest.fixture(autouse=True)
def clean_dispatch_state():
    ad._reset_for_tests()
    yield
    if ad._executor is not None:
        ad._executor.shutdown(wait=True)
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()


def dispatch(batch, runner):
    common = dict(context=None, toolsets=None, role="leaf", model=None,
                  session_key="owner", runner=runner, max_async_children=1)
    if batch:
        return ad.dispatch_async_delegation_batch(goals=["work"], **common)
    return ad.dispatch_async_delegation(goal="work", **common)


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize("remote", [False, True], ids=["local-ledger", "worker-ledger"])
@pytest.mark.parametrize("phase", ["persist", "prepare", "submit"])
def test_presubmission_failure_refuses_work_and_releases_its_slot(monkeypatch, batch, remote, phase):
    persist = ad._persist_dispatch
    called = threading.Event()

    def fail_after_commit(record):
        persist(record)
        raise sqlite3.OperationalError("dispatch storage unavailable")

    remote_rows = {}
    ledger = SimpleNamespace(
        dispatch=lambda record: remote_rows.update({record["delegation_id"]: record}),
        unscheduled=lambda delegation_id: remote_rows.pop(delegation_id),
    )

    def forbidden_connection():
        pytest.fail("a worker dispatch must not open the canonical SQLite ledger")

    def refuse_executor(*_args, **_kwargs):
        raise RuntimeError("executor unavailable")

    with monkeypatch.context() as scoped:
        if remote:
            scoped.setattr(ad, "worker_ledger", lambda: ledger)
            scoped.setattr(ad, "_connect", forbidden_connection)
        if phase == "persist":
            scoped.setattr(ad, "_persist_dispatch", fail_after_commit)
        else:
            executor = SimpleNamespace(submit=refuse_executor)
            scoped.setattr(ad, "_get_executor", refuse_executor if phase == "prepare" else lambda _workers: executor)
        result = dispatch(batch, lambda: called.set())
    assert result["status"] == "rejected"
    if phase == "persist":
        assert "persist" in result["error"].lower()
    assert not called.is_set()
    assert ad.active_count() == 0
    assert ad.list_async_delegations() == []
    if remote:
        assert remote_rows == {}
    else:
        with ad._DB_LOCK, closing(ad._connect()) as conn:
            assert conn.execute("SELECT COUNT(*) FROM async_delegations").fetchone()[0] == 0

    retry = dispatch(batch, lambda: {"status": "completed", "summary": "done"})
    assert retry["status"] == "dispatched"
    event = process_registry.completion_queue.get(timeout=5)
    assert event["delegation_id"] == retry["delegation_id"]


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_pruning_failure_does_not_veto_an_already_persisted_dispatch(monkeypatch, batch):
    def refuse_pruning():
        raise sqlite3.OperationalError("retention database is locked")

    monkeypatch.setattr(ad, "_prune_durable_records", refuse_pruning)
    result = dispatch(batch, lambda: {"status": "completed", "summary": "done"})
    assert result["status"] == "dispatched"
    event = process_registry.completion_queue.get(timeout=5)
    assert event["delegation_id"] == result["delegation_id"]
    assert ad.get_durable_delegation(result["delegation_id"])["state"] == "completed"
