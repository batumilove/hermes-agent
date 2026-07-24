import ast
import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import MonkeyPatch

import gateway.run as gateway_run
from agent.i18n import t
from gateway.platforms.base import MessageEvent, MessageType
from gateway.restart import DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
from gateway.session import SessionEntry, build_session_key
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


def monkeypatch_context():
    return MonkeyPatch.context()


@pytest.mark.asyncio
async def test_restart_command_while_busy_requests_drain_without_interrupt(monkeypatch):
    # Ensure INVOCATION_ID is NOT set — systemd sets this in service mode,
    # which changes the restart call signature.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    runner, _adapter = make_restart_runner()
    runner.request_restart = MagicMock(return_value=True)
    event = MessageEvent(
        text="/restart",
        message_type=MessageType.TEXT,
        source=make_restart_source(),
        message_id="m1",
    )
    session_key = build_session_key(event.source)
    running_agent = MagicMock()
    runner._running_agents[session_key] = running_agent

    result = await runner._handle_message(event)

    expected = t("gateway.draining", count=1)
    assert result == expected
    # Guard against the silent-degradation regression in #22266: if the i18n
    # catalog cannot be resolved (e.g. xdist workers losing the locales path)
    # then ``t("gateway.draining", count=1)`` returns the bare key
    # ``"gateway.draining"`` instead of the formatted English string, and both
    # sides of the equality above would still match. Assert on the catalog
    # output explicitly so a broken locale resolution fails loudly here.
    assert expected != "gateway.draining"
    assert "Draining" in expected and "1" in expected
    running_agent.interrupt.assert_not_called()
    runner.request_restart.assert_called_once_with(detached=True, via_service=False)


@pytest.mark.asyncio
async def test_drain_queue_mode_queues_follow_up_without_interrupt():
    runner, adapter = make_restart_runner()
    runner._draining = True
    runner._restart_requested = True
    runner._busy_input_mode = "queue"

    event = MessageEvent(
        text="follow up",
        message_type=MessageType.TEXT,
        source=make_restart_source(),
        message_id="m2",
    )
    session_key = build_session_key(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(event)

    assert session_key in adapter._pending_messages
    assert adapter._pending_messages[session_key].text == "follow up"
    assert not adapter._active_sessions[session_key].is_set()
    assert any("queued for the next turn" in message for message in adapter.sent)


@pytest.mark.asyncio
async def test_draining_rejects_new_session_messages():
    runner, _adapter = make_restart_runner()
    runner._draining = True
    runner._restart_requested = True

    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=make_restart_source("fresh"),
        message_id="m3",
    )

    result = await runner._handle_message(event)

    assert result == "⏳ Gateway is restarting and is not accepting new work right now."


def test_load_busy_input_mode_prefers_env_then_config_then_default(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_GATEWAY_BUSY_INPUT_MODE", raising=False)

    assert gateway_run.GatewayRunner._load_busy_input_mode() == "interrupt"

    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: queue\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "queue"

    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: steer\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "steer"

    monkeypatch.setenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "interrupt")
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "interrupt"

    monkeypatch.setenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "steer")
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "steer"

    # Unknown values fall through to the safe default
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "bogus")
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "interrupt"


def test_load_busy_text_mode_follows_input_mode_and_honors_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_GATEWAY_BUSY_TEXT_MODE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_BUSY_INPUT_MODE", raising=False)

    # No knobs set → follows busy_input_mode, which defaults to interrupt.
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "interrupt"

    # busy_input_mode=queue propagates to text handling (single source of truth).
    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: queue\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "queue"

    # Legacy explicit busy_text_mode still wins for backward compat.
    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: interrupt\n  busy_text_mode: queue\n",
        encoding="utf-8",
    )
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "queue"

    # Legacy env override wins too.
    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: interrupt\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_TEXT_MODE", "queue")
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "queue"

    # Bogus legacy value is ignored → falls through to busy_input_mode (interrupt).
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_TEXT_MODE", "bogus")
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "interrupt"


def test_load_restart_drain_timeout_prefers_env_then_config_then_default(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_RESTART_DRAIN_TIMEOUT", raising=False)

    assert (
        gateway_run.GatewayRunner._load_restart_drain_timeout()
        == DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    )

    (tmp_path / "config.yaml").write_text(
        "agent:\n  restart_drain_timeout: 12\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_restart_drain_timeout() == 12.0

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "7")
    assert gateway_run.GatewayRunner._load_restart_drain_timeout() == 7.0

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "invalid")
    assert (
        gateway_run.GatewayRunner._load_restart_drain_timeout()
        == DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    )
    assert "Invalid restart_drain_timeout" in caplog.text


