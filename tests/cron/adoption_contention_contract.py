"""Real SQLite contention contracts; standalone stdlib harness, no runtime imports.

Run directly with source root argv[1]. Dependencies unrelated to ledger SQL are
stubbed before loading the exact source file. All databases are temporary.
"""
import contextlib
import importlib.util
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import UTC, datetime

from typing import Any

ROOT = pathlib.Path(sys.argv.pop(1))

def stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod

stub('cron', __path__=[str(ROOT / 'cron')])
stub('hermes_constants', get_hermes_home=lambda: pathlib.Path('/nonexistent-test-home'))
stub('hermes_time', now=lambda: datetime.now(UTC))
stub('cron.constants', CLAIM_TTL_INACTIVITY_HEADROOM=3)
stub('hermes_cli.observability.shared_metrics_gateway', record_cron_finish=lambda **kw: None)
stub('cron.jobs', _ensure_cron_dir=lambda p: p.mkdir(parents=True, exist_ok=True))
stub('hermes_state_wal', apply_wal_with_fallback=lambda conn, **kw: conn.execute('PRAGMA journal_mode=WAL'))
spec = importlib.util.spec_from_file_location('cron.executions', ROOT / 'cron/executions.py')
assert spec is not None and spec.loader is not None
e: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = e
spec.loader.exec_module(e)
e._emit_execution_state = lambda *a, **kw: None
e._process_start_time = lambda pid: 123

class AdoptionContention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        e.EXECUTIONS_FILE = pathlib.Path(self.temp.name) / 'executions.db'
        conn = e._connect()
        conn.execute("INSERT INTO executions(id,job_id,source,process_id,pid,status,claimed_at,handoff_pending,handoff_started_at) VALUES ('x','test','test','parent',1,'claimed','now',1,?)", (time.time(),))
        conn.commit()
        conn.close()
        self.original_connect = e._connect
        self.attempts = 0
        def connect(**kw):
            self.attempts += 1
            conn = self.original_connect(**kw)
            conn.execute('PRAGMA busy_timeout=0')
            return conn
        e._connect = connect
        self.writer = sqlite3.connect(e.EXECUTIONS_FILE, timeout=0)
        self.writer.execute('BEGIN IMMEDIATE')
    def tearDown(self):
        self.writer.close()
        e._connect = self.original_connect
        self.temp.cleanup()
    def adopt(self, seconds=.4):
        import inspect
        if 'deadline' not in inspect.signature(e.adopt_claimed_execution).parameters:
            return e.adopt_claimed_execution('x')
        return e.adopt_claimed_execution('x', deadline=time.monotonic()+seconds)
    def test_release_within_budget_adopts_once(self):
        # Release only after a failed real SQLite attempt, not a scheduling sleep.
        old_sleep = e.time.sleep
        def release(delay):
            self.writer.commit()
            old_sleep(delay)
        e.time.sleep = release
        try:
            self.assertEqual(self.adopt()['status'], 'running')
        finally:
            e.time.sleep = old_sleep
        self.assertGreaterEqual(self.attempts, 2)
        self.assertIsNone(self.adopt())
    def test_exhaustion_preserves_claim(self):
        start = time.monotonic()
        self.assertIsNone(self.adopt(.06))
        self.assertLess(time.monotonic()-start, .3)
        self.writer.rollback()
        self.assertEqual(self.writer.execute("SELECT status FROM executions").fetchone()[0], 'claimed')
    def test_nonlock_error_not_retried(self):
        self.writer.rollback()
        self.writer.execute('DROP TABLE executions')
        self.writer.commit()
        def broken(**kw):
            self.attempts += 1
            c = sqlite3.connect(e.EXECUTIONS_FILE)
            c.row_factory = sqlite3.Row
            return c
        e._connect = broken
        with self.assertRaisesRegex(sqlite3.OperationalError, 'no such table'):
            self.adopt()
        self.assertEqual(self.attempts, 1)
    def test_cancelled_during_backoff_never_adopts(self):
        old_sleep = e.time.sleep
        def cancel(delay):
            raise KeyboardInterrupt('cancel')
        e.time.sleep = cancel
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.adopt()
        finally:
            e.time.sleep = old_sleep
        self.writer.rollback()
        self.assertEqual(self.writer.execute('SELECT process_id FROM executions').fetchone()[0], 'parent')
    def test_competing_adopter_wins_without_steal(self):
        old_sleep = e.time.sleep
        def compete(delay):
            self.writer.execute("UPDATE executions SET status='running',process_id='winner',handoff_pending=0")
            self.writer.commit()
        e.time.sleep = compete
        try:
            self.assertIsNone(self.adopt())
        finally:
            e.time.sleep = old_sleep
        self.assertEqual(self.writer.execute('SELECT process_id FROM executions').fetchone()[0], 'winner')
    def test_expired_recovery_grace_does_not_revoke_pending_handoff(self):
        self.writer.execute('UPDATE executions SET handoff_started_at=?', (time.time()-31,))
        self.writer.commit()
        self.writer.rollback()
        self.assertIsNotNone(self.adopt())
        self.assertEqual(self.writer.execute('SELECT status FROM executions').fetchone()[0], 'running')

    def test_terminalized_during_backoff_never_adopts(self):
        old_sleep = e.time.sleep
        def terminalize(delay):
            self.writer.execute("UPDATE executions SET status='failed',handoff_pending=0")
            self.writer.commit()
        e.time.sleep = terminalize
        try:
            self.assertIsNone(self.adopt())
        finally:
            e.time.sleep = old_sleep
        self.assertEqual(self.writer.execute('SELECT status FROM executions').fetchone()[0], 'failed')

    def test_expired_deadline_does_not_open_connection(self):
        self.writer.rollback()
        self.assertIsNone(e.adopt_claimed_execution('x', deadline=time.monotonic()-1))
        self.assertEqual(self.attempts, 0)

if __name__ == '__main__':
    unittest.main(verbosity=2)
