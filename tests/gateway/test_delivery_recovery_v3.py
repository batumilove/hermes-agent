"""v3 recovery regression contracts: real SQLite and fake network boundary only."""
import asyncio
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest
from tests.gateway.test_delivery_recovery_execution import case, decide, runner_for, state, until
from gateway import delivery_recovery as recovery
from gateway import delivery_ledger as ledger
from gateway.post_response import PostResponseWorker


def rows(db, table):
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()


@pytest.mark.asyncio
async def test_real_completed_recovery_prune_does_not_poison_later_short_page(case):
    sends = []
    async def send(**kw):
        sends.append(kw)
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    request = decide(case)
    with sqlite3.connect(case.db_path) as conn:
        conn.execute("UPDATE delivery_recovery_requests SET request_id='a-orphan'")
    worker = PostResponseWorker(runner)
    worker._ACTION_POLL_SECONDS = .01
    runner._post_response_controller = worker
    worker.start()
    try:
        await until(lambda: not case.has_post_response_barrier('route', 'lineage'))
        assert state(case) == 'delivered'
    finally:
        worker.shutdown(1)
        await asyncio.sleep(.05)
    # Actual finalizer completion, then actual retention, not manual deletion.
    with sqlite3.connect(case.db_path) as conn:
        assert conn.execute('SELECT state FROM gateway_post_response_actions').fetchone()[0] == 'done'
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(str(case.db_path.parent))
    try:
        ledger._prune(now=10**12)
    finally:
        reset_hermes_home_override(token)
    assert rows(case, 'delivery_obligations') == []
    audit = rows(case, 'delivery_recovery_requests')
    assert audit[0][0] == 'a-orphan'
    case.create_final_delivery_and_barrier(
        obligation_id='later', session_key='later', session_lineage='later',
        platform='slack', chat_id='chat', thread_id=None, content='later answer',
        actions=[{'action_kind': 'finalize_turn', 'payload': {'session_key': 'later'}}])
    with sqlite3.connect(case.db_path) as conn:
        conn.execute("UPDATE delivery_obligations SET state='failed' WHERE obligation_id='later'")
    recovery.request_retry(case.db_path, 'later',
        fingerprint=recovery.inspect_obligation(case.db_path, 'later')['fingerprint'],
        acknowledge_possible_duplicate=True, operator='test')
    with sqlite3.connect(case.db_path) as conn:
        conn.execute("UPDATE delivery_recovery_requests SET request_id='z-valid' WHERE obligation_id='later'")
    runner = runner_for(case, send)
    for _ in range(4):
        await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == 2
    assert sends[-1]['content'].endswith('later answer')
    assert recovery.inspect_obligation(case.db_path, 'later')['state'] == 'delivered'
    assert rows(case, 'delivery_recovery_requests')[0] == audit[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['snapshot', 'claim'])
