"""Acknowledged retries must retain the saved final's bot ownership (PR #385)."""
import asyncio
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from gateway.config import Platform
from gateway.run import GatewayRunner

from hermes_state import SessionDB

from gateway import delivery_recovery as recovery
from gateway import delivery_recovery_consumer as consumer

@pytest.mark.parametrize('owner_connected', [True, False])
def test_operator_retry_never_uses_another_profiles_bot(tmp_path, monkeypatch, owner_connected):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    home = tmp_path / 'home'
    home.mkdir()
    db = SessionDB(db_path=home / 'state.db')
    try:
        db.create_final_delivery_and_barrier(
            obligation_id='synthetic-obligation', session_key='agent:secondary:route',
            session_lineage='synthetic-lineage', platform='slack', chat_id='synthetic-chat',
            thread_id=None, content='synthetic-response', adapter_profile='secondary',
            actions=[{'action_kind': 'finalize_turn', 'payload': {
                'session_key': 'agent:secondary:route', 'active_turn_token': 'old',
                'session_id': 'synthetic-session'}}])
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("UPDATE delivery_obligations SET state='failed', attempts=3")
        fingerprint = recovery.inspect_obligation(db.db_path, 'synthetic-obligation')['fingerprint']
        recovery.request_retry(db.db_path, 'synthetic-obligation', fingerprint=fingerprint,
            acknowledge_possible_duplicate=True, operator='isolated-test')
        calls = []
        async def default_send(**kwargs):
            calls.append('default')
            return SimpleNamespace(success=True)
        async def secondary_send(**kwargs):
            calls.append('secondary')
            return SimpleNamespace(success=True)
        default_adapter = SimpleNamespace(send=default_send)
        secondary_adapter = SimpleNamespace(send=secondary_send)
        async def run():
            runner = GatewayRunner.__new__(GatewayRunner)
            runner._gateway_loop = asyncio.get_running_loop()
            runner._running = True
            runner.adapters = {Platform.SLACK: default_adapter}
            runner._primary_profile_name = 'default'
            runner._profile_adapters = {'secondary': {Platform.SLACK: secondary_adapter}} if owner_connected else {}
            assert runner._authorization_adapter(Platform.SLACK, 'secondary') is (secondary_adapter if owner_connected else None)
            try:
                await consumer.consume_requests(runner, db.db_path)
            finally:
                runner._shutdown_executor(drain_timeout=2)
        asyncio.run(run())
        assert calls == (['secondary'] if owner_connected else []), calls
    finally:
        db.close()