def test_load_loop_lag_warning_threshold_prefers_env_then_config_then_default(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_GATEWAY_LOOP_LAG_WARNING_SECONDS", raising=False)

    assert gateway_run.GatewayRunner._load_loop_lag_warning_threshold() == 5.0

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  loop_lag_warning_seconds: 2.5\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_loop_lag_warning_threshold() == 2.5

    monkeypatch.setenv("HERMES_GATEWAY_LOOP_LAG_WARNING_SECONDS", "7")
    assert gateway_run.GatewayRunner._load_loop_lag_warning_threshold() == 7.0

    monkeypatch.setenv("HERMES_GATEWAY_LOOP_LAG_WARNING_SECONDS", "0")
    assert gateway_run.GatewayRunner._load_loop_lag_warning_threshold() == 0.0

    monkeypatch.setenv("HERMES_GATEWAY_LOOP_LAG_WARNING_SECONDS", "invalid")
    assert gateway_run.GatewayRunner._load_loop_lag_warning_threshold() == 5.0
    assert "Invalid gateway loop lag warning threshold" in caplog.text


def test_load_loop_lag_traceback_threshold_prefers_env_then_config_then_default(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_GATEWAY_LOOP_LAG_TRACEBACK_SECONDS", raising=False)

    assert gateway_run.GatewayRunner._load_loop_lag_traceback_threshold() == 30.0

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  loop_lag_traceback_seconds: 12.5\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_loop_lag_traceback_threshold() == 12.5

    monkeypatch.setenv("HERMES_GATEWAY_LOOP_LAG_TRACEBACK_SECONDS", "9")
    assert gateway_run.GatewayRunner._load_loop_lag_traceback_threshold() == 9.0

    monkeypatch.setenv("HERMES_GATEWAY_LOOP_LAG_TRACEBACK_SECONDS", "0")
    assert gateway_run.GatewayRunner._load_loop_lag_traceback_threshold() == 0.0

    monkeypatch.setenv("HERMES_GATEWAY_LOOP_LAG_TRACEBACK_SECONDS", "invalid")
    assert gateway_run.GatewayRunner._load_loop_lag_traceback_threshold() == 30.0
    assert "Invalid gateway loop lag traceback threshold" in caplog.text


def test_load_loop_lag_prestall_threshold_uses_config_and_default(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    assert gateway_run.GatewayRunner._load_loop_lag_prestall_threshold() == 5.0

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  loop_lag_prestall_seconds: 2.5\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_loop_lag_prestall_threshold() == 2.5

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  loop_lag_prestall_seconds: 0\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_loop_lag_prestall_threshold() == 0.0

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  loop_lag_prestall_seconds: 0.5\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_loop_lag_prestall_threshold() == 5.0
    assert "must exceed monitor interval" in caplog.text

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  loop_lag_prestall_seconds: invalid\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_loop_lag_prestall_threshold() == 5.0
    assert "Invalid gateway loop lag pre-stall threshold" in caplog.text


def test_prestall_watchdog_runner_lifecycle(monkeypatch):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_prestall_threshold = 5.0
    runner._loop_lag_monitor_interval = 1.0
    runner._loop_lag_prestall_watchdog = None
    calls = []

    class DummyWatchdog:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def start(self):
            calls.append(("start", None))
            return True

        def stop(self, *, timeout):
            calls.append(("stop", timeout))
            return True

    monkeypatch.setattr(gateway_run, "PrestallLoopLagWatchdog", DummyWatchdog)

    assert runner._start_loop_lag_prestall_watchdog() is True
    assert runner._stop_loop_lag_prestall_watchdog() is True
    assert [name for name, _ in calls] == ["init", "start", "stop"]
    assert runner._loop_lag_prestall_watchdog is None


@pytest.mark.asyncio
async def test_gateway_loop_lag_monitor_logs_when_tick_lags(caplog):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_warning_threshold = 0.001
    runner._loop_lag_traceback_threshold = 0
    runner._loop_lag_monitor_interval = 0.001

    async def fake_sleep(_delay):
        # Force one delayed tick, then cancel the monitor loop cleanly.
        raise asyncio.CancelledError

    with monkeypatch_context() as monkeypatch:
        times = iter([0.0, 1.0])
        monkeypatch.setattr(gateway_run.time, "monotonic", lambda: next(times))
        monkeypatch.setattr(gateway_run.asyncio, "sleep", fake_sleep)
        with caplog.at_level("WARNING", logger="gateway.run"):
            await runner._gateway_loop_lag_monitor()

    assert "Gateway event loop lag" in caplog.text


@pytest.mark.asyncio
async def test_gateway_loop_lag_monitor_keeps_prestall_heartbeat_when_warnings_disabled(
    monkeypatch,
):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_warning_threshold = 0
    runner._loop_lag_monitor_interval = 0.001

    class Watchdog:
        def __init__(self):
            self.beats = 0

        def beat(self):
            self.beats += 1

    watchdog = Watchdog()
    runner._loop_lag_prestall_watchdog = watchdog

    async def fake_sleep(_delay):
        raise asyncio.CancelledError

    monkeypatch.setattr(gateway_run.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gateway_run.time, "monotonic", lambda: 1.0)

    await runner._gateway_loop_lag_monitor()

    assert watchdog.beats == 1


@pytest.mark.asyncio
async def test_gateway_loop_lag_logger_dumps_thread_stacks_over_traceback_threshold(caplog):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_traceback_threshold = 0.5
    runner._background_tasks = set()

    with caplog.at_level("WARNING", logger="gateway.run"):
        runner._log_loop_lag(1.0, 0.001)
        await asyncio.to_thread(runner._loop_lag_traceback_worker.join, 2.0)

    assert "exceeded traceback threshold" in caplog.text
    assert "thread" in caplog.text
    assert "_format_thread_tracebacks" in caplog.text


@pytest.mark.asyncio
async def test_severe_loop_lag_traceback_formatting_does_not_block_event_loop(
    monkeypatch, caplog
):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_traceback_threshold = 0.5
    runner._background_tasks = set()
    release = threading.Event()
    formatter_started = threading.Event()
    formatter_timed_out = threading.Event()
    loop_progressed = asyncio.Event()

    def slow_formatter():
        formatter_started.set()
        if not release.wait(timeout=1.0):
            formatter_timed_out.set()
        return "--- thread bounded-test ---\nstack"

    monkeypatch.setattr(runner, "_format_thread_tracebacks", slow_formatter)
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon(loop_progressed.set)
        loop.call_soon(release.set)
        with caplog.at_level("WARNING", logger="gateway.run"):
            runner._log_loop_lag(1.0, 0.001)
        await asyncio.wait_for(loop_progressed.wait(), timeout=2.0)

        assert not formatter_timed_out.is_set(), (
            "severe-lag diagnostics blocked the event loop until the formatter "
            "timed out instead of allowing the scheduled loop callback to run"
        )
        assert "Gateway event loop lag 1.000s" in caplog.text
        for _ in range(100):
            if formatter_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert formatter_started.is_set()
        await asyncio.to_thread(runner._loop_lag_traceback_worker.join, 2.0)
        assert not runner._loop_lag_traceback_worker.is_alive()
        assert "thread stacks follow" in caplog.text
        assert "bounded-test" in caplog.text
    finally:
        release.set()


def test_loop_lag_worker_construction_failure_is_contained(monkeypatch, caplog):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_traceback_threshold = 0.5

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("synthetic thread construction failure")

    monkeypatch.setattr(gateway_run.threading, "Thread", BrokenThread)

    with caplog.at_level("WARNING", logger="gateway.run"):
        runner._log_loop_lag(1.0, 0.001)

    assert "thread stack capture could not start" in caplog.text


def test_thread_traceback_dump_groups_duplicate_stacks_and_is_bounded(monkeypatch):
    frames = {ident: object() for ident in range(100)}
    threads = [
        SimpleNamespace(ident=ident, name=("MainThread" if ident == 0 else f"worker-{ident}"))
        for ident in frames
    ]

    monkeypatch.setattr(gateway_run.sys, "_current_frames", lambda: frames)
    monkeypatch.setattr(gateway_run.threading, "enumerate", lambda: threads)

    def fake_format_stack(frame, *, limit=None):
        assert limit is not None and limit <= 32
        group = "alpha" if id(frame) % 2 else "beta"
        return [f"  File synthetic.py, in {group}\n"] * 64

    monkeypatch.setattr(gateway_run.traceback, "format_stack", fake_format_stack)

    dump = gateway_run.GatewayRunner._format_thread_tracebacks()

    assert "threads=" in dump
    assert "duplicate stack" in dump
    assert "thread capture truncated" in dump
    assert len(dump.encode("utf-8")) <= 256 * 1024


@pytest.mark.asyncio
async def test_severe_loop_lag_uses_single_daemon_capture_worker(monkeypatch):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._loop_lag_traceback_threshold = 0.5
    release = threading.Event()
    started = threading.Event()

    def slow_formatter():
        started.set()
        release.wait(timeout=2.0)
        return "bounded stack"

    monkeypatch.setattr(runner, "_format_thread_tracebacks", slow_formatter)
    try:
        runner._log_loop_lag(1.0, 0.001)
        for _ in range(100):
            worker = getattr(runner, "_loop_lag_traceback_worker", None)
            if worker is not None and started.is_set():
                break
            await asyncio.sleep(0.01)

        assert worker is not None
        assert worker.daemon is True
        assert worker.is_alive()

        first_worker = worker
        runner._log_loop_lag(1.1, 0.001)
        assert runner._loop_lag_traceback_worker is first_worker
    finally:
        release.set()
        worker = getattr(runner, "_loop_lag_traceback_worker", None)
        if worker is not None:
            await asyncio.to_thread(worker.join, 2.0)



@pytest.mark.asyncio
async def test_request_restart_is_idempotent():
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock()
    runner._launch_detached_restart_command = AsyncMock()

    # _run_restart is held on self._restart_task and is intentionally NOT in
    # _background_tasks, so _stop_impl's cancel loop can't abort it mid-await
    # (see #12875).
    assert runner.request_restart(detached=True, via_service=False) is True
    assert runner._restart_task is not None
    assert runner._restart_task not in runner._background_tasks
    assert runner.request_restart(detached=True, via_service=False) is False

    await runner._restart_task

    runner._launch_detached_restart_command.assert_awaited_once_with()
    runner.stop.assert_awaited_once_with(
        restart=True, detached_restart=True, service_restart=False
    )


@pytest.mark.asyncio
async def test_pending_detached_restart_upgrades_to_service_recycle():
    """Owner fencing monotonically upgrades a queued restart before launch."""
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock()
    runner._launch_detached_restart_command = AsyncMock()

    restart_task = None
    completed = False
    cleanup_completed = False
    results = []
    try:
        accepted = runner.request_restart(detached=True, via_service=False)
        restart_task = runner._restart_task
        assert accepted is True
        assert restart_task is not None

        # No event-loop turn has occurred, so the detached helper is not yet
        # irreversibly launched. The stronger ownership fence must become the
        # authoritative request even though request_restart remains one-shot.
        assert runner.request_restart(detached=False, via_service=True) is False
        # A later weaker duplicate must not downgrade the ownership boundary.
        assert runner.request_restart(detached=True, via_service=False) is False
    finally:
        if restart_task is not None:
            done, pending = await asyncio.wait({restart_task}, timeout=2.0)
            completed = restart_task in done
            for task in pending:
                task.cancel()
            if pending:
                cancelled_done, pending = await asyncio.wait(pending, timeout=2.0)
                done |= cancelled_done
            cleanup_completed = not pending
            if restart_task in done:
                results = await asyncio.gather(restart_task, return_exceptions=True)

    assert completed is True
    assert cleanup_completed is True
    assert not any(isinstance(result, BaseException) for result in results)
    runner._launch_detached_restart_command.assert_not_awaited()
    runner.stop.assert_awaited_once_with(
        restart=True, detached_restart=False, service_restart=True
    )


@pytest.mark.asyncio
async def test_restart_worker_failure_forces_shutdown_signal():
    """A failed restart worker may not leave the gateway alive and fenced."""
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock(side_effect=RuntimeError("shutdown failed"))
    restart_task = None
    completed = False
    cleanup_completed = False
    results = []

    try:
        accepted = runner.request_restart(detached=False, via_service=True)
        restart_task = runner._restart_task
        assert accepted is True
        assert restart_task is not None
    finally:
        if restart_task is not None:
            done, pending = await asyncio.wait({restart_task}, timeout=2.0)
            completed = restart_task in done
            for task in pending:
                task.cancel()
            if pending:
                cancelled_done, pending = await asyncio.wait(pending, timeout=2.0)
                done |= cancelled_done
            cleanup_completed = not pending
            if restart_task in done:
                results = await asyncio.gather(restart_task, return_exceptions=True)

    assert completed is True
    assert cleanup_completed is True
    assert not any(isinstance(result, BaseException) for result in results)
    runner.stop.assert_awaited_once_with(
        restart=True, detached_restart=False, service_restart=True
    )
    assert runner._shutdown_event.is_set()
    assert runner._exit_code == 75


@pytest.mark.asyncio
async def test_restart_worker_baseexception_forces_shutdown_signal():
    """A non-Exception task failure must still force terminal shutdown."""
    class RestartWorkerBaseFailure(BaseException):
        pass

    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock(side_effect=RestartWorkerBaseFailure("fatal worker"))

    assert runner.request_restart(detached=False, via_service=True) is True
    await runner._restart_task

    assert runner._shutdown_event.is_set()
    assert runner._exit_code == 75


@pytest.mark.asyncio
async def test_run_restart_excluded_from_stop_cancel_loop():
    """Regression for #12875: _run_restart is held on self._restart_task and
    kept OUT of _background_tasks, and the _stop_impl cancel loop explicitly
    skips it. If it were in _background_tasks, the cancel loop (which fires
    while _run_restart is awaiting _stop_task) would propagate CancelledError
    into _stop_impl and skip _shutdown_event.set() / _exit_code = 75."""
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock()

    # A decoy background task that SHOULD be cancelled, plus the restart task
    # that must NOT be.
    async def _decoy():
        await asyncio.sleep(60)

    decoy = asyncio.create_task(_decoy())
    runner._background_tasks.add(decoy)
    decoy.add_done_callback(runner._background_tasks.discard)

    assert runner.request_restart(detached=False, via_service=True) is True
    restart_task = runner._restart_task
    assert restart_task is not None
    assert restart_task not in runner._background_tasks

    # Run the real cancel loop body in isolation (mirrors _stop_impl:7234).
    runner._stop_task = None
    for _task in list(runner._background_tasks):
        if _task is runner._stop_task:
            continue
        if _task is runner._restart_task:
            continue
        _task.cancel()

    await asyncio.sleep(0)  # let cancellation settle
    assert decoy.cancelled()
    assert not restart_task.cancelled()

    await restart_task
    runner.stop.assert_awaited_once_with(
        restart=True, detached_restart=False, service_restart=True
    )


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX detached watcher")
async def test_launch_detached_restart_command_uses_setsid(monkeypatch, tmp_path):
    """Execute the generated watcher for both owner-liveness outcomes."""
    actual_pid = os.getpid()
    calls_file = tmp_path / "calls"
    hermes = tmp_path / "hermes"
    hermes.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HERMES_CALLS"\n',
        encoding="utf-8",
    )
    hermes.chmod(0o755)
    counter = tmp_path / "date-counter"
    counter.write_text("0", encoding="utf-8")
    setsid_bin = shutil.which("setsid")
    if not setsid_bin:
        pytest.skip("setsid is unavailable")

    async def capture_watcher(pid):
        runner, _adapter = make_restart_runner()
        popen_calls = []
        with monkeypatch.context() as scoped:
            scoped.setenv("_HERMES_GATEWAY", "1")
            scoped.setattr(gateway_run.sys, "platform", "linux")
            scoped.setattr(gateway_run, "_resolve_hermes_bin", lambda: [str(hermes)])
            scoped.setattr(gateway_run.os, "getpid", lambda: pid)
            scoped.setattr(
                shutil, "which", lambda cmd: setsid_bin if cmd == "setsid" else None
            )
            scoped.setattr(
                subprocess,
                "Popen",
                lambda cmd, **kwargs: popen_calls.append((cmd, kwargs)) or MagicMock(),
            )
            await runner._launch_detached_restart_command()
        assert len(popen_calls) == 1
        cmd, kwargs = popen_calls[0]
        assert cmd[:2] == [setsid_bin, "bash"]
        assert kwargs["start_new_session"] is True
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["env"].get("_HERMES_GATEWAY") is None
        return cmd, kwargs

    def instrumented_launch(cmd, kwargs):
        counter.write_text("0", encoding="utf-8")
        counter_q = shlex.quote(str(counter))
        fake_date = (
            f'date() {{ n=$(cat {counter_q}); n=$((n + 10)); '
            f'printf "%s" "$n" > {counter_q}; printf "%s\\n" "$n"; }}; '
        )
        env = dict(kwargs["env"])
        env["HERMES_CALLS"] = str(calls_file)
        return [*cmd[:-1], fake_date + cmd[-1]], env

    alive_cmd, alive_kwargs = await capture_watcher(actual_pid)
    alive_launch, alive_env = instrumented_launch(alive_cmd, alive_kwargs)
    subprocess.run(alive_launch, check=True, env=alive_env, timeout=2)
    alive_calls = (
        calls_file.read_text(encoding="utf-8").splitlines()
        if calls_file.exists()
        else []
    )
    calls_file.unlink(missing_ok=True)

    dead_cmd, dead_kwargs = await capture_watcher(999_999_999)
    dead_launch, dead_env = instrumented_launch(dead_cmd, dead_kwargs)
    subprocess.run(dead_launch, check=True, env=dead_env, timeout=2)
    dead_calls = calls_file.read_text(encoding="utf-8").splitlines()

    assert alive_calls == []
    assert dead_calls == ["gateway restart"]


@pytest.mark.asyncio
async def test_detached_restart_helper_is_idempotent(monkeypatch):
    runner, _adapter = make_restart_runner()
    popen_calls = []

    monkeypatch.setattr(gateway_run, "_resolve_hermes_bin", lambda: ["/usr/bin/hermes"])
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 321)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)))

    await runner._launch_detached_restart_command()
    await runner._launch_detached_restart_command()

    assert len(popen_calls) == 1


