"""Tests for Telegram forum/private topic icon editing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_adapter():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra={})
    adapter._bot = MagicMock()
    adapter._bot.edit_forum_topic = AsyncMock(return_value=True)
    adapter._bot.get_forum_topic_icon_stickers = AsyncMock(
        return_value=[
            SimpleNamespace(custom_emoji_id="5312536423581202898", emoji="🧪"),
            SimpleNamespace(custom_emoji_id="5368324170671202286", emoji="🚀"),
        ]
    )
    return adapter


def _make_runner(adapter=None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter or _make_adapter()}
    runner._voice_mode = {}
    return runner


def _make_event(text="/topicicon 5312536423581202898", *, thread_id="42", chat_type="dm"):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="12345",
        chat_id="67890" if chat_type == "dm" else "-100123",
        user_name="testuser",
        chat_type=chat_type,
        thread_id=thread_id,
    )
    return MessageEvent(text=text, source=source)


@pytest.mark.asyncio
async def test_telegram_adapter_edits_topic_icon():
    adapter = _make_adapter()

    ok = await adapter.edit_topic_icon(
        chat_id="67890",
        thread_id="42",
        icon_custom_emoji_id="5312536423581202898",
    )

    assert ok is True
    adapter._bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=67890,
        message_thread_id=42,
        icon_custom_emoji_id="5312536423581202898",
    )


@pytest.mark.asyncio
async def test_telegram_adapter_allows_empty_icon_to_remove():
    adapter = _make_adapter()

    ok = await adapter.edit_topic_icon(
        chat_id="67890",
        thread_id="42",
        icon_custom_emoji_id="",
    )

    assert ok is True
    adapter._bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=67890,
        message_thread_id=42,
        icon_custom_emoji_id="",
    )


@pytest.mark.asyncio
async def test_telegram_adapter_lists_allowed_topic_icon_ids():
    adapter = _make_adapter()

    icons = await adapter.list_topic_icon_stickers()

    assert icons == [
        {"custom_emoji_id": "5312536423581202898", "emoji": "🧪"},
        {"custom_emoji_id": "5368324170671202286", "emoji": "🚀"},
    ]
    adapter._bot.get_forum_topic_icon_stickers.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_topicicon_command_edits_current_telegram_topic():
    adapter = _make_adapter()
    runner = _make_runner(adapter)

    result = await runner._handle_topic_icon_command(
        _make_event("/topicicon 5312536423581202898", thread_id="42")
    )

    assert "Topic icon updated" in result
    adapter._bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=67890,
        message_thread_id=42,
        icon_custom_emoji_id="5312536423581202898",
    )


@pytest.mark.asyncio
async def test_topicicon_command_lists_allowed_icons_without_args():
    adapter = _make_adapter()
    runner = _make_runner(adapter)

    result = await runner._handle_topic_icon_command(_make_event("/topicicon", thread_id="42"))

    assert "Available topic icons" in result
    assert "5312536423581202898" in result
    assert "🧪" in result


@pytest.mark.asyncio
async def test_topicicon_command_requires_telegram_topic():
    runner = _make_runner()

    result = await runner._handle_topic_icon_command(_make_event("/topicicon 123", thread_id=None))

    assert "must be used inside a Telegram topic" in result
