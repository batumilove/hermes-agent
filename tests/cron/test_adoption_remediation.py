"""Real ledger/worker boundary regressions; disposable fixtures only."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
import pytest

@pytest.fixture
def worker(tmp_path, monkeypatch):
    from cron import executions as e, scheduler as s
    monkeypatch.setattr(e, 'EXECUTIONS_FILE', tmp_path / 'executions.db')
    monkeypatch.setattr('hermes_cli.plugins.discover_plugins', lambda: None)
    monkeypatch.setattr('hermes_cli.env_loader.hydrate_profile_secret_sources', lambda *a: None)
    monkeypatch.setattr('gateway.run_startup.GatewayStartupMixin._register_config_hooks', lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(s, 'run_one_job', lambda *a, **k: calls.append(a) or True)
    r = e.create_execution('boundary', source='test')
    e.mark_execution_handoff_pending(r['id'])
    p, a = tmp_path / 'payload.json', tmp_path / 'ready'
    p.write_text(json.dumps({'job': {'id': 'boundary', 'execution_id': r['id']}, 'profile_home': str(tmp_path / 'profile')}))
    return e, s, r, p, a, calls

def test_fingerprint_change_between_select_and_update_refused(worker, monkeypatch):
    e, s, r, p, a, calls = worker
    old = e._hermes_now
    def now():
        with sqlite3.connect(e.EXECUTIONS_FILE) as c:
            c.execute('UPDATE executions SET process_started_at=999 WHERE id=?', (r['id'],))
        return old()
    monkeypatch.setattr(e, '_hermes_now', now)
    assert e.adopt_claimed_execution(r['id']) is None
    assert e.get_execution(r['id'])['process_started_at'] == 999

def test_pause_before_mutation_cannot_spend_expired_deadline(worker, monkeypatch):
    e, s, r, p, a, calls = worker
    old = e._hermes_now
    def now():
        time.sleep(.05)
        return old()
    monkeypatch.setattr(e, '_hermes_now', now)
    assert e.adopt_claimed_execution(r['id'], deadline=time.monotonic() + .02) is None
    assert e.get_execution(r['id'])['status'] == 'claimed'

@pytest.mark.parametrize('phase', ['post_commit', 'pre_ack_replace', 'post_ack_replace'])
def test_concurrent_invalidation_without_exception_never_executes(worker, monkeypatch, phase):
    e, s, r, p, a, calls = worker
    def invalidate():
        # A separate thread commits the real owner-fenced cancellation. No injected exception.
        errors = []
        def run():
            try:
                assert e.finish_execution(r['id'], success=False, error='cancelled') is not None
            except BaseException as ex:
                errors.append(ex)
        t = threading.Thread(target=run)
        t.start(); t.join(3)
        assert not t.is_alive() and not errors
    if phase == 'post_commit':
        old = e._emit_execution_state
        def emit(record, *args, **kw):
            if record and record['status'] == 'running':
                invalidate()
            return old(record, *args, **kw)
        monkeypatch.setattr(e, '_emit_execution_state', emit)
    else:
        old = os.replace
        def replace(src, dst):
            if Path(dst) == a:
                if phase == 'pre_ack_replace': invalidate()
                result = old(src, dst)
                if phase == 'post_ack_replace': invalidate()
                return result
            return old(src, dst)
        monkeypatch.setattr(os, 'replace', replace)
    assert s._run_external_worker_payload(p, a) is False
    assert calls == []
    assert e.get_execution(r['id'])['status'] == 'failed'

def test_worker_expired_shared_ack_budget_refuses(worker):
    e, s, r, p, a, calls = worker
    payload = json.loads(p.read_text())
    payload['handoff_deadline'] = time.monotonic() - 1
    p.write_text(json.dumps(payload))
    assert s._run_external_worker_payload(p, a) is False
    assert not a.exists() and calls == []


def test_ack_fsync_crossing_endpoint_never_publishes_or_executes(worker, monkeypatch):
    e, s, r, p, a, calls = worker
    payload = json.loads(p.read_text())
    payload['handoff_deadline'] = time.monotonic() + .4
    p.write_text(json.dumps(payload))
    old = os.fsync
    def slow(fd):
        time.sleep(.45)
        return old(fd)
    monkeypatch.setattr(os, 'fsync', slow)
    assert s._run_external_worker_payload(p, a) is False
    assert not a.exists() and calls == []
    assert not list(a.parent.glob(a.name + '.tmp*'))
