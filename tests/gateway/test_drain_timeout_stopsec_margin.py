"""Regression tests for drain deadline fitting inside systemd TimeoutStopSec.

Issue: with an active agent turn, the gateway drain timeout (default 60s) plus
post-interrupt cleanup could exceed the systemd TimeoutStopSec, causing
systemd to SIGKILL the cgroup before the gateway could finish cleanup.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.restart import DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
from gateway.run import GatewayRunner
from tests.gateway.test_shutdown_aggregate_budget_contract import _configure_fast_forced_shutdown
from tests.gateway.restart_test_helpers import make_restart_runner


@pytest.mark.asyncio
async def test_drain_active_agents_hard_interrupt_after_short_circuits_normal_timeout():
    """If hard_interrupt_after is shorter than timeout, the drain must stop
    early and report a timeout."""
    runner, _adapter = make_restart_runner()
    runner._running_agents = {"session": MagicMock()}

    start = asyncio.get_running_loop().time()
    snapshot, timed_out = await runner._drain_active_agents(
        timeout=10.0,
        hard_interrupt_after=0.05,
    )
    elapsed = asyncio.get_running_loop().time() - start

    assert timed_out is True
    # Should finish very close to hard_interrupt_after, not the 10s timeout.
    assert elapsed < 0.5
    assert "session" in snapshot


@pytest.mark.asyncio
async def test_drain_active_agents_uses_full_timeout_when_hard_interrupt_after_is_longer():
    """If the agent finishes before hard_interrupt_after, the drain succeeds
    and the hard_interrupt_after bound does not truncate it."""
    runner, _adapter = make_restart_runner()
    runner._running_agents = {"session": MagicMock()}

    observed = asyncio.Event()
    count_active = runner._drain_work_counts
    def count_and_observe():
        count = count_active()
        observed.set()
        return count
    runner._drain_work_counts = count_and_observe
    async def finish_quickly():
        await asyncio.wait_for(observed.wait(), timeout=1)
        runner._running_agents.clear()

    task = asyncio.create_task(finish_quickly())
    start = asyncio.get_running_loop().time()
    snapshot, timed_out = await runner._drain_active_agents(
        timeout=5.0,
        hard_interrupt_after=1.0,
    )
    elapsed = asyncio.get_running_loop().time() - start
    await task

    assert timed_out is False
    assert elapsed < 1.0
    assert "session" in snapshot


@pytest.mark.asyncio
async def test_effective_restart_drain_timeout_fits_inside_timeout_stopsec():
    """The effective drain timeout must leave headroom inside systemd's
    TimeoutStopSec.  The generated unit uses ``max(60, drain) + 30``; the
    gateway must hard-interrupt before that."""
    runner, _adapter = make_restart_runner()
    runner._restart_drain_timeout = DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT

    effective = runner._effective_restart_drain_timeout
    # systemd TimeoutStopSec mirrors hermes_cli/gateway.py formula.
    systemd_timeout = max(60.0, runner._restart_drain_timeout) + 30.0

    assert effective >= 0
    assert effective < systemd_timeout
    # With the default 60s drain: stop budget is 90s, headroom 10s, and the
    # configured 60s already fits inside the budget, so effective is 60s.
    assert effective == pytest.approx(
        min(runner._restart_drain_timeout, systemd_timeout - runner._stopsec_headroom)
    )


@pytest.mark.asyncio
async def test_gateway_stop_uses_effective_drain_timeout(monkeypatch):
    """stop() should call _drain_active_agents with the effective timeout
    while preserving the separate cron/API drain floor."""
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    monkeypatch.setattr(runner, "_restart_drain_timeout", 60.0)

    calls = []

    async def fake_drain(timeout, *, cron_timeout=None):
        calls.append((timeout, cron_timeout))
        return {}, True

    monkeypatch.setattr(runner, "_drain_active_agents", fake_drain)

    with patch("gateway.status.remove_pid_file"), patch("gateway.status.write_runtime_status"):
        await runner.stop()

    assert len(calls) == 1
    timeout, cron_timeout = calls[0]
    assert timeout == runner._effective_restart_drain_timeout
    assert timeout <= cron_timeout <= runner._shutdown_watchdog_delay_secs()
    # Must leave headroom inside the 90s TimeoutStopSec generated for the
    # default 60s drain timeout.
    assert timeout < 90.0


@pytest.mark.asyncio
async def test_gateway_stop_interrupts_before_systemd_timeout_stopsec(monkeypatch):
    """With a running agent that never finishes, stop() must interrupt and
    complete all post-interrupt cleanup well before a simulated systemd
    TimeoutStopSec of 90s."""
    runner, _adapter = make_restart_runner()
    running_agent = MagicMock()
    running_agent._session_messages = []
    _configure_fast_forced_shutdown(runner, monkeypatch, {"session": running_agent})
    runner._running_agents = {"session": running_agent}
    runner._drain_active_agents = GatewayRunner._drain_active_agents.__get__(runner, GatewayRunner)
    runner._restart_drain_timeout = 0.03
    runner._finalize_session_off_loop = AsyncMock(return_value=[])
    runner._cleanup_agent_resources_off_loop = AsyncMock(return_value=True)
    running_agent.interrupt.side_effect = lambda *_args, **_kwargs: runner._running_agents.clear()

    start = asyncio.get_running_loop().time()
    with (
        patch("gateway.status.remove_pid_file") as remove_pid,
        patch("gateway.status.release_gateway_runtime_lock") as release_lock,
        patch("gateway.status.write_runtime_status"),
    ):
        await runner.stop()
    elapsed = asyncio.get_running_loop().time() - start

    running_agent.interrupt.assert_called_once_with(
        "Gateway shutting down", tool_reason="gateway shutdown")
    remove_pid.assert_called_once()
    release_lock.assert_called_once()
    assert elapsed < 5.0
    assert runner._shutdown_event.is_set() is True
