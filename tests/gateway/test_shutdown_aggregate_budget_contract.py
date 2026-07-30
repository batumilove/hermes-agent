"""Protected RED contracts for bounded aggregate gateway shutdown cleanup."""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gateway.run as gateway_run
from tests.gateway.restart_test_helpers import make_restart_runner


async def _run_stop(runner):
    with (
        patch("gateway.status.remove_pid_file") as remove_pid,
        patch("gateway.status.release_gateway_runtime_lock") as release_lock,
        patch("gateway.status.write_runtime_status"),
    ):
        await runner.stop()
    remove_pid.assert_called_once()
    release_lock.assert_called_once()


def _configure_fast_forced_shutdown(runner, monkeypatch, active_agents):
    """Reach post-drain cleanup without real waits, processes, or user state."""
    runner._restart_drain_timeout = 0.01
    runner._SHUTDOWN_TAIL_RESERVE_S = 0.50
    monkeypatch.setattr(gateway_run, "resolve_shutdown_watchdog_delay", lambda _t: 1.50)
    runner._notify_active_sessions_with_timeout = AsyncMock(return_value=True)
    runner._drain_active_agents = AsyncMock(return_value=(active_agents, True))
    runner._running_agents = {}
    runner._finalize_shutdown_agents = (
        gateway_run.GatewayRunner._finalize_shutdown_agents.__get__(
            runner, gateway_run.GatewayRunner
        )
    )

    async def _inline_executor(func, *args):
        return func(*args)

    runner._run_in_executor_with_context = _inline_executor
    runner._cleanup_agent_resources = MagicMock()
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner.adapters.clear()
    runner._background_tasks.clear()

    import agent.auxiliary_client as auxiliary_client
    import cron.scheduler as cron_scheduler
    import tools.async_delegation as async_delegation
    import tools.browser_tool as browser_tool
    import tools.terminal_tool as terminal_tool

    monkeypatch.setattr(auxiliary_client, "shutdown_cached_clients", lambda: None)
    monkeypatch.setattr(cron_scheduler, "mark_running_jobs_interrupted", lambda _r: [])
    monkeypatch.setattr(async_delegation, "interrupt_all", lambda reason: 0)
    monkeypatch.setattr(terminal_tool, "cleanup_all_environments", lambda: None)
    monkeypatch.setattr(browser_tool, "cleanup_all_browsers", lambda: None)


class _BlockingFlushAgent:
    session_id = "aggregate-budget-agent"

    def __init__(self, started):
        self._session_messages = [{"role": "user", "content": "pending"}]
        self._started = started

    def _flush_messages_to_session_db(self, _messages):
        self._started.set()
        time.sleep(2.0)

    def _drop_trailing_empty_response_scaffolding(self, _messages):
        return None


@pytest.mark.asyncio
async def test_wedged_agent_finalize_cannot_starve_tail_release(monkeypatch):
    runner, _adapter = make_restart_runner()
    started = threading.Event()
    agents = {"session": _BlockingFlushAgent(started)}
    _configure_fast_forced_shutdown(runner, monkeypatch, agents)

    before = time.monotonic()
    await asyncio.wait_for(_run_stop(runner), timeout=4.0)
    elapsed = time.monotonic() - before

    assert started.is_set(), "agent finalization was never attempted"
    assert elapsed < 1.60, (
        f"agent finalization consumed aggregate budget: {elapsed:.3f}s"
    )


@pytest.mark.asyncio
async def test_wedged_cached_client_shutdown_cannot_starve_tail_release(monkeypatch):
    runner, _adapter = make_restart_runner()
    _configure_fast_forced_shutdown(runner, monkeypatch, {})
    started = threading.Event()

    import agent.auxiliary_client as auxiliary_client

    def _block():
        started.set()
        time.sleep(2.0)

    monkeypatch.setattr(auxiliary_client, "shutdown_cached_clients", _block)

    before = time.monotonic()
    await asyncio.wait_for(_run_stop(runner), timeout=4.0)
    elapsed = time.monotonic() - before

    assert started.is_set(), "cached-client cleanup was never attempted"
    assert elapsed < 1.60, (
        f"cached-client cleanup consumed aggregate budget: {elapsed:.3f}s"
    )


@pytest.mark.asyncio
async def test_wedged_database_close_cannot_starve_tail_release(monkeypatch):
    runner, _adapter = make_restart_runner()
    _configure_fast_forced_shutdown(runner, monkeypatch, {})
    started = threading.Event()

    class _BlockingDB:
        def close(self):
            started.set()
            time.sleep(2.0)

    database = _BlockingDB()
    runner._session_db = MagicMock()
    runner._session_db._db = database
    runner.session_store._db = None

    before = time.monotonic()
    await asyncio.wait_for(_run_stop(runner), timeout=4.0)
    elapsed = time.monotonic() - before

    assert started.is_set(), "database close was never attempted"
    assert elapsed < 1.60, f"database close consumed aggregate budget: {elapsed:.3f}s"
