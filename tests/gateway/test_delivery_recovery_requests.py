"""Operator decisions never forge delivery or release a turn barrier."""
import concurrent.futures
import importlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


class RecoveryRequestsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.db'
        with sqlite3.connect(self.path) as c:
            c.executescript('''
                CREATE TABLE delivery_obligations (
                    obligation_id TEXT PRIMARY KEY, session_key TEXT, platform TEXT,
                    chat_id TEXT, thread_id TEXT, content TEXT, state TEXT,
                    attempts INTEGER, created_at REAL, updated_at REAL,
                    owner_pid INTEGER, owner_started_at INTEGER, last_error TEXT);
                CREATE TABLE gateway_post_response_actions (
                    obligation_id TEXT, ordinal INTEGER, action_key TEXT,
                    action_kind TEXT, payload_json TEXT, state TEXT, attempts INTEGER,
                    owner_pid INTEGER, owner_started_at INTEGER);
                INSERT INTO delivery_obligations VALUES
                    ('oid','private-route','telegram','private-chat','private-thread',
                     'private-answer','failed',3,1,2,123,456,'private-error');
                INSERT INTO gateway_post_response_actions VALUES
                    ('oid',0,'action','finalize_turn',
                     '{"session_key":"private-route","active_turn_token":"old"}',
                     'pending',0,NULL,NULL);
            ''')
        # An assertion, not a collection ImportError, documents the missing API RED.
        spec = importlib.util.find_spec('gateway.delivery_recovery')
        self.assertIsNotNone(spec, 'missing explicit operator recovery API')
        self.api = importlib.import_module('gateway.delivery_recovery')

    def snapshot(self):
        return self.api.inspect_obligation(self.path, 'oid')

    def request(self, snapshot=None, **kw):
        return self.api.request_retry(
            self.path, 'oid', fingerprint=(snapshot or self.snapshot())['fingerprint'],
            acknowledge_possible_duplicate=True, operator='test-operator', **kw)

    def review(self, request_id, stamp):
        # Only process-identity probes are mocked; SQLite transitions are real.
        with patch.object(self.api, '_owner_stamp', return_value=stamp), patch.object(self.api.os, 'getpid', return_value=123):
            return self.api.review_retry_request(self.path, request_id)

    def rows(self):
        with sqlite3.connect(self.path) as c:
            return [c.execute('SELECT * FROM ' + table).fetchall() for table in
                    ('delivery_obligations', 'gateway_post_response_actions')]

    def mutate(self, sql):
        with sqlite3.connect(self.path) as c:
            c.execute(sql)

    def test_read_only_inspection_does_not_create_schema_or_expose_payloads(self):
        before = self.path.read_bytes()
        s = self.snapshot()
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(s['state'], 'failed')
        self.assertEqual(s['owner_pid'], 123)
        self.assertEqual(len(s['fingerprint']), 64)
        self.assertEqual(s['actions'][0]['kind'], 'finalize_turn')
        self.assertNotIn('private-', json.dumps(s))
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name='delivery_recovery_requests'").fetchone()[0], 0)

    def test_missing_db_and_parent_fail_without_creation(self):
        missing = self.path.parent / 'absent.db'
        with self.assertRaises((ValueError, sqlite3.Error, FileNotFoundError)):
            self.api.inspect_obligation(missing, 'oid')
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            self.api.inspect_obligation(self.path, 'missing')

    def test_request_is_audited_not_delivery_and_survives_open_existing(self):
        before = self.rows()
        result = self.request()
        self.assertEqual(result['status'], 'requested')
        self.assertEqual(before, self.rows())
        self.assertEqual(self.snapshot()['requests'][0]['request_id'], result['request_id'])
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT operator FROM delivery_recovery_requests').fetchone()[0], 'test-operator')

    def test_acknowledgement_and_operator_required(self):
        for ack, operator in [(False, 'operator'), (True, ''), (True, '  ')]:
            with self.subTest(ack=ack, operator=operator), self.assertRaises(ValueError):
                self.api.request_retry(self.path, 'oid', fingerprint=self.snapshot()['fingerprint'],
                                       acknowledge_possible_duplicate=ack, operator=operator)

    def test_dry_run_is_byte_read_only(self):
        before = self.path.read_bytes()
        result = self.request(dry_run=True)
        self.assertEqual(result['status'], 'dry_run')
        self.assertEqual(before, self.path.read_bytes())

    def test_five_incident_shapes_preserve_response_and_barrier(self):
        for state, attempts in [('failed', 0), ('failed', 1), ('failed', 3), ('abandoned', 0), ('abandoned', 3)]:
            with self.subTest(state=state, attempts=attempts):
                self.mutate(f"UPDATE delivery_obligations SET state='{state}',attempts={attempts}")
                before = self.rows()
                self.request()
                self.assertEqual(self.rows(), before)

    def test_uncertain_active_pending_and_delivered_cannot_be_requested(self):
        for state in ['pending', 'attempting', 'delivered', 'unknown']:
            self.mutate(f"UPDATE delivery_obligations SET state='{state}'")
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.request()

    def test_changed_receipt_or_action_rejects_stale_decision(self):
        for sql in ["UPDATE delivery_obligations SET owner_started_at=789",
                    "UPDATE delivery_obligations SET content='changed'",
                    "UPDATE gateway_post_response_actions SET payload_json='{}'",
                    "UPDATE gateway_post_response_actions SET state='running'"]:
            s = self.snapshot()
            self.mutate(sql)
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.request(s)

    def test_only_finalizer_remaining_is_eligible(self):
        self.mutate("UPDATE gateway_post_response_actions SET action_kind='append_to_transcript'")
        with self.assertRaises(ValueError):
            self.request()

    def test_competing_requests_have_single_winner(self):
        s = self.snapshot()
        def submit(_):
            try:
                return self.request(s)['status']
            except (ValueError, sqlite3.IntegrityError):
                return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(results), ['conflict', 'requested'])

    def test_profile_binding_rejects_same_rows_in_other_db(self):
        s = self.snapshot()
        other = self.path.parent / 'other.db'
        other.write_bytes(self.path.read_bytes())
        with self.assertRaises(ValueError):
            self.api.request_retry(other, 'oid', fingerprint=s['fingerprint'],
                                   acknowledge_possible_duplicate=True, operator='test')

    def test_owner_coordination_never_steals_live_or_pid_reused_owner(self):
        request = self.request()
        before = self.rows()
        for owner in [(999, 456), (123, 999), (123, None)]:
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                self.review(request['request_id'], owner)
        self.assertEqual(self.rows(), before)
        result = self.review(request['request_id'], (123, 456))
        self.assertEqual(result['status'], 'owner_reviewed')
        self.assertEqual(self.rows(), before)
        with self.assertRaises(ValueError):
            self.review(request['request_id'], (123, 456))

    def test_owner_review_rechecks_fingerprint_and_never_clears_new_token(self):
        request = self.request()
        self.mutate('''UPDATE gateway_post_response_actions SET payload_json='{"active_turn_token":"new"}' ''')
        before = self.rows()
        with self.assertRaises(ValueError):
            self.review(request['request_id'], (123, 456))
        self.assertEqual(self.rows(), before)

    def test_cli_dry_run_and_explicit_request_never_resend(self):
        command = [sys.executable, '-m', 'gateway.delivery_recovery', '--db', str(self.path)]
        before = self.rows()
        inspected = subprocess.run(command + ['inspect', 'oid'], capture_output=True, text=True)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        fingerprint = json.loads(inspected.stdout)['fingerprint']
        args = ['request-retry', 'oid', '--fingerprint', fingerprint,
                '--operator', 'cli-test', '--acknowledge-possible-duplicate']
        dry = subprocess.run(command + args, capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(json.loads(dry.stdout)['status'], 'dry_run')
        applied = subprocess.run(command + args + ['--apply'], capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['status'], 'requested')
        self.assertTrue(json.loads(applied.stdout)['execution_available'])
        self.assertFalse(json.loads(inspected.stdout)['cancellation_available'])
        self.assertEqual(self.rows(), before)

    def test_audit_write_failure_rolls_back_without_receipt_change(self):
        self.request()
        self.mutate("UPDATE delivery_obligations SET updated_at=3")
        with sqlite3.connect(self.path) as c:
            c.execute('''CREATE TRIGGER refuse_request BEFORE INSERT ON delivery_recovery_requests
                         BEGIN SELECT RAISE(ABORT, 'injected'); END''')
        before = self.rows()
        with self.assertRaises(sqlite3.IntegrityError):
            self.request()
        self.assertEqual(self.rows(), before)


if __name__ == '__main__':
    unittest.main()
