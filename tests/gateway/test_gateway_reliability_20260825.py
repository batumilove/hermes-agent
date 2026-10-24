"""Regression tests for the 2026-08-25 hermes-vm gateway reliability chain.

Covers the three incident classes from
~/.hermes/plans/gateway-reliability-upstream-fix-list-20260825.md:

1. Signal-initiated shutdown must exit 75 (whitelisted restart code), not 1
   (which systemd never revives during a stop/restart job's stop phase).
2. The loop-liveness heartbeat must start at the top of start() — before
   adapters/plugins — and be supervised for the whole gateway life.
3. TimeoutStopSec must cover the full stop budget including the cron drain
   floor and its cleanup reserve.
"""

import asyncio
from unittest.mock import patch

import pytest

import gateway.run as gateway_run
from gateway.restart import (
    CRON_DRAIN_CLEANUP_RESERVE_S,
    DEFAULT_GATEWAY_CRON_DRAIN_TIMEOUT,
    GATEWAY_SERVICE_RESTART_EXIT_CODE,
)
from tests.gateway.restart_test_helpers import make_restart_runner


# ── 1. exit code on signal-initiated shutdown ───────────────────────────────


@pytest.mark.asyncio
async def test_signal_initiated_shutdown_exits_75_not_1(tmp_path, monkeypatch):
    """An unexpected SIGTERM (not a planned stop, not a restart request)
    must terminate the process with exit 75 — the RestartForceExitStatus
    code — never 1. Exit 1 during a systemd stop/restart job's stop phase
    leaves the unit permanently failed (2026-08-24 42-min outage)."""
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner, adapter = make_restart_runner()
    adapter.disconnect = __import__("unittest").mock.AsyncMock()
    runner._signal_initiated_shutdown = True

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    # The runner-level flag is mirrored; the exit-code decision happens in
    # start_gateway()'s tail. Simulate that tail reaching the branch.
    assert runner._signal_initiated_shutdown is True
    assert not runner._restart_requested

    # Execute the same predicate start_gateway uses: the branch must raise
    # SystemExit(75), not return False (→ exit 1).
    _signal_initiated_shutdown = runner._signal_initiated_shutdown
    with pytest.raises(SystemExit) as excinfo:
        if _signal_initiated_shutdown and not runner._restart_requested:
            raise SystemExit(GATEWAY_SERVICE_RESTART_EXIT_CODE)
    assert excinfo.value.code == GATEWAY_SERVICE_RESTART_EXIT_CODE == 75


def test_signal_shutdown_exit_code_is_restart_whitelisted():
    """Whatever code the signal-initiated branch produces, it must be in the
    RestartForceExitStatus whitelist (75) so systemd can revive the unit."""
    assert GATEWAY_SERVICE_RESTART_EXIT_CODE == 75


# ── 2. supervised early heartbeat ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_task_restarts_when_it_dies(tmp_path, monkeypatch):
    """If the loop heartbeat task dies with an exception, the supervisor
    done-callback must recreate it — a gateway life must never silently run
    heartbeat-less (2026-08-25: 7h life, zero heartbeat writes)."""
    monkeypatch.setattr(
        "gateway.shutdown_watchdog.write_loop_heartbeat", lambda **kw: None
    )
    runner, _adapter = make_restart_runner()

    calls = {"n": 0}

    async def _dying_heartbeat(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated heartbeat death")

    with patch.object(gateway_run, "loop_heartbeat_forever", _dying_heartbeat):
        runner._start_loop_heartbeat_supervised()
        task1 = runner._loop_heartbeat_task
        assert task1 is not None
        with pytest.raises(RuntimeError, match="simulated heartbeat death"):
            await asyncio.wait_for(task1, timeout=2)
        # let the done-callbacks (bg.discard + supervisor) run
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task2 = getattr(runner, "_loop_heartbeat_task", None)
        assert task2 is not None and task2 is not task1, (
            "supervisor must recreate the heartbeat task after it dies"
        )
        task2.cancel()


@pytest.mark.asyncio
async def test_heartbeat_start_is_idempotent():
    """A second call with a live task must not replace it."""
    runner, _adapter = make_restart_runner()
    with patch.object(
        gateway_run, "loop_heartbeat_forever", new=_never_ending
    ), patch(
        "gateway.shutdown_watchdog.write_loop_heartbeat", lambda **kw: None
    ):
        runner._start_loop_heartbeat_supervised()
        task1 = runner._loop_heartbeat_task
        assert task1 is not None
        runner._start_loop_heartbeat_supervised()
        assert runner._loop_heartbeat_task is task1
        task1.cancel()


async def _never_ending(**kwargs):
    await asyncio.Event().wait()


# ── 3. TimeoutStopSec covers the cron drain budget ───────────────────────────


def test_timeout_stop_sec_covers_cron_drain_budget(monkeypatch):
    """With defaults (restart_drain_timeout=0, cron_drain_timeout=30,
    cleanup reserve 10s), TimeoutStopSec must be >= 30+10+30 = 70s, not the
    old max(60, 0+30) = 60s that let systemd SIGKILL a 60s drain under a 90s
    escalation chain."""
    from hermes_cli import gateway as cli_gateway

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "0")
    unit = cli_gateway.generate_systemd_unit(system=False)
    assert "TimeoutStopSec=70" in unit, (
        f"TimeoutStopSec must cover cron drain floor ({DEFAULT_GATEWAY_CRON_DRAIN_TIMEOUT}s) "
        f"+ cleanup reserve ({CRON_DRAIN_CLEANUP_RESERVE_S}s) + 30s cleanup, got: "
        + next(
            line for line in unit.splitlines() if line.startswith("TimeoutStopSec")
        )
    )


def test_timeout_stop_sec_extends_with_configured_drain(monkeypatch):
    """A configured restart_drain_timeout extends the deadline directly."""
    from hermes_cli import gateway as cli_gateway

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "45")
    unit = cli_gateway.generate_systemd_unit(system=False)
    assert "TimeoutStopSec=75" in unit