@pytest.mark.asyncio
async def test_detached_helper_never_restarts_while_old_pid_survives_deadline(monkeypatch):
    """The helper deadline bounds waiting, not the old process ownership fence."""
    runner, _adapter = make_restart_runner()
    popen_calls = []

    monkeypatch.setattr(gateway_run.sys, "platform", "linux")
    monkeypatch.setattr(gateway_run, "_resolve_hermes_bin", lambda: ["/usr/bin/hermes"])
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 321)
    monkeypatch.setattr(
        shutil, "which", lambda cmd: "/usr/bin/setsid" if cmd == "setsid" else None
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: popen_calls.append((cmd, kwargs)),
    )

    await runner._launch_detached_restart_command()

    assert len(popen_calls) == 1
    shell_cmd = popen_calls[0][0][-1]
    normalized = " ".join(shell_cmd.split())
    restart_after_s = max(float(runner._restart_drain_timeout) + 5.0, 5.0)
    expected = (
        f"deadline=$(( $(date +%s) + {int(restart_after_s)} )); "
        "while kill -0 321 2>/dev/null && [ $(date +%s) -lt $deadline ]; "
        "do sleep 0.2; done; if kill -0 321 2>/dev/null; then exit 0; fi; "
        "/usr/bin/hermes gateway restart"
    )
    assert normalized == expected
    assert normalized.count("/usr/bin/hermes gateway restart") == 1


