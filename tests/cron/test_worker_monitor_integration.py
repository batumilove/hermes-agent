"""Disposable real registry/ledger + adopted child through run_one_job."""
import json
import sqlite3
import subprocess
import sys

import pytest


@pytest.mark.parametrize("outcome", ["completed", "death"])
def test_run_one_job_real_busy_observation_preserves_claim(tmp_path, monkeypatch, outcome):
    from cron import executions, scheduler
    from cron.jobs import create_job, claim_job_for_fire, get_job, use_cron_store
    from tools.process_registry import GatewayChildDispatch
    from hermes_cli import sqlite_util

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = home / "cron" / "executions.db"
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", db)
    monkeypatch.setattr(scheduler, "load_config_readonly", dict)
    monkeypatch.setattr(scheduler, "_deliver_result", lambda *a, **k: None)
    gate = tmp_path / "continue"
    worker = tmp_path / "worker.py"
    worker.write_text('''import json,os,sys,time
from pathlib import Path
from cron import executions
args=sys.argv
payload=Path(args[args.index('--external-worker-file')+1])
ack=Path(args[args.index('--ack-file')+1])
data=json.loads(payload.read_text()); eid=data['job']['execution_id']
assert executions.adopt_claimed_execution(eid)
ack.write_text(json.dumps({'pid':os.getpid(),'execution_id':eid}))
deadline=time.monotonic()+20
while not Path(GATE).exists() and time.monotonic()<deadline: time.sleep(.02)
assert Path(GATE).exists(), 'observer did not finish'
if OUTCOME=='death': os._exit(9)
assert executions.finish_execution(eid, success=True)
from cron.jobs import mark_job_run
assert mark_job_run(data['job']['id'], True, expected_fire_owner=data['job']['fire_claim']['by'])
'''.replace('GATE', repr(str(gate))).replace('OUTCOME', repr(outcome)))
    children = []
    original_popen = subprocess.Popen
    def popen(*args, **kwargs):
        p = original_popen(*args, **kwargs)
        children.append(p)
        return p
    monkeypatch.setattr(scheduler.subprocess, "Popen", popen)
    def dispatch(command, **kwargs):
        flags = command[command.index("--external-worker-file"):]
        return GatewayChildDispatch("degraded", [sys.executable, str(worker), *flags])
    monkeypatch.setattr("tools.process_registry.restart_safe_gateway_child_argv", dispatch)
    original_open = sqlite_util.open_db
    def quick_open(*args, **kwargs):
        kwargs['busy_timeout_ms'] = 30
        return original_open(*args, **kwargs)
    monkeypatch.setattr(sqlite_util, "open_db", quick_open)
    real_read = executions.get_execution
    observed = []
    with use_cron_store(home):
        job = create_job(prompt="fixture only", schedule="every 1h", deliver="local")
        claimed = claim_job_for_fire(job['id'], manual=True, return_job=True)
        original_claim = get_job(job['id'])['fire_claim']
        def read(eid):
            if not observed:
                assert children and children[-1].poll() is None
                assert get_job(job['id'])['fire_claim'] == original_claim
                lock = sqlite3.connect(db)
                # Force the real connection initializer to need a schema write;
                # ordinary WAL SELECTs correctly remain readable under a writer.
                lock.execute('DROP INDEX idx_executions_status_claimed')
                lock.commit()
                lock.execute('BEGIN IMMEDIATE')
                try:
                    with pytest.raises(sqlite3.OperationalError, match='locked') as busy:
                        real_read(eid)
                    observed.append(str(busy.value))
                    assert get_job(job['id'])['fire_claim'] == original_claim
                finally:
                    lock.rollback(); lock.close()
                    gate.write_text('go')
                raise busy.value
            return real_read(eid)
        monkeypatch.setattr(scheduler, "get_execution", read)
        try:
            assert scheduler.run_one_job(claimed)
            assert observed == ['database is locked']
            row = real_read(claimed['execution_id'])
            assert row['status'] == ('unknown' if outcome == 'death' else 'completed')
            assert get_job(job['id']).get('fire_claim') is None
            assert claim_job_for_fire(job['id'], manual=True)
            replacement = get_job(job['id'])['fire_claim']
            from cron.scheduler_worker_failure import record_unknown_worker_outcome
            record_unknown_worker_outcome(claimed)
            assert get_job(job['id'])['fire_claim'] == replacement
            print('INTEGRATION', outcome, row['status'], 'busy preserved claim; replacement fenced')
        finally:
            gate.write_text('go')
            for child in children:
                child.wait(timeout=30)
                print('REAP', child.pid, child.returncode)
