"""Real child, SQLite contention and production entry; no provider invocation."""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest


CHILD = r'''
import json, pathlib, sys, time
from cron import scheduler as s, executions as e
import hermes_cli.plugins as plugins
import hermes_cli.env_loader as env
from gateway.run_startup import GatewayStartupMixin
plugins.discover_plugins=lambda:None
env.hydrate_profile_secret_sources=lambda *a:None
GatewayStartupMixin._register_config_hooks=lambda *a,**k:None
payload, ack, barrier, release = map(pathlib.Path, sys.argv[1:])
original_connect=e._connect
attempts=0
def observed_connect(*a,**kw):
    global attempts
    attempts+=1
    if attempts>=2:
        barrier.with_name('contended').write_text('fresh-connection-retry')
    return original_connect(*a,**kw)
e._connect=observed_connect
barrier.write_text('worker-imported')
while not release.exists():
    import threading
    threading.Event().wait(.01)
print('RESULT', s._run_external_worker_payload(payload,ack),flush=True)
'''


@pytest.mark.parametrize("mode", ["transient", "persistent", "cancel"])
def test_real_worker_contention_startup(tmp_path, monkeypatch, mode):
    from cron import executions as e
    from cron.jobs import create_job, use_cron_store
    from tests.waiting import wait_until

    home = tmp_path / "profile"
    home.mkdir()
    scripts = home / "scripts"
    scripts.mkdir()
    effect = home / "effect"
    script = scripts / "effect.py"
    script.write_text(f"from pathlib import Path\nPath({str(effect)!r}).write_text('once')\nprint('ok')\n")
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    monkeypatch.setattr(e, 'EXECUTIONS_FILE', home / 'cron' / 'executions.db')
    token = set_hermes_home_override(home)
    with use_cron_store(home):
        job = create_job(None, "1h", no_agent=True, script="effect.py", deliver="local")
        row = e.create_execution(job['id'], source="test")
        e.mark_execution_handoff_pending(row['id'])
        job['execution_id'] = row['id']
        db = home / 'cron' / 'executions.db'
    reset_hermes_home_override(token)
    payload, ack = home / "payload.json", home / "ready"
    barrier, release = home / "entered", home / "release"
    payload.write_text(json.dumps({'job':job, 'profile_home':str(home)}))
    lock = sqlite3.connect(db)
    lock.execute('BEGIN IMMEDIATE')
    env = dict(os.environ, HERMES_HOME=str(home))
    child = subprocess.Popen([sys.executable,'-u','-c',CHILD,str(payload),str(ack),str(barrier),str(release)],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_until(barrier.exists, timeout=15)
        data=json.loads(payload.read_text())
        data['handoff_deadline']=time.monotonic()+(2 if mode=='persistent' else 8)
        payload.write_text(json.dumps(data))
        release.write_text('go')
        assert wait_until((home / 'contended').exists, timeout=5)
        assert not ack.exists() and not effect.exists()
        if mode=='persistent':
            stdout,stderr=child.communicate(timeout=10)
            assert 'RESULT False' in stdout, (stdout,stderr)
        else:
            if mode=='cancel':
                lock.execute("UPDATE executions SET status='failed' WHERE id=?",(row['id'],))
            lock.commit()
            stdout,stderr=child.communicate(timeout=20)
            assert ('RESULT True' if mode=='transient' else 'RESULT False') in stdout, (stdout,stderr)
        assert child.returncode==0,(stdout,stderr)
        assert effect.exists() is (mode=='transient')
        assert ack.exists() is (mode=='transient')
        with sqlite3.connect(db) as c:
            current=c.execute('SELECT status,pid FROM executions WHERE id=?',(row['id'],)).fetchone()
        assert current[0]=={'transient':'completed','persistent':'claimed','cancel':'failed'}[mode]
        if mode=='transient':
            assert current[1]==child.pid and effect.read_text()=='once'
    finally:
        lock.close()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
