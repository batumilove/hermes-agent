"""Tests for Telegram text message aggregation.

When a user sends a long message, Telegram clients split it into multiple
updates.  The TelegramAdapter should buffer rapid successive text messages
from the same session and aggregate them before dispatching.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SessionSource
from gateway.session import build_session_key


def _make_adapter():
    """Create a minimal TelegramAdapter for testing text batching."""
    from plugins.platforms.telegram.adapter import TelegramAdapter

    config = PlatformConfig(enabled=True, token="test-token")
    adapter = object.__new__(TelegramAdapter)
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter.config = config
    adapter._running = True
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._fatal_error_retryable = True
    adapter._drop_delayed_deliveries = False
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._text_recovery_enqueue_lock = asyncio.Lock()
    adapter._text_recovery_lane_locks = {}
    adapter._text_recovery_tasks = set()
    adapter._pending_photo_batches = {}
    adapter._pending_photo_batch_tasks = {}
    adapter._media_group_events = {}
    adapter._media_group_tasks = {}
    adapter._polling_error_task = None
    adapter._polling_heartbeat_task = None
    adapter._app = None
    adapter._bot = None
    adapter._set_status_indicator = AsyncMock()
    adapter._release_platform_lock = lambda: None
    adapter._text_batch_delay_seconds = 0.1  # fast for tests
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    adapter._message_handler = AsyncMock()
    adapter.handle_message = AsyncMock()
    return adapter


def _make_event(text: str, chat_id: str = "12345") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, chat_type="dm"),
    )


class TestTextBatching:
    @pytest.mark.asyncio
    async def test_single_message_dispatched_after_delay(self):
        adapter = _make_adapter()
        event = _make_event("hello world")

        adapter._enqueue_text_event(event)

        # Not dispatched yet
        adapter.handle_message.assert_not_called()

        # Wait for flush
        await asyncio.sleep(0.2)

        adapter.handle_message.assert_called_once()
        dispatched = adapter.handle_message.call_args[0][0]
        assert dispatched.text == "hello world"

    @pytest.mark.asyncio
    async def test_split_messages_aggregated(self):
        """Two rapid messages from the same chat should be merged."""
        adapter = _make_adapter()

        adapter._enqueue_text_event(_make_event("This is part one of a long"))
        await asyncio.sleep(0.02)  # small gap, within batch window
        adapter._enqueue_text_event(_make_event("message that was split by Telegram."))

        # Not dispatched yet (timer restarted)
        adapter.handle_message.assert_not_called()

        # Wait for flush
        await asyncio.sleep(0.2)

        adapter.handle_message.assert_called_once()
        dispatched = adapter.handle_message.call_args[0][0]
        assert "part one" in dispatched.text
        assert "split by Telegram" in dispatched.text

    @pytest.mark.asyncio
    async def test_three_way_split_aggregated(self):
        """Three rapid messages should all merge."""
        adapter = _make_adapter()

        adapter._enqueue_text_event(_make_event("chunk 1"))
        await asyncio.sleep(0.02)
        adapter._enqueue_text_event(_make_event("chunk 2"))
        await asyncio.sleep(0.02)
        adapter._enqueue_text_event(_make_event("chunk 3"))

        await asyncio.sleep(0.2)

        adapter.handle_message.assert_called_once()
        text = adapter.handle_message.call_args[0][0].text
        assert "chunk 1" in text
        assert "chunk 2" in text
        assert "chunk 3" in text

    @pytest.mark.asyncio
    async def test_topic_recovery_does_not_block_event_loop(self):
        adapter = _make_adapter()
        heartbeat_ran = asyncio.Event()
        heartbeat_seen = []
        loop = asyncio.get_running_loop()

        def recover(_source):
            heartbeat_seen.append(
                asyncio.run_coroutine_threadsafe(
                    heartbeat_ran.wait(), loop
                ).result(timeout=1)
            )
            return "222"

        adapter.set_topic_recovery_fn(recover)

        async def heartbeat():
            await asyncio.sleep(0)
            heartbeat_ran.set()

        event = _make_event("heartbeat")
        await asyncio.gather(
            adapter._recover_and_enqueue_text_event(event),
            heartbeat(),
        )

        assert heartbeat_seen == [True]
        assert event.source.thread_id == "222"
        await asyncio.sleep(0.2)

    @pytest.mark.asyncio
    async def test_topic_recovery_is_fair_across_unrelated_raw_lanes(self):
        adapter = _make_adapter()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def recover(source):
            if source.chat_id == "chat-a":
                loop.call_soon_threadsafe(first_started.set)
                asyncio.run_coroutine_threadsafe(
                    release_first.wait(), loop
                ).result(timeout=2)
            else:
                loop.call_soon_threadsafe(second_started.set)
            return "222"

        adapter.set_topic_recovery_fn(recover)
        first = asyncio.create_task(
            adapter._recover_and_enqueue_text_event(_make_event("a", "chat-a"))
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second = asyncio.create_task(
            adapter._recover_and_enqueue_text_event(_make_event("b", "chat-b"))
        )

        await asyncio.wait_for(second_started.wait(), timeout=0.25)
        release_first.set()
        await asyncio.gather(first, second)

        assert len(adapter._pending_text_batches) == 2
        assert adapter._text_recovery_lane_locks == {}
        assert adapter._text_recovery_tasks == set()
        await asyncio.sleep(0.2)

    @pytest.mark.asyncio
    async def test_repeated_cancellation_drains_topic_recovery_worker(self):
        adapter = _make_adapter()
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()

        def recover(_source):
            loop.call_soon_threadsafe(started.set)
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=2)
            loop.call_soon_threadsafe(finished.set)
            return "222"

        adapter.set_topic_recovery_fn(recover)
        task = asyncio.create_task(
            adapter._recover_and_enqueue_text_event(_make_event("cancelled"))
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert task in adapter._text_recovery_tasks

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert finished.is_set()
        assert adapter._pending_text_batches == {}
        assert adapter._text_recovery_lane_locks == {}
        assert adapter._text_recovery_tasks == set()

    @pytest.mark.asyncio
    async def test_disconnect_drains_topic_recovery_and_drops_late_enqueue(self):
        adapter = _make_adapter()
        started = asyncio.Event()
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def recover(_source):
            loop.call_soon_threadsafe(started.set)
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=2)
            return "222"

        adapter.set_topic_recovery_fn(recover)
        recovery = asyncio.create_task(
            adapter._recover_and_enqueue_text_event(_make_event("stale"))
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        disconnect = asyncio.create_task(adapter.disconnect())
        await asyncio.sleep(0.05)
        assert not disconnect.done()

        release.set()
        await asyncio.gather(recovery, disconnect)

        adapter.handle_message.assert_not_called()
        assert adapter._pending_text_batches == {}
        assert adapter._text_recovery_lane_locks == {}
        assert adapter._text_recovery_tasks == set()


    @pytest.mark.asyncio
    async def test_disconnected_adapter_drops_pending_media_group_flush_before_dispatch(self):
        """A pending media group should not dispatch after disconnect starts."""
        from plugins.platforms.telegram.adapter import TelegramAdapter

        adapter = _make_adapter()
        event = _make_event("album caption")
        event.media_urls = ["/tmp/photo.jpg"]
        event.media_types = ["image/jpeg"]

        with patch.object(TelegramAdapter, "MEDIA_GROUP_WAIT_SECONDS", 0.1):
            await adapter._queue_media_group_event("album-1", event)
            adapter._mark_disconnected()
            await asyncio.sleep(0.2)

        adapter.handle_message.assert_not_called()
        assert adapter._media_group_events == {}
        assert adapter._media_group_tasks == {}


    @pytest.mark.asyncio
    async def test_disconnect_cancels_all_pending_delivery_task_maps(self):
        """Photo/media/polling delayed tasks are awaited and queues are cleared."""
        adapter = _make_adapter()
        tasks = [asyncio.create_task(asyncio.sleep(0.2)) for _ in range(4)]
        adapter._pending_text_batches["text"] = _make_event("text")
        adapter._pending_text_batch_tasks["text"] = tasks[0]
        adapter._pending_photo_batches["photo"] = _make_event("photo")
        adapter._pending_photo_batch_tasks["photo"] = tasks[1]
        adapter._media_group_events["media"] = _make_event("media")
        adapter._media_group_tasks["media"] = tasks[2]
        adapter._polling_error_task = tasks[3]

        await adapter.disconnect()

        assert all(task.done() for task in tasks)
        assert adapter._pending_text_batches == {}
        assert adapter._pending_text_batch_tasks == {}
        assert adapter._pending_photo_batches == {}
        assert adapter._pending_photo_batch_tasks == {}
        assert adapter._media_group_events == {}
        assert adapter._media_group_tasks == {}
        assert adapter._polling_error_task is None
