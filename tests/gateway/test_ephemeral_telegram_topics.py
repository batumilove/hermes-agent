"""Contracts for caller-owned ephemeral Telegram DM topics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron.scheduler import (
    _deliver_result,
    _maybe_assign_fresh_telegram_cron_thread,
)
from gateway.config import GatewayConfig, Platform
from gateway.delivery import DeliveryRouter, DeliveryTarget
from tests.gateway.test_dm_topics import _make_adapter


def test_cron_topic_generation_is_config_only_and_private_chat_scoped():
    config = {"cron": {"telegram_new_thread_per_output": True}}
    private = {"platform": "telegram", "chat_id": "722341991", "thread_id": "old"}

    adjusted = _maybe_assign_fresh_telegram_cron_thread(
        {"id": "abcdef123456", "name": "Nightly report"},
        private,
        config,
    )

    assert adjusted is not private
    assert adjusted["thread_id"].startswith("Cron: Nightly report · abcdef12 · ")
    assert len(adjusted["thread_id"]) <= 128
    assert (
        _maybe_assign_fresh_telegram_cron_thread(
            {"id": "job"},
            {"platform": "telegram", "chat_id": "-100123", "thread_id": "17"},
            config,
        )["thread_id"]
        == "17"
    )
    assert (
        _maybe_assign_fresh_telegram_cron_thread(
            {"id": "job"},
            private,
            config,
            can_create_named_dm_topic=False,
        )
        is private
    )


@pytest.mark.asyncio
async def test_delivery_router_consumes_marker_and_disables_persistence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("gateway.delivery.get_hermes_home", lambda: tmp_path)

    class Adapter:
        def __init__(self):
            self.ensure_calls = []
            self.send_calls = []

        async def ensure_dm_topic(
            self,
            chat_id,
            topic_name,
            force_create=False,
            persist=True,
        ):
            self.ensure_calls.append(
                {
                    "chat_id": chat_id,
                    "topic_name": topic_name,
                    "force_create": force_create,
                    "persist": persist,
                }
            )
            return "38049"

        async def send(self, chat_id, content, metadata=None):
            self.send_calls.append(dict(metadata or {}))
            return {"success": True}

    adapter = Adapter()
    router = DeliveryRouter(
        GatewayConfig(),
        adapters={Platform.TELEGRAM: adapter},
    )

    await router._deliver_to_platform(
        DeliveryTarget.parse("telegram:722341991:Ephemeral Report"),
        "hello",
        metadata={"job_id": "job1", "_telegram_ephemeral_dm_topic": True},
    )

    assert adapter.ensure_calls == [
        {
            "chat_id": "722341991",
            "topic_name": "Ephemeral Report",
            "force_create": False,
            "persist": False,
        }
    ]
    assert adapter.send_calls == [
        {
            "job_id": "job1",
            "thread_id": "38049",
            "telegram_dm_topic_created_for_send": True,
        }
    ]


@pytest.mark.asyncio
async def test_adapter_persist_false_leaves_named_topic_state_unchanged():
    adapter = _make_adapter()
    adapter._bot = MagicMock()
    adapter._bot.create_forum_topic = AsyncMock(
        return_value=SimpleNamespace(message_thread_id=777)
    )
    adapter._persist_dm_topic_thread_id = MagicMock()
    adapter._dm_topics = {"111:Report": 500}
    adapter._dm_topics_config = [
        {"chat_id": 111, "topics": [{"name": "Report", "thread_id": 500}]}
    ]

    result = await adapter.ensure_dm_topic("111", "Report", persist=False)

    assert result == "777"
    assert adapter._dm_topics == {"111:Report": 500}
    assert adapter._dm_topics_config == [
        {"chat_id": 111, "topics": [{"name": "Report", "thread_id": 500}]}
    ]
    adapter._persist_dm_topic_thread_id.assert_not_called()


def test_scheduler_marks_only_generated_named_topic_as_ephemeral():
    config = MagicMock()
    platform_config = MagicMock(enabled=True)
    platform_config.extra = {}
    config.platforms = {Platform.TELEGRAM: platform_config}

    class Loop:
        def is_running(self):
            return True

    class Future:
        def __init__(self, coro):
            self.coro = coro

        def result(self, timeout=None):
            import asyncio

            return asyncio.run(self.coro)

        def cancel(self):
            return True

    deliver = AsyncMock(return_value=SimpleNamespace(success=True, raw_response=None))
    with (
        patch("gateway.config.load_gateway_config", return_value=config),
        patch(
            "cron.scheduler.load_config",
            return_value={
                "cron": {
                    "wrap_response": False,
                    "telegram_new_thread_per_output": True,
                }
            },
        ),
        patch(
            "gateway.delivery.DeliveryRouter._deliver_to_platform",
            new=deliver,
        ),
        patch(
            "agent.async_utils.safe_schedule_threadsafe",
            side_effect=lambda coro, loop: Future(coro),
        ),
    ):
        error = _deliver_result(
            {
                "id": "job1",
                "name": "Report",
                "deliver": "origin",
                "origin": {
                    "platform": "telegram",
                    "chat_id": "722341991",
                    "thread_id": "104564",
                },
            },
            "hello",
            adapters={Platform.TELEGRAM: MagicMock()},
            loop=Loop(),
        )

    assert error is None
    route_metadata = deliver.await_args.args[2]
    assert route_metadata == {
        "job_id": "job1",
        "_telegram_ephemeral_dm_topic": True,
    }