@pytest.mark.asyncio
async def test_windows_detached_helper_fences_surviving_old_pid(monkeypatch, tmp_path):
    """The Windows watcher must exit before respawn when the old PID stays live."""
    runner, _adapter = make_restart_runner()
    popen_calls = []
    venv_dir = tmp_path / "venv"
    (venv_dir / "Lib" / "site-packages").mkdir(parents=True)

    monkeypatch.setattr(gateway_run.sys, "platform", "win32")
    monkeypatch.setattr(gateway_run, "_resolve_hermes_bin", lambda: ["hermes"])
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 321)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))

    import hermes_cli._subprocess_compat as subprocess_compat

    monkeypatch.setattr(subprocess_compat, "windows_detach_popen_kwargs", lambda: {})
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: popen_calls.append((cmd, kwargs)),
    )

    await runner._launch_detached_restart_command()

    assert len(popen_calls) == 1
    watcher = popen_calls[0][0][2]
    normalized = "\n".join(line.rstrip() for line in watcher.splitlines())
    guard = "if _alive(pid):\n    sys.exit(0)"
    assert guard in normalized
    assert normalized.index(guard) < normalized.index("subprocess.Popen(")
    assert normalized.count("subprocess.Popen(") == 1
    assert normalized.count("cmd = sys.argv[3:]") == 1

    tree = ast.parse(watcher)
    top_level_guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "_alive"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "sys"
            and child.func.attr == "exit"
            for child in ast.walk(node)
        )
    )
    top_level_launch = next(
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "subprocess"
        and node.value.func.attr == "Popen"
    )
    assert tree.body.index(top_level_guard) < tree.body.index(top_level_launch)


