"""Control status follows the ticker created by real gateway startup."""

import asyncio
import os
import threading
from types import SimpleNamespace


def test_startup_ticker_liveness_reaches_control_status(monkeypatch):
    from cron import scheduler_provider
    from gateway import run
    from gateway.run_bootstrap import _start_gateway_start_cron_and_housekeeping
    from gateway.run_fork_contracts import _build_live_control_status

    started = threading.Event()
    release = threading.Event()

    class Provider:
        name = "fixture"

        def start(self, stop, **kwargs):
            started.set()
            assert release.wait(10)

    provider = Provider()
    monkeypatch.setattr(scheduler_provider, "resolve_cron_scheduler", lambda: provider)
    monkeypatch.setattr(scheduler_provider, "scheduler_for_profile_mode", lambda p, **kw: p)
    monkeypatch.setattr(run, "_cron_tick_profile_homes", lambda config: [])
    monkeypatch.setattr(run, "_start_gateway_housekeeping", lambda *a, **kw: None)
    runner = SimpleNamespace(
        config=SimpleNamespace(), adapters={},
        _running_agent_count=lambda: 0, _active_cron_job_count=lambda: 0,
        _active_api_run_count=lambda: 0, _active_delegation_count=lambda: 0,
    )

    async def start():
        return _start_gateway_start_cron_and_housekeeping(runner)

    stop, _, ticker, housekeeping = asyncio.run(start())
    try:
        assert started.wait(5)
        assert _build_live_control_status(runner, payload={})["scheduler"] == {
            "status": "running", "writer_pid": os.getpid(),
        }
        release.set()
        ticker.join(5)
        assert not ticker.is_alive()
        assert _build_live_control_status(runner, payload={})["scheduler"] == {
            "status": "failed", "writer_pid": os.getpid(),
        }
    finally:
        stop.set()
        release.set()
        ticker.join(5)
        housekeeping.join(5)
