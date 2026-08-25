"""Regression tests for drain deadline fitting inside systemd TimeoutStopSec.

Issue: with an active agent turn, the gateway drain timeout (default 60s) plus
post-interrupt cleanup could exceed the systemd TimeoutStopSec, causing
systemd to SIGKILL the cgroup before the gateway could finish cleanup.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.restart import DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
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

    async def finish_quickly():
        await asyncio.sleep(0.05)
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
    # With the default 60s drain and 10s headroom, effective is 80s.
    assert effective == pytest.approx(systemd_timeout - runner._stopsec_headroom)


@pytest.mark.asyncio
async def test_gateway_stop_uses_effective_drain_timeout(monkeypatch):
    """stop() should call _drain_active_agents with the effective timeout
    and a matching hard_interrupt_after bound."""
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    monkeypatch.setattr(runner, "_restart_drain_timeout", 60.0)

    calls = []

    async def fake_drain(timeout, *, hard_interrupt_after=None):
        calls.append((timeout, hard_interrupt_after))
        return {}, True

    monkeypatch.setattr(runner, "_drain_active_agents", fake_drain)

    with patch("gateway.status.remove_pid_file"), patch("gateway.status.write_runtime_status"):
        await runner.stop()

    assert len(calls) == 1
    timeout, hard_interrupt_after = calls[0]
    assert timeout == runner._effective_restart_drain_timeout
    assert hard_interrupt_after == runner._effective_restart_drain_timeout
    # Must leave headroom inside the 90s TimeoutStopSec generated for the
    # default 60s drain timeout.
    assert timeout < 90.0


@pytest.mark.asyncio
async def test_gateway_stop_interrupts_before_systemd_timeout_stopsec(monkeypatch):
    """With a running agent that never finishes, stop() must interrupt and
    complete all post-interrupt cleanup well before a simulated systemd
    TimeoutStopSec of 90s."""
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    running_agent = MagicMock()
    runner._running_agents = {"session": running_agent}

    # Force the drain to use a tiny effective timeout so the test runs fast,
    # while still exercising the full stop() path.
    monkeypatch.setattr(runner, "_restart_drain_timeout", 0.0)

    async def _fake_interrupt(reason: str) -> None:
        runner._running_agents.clear()

    monkeypatch.setattr(runner, "_interrupt_running_agents", _fake_interrupt)
    # Avoid launching a detached restart helper that can leave a background task.
    monkeypatch.setattr(runner, "_launch_detached_restart_command", AsyncMock())

    async def _fake_stop_impl() -> None:
        runner._running = False
        runner._shutdown_event.set()

    monkeypatch.setattr(runner, "stop", _fake_stop_impl)

    start = asyncio.get_running_loop().time()
    await runner.stop()
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 5.0
    assert runner._shutdown_event.is_set() is True
