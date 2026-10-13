"""Residual process evidence must precede releasing the runtime identity."""

import asyncio
from unittest.mock import patch

import pytest

from gateway import shutdown_residual_children
from tests.gateway.restart_test_helpers import make_restart_runner
from tests.gateway.test_shutdown_aggregate_budget_contract import _configure_fast_forced_shutdown


@pytest.mark.asyncio
async def test_residual_snapshot_runs_before_pid_and_lock_release(monkeypatch):
    runner, _adapter = make_restart_runner()
    _configure_fast_forced_shutdown(runner, monkeypatch, {})
    order = []

    def snapshot(**kwargs):
        assert kwargs["phase"] == "before_identity_release"
        assert kwargs["deadline_remaining"] > 0
        order.append("snapshot")

    def release_lock(*, timeout):
        assert timeout == 0.0
        order.append("lock")

    monkeypatch.setattr(shutdown_residual_children, "capture_residual_children", snapshot)
    with (
        patch("gateway.status.remove_pid_file", side_effect=lambda: order.append("pid")),
        patch("gateway.status.release_gateway_runtime_lock", side_effect=release_lock),
    ):
        await runner.stop()

    assert order == ["snapshot", "pid", "lock"]


@pytest.mark.asyncio
async def test_exhausted_shutdown_budget_refuses_additional_async_cleanup():
    runner, _adapter = make_restart_runner()
    started = []

    async def cleanup():
        started.append(True)
        await asyncio.Event().wait()

    result = await runner._await_shutdown_phase(
        cleanup(), deadline=asyncio.get_running_loop().time() - 1, context="expired cleanup")

    assert result is None
    assert started == []
