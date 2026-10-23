"""Named PBS requirements: execute scheduler and real Telegram text send paths."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cron import scheduler_delivery as delivery
from gateway.config import Platform
from tests.gateway.test_telegram_thread_fallback import (
    FakeBadRequest, _inject_fake_telegram, _make_adapter,
)

# Reuse the established transport-only Telegram boundary, not replacement routing.
assert _inject_fake_telegram


def target(platform=Platform.TELEGRAM, thread="83178"):
    return SimpleNamespace(platform=platform, platform_name=platform.value,
                           thread_id=thread, chat_id="407304892", job={"id": "pbs"},
                           where="telegram:407304892:83178", notify_delivery=True,
                           fresh_telegram_topic_assigned=False, runtime_adapter=None,
                           loop=None, origin_target=False, is_relay=False, pconfig=None)


@pytest.mark.parametrize("platform,thread,strict", [
    (Platform.TELEGRAM, "83178", True),
    (Platform.TELEGRAM, None, False),
    (Platform.SLACK, "83178", False),
])
def test_scheduler_marks_only_telegram_topic_text(platform, thread, strict):
    _, text, media = delivery._live_route_metadata(target(platform, thread))
    assert text.get("cron_strict_topic", False) is strict
    assert "cron_strict_topic" not in media


def test_scheduler_refuses_standalone_topic_before_transport(monkeypatch):
    sender = Mock(side_effect=AssertionError("must not enter standalone transport"))
    monkeypatch.setattr("tools.send_message_tool._send_to_platform", sender)
    result, error = delivery._standalone_send(target(), "alert", [])
    assert result is None
    assert "standalone fallback refused" in error
    sender.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat", ["407304892", "-1001234567890"])
@pytest.mark.parametrize("parse_first", [False, True])
async def test_cron_text_rejection_never_retries_root(chat, parse_first):
    adapter = _make_adapter()
    calls = []

    async def send(**kwargs):
        calls.append(kwargs)
        if parse_first and len(calls) == 1:
            raise FakeBadRequest("Can't parse entities")
        if kwargs.get("message_thread_id") is not None:
            raise FakeBadRequest("Message thread not found")
        return SimpleNamespace(message_id=99)

    adapter._bot = SimpleNamespace(send_message=send)
    adapter._prune_stale_dm_topic_binding = Mock()
    _, metadata, _ = delivery._live_route_metadata(target())
    result = await adapter.send(chat, "PBS alert", metadata=metadata)
    assert not result.success
    assert not result.retryable
    assert all(call.get("message_thread_id") == 83178 for call in calls)
    assert len(calls) == (2 if parse_first else 1)
    adapter._prune_stale_dm_topic_binding.assert_not_called()


@pytest.mark.asyncio
async def test_interactive_text_keeps_existing_root_fallback():
    adapter = _make_adapter()
    calls = []

    async def send(**kwargs):
        calls.append(kwargs)
        if kwargs.get("message_thread_id") is not None:
            raise FakeBadRequest("Message thread not found")
        return SimpleNamespace(message_id=99)

    adapter._bot = SimpleNamespace(send_message=send)
    result = await adapter.send("407304892", "interactive", metadata={"thread_id": "83178"})
    assert result.success
    assert calls[-1].get("message_thread_id") is None