async def test_bad_request_isolated_from_later_request_in_same_page(case, monkeypatch, boundary):
    decide(case)
    with sqlite3.connect(case.db_path) as conn:
        conn.execute("UPDATE delivery_recovery_requests SET request_id='z-valid'")
        conn.execute('''INSERT INTO delivery_recovery_requests SELECT
            'a-bad', obligation_id, 'stale', operator, acknowledgement, status,
            requested_at, reviewed_at, owner_pid, owner_started_at
            FROM delivery_recovery_requests''')
    calls = 0
    original = recovery._snapshot if boundary == 'snapshot' else recovery.claim_retry
    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError('injected per-request read failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(recovery, '_snapshot' if boundary == 'snapshot' else 'claim_retry', fail_once)
    sends = []
    async def send(**kw):
        sends.append(kw)
        return SimpleNamespace(success=True)
    await recovery.consume_requests(runner_for(case, send), case.db_path)
    assert len(sends) == 1
    assert state(case) == 'delivered'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stale', 'settlement_error', 'cleanup_error'])
async def test_completed_send_uncertain_then_fresh_ack_single_resend(case, monkeypatch, failure):
    sends = []
    before = {}
    original_connection = recovery._connection
    failures = 0
    # Errors are raised at the real transaction boundary after send returns.
    @contextmanager
    def connection(*args, **kwargs):
        nonlocal failures
        if kwargs.get('write') and sends and failures < (2 if failure == 'cleanup_error' else 1) and failure != 'stale':
            failures += 1
            raise sqlite3.OperationalError('transient settlement/cleanup write failure')
        with original_connection(*args, **kwargs) as value:
            yield value
    monkeypatch.setattr(recovery, '_connection', connection)
    async def send(**kw):
        sends.append(kw)
        if len(sends) == 1:
            if failure == 'stale':
                with sqlite3.connect(case.db_path) as conn:
                    conn.execute('UPDATE delivery_obligations SET updated_at=updated_at+1')
            before['receipt'] = rows(case, 'delivery_obligations')
            before['actions'] = rows(case, 'gateway_post_response_actions')
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    await recovery.consume_requests(runner, case.db_path)
    assert rows(case, 'delivery_obligations') == before['receipt']
    assert rows(case, 'gateway_post_response_actions') == before['actions']
    assert rows(case, 'delivery_recovery_executions')[0][5] == 'uncertain'
    assert state(case) == 'abandoned'
    assert case.has_post_response_barrier('route', 'lineage')
    for _ in range(2):
        await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == 1  # uncertainty never authorizes replay
    decide(case)  # explicit new acknowledgement of the current fingerprint
    await asyncio.gather(*(recovery.consume_requests(runner, case.db_path) for _ in range(3)))
    assert len(sends) == 2
    assert state(case) == 'delivered'


@pytest.mark.asyncio
async def test_exhausted_cleanup_retained_for_later_poll_without_resend(case, monkeypatch):
    real_connection = recovery._connection
    broken = False
    writes = 0
    @contextmanager
    def connection(*args, **kwargs):
        nonlocal writes
        if kwargs.get('write') and broken:
            writes += 1
            raise sqlite3.OperationalError('temporarily unavailable')
        with real_connection(*args, **kwargs) as value:
            yield value
    monkeypatch.setattr(recovery, '_connection', connection)
    sends = []
    async def send(**kw):
        nonlocal broken
        sends.append(kw)
        broken = len(sends) == 1
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    await recovery.consume_requests(runner, case.db_path)
    assert 1 < writes <= 4  # bounded immediate reconciliation, not an infinite loop
    assert rows(case, 'delivery_recovery_executions')[0][5] == 'sending'
    broken = False
    with pytest.raises(ValueError, match='send still owned'):
        decide(case)  # not yet reconciled; do not admit a concurrent decision
    await recovery.consume_requests(runner, case.db_path)
    assert rows(case, 'delivery_recovery_executions')[0][5] == 'uncertain'
    assert len(sends) == 1
    decide(case)
    await recovery.consume_requests(runner, case.db_path)
    assert len(sends) == 2
    assert state(case) == 'delivered'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['fingerprint', 'owner', 'generation'])
async def test_cleanup_never_retires_changed_execution_owner_or_claim(case, monkeypatch, mutation):
    sends = []
    async def send(**kw):
        sends.append(kw)
        with sqlite3.connect(case.db_path) as conn:
            if mutation == 'fingerprint':
                conn.execute("UPDATE delivery_recovery_executions SET fingerprint='different-claim'")
            elif mutation == 'owner':
                conn.execute('UPDATE delivery_recovery_executions SET owner_pid=owner_pid+1')
            else:
                conn.execute('UPDATE delivery_recovery_executions SET owner_started_at=owner_started_at+1')
        monkeypatch.setattr(ledger, '_owner_alive', lambda *a: True)
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    await recovery.consume_requests(runner, case.db_path)
    assert rows(case, 'delivery_recovery_executions')[0][5] == 'sending'
    assert state(case) == 'abandoned'
    with pytest.raises(ValueError, match='send still owned'):
        decide(case)
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_active_send_excluded_during_concurrent_cleanup_polls(case):
    entered, release = asyncio.Event(), asyncio.Event()
    sends = []
    async def send(**kw):
        sends.append(kw)
        entered.set()
        await release.wait()
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    task = asyncio.create_task(recovery.consume_requests(runner, case.db_path))
    try:
        await entered.wait()
        for _ in range(3):
            await recovery.consume_requests(runner, case.db_path)
            with pytest.raises(ValueError, match='send still owned'):
                decide(case)
        assert rows(case, 'delivery_recovery_executions')[0][5] == 'sending'
        assert len(sends) == 1
    finally:
        release.set()
        await task
    assert state(case) == 'delivered'
