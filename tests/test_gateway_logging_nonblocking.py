"""Blocked log sinks must not stall gateway callers or its asyncio loop."""
import asyncio
import io
import logging
import sys
import threading
import time
import types

import pytest
import hermes_logging


@pytest.mark.parametrize("mode", ["gateway-warning", "gateway-info", "gateway-debug", "verbose"])
@pytest.mark.parametrize("caller", ["worker", "event-loop"])
def test_blocked_stderr_does_not_block_callers(mode, caller, tmp_path, monkeypatch):
    root = logging.getLogger()
    previous, level = list(root.handlers), root.level
    hermes_logging._reset_queued_handlers()
    root.handlers.clear()
    entered, release, worker_done = threading.Event(), threading.Event(), threading.Event()
    output = io.StringIO()

    class BlockedStream:
        def write(self, text):
            entered.set()
            release.wait(3)
            return output.write(text)

        def flush(self):
            pass

    monkeypatch.setattr(hermes_logging, "_safe_stderr", lambda: BlockedStream())
    monkeypatch.setattr(hermes_logging, "_line_buffer_piped_stdout", lambda: None)
    monkeypatch.setattr(hermes_logging, "_read_logging_config", lambda: (None, None, None))
    # Bootstrap's imports are scoped to this dependency facade; no gateway,
    # skills synchronization, security audit or live state is started.
    facade = types.ModuleType("gateway.run")
    facade._best_effort = lambda *a, **k: None
    facade._hermes_home = tmp_path
    facade.logging = logging
    facade._gateway_stderr_formatter = lambda: logging.Formatter("%(levelname)s:%(message)s")
    monkeypatch.setitem(sys.modules, "gateway.run", facade)
    timer = threading.Timer(2, release.set)
    try:
        if mode == "verbose":
            hermes_logging.setup_logging(hermes_home=tmp_path)
            hermes_logging.setup_verbose_logging()
        else:
            from gateway.run_bootstrap import _start_gateway_configure_logging
            _start_gateway_configure_logging({"gateway-warning": 0, "gateway-info": 1, "gateway-debug": 2}[mode])
        timer.start()
        worker = threading.Thread(target=lambda: (root.warning("worker-witness"), worker_done.set()), daemon=True)
        worker.start()
        assert entered.wait(1), "sink was never exercised"
        if caller == "worker":
            assert worker_done.wait(.3), "logging caller blocked behind stderr I/O"

        async def exercise_loop():
            start = time.monotonic()
            root.warning("event-loop-witness")
            await asyncio.sleep(0)
            assert time.monotonic() - start < .3, "event loop blocked behind stderr handler lock"

        asyncio.run(exercise_loop())
    finally:
        release.set()
        timer.cancel()
        hermes_logging.flush_log_queue()
        hermes_logging._reset_queued_handlers()
        root.handlers[:] = previous
        root.setLevel(level)
        hermes_logging._logging_initialized = False
    assert "worker-witness" in output.getvalue()
    assert "event-loop-witness" in output.getvalue()


@pytest.mark.parametrize("action", ["verbose", "file", "stop"])
def test_blocked_stderr_lifecycle_is_bounded(action, tmp_path, monkeypatch):
    root = logging.getLogger()
    previous, level = list(root.handlers), root.level
    hermes_logging._reset_queued_handlers()
    root.handlers.clear()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    class BlockedStream:
        def write(self, text):
            entered.set()
            release.wait(5)
        def flush(self):
            pass

    monkeypatch.setattr(hermes_logging, "_safe_stderr", lambda: BlockedStream())
    monkeypatch.setattr(hermes_logging, "_read_logging_config", lambda: (None, None, None))
    hermes_logging.setup_verbose_logging()
    root.warning("blocked")
    assert entered.wait(1)
    # Remove only the idempotence marker to exercise registration of another sink.
    for handler in [*root.handlers, *hermes_logging._queued_file_handlers]:
        if getattr(handler, "_hermes_verbose", False):
            handler._hermes_verbose = False

    def exercise():
        if action == "verbose":
            hermes_logging.setup_verbose_logging()
        elif action == "file":
            hermes_logging.setup_logging(hermes_home=tmp_path, mode="gateway")
            root.warning("file-independent")
            hermes_logging.flush_log_queue()
        else:
            hermes_logging._stop_queue_listener()
        done.set()

    worker = threading.Thread(target=exercise, daemon=True)
    try:
        worker.start()
        assert done.wait(.5), "lifecycle joined a blocked stderr writer"
        if action == "file":
            assert "file-independent" in (tmp_path / "logs/agent.log").read_text()
    finally:
        release.set()
        worker.join(3)
        hermes_logging.flush_log_queue()
        hermes_logging._reset_queued_handlers()
        root.handlers[:] = previous
        root.setLevel(level)
        hermes_logging._logging_initialized = False


def test_process_exit_with_permanently_blocked_stderr(tmp_path):
    import os
    import subprocess
    script = """
import logging, threading
import hermes_logging
entered = threading.Event()
class Stuck:
    def write(self, text):
        entered.set()
        threading.Event().wait()
    def flush(self):
        threading.Event().wait()
hermes_logging._safe_stderr = lambda: Stuck()
hermes_logging.setup_verbose_logging()
logging.warning('stuck forever')
assert entered.wait(1)
"""
    env = dict(os.environ, HERMES_HOME=str(tmp_path))
    subprocess.run([sys.executable, "-c", script], env=env, check=True, timeout=3)


def test_stderr_queue_is_bounded_and_close_does_not_wait(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class Stuck:
        def write(self, text):
            entered.set()
            release.wait(5)
        def flush(self):
            pass
    handler = hermes_logging._AsyncStderrHandler(Stuck())
    record = logging.LogRecord('test', logging.WARNING, '', 0, 'queued', (), None)
    try:
        handler.handle(record)
        assert entered.wait(1)
        for _ in range(2048):
            handler.handle(record)
        assert handler.queue.qsize() == 1024
        start = time.monotonic()
        handler.close()
        assert time.monotonic() - start < .5
        handler.handle(record)  # closed sink silently drops further records
    finally:
        release.set()
        handler.close()
        handler._worker.join(2)
    assert not handler._worker.is_alive()
