"""Regression tests for gateway signal-initiated shutdown exit behavior."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Optional

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig


class _SignalShutdownRunner:
    """Minimal stand-in for GatewayRunner that exits on a simulated signal."""

    def __init__(self, config):
        self.config = config
        self.adapters = {}
        self._shutdown_event = asyncio.Event()
        self._restart_requested = False
        self._restart_via_service = False
        self._exit_reason = None
        self._exit_code: Optional[int] = None

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
    monkeypatch.setattr("gateway.status.release_gateway_runtime_lock", lambda: None)
    monkeypatch.setattr("gateway.status.write_pid_file", lambda: None)
    monkeypatch.setattr("gateway.status.remove_pid_file", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: None)
    monkeypatch.setattr("hermes_logging._add_rotating_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run.os.getpid", lambda: 12345)
    monkeypatch.setattr("gateway.run._start_cron_ticker", lambda *args, **kwargs: None)


@pytest.mark.asyncio
async def test_signal_shutdown_exits_zero(monkeypatch, tmp_path):
    """SIGTERM during normal operation should result in start_gateway returning True.

    Regression for: gateway main process exiting 1 on signal-initiated shutdown,
    which caused systemd to mark the unit failed and prevented auto-restart.
    """
    _patch_gateway_run(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.run.GatewayRunner", _SignalShutdownRunner)

    # Capture the registered signal handler and invoke it directly instead of
    # sending a real signal to the test process (which conflicts with the
    # global SIGALRM timeout fixture).
    registered_handlers = {}

    def _fake_add_signal_handler(sig, callback, *args):
        registered_handlers[sig] = callback

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", _fake_add_signal_handler)

    from gateway.run import start_gateway

    # Run start_gateway in a background task so we can trigger the handler.
    task = asyncio.create_task(start_gateway(config=GatewayConfig(), replace=False, verbosity=None))

    # Let the gateway get past signal handler registration and into wait_for_shutdown().
    for _ in range(100):
        await asyncio.sleep(0.01)
        if signal.SIGTERM in registered_handlers:
            break

    assert signal.SIGTERM in registered_handlers, "SIGTERM handler was not registered"

    # Trigger the captured handler directly.
    registered_handlers[signal.SIGTERM]()

    ok = await task
    assert ok is True


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
            self._exit_code = gateway_run.GATEWAY_SERVICE_RESTART_EXIT_CODE
            self._shutdown_event.set()
            return True

    monkeypatch.setattr("gateway.run.GatewayRunner", _ExitCode75Runner)

    from gateway.run import start_gateway

    with pytest.raises(SystemExit) as exc_info:
        await start_gateway(config=GatewayConfig(), replace=False, verbosity=None)

    assert exc_info.value.code == gateway_run.GATEWAY_SERVICE_RESTART_EXIT_CODE
