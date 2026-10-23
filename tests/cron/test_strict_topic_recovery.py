"""Durable scheduled routing must survive both recovery claim paths."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from gateway import delivery_ledger as ledger
from gateway.config import Platform
from cron import scheduler_delivery as delivery


@pytest.mark.parametrize('startup', [False, True])
@pytest.mark.parametrize('native', [False, True])
def test_scheduled_route_survives_durable_claim(tmp_path, monkeypatch, startup, native):
    monkeypatch.setattr(ledger, '_db_path', lambda: tmp_path / 'state.db')
    monkeypatch.setattr(ledger, '_owner_stamp', lambda: (42, 123))
    monkeypatch.setattr(ledger, '_owner_alive', lambda *a: False)
    monkeypatch.setattr(ledger, 'ledger_enabled', lambda: True)
    metadata = {'thread_id': '333907', 'strict_topic': True}
    if native:
        metadata.update(direct_messages_topic_id='333907', telegram_dm_topic=True)
    monkeypatch.setattr(delivery, '_live_route_metadata', lambda t: (None, metadata, metadata))
    target = NS(live_error='send_path_degraded', platform_name='telegram',
                platform=Platform.TELEGRAM, chat_id='-100123', thread_id='333907',
                job={'id': 'synthetic'}, transport=NS(adapter=None), where='synthetic')
    errors = []
    from cron.scheduler_reconnect import _queue_for_live_reconnect
    _queue_for_live_reconnect(target, 'alert', [], errors)
    # A fresh connection/claim reads the committed SQLite row, not producer memory.
    rows = (ledger.sweep_recoverable() if startup
            else ledger.sweep_failed_for_runtime('telegram'))
    assert len(rows) == 1
    assert rows[0].get('routing_metadata') == metadata
    from gateway.run import GatewayRunner
    from gateway.platforms.base import SendResult
    from unittest.mock import AsyncMock
    runner = object.__new__(GatewayRunner)
    calls = []
    async def reject(**kwargs):
        calls.append(kwargs)
        assert kwargs['metadata'] == metadata
        return SendResult(success=False, error='Message thread not found')
    adapter = NS(send=reject)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._authorization_adapter = lambda *a: adapter
    runner._arm_delivery_retry_timers = AsyncMock()
    async def run_operation(operation, *args, **kwargs):
        return operation(*args, **kwargs)
    runner._run_delivery_ledger_sync = run_operation
    assert asyncio.run(runner._redeliver_claimed_obligations(rows)) == 0
    assert len(calls) == 1


@pytest.mark.parametrize('startup', [True, False])
@pytest.mark.parametrize('content', ['legacy alert', 'legacy alert MEDIA:/unavailable/photo.jpg'])
@pytest.mark.parametrize('scheduled', [True, False])
def test_legacy_null_route_recovery_fails_closed_only_for_scheduled_topic(
        tmp_path, monkeypatch, content, scheduled, startup):
    from unittest.mock import AsyncMock
    from gateway.run import GatewayRunner
    from gateway.platforms.base import SendResult
    import sqlite3

    path = tmp_path / 'legacy.db'
    monkeypatch.setattr(ledger, '_db_path', lambda: path)
    monkeypatch.setattr(ledger, '_owner_alive', lambda *a: False)
    key = 'cron:telegram:-100123:333907' if scheduled else 'interactive'
    ledger.record_obligation(obligation_id='legacy', session_key=key,
                             platform='telegram', chat_id='-100123',
                             thread_id='333907', content=content)
    # Recreate the genuine pre-column database, then reopen through migration.
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE delivery_obligations DROP COLUMN routing_metadata')
    if not startup:
        ledger.mark_failed('legacy', 'send_path_degraded')
    rows = (ledger.sweep_recoverable() if startup
            else ledger.sweep_failed_for_runtime('telegram'))
    assert len(rows) == 1
    assert rows[0]['routing_metadata'] == {}
    adapter = NS(send=AsyncMock(return_value=SendResult(
        success=False, error='Message thread not found')))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._authorization_adapter = lambda *a: adapter
    runner._arm_flood_timers_for_waiting_rows = AsyncMock()
    async def operation(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    runner._run_delivery_ledger_sync = operation
    assert asyncio.run(runner._redeliver_claimed_obligations(rows)) == 0
    assert adapter.send.await_count == (0 if scheduled else 1)
    with sqlite3.connect(path) as conn:
        state, error = conn.execute(
            'SELECT state,last_error FROM delivery_obligations').fetchone()
    assert state == 'failed'
    if scheduled:
        assert error == 'scheduled_topic_route_missing'


def test_interactive_ledger_does_not_acquire_strictness(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, '_db_path', lambda: tmp_path / 'state.db')
    monkeypatch.setattr(ledger, '_owner_alive', lambda *a: False)
    ledger.record_obligation(obligation_id='interactive', session_key='interactive',
                             platform='telegram', chat_id='1', thread_id='2', content='hello')
    row = ledger.sweep_recoverable()[0]
    assert not row.get('routing_metadata', {}).get('strict_topic')
