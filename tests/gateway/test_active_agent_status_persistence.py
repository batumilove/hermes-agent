"""Regression tests for best-effort active-agent status persistence."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time


def test_active_agent_status_capacity_exhaustion_does_not_fail_turn_cleanup(
    monkeypatch,
):
    """Diagnostic status backpressure must not escape into message handling."""
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    runner = GatewayRunner(GatewayConfig())

    async def _capacity_exhausted(*args, **kwargs):
        raise TimeoutError("serial daemon worker capacity exhausted")

    monkeypatch.setattr(
        "gateway.run.run_sync_in_detached_serial_daemon_thread",
        _capacity_exhausted,
    )

    asyncio.run(runner._persist_active_agents_async())


def test_active_agent_status_worker_failure_does_not_fail_turn_cleanup(monkeypatch):
    """Ordinary worker/scheduling failures remain best-effort diagnostics."""
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    runner = GatewayRunner(GatewayConfig())

    async def _worker_failed(*args, **kwargs):
        raise RuntimeError("detached worker failed to start")

    monkeypatch.setattr(
        "gateway.run.run_sync_in_detached_serial_daemon_thread",
        _worker_failed,
    )

    asyncio.run(runner._persist_active_agents_async())


def _successor_status(successor_record):
    return {
        **successor_record,
        "gateway_state": "running",
        "active_agents": 3,
    }


def test_abandoned_old_process_status_write_fails_closed_on_malformed_lock(
    tmp_path,
    monkeypatch,
):
    """A late serial writer must not trust partial successor lock metadata."""
    from gateway import status

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    old_record = {
        "pid": os.getpid(),
        "kind": "hermes-gateway",
        "argv": ["hermes", "gateway", "run"],
        "start_time": 111,
    }
    successor_record = {
        "pid": os.getpid() + 1,
        "kind": "hermes-gateway",
        "argv": ["hermes", "gateway", "run"],
        "start_time": 222,
    }
    state_path = tmp_path / "gateway_state.json"
    state_path.write_text(json.dumps(_successor_status(successor_record)))
    (tmp_path / "gateway.lock").write_text("{partial")

    monkeypatch.setattr(status, "_build_pid_record", lambda: old_record)
    monkeypatch.setattr(status, "current_serial_daemon_sequence", lambda: 7)
    monkeypatch.setattr(status, "_gateway_lock_handle", object())

    status.write_runtime_status(active_agents=0)

    assert json.loads(state_path.read_text()) == _successor_status(successor_record)


def test_runtime_lock_release_waits_for_short_status_commit(tmp_path, monkeypatch):
    """Normal release preserves ownership through a recoverable status commit."""
    from gateway import status

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    lock_path = tmp_path / "gateway.lock"
    lock_path.write_text("\n")
    handle = open(lock_path, "a+", encoding="utf-8")
    monkeypatch.setattr(status, "_gateway_lock_handle", handle)

    write_entered = threading.Event()
    allow_write = threading.Event()
    release_finished = threading.Event()
    original_write = status._write_json_file

    def _paused_write(path, payload):
        write_entered.set()
        assert allow_write.wait(timeout=2)
        original_write(path, payload)

    monkeypatch.setattr(status, "_write_json_file", _paused_write)
    monkeypatch.setattr(status, "current_serial_daemon_sequence", lambda: None)

    writer = threading.Thread(
        target=status.write_runtime_status,
        kwargs={"active_agents": 1},
        daemon=True,
    )
    releaser = threading.Thread(
        target=lambda: (
            status.release_gateway_runtime_lock(timeout=1.0),
            release_finished.set(),
        ),
        daemon=True,
    )
    try:
        writer.start()
        assert write_entered.wait(timeout=1)
        releaser.start()
        time.sleep(0.05)
        assert not release_finished.is_set()
        assert status._gateway_lock_handle is handle
        allow_write.set()
        writer.join(timeout=2)
        releaser.join(timeout=2)
        assert release_finished.is_set()
        assert status._gateway_lock_handle is None
    finally:
        allow_write.set()
        if not handle.closed:
            handle.close()


def test_runtime_lock_release_timeout_cannot_wedge_forced_exit(tmp_path, monkeypatch):
    """A permanently stalled status commit cannot block watchdog lock release."""
    from gateway import status

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    lock_path = tmp_path / "gateway.lock"
    lock_path.write_text("\n")
    handle = open(lock_path, "a+", encoding="utf-8")
    monkeypatch.setattr(status, "_gateway_lock_handle", handle)

    holder_ready = threading.Event()
    release_holder = threading.Event()

    def _hold_status_lock():
        with status._runtime_status_write_lock:
            holder_ready.set()
            release_holder.wait(timeout=2)

    holder = threading.Thread(target=_hold_status_lock, daemon=True)
    holder.start()
    assert holder_ready.wait(timeout=1)

    started = time.monotonic()
    try:
        assert status.release_gateway_runtime_lock(timeout=0.05) is False
        assert time.monotonic() - started < 0.5
        assert status._gateway_lock_handle is handle
    finally:
        release_holder.set()
        holder.join(timeout=2)
        status.release_gateway_runtime_lock(timeout=1.0)
        if not handle.closed:
            handle.close()
