"""Offline strict scheduler routing contracts; real modules, synthetic send boundary."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from gateway.config import Platform
from cron.scheduler_delivery import _live_route_metadata
from plugins.platforms.telegram.adapter import TelegramAdapter
from plugins.platforms.telegram.adapter_thread_retry import retry_missing_thread
from tools import send_message_senders as senders


def test_scheduler_marks_topic_strict(monkeypatch):
    import cron.scheduler_delivery as delivery
    monkeypatch.setattr(delivery, '_is_channel_dm_topic', lambda *a: False)
    monkeypatch.setattr(delivery._origin, 'stamp_origin_discriminators', lambda *a: None)
    t = NS(job={'id': 'synthetic'}, thread_id='333907', platform=Platform.TELEGRAM,
           chat_id='407304892', runtime_adapter=None, loop=None, notify_delivery=True,
           fresh_telegram_topic_assigned=False)
    _, text, media = _live_route_metadata(t)
    assert text.get('strict_topic') is True
    assert media.get('strict_topic') is True


def test_scheduler_new_topic_preference_cannot_replace_explicit_topic():
    from cron.scheduler_topics import _maybe_assign_fresh_telegram_cron_thread
    target = {'platform': 'telegram', 'chat_id': '407304892', 'thread_id': '333907'}
    result = _maybe_assign_fresh_telegram_cron_thread({'id': 'synthetic'}, target,
        {'cron': {'telegram_new_thread_per_output': True}})
    assert result == target


def test_live_thread_rejection_never_discards_strict_topic():
    adapter = NS(name='test', _prune_stale_dm_topic_binding=lambda *a, **k: pytest.fail('pruned'))
    result = retry_missing_thread(adapter, ValueError('Message thread not found'), '-100123', None,
        {'strict_topic': True}, False, 333907, {'message_thread_id': 333907}, False, True)
    assert result.success is False


def test_live_media_strict_topic_rejects_anchor_fallback():
    from telegram.error import BadRequest
    assert not TelegramAdapter._should_retry_without_dm_topic_reply_anchor(
        BadRequest('Message thread not found'),
        {'strict_topic': True, 'telegram_dm_topic_reply_fallback': True, 'direct_messages_topic_id': '333907'}, None)


@pytest.mark.parametrize('topic', [None, '', '1'])
def test_live_strict_topic_cannot_normalize_to_root(topic):
    with pytest.raises(ValueError, match='topic'):
        TelegramAdapter._thread_kwargs_for_send('-100123', topic, {'strict_topic': True})


def test_platform_dispatch_carries_strict_topic(monkeypatch):
    from tools import send_message_tool as tool
    sender = AsyncMock(return_value={'error': 'rejected'})
    monkeypatch.setattr(tool, '_send_telegram', sender)
    asyncio.run(tool._send_to_platform(Platform.TELEGRAM, NS(token='synthetic', extra={}),
        '-100123', 'alert', thread_id='333907', strict_topic=True))
    assert sender.call_args.kwargs['strict_topic'] is True


def test_scheduler_inchannel_cannot_flatten_explicit_telegram_topic(monkeypatch):
    import cron.scheduler_delivery as d
    origin = {'platform': 'telegram', 'chat_id': '-100123', 'thread_id': '333907'}
    adapter = NS(supports_inchannel_continuable=True)
    monkeypatch.setattr(d, '_resolve_origin', lambda job: origin)
    monkeypatch.setattr(d, '_resolve_target_transport', lambda *a: ((None, NS(extra={}), adapter, {}), None))
    monkeypatch.setattr(d, '_maybe_assign_fresh_telegram_cron_thread', lambda job, target, *a, **k: target)
    monkeypatch.setattr(d, '_resolve_cron_surface_mode', lambda *a: 'in_channel')
    target = d._prepare_target_delivery({'id': 'synthetic'}, dict(origin), adapters={},
        loop=NS(is_running=lambda: True), config=NS(), notify_delivery=True,
        mirror_enabled=False, mirror_text='', delivery_errors=[])
    assert target.thread_id == '333907'
    assert target.in_channel_surface is False


@pytest.mark.parametrize('media', [False, True])
def test_standalone_strict_rejection_never_sends_root(monkeypatch, tmp_path, media):
    calls = []
    async def reject(*a, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get('message_thread_id'):
            raise ValueError('Message thread not found')
        return NS(message_id=1)
    monkeypatch.setattr(senders, '_send_telegram_message_with_retry', reject)
    monkeypatch.setattr(senders, '_telegram_send_media', reject)
    monkeypatch.setattr(senders, '_telegram_bot', lambda token: NS())
    monkeypatch.setattr(senders, '_telegram_format', lambda message: (message, None, False))
    path = tmp_path / 'test.bin'
    path.write_bytes(b'synthetic')
    result = asyncio.run(senders._send_telegram('synthetic', '-100123', 'alert',
        media_files=[(str(path), False)] if media else [], thread_id='333907', strict_topic=True))
    assert 'error' in result
    assert calls and all(c.get('message_thread_id') == 333907 for c in calls)


def test_standalone_interactive_fallback_unchanged(monkeypatch):
    calls = []
    async def reject(*a, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get('message_thread_id'):
            raise ValueError('Message thread not found')
        return NS(message_id=1)
    monkeypatch.setattr(senders, '_send_telegram_message_with_retry', reject)
    asyncio.run(senders._telegram_send_text_chunk(NS(), '-100123', 'alert', None, False,
                                                {'message_thread_id': 333907}))
    assert len(calls) == 2 and 'message_thread_id' not in calls[-1]
