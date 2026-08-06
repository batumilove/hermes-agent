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
    import tools.process_registry as process_registry_module
    import tools.terminal_tool as terminal_tool

    monkeypatch.setattr(auxiliary_client, "shutdown_cached_clients", lambda: None)
    monkeypatch.setattr(cron_scheduler, "mark_running_jobs_interrupted", lambda _r: [])
    monkeypatch.setattr(async_delegation, "interrupt_all", lambda reason: 0)
    monkeypatch.setattr(process_registry_module.process_registry, "kill_all", lambda: 0)
    monkeypatch.setattr(terminal_tool, "cleanup_all_environments", lambda: None)
    monkeypatch.setattr(browser_tool, "cleanup_all_browsers", lambda: None)


def test_systemd_stop_timeout_caps_internal_shutdown_watchdog():
    """The internal exit leash must fire before systemd's SIGKILL boundary."""
    runner, _adapter = make_restart_runner()
    runner._restart_drain_timeout = 60.0
    runner._systemd_timeout_stop_s = 90.0

    assert runner._shutdown_watchdog_delay_secs() == 85.0


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
async def test_wedged_notification_cannot_consume_shared_shutdown_deadline(monkeypatch):
    runner, _adapter = make_restart_runner()
    _configure_fast_forced_shutdown(runner, monkeypatch, {})
    runner._restart_drain_timeout = 0.20
    runner._SHUTDOWN_TAIL_RESERVE_S = 0.20
    monkeypatch.setattr(
        gateway_run, "resolve_shutdown_watchdog_delay", lambda _timeout: 0.50
    )
    never = asyncio.Event()

    async def _wedged_notification():
        await never.wait()

    runner._notify_active_sessions_of_shutdown = _wedged_notification
    runner._drain_active_agents = AsyncMock(return_value=({}, False))

    await asyncio.wait_for(_run_stop(runner), timeout=1.20)

    runner._drain_active_agents.assert_awaited_once()


@pytest.mark.asyncio
async def test_wedged_pre_drain_marker_cannot_prevent_interrupt(monkeypatch):
    runner, _adapter = make_restart_runner()
    active_agent = MagicMock()
    _configure_fast_forced_shutdown(runner, monkeypatch, {"session": active_agent})
    runner._running_agents = {"session": active_agent}
    runner._restart_drain_timeout = 0.20
    runner._SHUTDOWN_TAIL_RESERVE_S = 0.20
    monkeypatch.setattr(
        gateway_run, "resolve_shutdown_watchdog_delay", lambda _timeout: 0.50
    )
    runner._notify_active_sessions_of_shutdown = AsyncMock()
    never = asyncio.Event()

    async def _wedged_marker(*_args, **_kwargs):
        await never.wait()

    monkeypatch.setattr(
        runner.async_session_store,
        "mark_resume_pending",
        _wedged_marker,
        raising=False,
    )
    runner._drain_active_agents = AsyncMock(
        return_value=({"session": active_agent}, True)
    )

    async def _interrupt(_reason, _deadline):
        runner._running_agents.clear()

    runner._interrupt_running_agents = AsyncMock(side_effect=_interrupt)

    await asyncio.wait_for(_run_stop(runner), timeout=1.20)

    runner._drain_active_agents.assert_awaited_once()
    runner._interrupt_running_agents.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_pre_drain_marker_finishes_before_compensating_clear(monkeypatch):
    """A timed-out marker must not commit after graceful marker cleanup."""
    runner, _adapter = make_restart_runner()
    active_agent = MagicMock()
    _configure_fast_forced_shutdown(runner, monkeypatch, {"session": active_agent})
    runner._running_agents = {"session": active_agent}
    runner._restart_drain_timeout = 0.05
    runner._SHUTDOWN_TAIL_RESERVE_S = 0.20
    monkeypatch.setattr(
        gateway_run, "resolve_shutdown_watchdog_delay", lambda _timeout: 0.70
    )
    runner._notify_active_sessions_of_shutdown = AsyncMock()
    release_marker = asyncio.Event()
    operations = []

    async def _late_marker(*_args, **_kwargs):
        await release_marker.wait()
        operations.append("mark")

    async def _clear_marker(*_args, **_kwargs):
        operations.append("clear")

    monkeypatch.setattr(
        runner.async_session_store,
        "mark_resume_pending",
        _late_marker,
        raising=False,
    )
    monkeypatch.setattr(
        runner.async_session_store,
        "clear_resume_pending",
        _clear_marker,
        raising=False,
    )

    async def _finish_gracefully(_timeout):
        release_marker.set()
        runner._running_agents.clear()
        return {"session": active_agent}, False

    runner._drain_active_agents = _finish_gracefully

    await asyncio.wait_for(_run_stop(runner), timeout=2.0)

    assert operations == ["mark", "clear"]


@pytest.mark.asyncio
async def test_pre_drain_work_reduces_agent_drain_budget(monkeypatch):
    """Notification time must be charged to the shared shutdown deadline."""
    runner, _adapter = make_restart_runner()
    _configure_fast_forced_shutdown(runner, monkeypatch, {})
    runner._restart_drain_timeout = 0.20
    runner._SHUTDOWN_TAIL_RESERVE_S = 0.15
    monkeypatch.setattr(
        gateway_run, "resolve_shutdown_watchdog_delay", lambda _timeout: 0.40
    )

    async def _slow_notification():
        await asyncio.sleep(0.10)

    observed_drain_budgets = []

    async def _capture_drain_budget(timeout):
        observed_drain_budgets.append(timeout)
        return {}, False

    runner._notify_active_sessions_of_shutdown = _slow_notification
    runner._drain_active_agents = _capture_drain_budget

    await asyncio.wait_for(_run_stop(runner), timeout=2.0)

    assert len(observed_drain_budgets) == 1
    assert 0.0 < observed_drain_budgets[0] < 0.18, (
        "pre-drain notification time was not charged to the aggregate shutdown "
        f"budget: drain received {observed_drain_budgets[0]:.3f}s"
    )


@pytest.mark.asyncio
async def test_drain_exhaustion_reduces_interrupt_grace_budget(monkeypatch):
    """Interrupt grace must not receive time beyond the shared cleanup deadline."""
    runner, _adapter = make_restart_runner()
    active_agent = MagicMock()
    _configure_fast_forced_shutdown(runner, monkeypatch, {"session": active_agent})
    runner._running_agents = {"session": active_agent}
    runner._restart_drain_timeout = 0.20
    runner._SHUTDOWN_TAIL_RESERVE_S = 0.15
    monkeypatch.setattr(
        gateway_run, "resolve_shutdown_watchdog_delay", lambda _timeout: 0.40
    )
    runner._notify_active_sessions_of_shutdown = AsyncMock()

    async def _consume_drain_budget(_timeout):
        await asyncio.sleep(0.20)
        return {"session": active_agent}, True

    observed_interrupt_budgets = []

    async def _capture_interrupt(_reason, deadline):
        observed_interrupt_budgets.append(
            deadline - asyncio.get_running_loop().time()
        )
        runner._running_agents.clear()

    runner._drain_active_agents = _consume_drain_budget
    runner._interrupt_running_agents = _capture_interrupt

    await asyncio.wait_for(_run_stop(runner), timeout=2.0)

    assert len(observed_interrupt_budgets) == 1
    assert 0.0 <= observed_interrupt_budgets[0] < 0.10, (
        "interrupt grace exceeded the aggregate shutdown deadline: "
        f"received {observed_interrupt_budgets[0]:.3f}s"
    )


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
