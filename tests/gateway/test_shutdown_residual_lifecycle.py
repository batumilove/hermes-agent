"""Residual process evidence must precede releasing the runtime identity."""

import asyncio
from unittest.mock import patch

import pytest

from gateway import shutdown_residual_children
from tests.gateway.restart_test_helpers import make_restart_runner
from tests.gateway.test_shutdown_aggregate_budget_contract import _configure_fast_forced_shutdown


@pytest.mark.asyncio
async def test_runner_snapshot_keeps_identity_for_bootstrap(monkeypatch):
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

    assert order == ["snapshot"]


@pytest.mark.asyncio
async def test_bootstrap_releases_identity_after_residual_snapshot(monkeypatch):
    from gateway import run_runtime, status
    from gateway.config import GatewayConfig
    from gateway.run_bootstrap import start_gateway
    from hermes_constants import get_hermes_home

    get_hermes_home().chmod(0o700)
    order = []
    remove_pid = status.remove_pid_file
    release_lock = status.release_gateway_runtime_lock

    async def ready_to_stop(runner):
        return None

    def snapshot(**kwargs):
        assert kwargs["phase"] == "before_identity_release"
        assert (get_hermes_home() / "gateway.pid").exists()
        order.append("snapshot")

    def remove():
        order.append("pid")
        remove_pid()

    def release(**kwargs):
        order.append("lock")
        return release_lock(**kwargs)

    monkeypatch.setattr(run_runtime, "wait_gateway_runtime", ready_to_stop)
    monkeypatch.setattr(shutdown_residual_children, "capture_residual_children", snapshot)
    with (
        patch.object(status, "remove_pid_file", side_effect=remove),
        patch.object(status, "release_gateway_runtime_lock", side_effect=release),
    ):
        await asyncio.wait_for(start_gateway(GatewayConfig()), 30)
    assert order == ["snapshot", "pid", "lock"]
    assert not (get_hermes_home() / "gateway.pid").exists()


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
