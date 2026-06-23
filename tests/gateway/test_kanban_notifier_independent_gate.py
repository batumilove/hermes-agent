"""Tests for independently gating Kanban gateway notifications.

External dispatcher mode disables worker spawning in the Telegram gateway, but
the gateway still needs to deliver task completion/block/crash notifications.
Therefore notifier ownership must be controlled by its own key rather than
``kanban.dispatch_in_gateway``.
"""
import asyncio
from unittest.mock import MagicMock, patch

from gateway.config import Platform
from gateway.run import GatewayRunner


def _make_runner(with_adapter=False):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: MagicMock()} if with_adapter else {}
    runner._kanban_sub_fail_counts = {}
    return runner


def _fake_config(**kanban):
    return {"kanban": kanban}


def test_notifier_continues_when_dispatch_disabled_but_notify_enabled():
    """External dispatcher mode should not silence Telegram Kanban events."""
    runner = _make_runner(with_adapter=True)
    past_gate = []
    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)
        if len(sleep_calls) >= 2:
            runner._running = False

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    import hermes_cli.kanban_db as _kb

    with patch(
        "hermes_cli.config.load_config",
        return_value=_fake_config(dispatch_in_gateway=False, notify_in_gateway=True),
    ):
        with patch.object(
            _kb,
            "list_boards",
            side_effect=lambda *a, **kw: past_gate.append(True) or [],
        ):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                with patch("asyncio.to_thread", side_effect=fake_to_thread):
                    asyncio.run(runner._kanban_notifier_watcher())

    assert past_gate, "notifier should poll boards when notify_in_gateway=true"


def test_notifier_can_be_disabled_independently():
    """notify_in_gateway=false exits before opening any board DB."""
    runner = _make_runner()
    with patch(
        "hermes_cli.config.load_config",
        return_value=_fake_config(dispatch_in_gateway=True, notify_in_gateway=False),
    ):
        with patch("hermes_cli.kanban_db.connect") as mock_connect:
            asyncio.run(runner._kanban_notifier_watcher())
    mock_connect.assert_not_called()


def test_notifier_env_override_disables_without_loading_config(monkeypatch):
    """HERMES_KANBAN_NOTIFY_IN_GATEWAY=false is the notifier-specific kill switch."""
    runner = _make_runner()
    monkeypatch.setenv("HERMES_KANBAN_NOTIFY_IN_GATEWAY", "false")
    with patch("hermes_cli.config.load_config") as mock_load_config:
        with patch("hermes_cli.kanban_db.connect") as mock_connect:
            asyncio.run(runner._kanban_notifier_watcher())
    mock_load_config.assert_not_called()
    mock_connect.assert_not_called()
