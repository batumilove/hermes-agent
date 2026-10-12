"""Real worker, real SQLite, fake platform boundary (no model/network calls)."""
import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from hermes_state import SessionDB
from gateway import delivery_recovery as recovery
from gateway import delivery_ledger as ledger
from gateway.config import Platform
from gateway.post_response import PostResponseWorker


@pytest.fixture
def case(tmp_path):
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_final_delivery_and_barrier(
        obligation_id='oid', session_key='route', session_lineage='lineage',
        platform='slack', chat_id='chat', thread_id='thread', content='answer',
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
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.01)
    assert predicate(), 'worker did not reach expected boundary'


def runner_for(db, send):
    runner = SimpleNamespace()
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
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'error', 'negative', 'cancel'])
async def test_real_worker_consumer_claim_send_settlement(case, outcome):
    assert hasattr(recovery, 'consume_requests'), 'missing working owner-loop consumer'
    sends = []
    loop_thread = threading.get_ident()
    async def send(**kw):
        assert threading.get_ident() == loop_thread
        assert state(case) == 'abandoned'  # durable ambiguity fence before send
        with sqlite3.connect(case.db_path) as c:
            assert c.execute("SELECT count(*) FROM delivery_recovery_executions WHERE status='sending'").fetchone()[0] == 1
        assert 'possible duplicate' in kw['content'].lower()
        assert kw['content'].endswith('answer')
        assert kw['metadata'] == {'thread_id': 'thread'}
        sends.append(kw)
        if outcome == 'error':
            raise OSError('accepted remotely then connection lost')
        if outcome == 'cancel':
            raise asyncio.CancelledError()
        return SimpleNamespace(success=outcome == 'success')
    runner = runner_for(case, send)
    worker = PostResponseWorker(runner)
    worker._ACTION_POLL_SECONDS = .01
    runner._post_response_controller = worker
    decide(case)
    worker.start()
    try:
        await until(lambda: bool(sends))
        await asyncio.sleep(.1)
        assert len(sends) == 1
        assert runner.session_store.token == 'new'
        if outcome == 'success':
            await until(lambda: not case.has_post_response_barrier('route', 'lineage'))
            assert state(case) == 'delivered'
        else:
            assert state(case) == 'abandoned'
            assert case.has_post_response_barrier('route', 'lineage')
            decide(case)  # fresh acknowledgement, never automatic replay
            await until(lambda: len(sends) == 2)
    finally:
        worker.shutdown(1)


@pytest.mark.asyncio
async def test_racing_consumers_and_inflight_decision(case):
    assert hasattr(recovery, 'consume_requests'), 'missing working owner-loop consumer'
    entered, release = asyncio.Event(), asyncio.Event()
    sends = []
    async def send(**kw):
        sends.append(kw)
        entered.set()
        await release.wait()
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    first = asyncio.create_task(recovery.consume_requests(runner, case.db_path))
    await entered.wait()
    with pytest.raises(ValueError):
        decide(case)
    await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == 1
    release.set()
    await first
    assert state(case) == 'delivered'


@pytest.mark.asyncio
async def test_crash_claim_not_replayed_and_requires_new_decision(case, monkeypatch):
    assert hasattr(recovery, 'claim_retry'), 'missing durable pre-send claim'
    request = decide(case)
    claim = recovery.claim_retry(case.db_path, request['request_id'])
    assert claim
    sends = []
    async def send(**kw):
        sends.append(kw)
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    await recovery.consume_requests(runner, case.db_path)
    assert not sends
    monkeypatch.setattr(ledger, '_owner_alive', lambda *a: False)
    # Simulate a new process identity; receipt and execution retain dead owner.
    original = recovery._owner_stamp()
    monkeypatch.setattr(recovery, '_owner_stamp', lambda: (original[0], original[1] + 1))
    decide(case)
    await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == 1
    assert state(case) == 'delivered'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['receipt', 'action', 'settlement'])
async def test_stale_identity_never_sends_or_finalizes(case, mutation):
    assert hasattr(recovery, 'consume_requests'), 'missing working owner-loop consumer'
    sends = []
    async def send(**kw):
        sends.append(kw)
        with sqlite3.connect(case.db_path) as c:
            c.execute("UPDATE delivery_obligations SET content='newer'")
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    with sqlite3.connect(case.db_path) as c:
        if mutation == 'receipt':
            c.execute("UPDATE delivery_obligations SET content='changed'")
        elif mutation == 'action':
            c.execute("UPDATE gateway_post_response_actions SET payload_json='{}'")
    await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == (1 if mutation == 'settlement' else 0)
    assert state(case) != 'delivered'
    assert case.has_post_response_barrier('route', 'lineage')


@pytest.mark.asyncio
async def test_stale_request_history_cannot_starve_valid_decision(case):
    decide(case)
    with sqlite3.connect(case.db_path) as c:
        for i in range(40):
            c.execute('''INSERT INTO delivery_recovery_requests
                SELECT ?, obligation_id, ?, operator, acknowledgement, status,
                       requested_at+100, reviewed_at, owner_pid, owner_started_at
                FROM delivery_recovery_requests LIMIT 1''',
                (f'history-{i:02}', f'stale-{i}'))
    sends = []
    async def send(**kw):
        sends.append(kw)
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    for _ in range(4):
        await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == 1
    assert state(case) == 'delivered'


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_recovery_without_retry(case):
    entered = asyncio.Event()
    async def send(**kw):
        entered.set()
        await asyncio.Event().wait()
    runner = runner_for(case, send)
    worker = PostResponseWorker(runner)
    worker._ACTION_POLL_SECONDS = .01
    runner._post_response_controller = worker
    decide(case)
    worker.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert worker.shutdown(1)
        await asyncio.sleep(.05)
        assert worker._recovery_future is not None
        assert worker._recovery_future.done()
        assert state(case) == 'abandoned'
        assert case.has_post_response_barrier('route', 'lineage')
    finally:
        if worker._recovery_future is not None:
            worker._recovery_future.cancel()
        await asyncio.sleep(.01)


def test_startup_sweep_and_prune_preserve_crash_fence(case, monkeypatch):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    request = decide(case)
    claim = recovery.claim_retry(case.db_path, request['request_id'])
    monkeypatch.setattr(ledger, '_owner_alive', lambda *a: False)
    token = set_hermes_home_override(str(case.db_path.parent))
    try:
        assert ledger.sweep_recoverable(now=10**12) == []
        ledger._prune(now=10**12)
        assert state(case) == 'abandoned'
        assert case.has_post_response_barrier('route', 'lineage')
        assert recovery.claim_retry(case.db_path, request['request_id']) is None
    finally:
        reset_hermes_home_override(token)
