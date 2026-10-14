"""Failed durable finals retain exact scoped receipt and recovery ownership."""

import asyncio
import shutil
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway import delivery_ledger as ledger
from gateway.delivery_ledger import RECONNECTED_MARKER, pending_retries
from gateway.platforms.base import SendResult
from gateway.run import GatewayRunner
from hermes_state import SessionDB
from tests.waiting import async_wait_until


@pytest.fixture(autouse=True)
def close_owned_delivery_pools(monkeypatch):
    runners = set()
    original = GatewayRunner._get_executor

    def get_executor(runner):
        runners.add(runner)
        return original(runner)

    monkeypatch.setattr(GatewayRunner, "_get_executor", get_executor)
    yield
    for runner in runners:
        assert runner._shutdown_executor(5) == 0


def _receipt(db, *, profile="receiving-bot", content="private response"):
    barrier = db.create_final_delivery_and_barrier(
        obligation_id="same-id", session_key="agent:runtime:telegram:dm:chat", session_lineage="lineage",
        platform="telegram", chat_id="chat", thread_id="7", content=content, adapter_profile=profile,
        actions=[{"action_kind": "finalize_turn", "payload": {"session_key": "session"}}],
    )
    assert db.mark_final_delivery_attempting("same-id")
    return barrier


@pytest.mark.parametrize("token,terminal", [
    ("send_path_degraded", False), ("flood_control:12.5", False),
    ("chat not found", True), ("bot blocked", True), ("flood_control:nan secret", False),
])
def test_failed_outcome_retains_safe_classification_and_terminal_authority(tmp_path, token, terminal):
    with SessionDB(tmp_path / "state.db") as db:
        barrier = _receipt(db)
        assert db.settle_final_delivery("same-id", delivered=False, error="private transport secret",
                                        operational_error=token, expected_attempts=1)
        row = db._read_one("SELECT * FROM delivery_obligations WHERE obligation_id='same-id'")
        assert row["state"] == ("abandoned" if terminal else "failed")
        assert row["attempts"] == 1 and row["content"] == "private response"
        assert "secret" not in row["last_error"]
        assert row["last_error"] == token if "secret" not in token else row["last_error"].startswith("sha256:")
        assert not db.is_final_delivery_delivered("same-id")
        assert db.is_final_delivery_finalizable("same-id") is terminal
        ready = db.claim_ready_post_response_actions()
        assert [action.action_key for action in ready] == (list(barrier.action_keys) if terminal else [])


