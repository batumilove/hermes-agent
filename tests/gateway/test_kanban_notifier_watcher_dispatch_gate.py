"""Tests for the dispatch_in_gateway gate on _kanban_dispatcher_watcher.

Notifier delivery is gated independently by notify_in_gateway; this file verifies
that worker spawning remains controlled by dispatch_in_gateway.
"""

import asyncio
from unittest.mock import patch

from gateway.run import GatewayRunner


def _make_runner():
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_dispatcher_lock_handle = None
    return runner


def _fake_config(dispatch_in_gateway):
    return {"kanban": {"dispatch_in_gateway": dispatch_in_gateway}}


def test_dispatcher_watcher_skips_when_dispatch_disabled():
    """dispatch_in_gateway=false returns before opening any board DB."""
    runner = _make_runner()
    with patch("hermes_cli.config.load_config", return_value=_fake_config(False)):
        with patch("hermes_cli.kanban_db.connect") as mock_connect:
            asyncio.run(runner._kanban_dispatcher_watcher())
    mock_connect.assert_not_called()


def test_dispatcher_watcher_env_override_disables(monkeypatch):
    """HERMES_KANBAN_DISPATCH_IN_GATEWAY=false skips config load entirely."""
    runner = _make_runner()
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_IN_GATEWAY", "false")
    with patch("hermes_cli.config.load_config") as mock_load_config:
        with patch("hermes_cli.kanban_db.connect") as mock_connect:
            asyncio.run(runner._kanban_dispatcher_watcher())
    mock_load_config.assert_not_called()
    mock_connect.assert_not_called()


def test_dispatcher_watcher_runs_when_dispatch_enabled():
    """dispatch_in_gateway=true proceeds past the gate to the board fan-out."""
    runner = _make_runner()
    past_gate = []
    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)
        # Stop after the initial delay + first per-interval sleep so the loop
        # body runs exactly once.
        if len(sleep_calls) >= 2:
            runner._running = False

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    import hermes_cli.kanban_db as _kb

    config = {
        "kanban": {
            "dispatch_in_gateway": True,
            "dispatch_interval_seconds": 1,
            "auto_decompose": False,
        }
    }
    with patch("hermes_cli.config.load_config", return_value=config):
        with patch("gateway.kanban_watchers._acquire_singleton_lock", return_value=(None, "unavailable")):
            with patch.object(_kb, "reap_worker_zombies", return_value=[]):
                with patch.object(
                    _kb,
                    "list_boards",
                    side_effect=lambda *a, **kw: past_gate.append(True) or [],
                ):
                    with patch("asyncio.sleep", side_effect=fake_sleep):
                        with patch("asyncio.to_thread", side_effect=fake_to_thread):
                            asyncio.run(runner._kanban_dispatcher_watcher())

    assert past_gate, "list_boards should be called when dispatch_in_gateway=true"
