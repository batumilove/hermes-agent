"""Shutdown completion owns the terminal status and durable writer quiescence."""

import asyncio
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.post_response import PostResponseWorker
from gateway.run import GatewayRunner
from gateway.session_db_recovery import RecoverableHandleCache
from gateway.run_shutdown import GatewayShutdownMixin
from hermes_state import SessionDB
from tests.gateway.restart_test_helpers import make_restart_runner
from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_shutdown_waiter_requires_cleanup_and_terminal_status(monkeypatch):
    runner, _ = make_restart_runner()
    cleanup_entered, release_cleanup = asyncio.Event(), asyncio.Event()
    terminal_entered, release_terminal = asyncio.Event(), asyncio.Event()
    terminal_settled = []

    async def cleanup(_phase):
        cleanup_entered.set()
        await release_cleanup.wait()
        return []

    async def terminal(self, _ctx):
        assert self is runner
        terminal_entered.set()
        await release_terminal.wait()
        terminal_settled.append("stopped")

    monkeypatch.setattr(GatewayRunner, "_stop_kill_tool_subprocesses_off_loop", staticmethod(cleanup))
    monkeypatch.setattr(GatewayRunner, "_stop_persist_exit_state", terminal)
    runner._record_drain_attribution = AsyncMock()
    stopping = asyncio.create_task(runner.stop())
    waiting = asyncio.create_task(runner.wait_for_shutdown())
    try:
        await asyncio.wait_for(cleanup_entered.wait(), 5)
        assert not runner._shutdown_event.is_set(), "cleanup has not completed"
        assert not waiting.done()
        release_cleanup.set()
        await asyncio.wait_for(terminal_entered.wait(), 5)
        assert not runner._shutdown_event.is_set(), "terminal status has not settled"
        assert not waiting.done()
        release_terminal.set()
        await asyncio.wait_for(waiting, 5)
        assert terminal_settled == ["stopped"]
    finally:
        release_cleanup.set()
        release_terminal.set()
        await asyncio.wait_for(stopping, 5)
        await asyncio.wait_for(waiting, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled", [False, True], ids=["settled", "live-writer"])
async def test_post_response_writer_is_sealed_before_database_close(tmp_path, monkeypatch, stalled):
    db = SessionDB(tmp_path / "state.db")
    barrier = db.create_final_delivery_and_barrier(
        obligation_id="shutdown", session_key="session", session_lineage="lineage",
        platform="telegram", chat_id="chat", thread_id=None, content="final",
        actions=[{"action_kind": "after_response", "payload": {"step": n}} for n in (1, 2)],
    )
    assert db.mark_final_delivery_attempting("shutdown")
    assert db.settle_final_delivery("shutdown", delivered=True)
    runner, _ = make_restart_runner()
    runner.session_store = SimpleNamespace(_db=db)
    runner._session_db = None
    runner._session_db_handle_cache = RecoverableHandleCache(handles={}, lock=threading.Lock())
    runner._executor_lock = threading.Lock()
    runner._executor = None
    runner._executor_closing = False
    controller = PostResponseWorker(runner)
    runner._post_response_controller = controller
    entered, release = threading.Event(), threading.Event()
    dispatched, closes = [], []

    def dispatch(action):
        dispatched.append(action)
        entered.set()
        assert release.wait(5), "test did not release the owned action"

    def close():
        closes.append(controller.thread.is_alive())

    controller._dispatch = dispatch
    monkeypatch.setattr(db, "close", close)
    controller.start()
    try:
        await async_wait_until(entered.is_set, timeout=5, what="durable action claimed")
        claimed = dispatched[0]
        ctx = GatewayShutdownMixin._StopContext(deferred_count=lambda: 0)
        ctx.started_at = time.monotonic()
        ctx.teardown_deadline = ctx.started_at + (0 if stalled else 5)
        quiescing = asyncio.create_task(asyncio.to_thread(
            runner._stop_quiesce_and_close_session_dbs, 1.0, ctx,
        ))
        try:
            await async_wait_until(controller._stop.is_set, timeout=5, what="action worker sealed")
            if not stalled:
                release.set()
            await asyncio.wait_for(quiescing, 5)
            assert runner._executor_closing is True
            assert ctx.budget_exhausted is stalled
            assert closes == ([] if stalled else [False])
            controller.wake()  # late receipts cannot restart the sealed worker
            release.set()
            await async_wait_until(lambda: not controller.thread.is_alive(), timeout=5,
                                   what="owned action worker exited")
            controller.wake()
            assert not controller.thread.is_alive()
            assert len(dispatched) == 1
            with sqlite3.connect(db.db_path) as conn:
                rows = conn.execute(
                    "SELECT action_key,state,attempts,owner_pid,owner_started_at "
                    "FROM gateway_post_response_actions ORDER BY ordinal"
                ).fetchall()
            assert rows[0] == (barrier.action_keys[0], "running", claimed.claim_attempt,
                               claimed.owner_pid, claimed.owner_started_at)
            assert rows[1][0:3] == (barrier.action_keys[1], "pending", 0)
        finally:
            release.set()
            await asyncio.wait_for(quiescing, 5)
    finally:
        release.set()
        assert controller.shutdown(2)
        SessionDB.close(db)
