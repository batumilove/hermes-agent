"""Regression tests for gateway signal-initiated shutdown exit behavior."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig
from gateway.restart import GATEWAY_SERVICE_RESTART_EXIT_CODE


class _SignalShutdownRunner(gateway_run.GatewayRunner):
    """Minimal stand-in for GatewayRunner that exits on a simulated signal."""

    def __init__(self, config):
        self.config = config
        self._primary_profile_name = "default"
        self.adapters = {}
        self._shutdown_event = asyncio.Event()
        self._restart_requested = False
        self._restart_via_service = False
        self._exit_reason = None
        self._exit_code: Optional[int] = None
        # live gateway/run.py accesses these during shutdown teardown
        self._running = True
        self._running_agents: dict = {}
        self._background_tasks: set = set()
        self._loop_heartbeat_task = None

    async def start(self):
        return True

    async def stop(self):
        self._shutdown_event.set()

    async def wait_for_shutdown(self):
        await self._shutdown_event.wait()

    @property
    def should_exit_cleanly(self):
        return False

    @property
    def should_exit_with_failure(self):
        return False

    @property
    def exit_reason(self):
        return self._exit_reason

    @property
    def exit_code(self):
        return self._exit_code


def _patch_gateway_run(monkeypatch, tmp_path):
    """Apply common monkeypatches for start_gateway tests."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("gateway.status.acquire_gateway_runtime_lock", lambda: True)
    monkeypatch.setattr("gateway.status.release_gateway_runtime_lock", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.status.write_pid_file", lambda: None)
    monkeypatch.setattr("gateway.status.remove_pid_file", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: None)
    monkeypatch.setattr("hermes_logging._add_rotating_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run.os.getpid", lambda: 12345)
    # Signal/exit policy is the subject here; real daemon startup and identity
    # release are exercised by the bootstrap lifecycle tests.
    from gateway import run_runtime, run_bootstrap
    for name in ("initialize_gateway_runtime", "start_gateway_runtime_api", "drain_gateway_runtime"):
        monkeypatch.setattr(run_runtime, name, AsyncMock())
    monkeypatch.setattr(run_runtime, "publish_gateway_runtime_ready", lambda runner: None)
    monkeypatch.setattr(run_runtime, "wait_gateway_runtime", lambda runner: runner.wait_for_shutdown())
    monkeypatch.setattr(run_bootstrap, "_start_gateway_start_control_socket", AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(gateway_run, "_refresh_host_gateway_record", lambda runner: None)
    monkeypatch.setattr("gateway.run_api.warm_gateway_dashboard", lambda runner: None)
    idle_thread = SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(
        "gateway.run._start_gateway_start_cron_and_housekeeping",
        lambda runner: (threading.Event(), SimpleNamespace(stop=lambda: None), idle_thread, idle_thread),
    )


def _register_signal_handler(monkeypatch):
    """Set up a fake signal handler registry and return (task, registered_handlers)."""
    registered_handlers = {}

    def _fake_add_signal_handler(sig, callback, *args):
        registered_handlers[sig] = callback

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", _fake_add_signal_handler)

    from gateway.run import start_gateway

    async def _guarded():
        try:
            return ("returned", await start_gateway(config=GatewayConfig(), replace=False, verbosity=None))
        except SystemExit as e:
            return ("SystemExit", e.code)

    task = asyncio.create_task(_guarded())

    return task, registered_handlers


async def _run_until_handler_registered(registered_handlers):
    """Busy-wait until SIGTERM handler is registered."""
    for _ in range(100):
        await asyncio.sleep(0.01)
        if signal.SIGTERM in registered_handlers:
            break
    assert signal.SIGTERM in registered_handlers, "SIGTERM handler was not registered"


@pytest.mark.asyncio
async def test_signal_shutdown_requests_restart_in_systemd_context(monkeypatch, tmp_path):
    """Unexpected SIGTERM must request a replacement under systemd management.

    Regression for: gateway main process exiting 1 on signal-initiated shutdown,
    which caused systemd to mark the unit failed and prevented auto-restart.
    """
    _patch_gateway_run(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.run.GatewayRunner", _SignalShutdownRunner)
    # Simulate a systemd-managed service invocation.
    monkeypatch.setenv("INVOCATION_ID", "test-invocation-id-12345")

    task, registered_handlers = _register_signal_handler(monkeypatch)

    await _run_until_handler_registered(registered_handlers)

    # Trigger the captured handler directly.
    registered_handlers[signal.SIGTERM]()

    kind, ok = await task
    assert kind == "SystemExit"
    assert ok == GATEWAY_SERVICE_RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_stray_signal_shutdown_requests_restart_outside_systemd_context(monkeypatch, tmp_path):
    """Stray SIGTERM outside systemd context should exit 75 (the in-band
    "please relaunch" code) so the service manager revives the gateway."""
    _patch_gateway_run(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.run.GatewayRunner", _SignalShutdownRunner)
    # Explicitly clear any systemd context variables.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("MANAGERPID", raising=False)

    task, registered_handlers = _register_signal_handler(monkeypatch)

    await _run_until_handler_registered(registered_handlers)

    registered_handlers[signal.SIGTERM]()

    kind, code = await task
    assert kind == "SystemExit"
    assert code == GATEWAY_SERVICE_RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_signal_shutdown_requests_restart_with_notify_socket(monkeypatch, tmp_path):
    """Unexpected SIGTERM with NOTIFY_SOCKET requests a replacement."""
    _patch_gateway_run(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.run.GatewayRunner", _SignalShutdownRunner)
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")

    task, registered_handlers = _register_signal_handler(monkeypatch)

    await _run_until_handler_registered(registered_handlers)

    registered_handlers[signal.SIGTERM]()

    kind, ok = await task
    assert kind == "SystemExit"
    assert ok == GATEWAY_SERVICE_RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_stray_signal_shutdown_requests_restart_with_managerpid_mismatch(monkeypatch, tmp_path):
    """MANAGERPID that does not match our parent PID should be treated as stray."""
    _patch_gateway_run(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.run.GatewayRunner", _SignalShutdownRunner)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # Parent PID is mocked to 12345; set MANAGERPID to something else.
    monkeypatch.setenv("MANAGERPID", "99999")

    task, registered_handlers = _register_signal_handler(monkeypatch)

    await _run_until_handler_registered(registered_handlers)

    registered_handlers[signal.SIGTERM]()

    kind, code = await task
    assert kind == "SystemExit"
    assert code == GATEWAY_SERVICE_RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_signal_shutdown_requests_restart_with_managerpid_match(monkeypatch, tmp_path):
    """Unexpected SIGTERM requests a replacement even when MANAGERPID matches."""
    _patch_gateway_run(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.run.GatewayRunner", _SignalShutdownRunner)
    # os.getpid is mocked to 12345 in _patch_gateway_run; on Linux getppid() returns
    # the real parent, so we use MANAGERPID matching the real parent to avoid
    # relying on the getpid mock. getppid is not mocked so the helper sees the
    # actual test runner parent PID.
    monkeypatch.setenv("MANAGERPID", str(os.getppid()))

    task, registered_handlers = _register_signal_handler(monkeypatch)

    await _run_until_handler_registered(registered_handlers)

    registered_handlers[signal.SIGTERM]()

    kind, ok = await task
    assert kind == "SystemExit"
    assert ok == GATEWAY_SERVICE_RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_exit_code_75_propagates_on_service_restart(monkeypatch, tmp_path):
    """start_gateway should propagate a runner-set exit code 75 via SystemExit.

    When the runner's own restart path sets exit_code to
    GATEWAY_SERVICE_RESTART_EXIT_CODE, start_gateway raises SystemExit(code)
    so the service manager can restart the gateway.
    """
    _patch_gateway_run(monkeypatch, tmp_path)

    class _ExitCode75Runner(_SignalShutdownRunner):
        async def start(self):
            # Simulate a service-initiated restart path: immediately after
            # startup the runner decides it must restart via the service.
            self._exit_code = GATEWAY_SERVICE_RESTART_EXIT_CODE
            self._shutdown_event.set()
            return True

    monkeypatch.setattr("gateway.run.GatewayRunner", _ExitCode75Runner)

    from gateway.run import start_gateway

    with pytest.raises(SystemExit) as exc_info:
        await start_gateway(config=GatewayConfig(), replace=False, verbosity=None)

    assert exc_info.value.code == GATEWAY_SERVICE_RESTART_EXIT_CODE
