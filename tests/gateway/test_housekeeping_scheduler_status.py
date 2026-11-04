"""The durable heartbeat reports this process's scheduler, including ticker failure."""

import os
import threading
from types import SimpleNamespace


def test_housekeeping_refreshes_scheduler_owner_and_liveness(tmp_path, monkeypatch):
    from cron.scheduler_thread import SupervisedTickerThread
    from gateway import run, status

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Status left by the previous gateway must not survive a fresh heartbeat.
    (tmp_path / "gateway_state.json").write_text(
        '{"scheduler":{"status":"running","provider":"old","writer_pid":999999}}'
    )
    monkeypatch.setattr(
        run, "_housekeeping_chore",
        lambda label, fn: fn() if label == "Runtime heartbeat" else None,
    )

    class OneTick:
        waited = False

        def is_set(self):
            return self.waited

        def wait(self, timeout=None):
            self.waited = True
            return True

    release = threading.Event()
    ticker = SupervisedTickerThread(release.wait, stop_event=release)
    ticker.start()
    provider = SimpleNamespace(name="fixture")

    def tick():
        stop = OneTick()
        run._start_gateway_housekeeping(
            stop, interval=0, cron_provider=provider, cron_thread=ticker,
        )
        assert status.flush_runtime_status(timeout=5)
        return status.read_runtime_status()["scheduler"]

    try:
        state = tick()
        assert state["status"] == "running"
        assert state["provider"] == "fixture"
        assert state["writer_pid"] == os.getpid()
        assert state["writer_start_time"] == status._get_process_start_time(os.getpid())
        release.set()
        ticker.join(5)
        assert not ticker.is_alive()
        assert tick()["status"] == "failed"
    finally:
        release.set()
        ticker.join(5)
