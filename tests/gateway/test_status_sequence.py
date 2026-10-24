"""Delayed lifecycle callbacks cannot supersede newer status publications."""

import asyncio
import threading

import pytest

from agent.async_utils import run_sync_in_detached_serial_daemon_thread
from gateway import status, status_sequence
from tests.waiting import async_wait_until


@pytest.mark.parametrize("publication", ["sync", "async"])
def test_abandoned_callback_cannot_replace_newer_status(tmp_path, monkeypatch, publication):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(status_sequence, "_latest_serial_status_sequence", 0)
    monkeypatch.setattr("agent.async_utils._SERIAL_JOB_TIMEOUT_SECONDS", 0.05)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def old_callback():
        started.set()
        release.wait()
        try:
            status.write_runtime_status(gateway_state="starting")
        finally:
            finished.set()

    async def exercise():
        old = asyncio.create_task(run_sync_in_detached_serial_daemon_thread(old_callback))
        try:
            assert await async_wait_until(started.is_set, timeout=2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(old, timeout=2)
            if publication == "sync":
                assert status.write_runtime_status(gateway_state="running", wait_timeout=2)
            else:
                assert status.publish_runtime_status(gateway_state="running") > 0
            release.set()
            assert await async_wait_until(finished.is_set, timeout=2)
            assert await status.flush_runtime_status_async(timeout=2)
            assert status.read_runtime_status()["gateway_state"] == "running"
        finally:
            release.set()

    asyncio.run(exercise())
