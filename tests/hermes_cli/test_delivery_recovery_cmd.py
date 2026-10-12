"""Real CLI subprocess + unchanged live-guard decision contract, temp DB only."""
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def home(tmp_path):
    home = tmp_path / 'isolated'
    home.mkdir()
    with sqlite3.connect(home / 'state.db') as conn:
        conn.executescript('''
        CREATE TABLE delivery_obligations (
          obligation_id TEXT PRIMARY KEY, state TEXT, attempts INTEGER,
          created_at REAL, updated_at REAL, owner_pid INTEGER, owner_started_at INTEGER);
        CREATE TABLE gateway_post_response_actions (
          obligation_id TEXT, ordinal INTEGER, action_kind TEXT, state TEXT,
          attempts INTEGER, owner_pid INTEGER, owner_started_at INTEGER);
        INSERT INTO delivery_obligations VALUES ('one','failed',3,1,2,123,456);
        INSERT INTO delivery_obligations VALUES ('excluded','failed',3,1,2,123,456);
        INSERT INTO gateway_post_response_actions VALUES ('one',0,'finalize_turn','pending',0,NULL,NULL);
        ''')
    return home


def cli(home, *args):
    # Real packaging entrypoint; PYTHONPATH selects only the isolated candidate.
    # This is test-only routing to a temp DB, never a live recovery command.
    import shutil
    entrypoint = shutil.which('hermes')
    assert entrypoint, 'installed canonical console script required'
    env = dict(os.environ, HERMES_HOME=str(home), PYTHONPATH=str(ROOT))
    return subprocess.run([entrypoint, 'delivery-recovery', *args], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=40)


def inspect(home):
    result = cli(home, 'inspect', 'one', '--expect-home', str(home))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def retry_args(home, fp):
    return ['request-retry', 'one', '--expect-home', str(home),
            '--fingerprint', fp, '--operator', 'fixture-operator',
            '--acknowledge-possible-duplicate']


def test_cli_records_one_audited_intent_without_touching_receipts(home):
    snapshot = inspect(home)
    assert snapshot['profile_home'] == str(home)
    args = retry_args(home, snapshot['fingerprint'])
    result = cli(home, *args)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'dry_run'
    with sqlite3.connect(home / 'state.db') as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_requests'").fetchone()
        before = c.execute('SELECT * FROM delivery_obligations').fetchall()
    result = cli(home, *args, '--apply')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'requested'
    assert cli(home, *args, '--apply').returncode != 0
    with sqlite3.connect(home / 'state.db') as c:
        assert c.execute('SELECT * FROM delivery_obligations').fetchall() == before
        assert c.execute('SELECT obligation_id, operator, acknowledgement FROM delivery_recovery_requests').fetchall() == [('one','fixture-operator','possible_duplicate')]


@pytest.mark.parametrize('change', ['ack', 'fingerprint', 'profile', 'db', 'sql', 'operator', 'missing', 'abbreviated'])
def test_cli_rejects_unsafe_requests(home, change):
    args = retry_args(home, inspect(home)['fingerprint']) + ['--apply']
    if change == 'ack': args.remove('--acknowledge-possible-duplicate')
    if change == 'fingerprint': args[args.index('--fingerprint')+1] = '0'*64
    if change == 'profile': args[args.index('--expect-home')+1] = str(home.parent)
    if change == 'db': args += ['--db', str(home/'state.db')]
    if change == 'sql': args += ['--sql', 'DELETE FROM delivery_obligations']
    if change == 'operator': args[args.index('--operator')+1] = ' '
    if change == 'missing': args[1] = 'absent'
    if change == 'abbreviated': args[args.index('--apply')] = '--app'
    assert cli(home, *args).returncode != 0
    with sqlite3.connect(home/'state.db') as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_requests'").fetchone()


def test_real_guard_decisions_preserve_raw_sqlite_blocks(home):
    from tools.live_state_db_guard import check_live_state_db_command
    command = shlex.join(['hermes', 'delivery-recovery', *retry_args(home, 'a'*64), '--apply'])
    for text, expected in [(command, False),
        (f'python -m gateway.delivery_recovery --db {home}/state.db inspect one', True),
        (f'sqlite3 {home}/state.db "SELECT 1"', True),
        (command + f'; sqlite3 {home}/state.db "DELETE FROM delivery_obligations"', True)]:
        assert check_live_state_db_command(text, env_type='local', cwd=ROOT,
            hermes_home=home, gateway_is_live=lambda _: True)[0] is expected


def test_recovery_connections_are_tracked(home):
    from gateway.delivery_recovery import _connection
    from hermes_cli.sqlite_safe_read import has_live_connection
    for write in (False, True):
        with _connection(home/'state.db', write=write):
            assert has_live_connection(home/'state.db')
        assert not has_live_connection(home/'state.db')