def test_stale_settlement_cannot_overwrite_newer_attempt_or_owner(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        _receipt(db)
        assert not db.settle_final_delivery("same-id", delivered=True, expected_attempts=2)
        db._execute_write(lambda conn: conn.execute(
            "UPDATE delivery_obligations SET owner_started_at=0 WHERE obligation_id='same-id'"))
        assert not db.settle_final_delivery("same-id", delivered=False,
                                            operational_error="chat not found", expected_attempts=1)
        row = db._read_one("SELECT state,attempts,content,last_error FROM delivery_obligations")
        assert tuple(row) == ("attempting", 1, "private response", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("routed", [False, True], ids=["default-db", "routed-db"])
async def test_live_retry_preserves_receiving_bot_and_exact_database(tmp_path, routed):
    (tmp_path / "home" / "profiles" / "runtime").mkdir(parents=True)
    root = SessionDB(tmp_path / "home" / "state.db")
    satellite = SessionDB(tmp_path / "home" / "profiles" / "runtime" / "state.db")
    db, other = (satellite, root) if routed else (root, satellite)
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._background_tasks = set()
    runner._profile_adapters = {}
    runner.session_store = None
    runner._async_session_store = SimpleNamespace(clear_resume_pending=AsyncMock(), _store=None)
    wakes, sends = [], []
    runner._post_response_controller = SimpleNamespace(wake=lambda: wakes.append(True))

    async def send(**kwargs):
        sends.append(kwargs)
        return SendResult(success=True, message_id="reply")

    adapter = SimpleNamespace(send=send)
    runner._authorization_adapter = lambda platform, owner: adapter if owner == "receiving-bot" else None
    runner.adapters = {Platform.TELEGRAM: adapter}
    try:
        before = _receipt(db, content="selected body")
        _receipt(other, content="other profile body")
        for handle in (db, other):
            assert handle.settle_final_delivery("same-id", delivered=False, error="Timed out")
            handle._execute_write(lambda conn: conn.execute(
                "UPDATE delivery_obligations SET updated_at=?", (time.time() - 35,)))
        assert pending_retries(db_path=db.db_path)[0]["profile"] == "receiving-bot"
        assert await runner._redeliver_failed_obligations_for_platform(
            Platform.TELEGRAM, profile="wrong-bot", db_path=db.db_path) == 0
        handoff = SimpleNamespace(delivery_platform="telegram", delivery_adapter_profile="receiving-bot",
                                  post_response_db_path=db.db_path)
        await runner._schedule_final_response_redelivery(handoff, "sha256:opaque")
        await async_wait_until(lambda: db.is_final_delivery_delivered("same-id"), timeout=5,
                               what="scoped receipt redelivered")
        await async_wait_until(lambda: all(task.done() for task in runner._background_tasks), timeout=5,
                               what="retry timer settled")
        assert sends == [{"chat_id": "chat", "content": RECONNECTED_MARKER + "selected body",
                          "metadata": {"thread_id": "7"}}]
        selected = db._read_one("SELECT state,attempts,content,adapter_profile FROM delivery_obligations")
        untouched = other._read_one("SELECT state,attempts,content FROM delivery_obligations")
        assert tuple(selected) == ("delivered", 2, "selected body", "receiving-bot")
        assert tuple(untouched) == ("failed", 1, "other profile body")
        assert [action.action_key for action in db.claim_ready_post_response_actions()] == list(before.action_keys)
        assert wakes
        runner._running = False
        await runner._schedule_final_response_redelivery(handoff, "sha256:opaque")
        assert not any(not task.done() for task in runner._background_tasks)
    finally:
        runner._running = False
        for task in list(runner._background_tasks):
            task.cancel()
        if runner._background_tasks:
            import asyncio
            await asyncio.gather(*runner._background_tasks, return_exceptions=True)
        root.close()
        satellite.close()


@pytest.fixture
def databases(tmp_path):
    (tmp_path / "home" / "profiles" / "runtime").mkdir(parents=True)
    with SessionDB(tmp_path / "home" / "state.db") as root, SessionDB(
            tmp_path / "home" / "profiles" / "runtime" / "state.db") as routed:
        yield root, routed


def _recovery_runner(databases, adapter):
    root, routed = databases
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._background_tasks = set()
    runner.session_store = None
    runner._async_session_store = SimpleNamespace(_store=None, clear_resume_pending=AsyncMock())
    runner.adapters = {}
    runner._profile_adapters = {"receiving-bot": {Platform.TELEGRAM: adapter}}
    runner._post_response_controller = SimpleNamespace(
        wake=lambda: None, _round_robin_db_paths=lambda: [root.db_path, routed.db_path],
    )
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize("late_failure", [False, True], ids=["reconnect", "late-settlement"])
async def test_routed_reconnect_and_late_failure_use_current_receiving_bot(databases, late_failure):
    root, routed = databases
    sends = []

    async def send(**kwargs):
        sends.append(kwargs)
        return SendResult(success=True, message_id="reply")

    runner = _recovery_runner(databases, SimpleNamespace(send=send))
    _receipt(root, content="unrelated root body")
    _receipt(routed, content="routed body")
    assert root.settle_final_delivery("same-id", delivered=False, error="Timed out")
    assert routed.settle_final_delivery("same-id", delivered=False, operational_error="send_path_degraded")
    assert pending_retries(db_path=routed.db_path) == []  # Reconnect owns this category.
    if late_failure:
        await runner._schedule_final_response_redelivery(SimpleNamespace(
            delivery_platform="telegram", delivery_adapter_profile="receiving-bot",
            post_response_db_path=routed.db_path, delivery_adapter_ref=object(),
        ), "send_path_degraded")
    else:
        assert await runner._redeliver_failed_obligations_for_platform(
            Platform.TELEGRAM, profile="receiving-bot") == 1
    assert routed.is_final_delivery_delivered("same-id")
    assert not root.is_final_delivery_delivered("same-id")
    assert sends == [{"chat_id": "chat", "content": RECONNECTED_MARKER + "routed body",
                      "metadata": {"thread_id": "7"}}]


def _orphan_failed_receipt(db):
    _receipt(db)
    assert db.settle_final_delivery("same-id", delivered=False, error="Timed out")
    db._execute_write(lambda conn: conn.execute("UPDATE delivery_obligations SET owner_started_at=0"))


def _claim_state(db):
    return tuple(db._read_one("SELECT state,attempts,owner_pid,owner_started_at,last_error,updated_at FROM delivery_obligations"))


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["later-db-failure", "cancel-after-later-commit"])
async def test_multi_database_claims_are_rolled_back_without_losing_custody(databases, monkeypatch, cancel):
    _, routed = databases
    for db in databases:
        _orphan_failed_receipt(db)
    before = [_claim_state(db) for db in databases]
    runner = _recovery_runner(databases, SimpleNamespace())
    original = ledger.sweep_recoverable
    committed, release = threading.Event(), threading.Event()
    failure = ValueError("later database failed")

    def sweep(*args, **kwargs):
        if kwargs["db_path"] != routed.db_path:
            return original(*args, **kwargs)
        if not cancel:
            raise failure
        rows = original(*args, **kwargs)
        committed.set()
        assert release.wait(5), "test did not release committed claims"
        return rows

    monkeypatch.setattr(ledger, "sweep_recoverable", sweep)
    task = asyncio.create_task(runner._sweep_delivery_databases(
        {"telegram"}, {("telegram", "receiving-bot")},
    ))
    try:
        if cancel:
            await async_wait_until(committed.is_set, timeout=5, what="second database claims committed")
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError) as error:
                await task
            assert error.value is failure
        assert [_claim_state(db) for db in databases] == before
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_restart_claims_registered_routed_receipts_and_retains_stale_cas(databases):
    root, routed = databases
    for db in databases:
        _orphan_failed_receipt(db)
    runner = _recovery_runner(databases, SimpleNamespace())
    claims = await runner._claim_pending_obligations()
    assert {row["db_path"] for row in claims} == {root.db_path, routed.db_path}
    assert len(claims) == 2 and all(row["attempts"] == 2 and row["needs_marker"] for row in claims)
    # A newer owner must survive an old recovery caller's cancellation rollback.
    routed._execute_write(lambda conn: conn.execute(
        "UPDATE delivery_obligations SET owner_started_at=owner_started_at+1"))
    replacement = _claim_state(routed)
    assert ledger.release_recoverable_claims(claims) == 1
    assert _claim_state(root)[0:2] == ("failed", 1)
    assert _claim_state(routed) == replacement