def test_windows_gateway_venv_imports_add_site_packages(monkeypatch, tmp_path):
    venv_dir = tmp_path / "venv"
    site_packages = venv_dir / "Lib" / "site-packages"
    pth_extra = tmp_path / "pywin32_system32"
    site_packages.mkdir(parents=True)
    pth_extra.mkdir()
    (site_packages / "pywin32.pth").write_text(str(pth_extra), encoding="utf-8")
    project_root = str(gateway_run.Path(gateway_run.__file__).resolve().parent.parent)

    monkeypatch.setattr(gateway_run.sys, "platform", "win32")
    monkeypatch.setattr(gateway_run.sys, "path", ["existing"])
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))
    monkeypatch.setenv("PYTHONPATH", "already-there")

    gateway_run._ensure_windows_gateway_venv_imports()

    assert gateway_run.sys.path[:2] == [project_root, str(site_packages)]
    assert str(pth_extra) in gateway_run.sys.path
    assert gateway_run.os.environ["VIRTUAL_ENV"] == str(venv_dir.resolve())
    pythonpath = gateway_run.os.environ["PYTHONPATH"].split(gateway_run.os.pathsep)
    assert pythonpath[:3] == [project_root, str(site_packages), "already-there"]


@pytest.mark.asyncio
async def test_windows_detached_restart_scrubs_gateway_marker(monkeypatch, tmp_path):
    runner, _adapter = make_restart_runner()
    popen_calls = []
    venv_dir = tmp_path / "venv"
    site_packages = venv_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)

    monkeypatch.setattr(gateway_run.sys, "platform", "win32")
    monkeypatch.setattr(gateway_run, "_resolve_hermes_bin", lambda: ["hermes"])
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 321)
    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))

    import hermes_cli._subprocess_compat as subprocess_compat

    monkeypatch.setattr(
        subprocess_compat,
        "windows_detach_popen_kwargs",
        lambda: {},
    )

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    await runner._launch_detached_restart_command()

    assert len(popen_calls) == 1
    cmd, kwargs = popen_calls[0]
    assert cmd[-3:] == ["hermes", "gateway", "restart"]
    watcher = cmd[2]
    assert kwargs["env"].get("_HERMES_GATEWAY") is None
    assert kwargs["env"]["VIRTUAL_ENV"] == str(venv_dir)
    assert str(site_packages) in kwargs["env"]["PYTHONPATH"].split(gateway_run.os.pathsep)
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL

    monkeypatch.setattr(
        subprocess_compat,
        "windows_detach_flags_without_breakaway",
        lambda: 0,
    )

    def execute_watcher(*, owner_alive):
        launches = []
        monotonic = MagicMock(side_effect=[0.0, 999.0])

        def fake_kill(_pid, _signal):
            if not owner_alive:
                raise ProcessLookupError

        with monkeypatch.context() as scoped:
            scoped.setattr(time, "monotonic", monotonic)
            scoped.setattr(time, "sleep", lambda _delay: None)
            scoped.setattr(os, "kill", fake_kill)
            scoped.setattr(
                subprocess,
                "Popen",
                lambda argv, **popen_kwargs: launches.append((argv, popen_kwargs))
                or MagicMock(),
            )
            scoped.setattr(sys, "argv", ["watcher", *cmd[3:]])
            exited = False
            try:
                exec(compile(watcher, "<windows-restart-watcher>", "exec"), {})
            except SystemExit:
                exited = True
        return exited, launches

    alive_exited, alive_launches = execute_watcher(owner_alive=True)
    dead_exited, dead_launches = execute_watcher(owner_alive=False)
    assert alive_exited is True
    assert alive_launches == []
    assert dead_exited is False
    assert len(dead_launches) == 1
    assert dead_launches[0][0] == ["hermes", "gateway", "restart"]


