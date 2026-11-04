"""Scoped database fairness and actual writer lifetime for acknowledged retries."""

import asyncio
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from gateway import delivery_recovery as recovery
from gateway import delivery_recovery_consumer as consumer
from gateway.config import Platform
from gateway.post_response import PostResponseWorker
from hermes_constants import get_hermes_home
from hermes_state import SessionDB
from tests.gateway.delivery_recovery_fixtures import decide, runner_for, state, until

pytest_plugins = ['tests.gateway.delivery_recovery_fixtures']


@pytest.mark.asyncio
async def test_registered_databases_progress_while_another_profiles_send_is_stuck(tmp_path, monkeypatch):
    from agent.secret_scope import get_secret

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    dbs = {}
    calls = []
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    runner = None
    worker = None
    try:
        for profile in ('default', 'a', 'b'):
            profile_home = home if profile == 'default' else home / 'profiles' / profile
            profile_home.mkdir(parents=True, exist_ok=True)
            (profile_home / '.env').write_text(f'OPENAI_API_KEY=synthetic-{profile}\n')
            db = dbs[profile] = SessionDB(db_path=profile_home / 'state.db')
            db.create_final_delivery_and_barrier(
                obligation_id='oid', session_key=f'agent:{profile}:route', session_lineage='lineage',
                platform='slack', chat_id='synthetic-chat', thread_id=None, content='saved answer',
                adapter_profile=profile, actions=[{'action_kind': 'finalize_turn', 'payload': {}}])
            with sqlite3.connect(db.db_path) as conn:
                conn.execute("UPDATE delivery_obligations SET state='failed'")
            decide(db)

        def adapter(profile):
            async def send(**kwargs):
                assert get_hermes_home().resolve() == dbs[profile].db_path.parent.resolve()
                assert get_secret('OPENAI_API_KEY') == f'synthetic-{profile}'
                calls.append(profile)
                if profile == 'a':
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        await release.wait()  # Simulate a transport still unwinding.
                return SimpleNamespace(success=True)
            return SimpleNamespace(send=send)

        runner = runner_for(dbs['default'], adapter('default').send)
        runner._profile_adapters = {name: {Platform.SLACK: adapter(name)} for name in ('a', 'b')}
        class Store:
            @property
            def _db(self):
                return next(db for db in dbs.values() if db.db_path.parent == get_hermes_home())
            def clear_turn_active(self, *args):
                return False
            def peek_active_turn_token(self, *args):
                return 'new-token'
        runner.session_store = Store()
        worker = runner._post_response_controller = PostResponseWorker(runner)
        worker._ACTION_POLL_SECONDS = .01
        worker.start()
        await asyncio.wait_for(entered.wait(), 5)
        await until(lambda: state(dbs['b']) == 'delivered' and state(dbs['default']) == 'delivered')
        assert sorted(calls) == ['a', 'b', 'default']
        assert not worker.shutdown(1), 'A suspended transport still owns recovery lifetime'
        await asyncio.wait_for(cancelled.wait(), 5)
        assert not worker._recovery.is_idle()
        release.set()
        await until(worker._recovery.is_idle)
        assert worker.shutdown(0)
        assert calls.count('a') == 1
    finally:
        release.set()
        if worker is not None:
            worker.shutdown(1)
            await until(worker._recovery.is_idle)
        if runner is not None:
            runner._shutdown_executor(drain_timeout=2)
        for db in dbs.values():
            db.close()


@pytest.mark.asyncio
async def test_cancelled_sqlite_claim_is_retired_without_network_io(case, monkeypatch):
    import threading

    committed, release = threading.Event(), threading.Event()
    original = recovery.claim_retry
    def claim(*args):
        result = original(*args)
        committed.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(recovery, 'claim_retry', claim)
    sends = []
    async def send(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(success=True)
    runner = runner_for(case, send)
    decide(case)
    task = asyncio.create_task(consumer.consume_requests(runner, case.db_path))
    try:
        await until(committed.is_set)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not sends
    assert state(case) == 'abandoned'
    with sqlite3.connect(case.db_path) as conn:
        assert conn.execute('SELECT status FROM delivery_recovery_executions').fetchone()[0] == 'uncertain'
    decide(case)  # A fresh acknowledgement is required even though cancellation prevented send.


@pytest.mark.asyncio
async def test_executor_closing_refuses_consumption_before_any_claim(case):
    async def send(**kwargs):
        pytest.fail('closing gateway must not send')
    runner = runner_for(case, send)
    decide(case)
    runner._get_executor()
    runner._shutdown_executor(drain_timeout=0)
    await consumer.consume_requests(runner, case.db_path)
    assert state(case) == 'failed'
    with sqlite3.connect(case.db_path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_executions'").fetchone()

@pytest.mark.asyncio
async def test_poll_without_an_authorized_request_does_not_hydrate_credentials(case, monkeypatch):
    from gateway import run
    def refuse_hydration(*args):
        pytest.fail('an empty recovery poll must not invoke credential sources')
    monkeypatch.setattr(run, '_load_profile_secret_scope', refuse_hydration)
    async def send(**kwargs):
        pytest.fail('no operator decision exists')
    await consumer.consume_requests(runner_for(case, send), case.db_path)
    assert state(case) == 'failed'


@pytest.mark.parametrize('owner', [None, '', '  '])
def test_receipt_without_durable_bot_owner_refuses_request(case, owner):
    with sqlite3.connect(case.db_path) as conn:
        conn.execute('UPDATE delivery_obligations SET adapter_profile=?', (owner,))
    with pytest.raises(ValueError, match='durable delivery adapter owner'):
        decide(case)
    assert state(case) == 'failed'


def test_tombstoned_profile_database_refuses_inspection_and_request(tmp_path, monkeypatch):
    from hermes_constants import mark_named_profile_deleted
    root = tmp_path / '.hermes'
    home = root / 'profiles' / 'deleted-profile'
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    db = SessionDB(db_path=home / 'state.db')
    try:
        mark_named_profile_deleted(home)
        with pytest.raises(FileNotFoundError, match='Named profile home'):
            recovery.inspect_obligation(db.db_path, 'oid')
        with pytest.raises(FileNotFoundError, match='Named profile home'):
            recovery.request_retry(db.db_path, 'oid', fingerprint='0' * 64,
                acknowledge_possible_duplicate=True, operator='synthetic-operator')
    finally:
        db.close()