def test_durable_initial_send_keeps_the_last_unknown_attempt_for_restart(databases):
    db, _ = databases
    _receipt(db)
    assert db.settle_final_delivery("same-id", delivered=False, error="Timed out")
    db._execute_write(lambda conn: conn.execute(
        "UPDATE delivery_obligations SET updated_at=?", (time.time() - 35,)))
    claims = ledger.sweep_failed_for_runtime("telegram", profile="receiving-bot", db_path=db.db_path)
    assert len(claims) == 1 and claims[0]["attempts"] == 2
    assert ledger.mark_failed("same-id", "sha256:opaque", expected_attempts=2, db_path=db.db_path)
    assert pending_retries(db_path=db.db_path) == []
    assert ledger.sweep_failed_for_runtime("telegram", time.time() + 3600,
                                          profile="receiving-bot", db_path=db.db_path) == []
    db._execute_write(lambda conn: conn.execute("UPDATE delivery_obligations SET owner_started_at=0"))
    boot = ledger.sweep_recoverable(db_path=db.db_path)
    assert len(boot) == 1 and boot[0]["attempts"] == 3


@pytest.mark.parametrize("remove_home", [False, True], ids=["missing-db", "deleted-profile"])
def test_scoped_recovery_never_recreates_a_removed_database(databases, remove_home):
    _, db = databases
    path = db.db_path
    db.close()
    if remove_home:
        shutil.rmtree(path.parent)
    else:
        path.unlink()
    with pytest.raises((FileNotFoundError, sqlite3.OperationalError)):
        pending_retries(db_path=path)
    assert not path.exists()
    assert path.parent.exists() is (not remove_home)


