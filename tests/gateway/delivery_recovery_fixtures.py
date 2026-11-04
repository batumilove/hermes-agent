"""Temporary SQLite receipts and real gateway-executor test ownership."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from gateway import delivery_recovery as recovery
from gateway.config import Platform
from hermes_state import SessionDB
from tests.waiting import async_wait_until

_RUNNERS = []


@pytest.fixture
def case(tmp_path):
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_final_delivery_and_barrier(
        obligation_id='oid', session_key='route', session_lineage='lineage',
        platform='slack', chat_id='chat', thread_id='thread', content='answer', adapter_profile='default',
        actions=[{'action_kind': 'finalize_turn', 'payload': {
            'session_key': 'route', 'active_turn_token': 'old', 'session_id': 'sid'}}])
    with sqlite3.connect(db.db_path) as c:
        c.execute("UPDATE delivery_obligations SET state='failed',attempts=3")
    yield db
    db.close()


def decide(db):
    return recovery.request_retry(db.db_path, 'oid',
        fingerprint=recovery.inspect_obligation(db.db_path, 'oid')['fingerprint'],
        acknowledge_possible_duplicate=True, operator='test')


def state(db):
    return recovery.inspect_obligation(db.db_path, 'oid')['state']


async def until(predicate):
    await async_wait_until(predicate, timeout=5)


def runner_for(db, send):
    # weakref-able runner, using the actual finalizer for token isolation.
    from gateway.run import GatewayRunner
    runner = GatewayRunner.__new__(GatewayRunner)
    class Store:
        _db = db
        token = 'new'
        def clear_turn_active(self, key, token):
            if self.token != token:
                return False
            self.token = None
            return True
        def peek_active_turn_token(self, key):
            return self.token
    runner.session_store = Store()
    runner._rebaseline_agent_cache_message_count_sync = lambda *a: None
    runner._gateway_loop = asyncio.get_running_loop()
    runner._running = True
    runner.adapters = {Platform.SLACK: SimpleNamespace(send=send)}
    runner._primary_profile_name = 'default'
    _RUNNERS.append(runner)
    return runner


@pytest.fixture(autouse=True)
def cleanup_recovery_runners():
    yield
    for runner in _RUNNERS:
        runner._shutdown_executor(drain_timeout=2)
    _RUNNERS.clear()
