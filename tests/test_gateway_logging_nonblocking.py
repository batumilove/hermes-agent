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