@pytest.mark.asyncio
async def test_scoped_cancelled_ledger_write_remains_in_shutdown_census(databases, monkeypatch):
    db, _ = databases
    runner = _recovery_runner(databases, SimpleNamespace(send=AsyncMock(return_value=SendResult(success=True))))
    _receipt(db)
    entered, release = threading.Event(), threading.Event()
    original = ledger.mark_delivered
    monkeypatch.setattr(ledger, "_db_path", lambda: db.db_path)

    def settle(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test did not release scoped ledger writer"
        return original(*args, **kwargs)

    monkeypatch.setattr(ledger, "mark_delivered", settle)
    row = dict(db._read_one("SELECT * FROM delivery_obligations"))
    row.update(db_path=db.db_path, profile="receiving-bot", needs_marker=True)
    writing = asyncio.create_task(runner._redeliver_claimed_obligations([row]))
    try:
        await async_wait_until(entered.is_set, timeout=5, what="scoped ledger worker entered")
        writing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writing
        workers = list(getattr(getattr(runner, "_executor", None), "_threads", ()))
        assert runner._shutdown_executor(0) == 1  # DB close must see the live writer.
        with pytest.raises(RuntimeError, match="shutting down"):
            await runner._run_delivery_ledger_sync(settle, db_path=db.db_path)
        release.set()
        await async_wait_until(lambda: db.is_final_delivery_delivered("same-id"), timeout=5,
                               what="original owned ledger write settled")
        await async_wait_until(lambda: all(not worker.is_alive() for worker in workers), timeout=5,
                               what="owned ledger worker exited")
    finally:
        release.set()


@pytest.mark.asyncio
async def test_runtime_claim_cancellation_restores_exact_committed_receipt(databases, monkeypatch):
    db, _ = databases
    _receipt(db)
    assert db.settle_final_delivery("same-id", delivered=False, operational_error="send_path_degraded")
    before = _claim_state(db)
    runner = _recovery_runner(databases, SimpleNamespace())
    original = ledger.sweep_failed_for_runtime
    committed, release = threading.Event(), threading.Event()

    def sweep(*args, **kwargs):
        rows = original(*args, **kwargs)
        assert rows and rows[0]["attempts"] == 2
        committed.set()
        assert release.wait(5), "test did not release committed runtime claim"
        return rows

    monkeypatch.setattr(ledger, "sweep_failed_for_runtime", sweep)
    claiming = asyncio.create_task(runner._claim_runtime_delivery_rows(
        Platform.TELEGRAM, "receiving-bot", db.db_path,
    ))
    try:
        await async_wait_until(committed.is_set, timeout=5, what="runtime claim committed")
        claiming.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await claiming
        assert _claim_state(db) == before
    finally:
        release.set()
        if not claiming.done():
            await asyncio.gather(claiming, return_exceptions=True)


def test_cancelled_startup_adoption_preserves_platform_wait_exactly(databases):
    db, _ = databases
    _receipt(db)
    assert db.settle_final_delivery("same-id", delivered=False, operational_error="flood_control:900")
    db._execute_write(lambda conn: conn.execute("UPDATE delivery_obligations SET owner_started_at=0"))
    before = _claim_state(db)
    claims = ledger.sweep_recoverable(db_path=db.db_path)
    assert len(claims) == 1 and claims[0]["adopted"] and claims[0]["attempts"] == 1
    assert ledger.release_recoverable_claims(claims) == 1
    assert _claim_state(db) == before
    next_boot = ledger.sweep_recoverable(db_path=db.db_path)
    assert len(next_boot) == 1 and next_boot[0]["adopted"] and next_boot[0]["attempts"] == 1


def test_multi_database_rollback_attempts_later_stores_after_first_is_removed(databases):
    root, routed = databases
    for db in databases:
        _orphan_failed_receipt(db)
    routed_before = _claim_state(routed)
    claims = ledger.sweep_recoverable(db_path=root.db_path) + ledger.sweep_recoverable(db_path=routed.db_path)
    assert len(claims) == 2
    root.close()
    root.db_path.unlink()
    with pytest.raises((OSError, sqlite3.Error)):
        ledger.release_recoverable_claims(claims)
    assert not root.db_path.exists()
    assert _claim_state(routed) == routed_before