@pytest.mark.asyncio
async def test_windows_detached_restart_uses_pythonw_for_watcher(monkeypatch, tmp_path):
    runner, _adapter = make_restart_runner()
    popen_calls = []
    venv_dir = tmp_path / "venv"
    site_packages = venv_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)

    monkeypatch.setattr(gateway_run.sys, "platform", "win32")
    monkeypatch.setattr(gateway_run.sys, "executable", r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(gateway_run, "_resolve_hermes_bin", lambda: ["hermes"])
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 321)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))

    import hermes_cli._subprocess_compat as subprocess_compat
    import hermes_cli.gateway_windows as gateway_windows

    monkeypatch.setattr(
        gateway_windows,
        "_resolve_detached_python",
        lambda _python: (r"C:\Python311\pythonw.exe", venv_dir, [str(site_packages)]),
    )
    monkeypatch.setattr(
        subprocess_compat,
        "windows_detach_popen_kwargs",
        lambda: {"creationflags": 0x08000008},
    )

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    await runner._launch_detached_restart_command()

    assert len(popen_calls) == 1
    cmd, kwargs = popen_calls[0]
    assert cmd[0] == r"C:\Python311\pythonw.exe"
    assert cmd[-3:] == ["hermes", "gateway", "restart"]
    assert kwargs["creationflags"] == 0x08000008


