"""Current-baseline cron topic guards; transport fakes never contact Telegram."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cron import scheduler_delivery as delivery
from gateway.config import Platform
from tests.gateway.test_telegram_thread_fallback import (
    FakeBadRequest,
    _inject_fake_telegram,
    _make_adapter,
)


def target(platform=Platform.TELEGRAM, thread_id="83178", chat_id="-100123"):
    return SimpleNamespace(
        platform=platform, platform_name=platform.value, thread_id=thread_id,
        chat_id=chat_id, job={"id": "strict-test", "origin": {}},
        notify_delivery=True, fresh_telegram_topic_assigned=False,
        runtime_adapter=None, loop=None, where=f"{platform.value}:{chat_id}:{thread_id}",
        pconfig=None, origin_target=False, is_relay=False,
    )


@pytest.mark.parametrize("chat_id", ["-100123", "407304892"])
def test_cron_topic_metadata_is_strict(chat_id):
    thread, metadata, _ = delivery._live_route_metadata(target(chat_id=chat_id))
    assert thread == "83178"
    assert metadata["cron_strict_topic"] is True


@pytest.mark.parametrize("platform,thread_id", [(Platform.TELEGRAM, None), (Platform.DISCORD, "42")])
def test_non_topic_or_non_telegram_metadata_is_not_strict(platform, thread_id):
    _, metadata, _ = delivery._live_route_metadata(target(platform, thread_id))
    assert "cron_strict_topic" not in metadata


def test_standalone_topic_is_refused_before_transport(monkeypatch):
    from tools import send_message_tool

    sender = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(send_message_tool, "_send_to_platform", sender)
    result, error = delivery._standalone_send(target(), "alert", [])
    assert result is None
    assert "requires the live adapter; standalone fallback refused" in error
    sender.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", ["-100123", "407304892"])
async def test_strict_missing_thread_text_is_terminal(chat_id):
    adapter = _make_adapter()
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs.get("message_thread_id") is not None:
            raise FakeBadRequest("Message thread not found")
        return SimpleNamespace(message_id=99)

    adapter._bot = SimpleNamespace(send_message=send_message)
    result = await adapter.send(chat_id, "strict alert", metadata={"thread_id": "83178", "cron_strict_topic": True})
    assert result.success is False
    assert result.retryable is False
    assert len(calls) == 1
    assert calls[0]["message_thread_id"] == 83178


@pytest.mark.asyncio
async def test_strict_valid_topic_text_still_delivers():
    adapter = _make_adapter()
    sender = AsyncMock(return_value=SimpleNamespace(message_id=99))
    adapter._bot = SimpleNamespace(send_message=sender)
    result = await adapter.send("407304892", "strict alert", metadata={"thread_id": "83178", "cron_strict_topic": True})
    assert result.success is True
    assert sender.call_args.kwargs["message_thread_id"] == 83178


@pytest.mark.asyncio
async def test_interactive_missing_group_topic_retains_root_fallback():
    adapter = _make_adapter()
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs.get("message_thread_id") is not None:
            raise FakeBadRequest("Message thread not found")
        return SimpleNamespace(message_id=99)

    adapter._bot = SimpleNamespace(send_message=send_message)
    result = await adapter.send("-100123", "interactive reply", metadata={"thread_id": "83178"})
    assert result.success is True
    assert calls[-1].get("message_thread_id") is None
