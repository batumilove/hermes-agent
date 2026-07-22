"""Operation-scoped Telegram run-phase delivery contracts."""

import asyncio

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    RunPhaseReply,
    SendResult,
)
from gateway.session import SessionSource, build_session_key


class _ConcreteAdapter(BasePlatformAdapter):
    platform = Platform.TELEGRAM

    def __init__(self):
        super().__init__(
            PlatformConfig(enabled=True, token="fake", typing_indicator=False),
            Platform.TELEGRAM,
        )
        self.sent = []

    async def connect(self, *, is_reconnect: bool = False):
        return None

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append({
            "chat_id": chat_id,
            "content": content,
            "reply_to": reply_to,
            "metadata": dict(metadata or {}),
        })
        return SendResult(success=True, message_id="sent-1")

    async def get_chat_info(self, chat_id):
        return {}


@pytest.mark.asyncio
async def test_background_delivery_preserves_handler_run_phase():
    """The base delivery layer must not turn a dispatch acknowledgement into
    notify-only metadata, because Telegram maps notify-only to `Final`."""
    adapter = _ConcreteAdapter()

    async def handler(_event):
        return RunPhaseReply("Two reviewers are still running.", "dispatched")

    adapter.set_message_handler(handler)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="407304892",
        user_id="user",
        chat_type="dm",
        message_id="trigger-1",
    )
    event = MessageEvent(text="review", source=source, message_id="trigger-1")
    session_key = build_session_key(source)

    await adapter.handle_message(event)
    await asyncio.gather(*list(adapter._background_tasks))

    assert len(adapter.sent) == 1
    assert adapter.sent[0]["metadata"]["notify"] is True
    assert adapter.sent[0]["metadata"]["message_phase"] == "dispatched"