# ── Shutdown notification tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_notification_sent_to_active_sessions():
    """Active sessions receive a notification when the gateway starts shutting down."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="999", chat_type="dm")
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    assert "shutting down" in adapter.sent[0]
    assert "interrupted" in adapter.sent[0]


@pytest.mark.asyncio
async def test_shutdown_notification_says_restarting_when_restart_requested():
    """When _restart_requested is True, the message says 'restarting' and mentions /retry."""
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    assert "restarting" in adapter.sent[0]
    assert "resume" in adapter.sent[0]


@pytest.mark.asyncio
async def test_shutdown_notification_deduplicates_per_chat():
    """Multiple sessions in the same chat only get one notification."""
    runner, adapter = make_restart_runner()
    # Two sessions (different users) in the same chat
    runner._running_agents["agent:main:telegram:group:chat1:u1"] = MagicMock()
    runner._running_agents["agent:main:telegram:group:chat1:u2"] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_shutdown_notification_skipped_when_no_active_agents():
    """No notification is sent when there are no active agents."""
    runner, adapter = make_restart_runner()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 0


@pytest.mark.asyncio
async def test_shutdown_notification_ignores_pending_sentinels():
    """Pending sentinels (not-yet-started agents) don't trigger notifications."""
    from gateway.run import _AGENT_PENDING_SENTINEL

    runner, adapter = make_restart_runner()
    runner._running_agents["agent:main:telegram:dm:999"] = _AGENT_PENDING_SENTINEL

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 0


@pytest.mark.asyncio
async def test_shutdown_notification_send_failure_does_not_block():
    """If sending a notification fails, the method still completes."""
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock(side_effect=Exception("network error"))
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    # Should not raise
    await runner._notify_active_sessions_of_shutdown()


@pytest.mark.asyncio
async def test_shutdown_notification_suppressed_when_flag_disabled():
    """Active-session ping is muted when gateway_restart_notification=False on the platform."""
    from gateway.config import Platform

    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner.config.platforms[Platform.TELEGRAM].gateway_restart_notification = False
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == []


@pytest.mark.asyncio
async def test_shutdown_notification_home_channel_suppressed_when_flag_disabled():
    """Home-channel ping during shutdown is muted when the flag is False."""
    from gateway.config import HomeChannel, Platform

    runner, adapter = make_restart_runner()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )
    runner.config.platforms[Platform.TELEGRAM].gateway_restart_notification = False

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == []


@pytest.mark.asyncio
async def test_shutdown_notification_uses_persisted_origin_for_colon_ids():
    """Shutdown notifications should route from persisted origin, not reparsed keys."""
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock()
    source = make_restart_source(chat_id="!room123:example.org", chat_type="group")
    source.platform = gateway_run.Platform.MATRIX
    session_key = build_session_key(source)
    runner._running_agents[session_key] = MagicMock()
    runner.session_store._entries = {
        session_key: SessionEntry(
            session_key=session_key,
            session_id="sess-1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=source.platform,
            chat_type=source.chat_type,
        )
    }
    runner.adapters = {gateway_run.Platform.MATRIX: adapter}

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.send.await_count == 1


@pytest.mark.asyncio
async def test_drain_suppress_skips_home_channel_keeps_session_ping(tmp_path, monkeypatch):
    """A suppress_notification drain marker mutes ONLY the home-channel broadcast.

    The per-active-session interrupt ping MUST still fire (it carries the
    "your task was interrupted, message me to resume" hint). This is the core
    drain-notification-suppression contract.
    """
    from gateway.config import HomeChannel, Platform
    import gateway.drain_control as dc

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    runner, adapter = make_restart_runner()
    # A home channel distinct from the active session's chat.
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )
    # One active session in a different chat.
    runner._running_agents["agent:main:telegram:dm:999"] = MagicMock()

    # NAS auto-update drain: marker present with suppress_notification=True.
    dc.write_drain_request(principal="nas", suppress_notification=True)

    await runner._notify_active_sessions_of_shutdown()

    # Exactly one send — the active-session ping to chat 999. The home-channel
    # broadcast to home-42 was suppressed.
    assert len(adapter.sent_calls) == 1
    sent_chat_ids = {chat_id for chat_id, _content, _meta in adapter.sent_calls}
    assert "999" in sent_chat_ids
    assert "home-42" not in sent_chat_ids
    assert "shutting down" in adapter.sent[0]


@pytest.mark.asyncio
async def test_drain_without_suppress_flag_still_broadcasts_home_channel(tmp_path, monkeypatch):
    """A drain marker WITHOUT the suppress flag leaves today's behaviour intact.

    Both the active-session ping AND the home-channel broadcast fire — proving
    the suppression is opt-in and operator/legacy drains are unaffected.
    """
    from gateway.config import HomeChannel, Platform
    import gateway.drain_control as dc

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    runner, adapter = make_restart_runner()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )
    runner._running_agents["agent:main:telegram:dm:999"] = MagicMock()

    # Operator drain: marker present, suppress_notification defaults False.
    dc.write_drain_request(principal="dashboard")

    await runner._notify_active_sessions_of_shutdown()

    sent_chat_ids = {chat_id for chat_id, _content, _meta in adapter.sent_calls}
    # Both targets notified (today's behaviour preserved).
    assert "999" in sent_chat_ids
    assert "home-42" in sent_chat_ids
