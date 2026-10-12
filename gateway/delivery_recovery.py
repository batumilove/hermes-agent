"""Explicit, audited operator retry for stranded final-response barriers.

CLI requests are consumed by the owning gateway's post-response worker on its
async event loop. Only an acknowledged decision may resend existing content;
no model replay is performed. Cancellation is explicitly unavailable.

CLI: python -m gateway.delivery_recovery --db /exact/profile/state.db inspect ID
     python -m gateway.delivery_recovery --db /exact/profile/state.db request-retry ID
       --fingerprint SHA256 --operator LABEL --acknowledge-possible-duplicate
By default request-retry is a read-only dry run; --apply authorizes one resend.
No state DB is created, no SessionDB startup/migration is invoked by inspection.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def _path(db_path):
    path = Path(db_path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("an existing non-symlink state DB is required")
    if path.resolve() != path:
        raise ValueError("state DB path must be canonical")
    return path


@contextmanager
def _connection(db_path, *, write=False):
    path = _path(db_path)
    conn = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                           uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    try:
        if not write:
            conn.execute('PRAGMA query_only=ON')
        # Snapshot all related rows together; reserve writer before the CAS
        # read so competing processes cannot both accept the same decision.
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield path, conn
        if write:
            conn.commit()
    finally:
        conn.close()  # also rolls back BaseException/failed audit writes


def _snapshot(path, conn, obligation_id):
    row = conn.execute('SELECT * FROM delivery_obligations WHERE obligation_id=?',
                       (obligation_id,)).fetchone()
    if row is None:
        raise ValueError('obligation not found in selected DB')
    receipt = dict(row)
    actions = [dict(a) for a in conn.execute(
        'SELECT * FROM gateway_post_response_actions WHERE obligation_id=? ORDER BY ordinal',
        (obligation_id,))]
    # Hash the complete rows, including payload, owner generation, attempts and
    # timestamps. Bind the selected profile path and file identity as well.
    st = path.stat()
    material = [str(path), st.st_dev, st.st_ino, receipt, actions]
    fingerprint = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(',', ':'), ensure_ascii=True
    ).encode()).hexdigest()
    return receipt, actions, fingerprint


def _requests(conn, obligation_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_recovery_requests'").fetchone() is None:
        return []
    # Never expose free-form operator strings via diagnostics.
    return [dict(row) for row in conn.execute(
        '''SELECT request_id, fingerprint, status, requested_at, reviewed_at
           FROM delivery_recovery_requests WHERE obligation_id=? ORDER BY requested_at''',
        (obligation_id,))]


def inspect_obligation(db_path, obligation_id):
    """Read-only exact obligation view without response, routing or payload text."""
    with _connection(db_path) as (path, conn):
        receipt, actions, fingerprint = _snapshot(path, conn, obligation_id)
        return {
            'obligation_id': receipt['obligation_id'], 'fingerprint': fingerprint,
            'state': receipt['state'], 'attempts': receipt['attempts'],
            'created_at': receipt['created_at'], 'updated_at': receipt['updated_at'],
            'age_seconds': max(0, time.time() - receipt['created_at']),
            'owner_pid': receipt['owner_pid'], 'owner_started_at': receipt['owner_started_at'],
            'actions': [{'ordinal': a['ordinal'], 'kind': a['action_kind'],
                         'state': a['state'], 'attempts': a['attempts']} for a in actions],
            'requests': _requests(conn, obligation_id),
            'execution_available': True,
            'cancellation_available': False,
        }


def _eligible(receipt, actions):
    if receipt['state'] not in ('failed', 'abandoned'):
        raise ValueError('only failed or abandoned receipts permit explicit retry requests')
    remaining = [a for a in actions if a['state'] != 'done']
    if (len(remaining) != 1 or remaining[0]['action_kind'] != 'finalize_turn'
            or remaining[0]['state'] != 'pending' or remaining[0]['attempts'] != 0
            or remaining[0]['owner_pid'] is not None
            or remaining[0]['owner_started_at'] is not None
            or remaining[0]['ordinal'] != max(a['ordinal'] for a in actions)):
        raise ValueError('requires an unclaimed terminal finalizer and completed earlier actions')


def _schema(conn):
    # Additive, lazy schema; existing SessionDB/ledger schemas are not changed.
    conn.execute('''CREATE TABLE IF NOT EXISTS delivery_recovery_requests (
        request_id TEXT PRIMARY KEY, obligation_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, operator TEXT NOT NULL,
        acknowledgement TEXT NOT NULL CHECK(acknowledgement='possible_duplicate'),
        status TEXT NOT NULL CHECK(status IN ('requested','owner_reviewed')),
        requested_at REAL NOT NULL, reviewed_at REAL,
        owner_pid INTEGER, owner_started_at INTEGER,
        UNIQUE(obligation_id, fingerprint))''')


def request_retry(db_path, obligation_id, *, fingerprint,
                  acknowledge_possible_duplicate, operator, dry_run=False):
    """Record intent only. Never reset attempts, re-stamp owners or send content."""
    if acknowledge_possible_duplicate is not True:
        raise ValueError('explicit acknowledgement of possible duplicate is required')
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 200:
        raise ValueError('a bounded nonempty operator label is required')
    with _connection(db_path, write=not dry_run) as (path, conn):
        receipt, actions, actual = _snapshot(path, conn, obligation_id)
        if actual != fingerprint:
            raise ValueError('stale or wrong-profile fingerprint; inspect again')
        _eligible(receipt, actions)
        _reject_live_execution(conn, obligation_id)
        if any(r['fingerprint'] == fingerprint for r in _requests(conn, obligation_id)):
            raise ValueError('this exact decision is already recorded')
        if dry_run:
            return {'status': 'dry_run', 'fingerprint': actual, 'execution_available': True}
        _schema(conn)
        request_id = uuid.uuid4().hex
        conn.execute('''INSERT INTO delivery_recovery_requests
            (request_id, obligation_id, fingerprint, operator, acknowledgement,
             status, requested_at, owner_pid, owner_started_at)
            VALUES (?, ?, ?, ?, 'possible_duplicate', 'requested', ?, ?, ?)''',
            (request_id, obligation_id, actual, operator.strip(), time.time(),
             receipt['owner_pid'], receipt['owner_started_at']))
        return {'status': 'requested', 'request_id': request_id, 'execution_available': True}


def _owner_stamp():
    from gateway.delivery_ledger import _owner_stamp as stamp
    return stamp()


def review_retry_request(db_path, request_id):
    """Compatibility audit-only review; execution independently rechecks CAS.

    This optional v1 API neither claims nor sends. The worker consumes both
    requested and reviewed decisions through the same durable send boundary.
    """
    pid, started = _owner_stamp()
    if pid != os.getpid() or started is None:
        raise ValueError('provable current-process owner stamp required')
    with _connection(db_path, write=True) as (path, conn):
        request = conn.execute('SELECT * FROM delivery_recovery_requests WHERE request_id=?',
                               (request_id,)).fetchone()
        if request is None or request['status'] != 'requested':
            raise ValueError('request missing or already reviewed')
        receipt, actions, actual = _snapshot(path, conn, request['obligation_id'])
        if actual != request['fingerprint']:
            raise ValueError('receipt changed; inspect and request again')
        _eligible(receipt, actions)
        if (receipt['owner_pid'], receipt['owner_started_at']) != (pid, started):
            raise ValueError('only the exact receipt owner may review this request')
        cursor = conn.execute('''UPDATE delivery_recovery_requests
            SET status='owner_reviewed', reviewed_at=?
            WHERE request_id=? AND status='requested' AND fingerprint=?
              AND owner_pid=? AND owner_started_at=?''',
            (time.time(), request_id, actual, pid, started))
        if cursor.rowcount != 1:
            raise ValueError('request ownership changed')
        return {'request_id': request_id, 'status': 'owner_reviewed', 'execution_available': True}


def _execution_schema(conn):
    # Separate from the v1 audit table so existing reviewed requests retain
    # their history without rebuilding a CHECK-constrained table.
    conn.execute('''CREATE TABLE IF NOT EXISTS delivery_recovery_executions (
        request_id TEXT PRIMARY KEY, obligation_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, owner_pid INTEGER NOT NULL,
        owner_started_at INTEGER NOT NULL, status TEXT NOT NULL,
        updated_at REAL NOT NULL)''')


def _reject_live_execution(conn, obligation_id):
    from gateway.delivery_ledger import _owner_alive
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_executions'").fetchone():
        return
    for row in conn.execute('''SELECT owner_pid, owner_started_at
            FROM delivery_recovery_executions WHERE obligation_id=? AND status='sending' ''',
            (obligation_id,)):
        if _owner_alive(row['owner_pid'], row['owner_started_at']):
            raise ValueError('send still owned by a live process; no concurrent decision')


def claim_retry(db_path, request_id):
    """Consume one acknowledged decision, fencing crash outcomes before I/O.

    An abandoned receipt is intentionally NOT startup-redeliverable. A crash
    after this commit requires a fresh decision; the old request is spent.
    """
    from gateway.delivery_ledger import _owner_alive
    pid, started = _owner_stamp()
    if pid != os.getpid() or started is None:
        raise ValueError('provable current-process identity required')
    with _connection(db_path, write=True) as (path, conn):
        _execution_schema(conn)
        request = conn.execute('SELECT * FROM delivery_recovery_requests WHERE request_id=?',
                               (request_id,)).fetchone()
        if request is None or conn.execute(
                'SELECT 1 FROM delivery_recovery_executions WHERE request_id=?', (request_id,)).fetchone():
            return None
        receipt, actions, actual = _snapshot(path, conn, request['obligation_id'])
        if actual != request['fingerprint']:
            return None
        _eligible(receipt, actions)
        owner = (receipt['owner_pid'], receipt['owner_started_at'])
        if owner != (pid, started) and _owner_alive(*owner):
            return None
        _reject_live_execution(conn, receipt['obligation_id'])
        # Retire dead sends only while consuming a NEW acknowledged decision.
        conn.execute("UPDATE delivery_recovery_executions SET status='uncertain' WHERE obligation_id=? AND status='sending'",
                     (receipt['obligation_id'],))
        conn.execute('''UPDATE delivery_obligations SET state='abandoned',
            attempts=attempts+1, owner_pid=?, owner_started_at=?, updated_at=?,
            last_error='operator_retry_outcome_uncertain' WHERE obligation_id=?''',
            (pid, started, time.time(), receipt['obligation_id']))
        receipt, _, claimed = _snapshot(path, conn, receipt['obligation_id'])
        conn.execute('''INSERT INTO delivery_recovery_executions VALUES (?,?,?,?,?,'sending',?)''',
            (request_id, receipt['obligation_id'], claimed, pid, started, time.time()))
        return {'request_id': request_id, 'fingerprint': claimed, 'receipt': receipt}


def _owned_execution(row, claim):
    """Bind cleanup to this claim, not merely to a live PID or obligation."""
    receipt = claim['receipt']
    return (row is not None and row['status'] == 'sending'
            and row['request_id'] == claim['request_id']
            and row['obligation_id'] == receipt['obligation_id']
            and row['fingerprint'] == claim['fingerprint']
            and (row['owner_pid'], row['owner_started_at'])
            == (receipt['owner_pid'], receipt['owner_started_at']) == _owner_stamp())


def _retire_completed(db_path, claim):
    """Owner-only completion cleanup. Caller must have finished adapter I/O.

    Deliberately does not inspect or modify the receipt/actions: losing their
    CAS is exactly why a completed execution may need uncertainty retirement.
    """
    with _connection(db_path, write=True) as (_, conn):
        row = conn.execute('SELECT * FROM delivery_recovery_executions WHERE request_id=?',
                           (claim['request_id'],)).fetchone()
        if _owned_execution(row, claim):
            conn.execute('''UPDATE delivery_recovery_executions
                SET status='uncertain',updated_at=? WHERE request_id=?''',
                (time.time(), claim['request_id']))


def _reconcile_completed(runner, db_path):
    """Bounded owner-side retry; never infer completion from PID liveness.

    Entries originate only after adapter return/exception. Keep failed cleanup
    discoverable on the runner for future polls; durable sending still excludes
    new acknowledgements until cleanup succeeds (or that owner process dies).
    """
    pending = getattr(runner, '_delivery_recovery_completed', {})
    matching = [(key, claim) for key, claim in pending.items() if key[0] == str(db_path)]
    for key, claim in matching[:32]:
        for _ in range(3):
            try:
                _retire_completed(db_path, claim)
            except (sqlite3.Error, OSError, ValueError):
                continue
            pending.pop(key, None)
            break
    return any(key[0] == str(db_path) for key in pending)


def settle_retry(db_path, claim, *, delivered):
    """Exact full-row/action CAS; never turn an unproved outcome into delivery."""
    with _connection(db_path, write=True) as (path, conn):
        row = conn.execute('SELECT * FROM delivery_recovery_executions WHERE request_id=?',
                           (claim['request_id'],)).fetchone()
        if not _owned_execution(row, claim):
            return False
        receipt, _, actual = _snapshot(path, conn, row['obligation_id'])
        if actual != claim['fingerprint'] or actual != row['fingerprint']:
            return False
        conn.execute('''UPDATE delivery_obligations SET state=?, updated_at=?, last_error=?
            WHERE obligation_id=?''',
            ('delivered' if delivered else 'abandoned', time.time(),
             None if delivered else 'operator_retry_outcome_uncertain', receipt['obligation_id']))
        conn.execute('''UPDATE delivery_recovery_executions SET status=?,updated_at=? WHERE request_id=?''',
            ('delivered' if delivered else 'uncertain', time.time(), claim['request_id']))
        return True


async def consume_requests(runner, db_path):
    """Bounded send-only consumer. Must execute on the owning gateway loop.

    No turn replay, no direct finalization, no automatic repeat. Even task
    cancellation settles uncertain; process death leaves the durable fence.
    """
    import asyncio
    from gateway.config import Platform
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    if asyncio.get_running_loop() is not getattr(runner, '_gateway_loop', None):
        raise ValueError('recovery must run on the owning gateway event loop')
    if not getattr(runner, '_running', False):
        return
    token = set_hermes_home_override(str(Path(db_path).parent))
    try:
        if _reconcile_completed(runner, db_path):
            return  # bound retained cleanup; do not accumulate more sends
        with _connection(db_path) as (_, conn):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_requests'").fetchone():
                return
            # Keyset pagination keeps each poll bounded without allowing stale
            # or spent history to hide a valid decision forever. This cursor is
            # only a scheduling aid: claim_retry remains the durable CAS gate.
            cursors = getattr(runner, '_delivery_recovery_cursors', None)
            if cursors is None:
                cursors = runner._delivery_recovery_cursors = {}
            key = str(db_path)
            has_executions = conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_executions'").fetchone()
            unspent = ('''AND NOT EXISTS (SELECT 1 FROM delivery_recovery_executions e
                WHERE e.request_id=r.request_id)''' if has_executions else '')
            requests = conn.execute('''SELECT r.request_id, r.obligation_id
                FROM delivery_recovery_requests r
                JOIN delivery_obligations o ON o.obligation_id=r.obligation_id
                WHERE r.request_id > ? ''' + unspent + '''
                ORDER BY r.request_id LIMIT 32''',
                (cursors.get(key, ''),)).fetchall()
            cursors[key] = requests[-1]['request_id'] if len(requests) == 32 else ''
        for request in requests:
            controller = getattr(runner, '_post_response_controller', None)
            if (not getattr(runner, '_running', False)
                    or (controller is not None and controller._stop.is_set())):
                return
            try:
                with _connection(db_path) as (path, conn):
                    receipt, _, _ = _snapshot(path, conn, request['obligation_id'])
                adapter = runner.adapters.get(Platform(receipt['platform']))
                if adapter is None:
                    continue  # no send claim spent while adapter unavailable
                claim = claim_retry(db_path, request['request_id'])
            except (ValueError, sqlite3.Error, OSError):
                continue  # a pruned/stale/broken request must not abort this page
            if claim is None:
                continue
            receipt = claim['receipt']
            delivered = False
            try:
                result = await adapter.send(
                    chat_id=receipt['chat_id'],
                    content='♻️ Operator-requested retry — possible duplicate:\n\n' + receipt['content'],
                    metadata={'thread_id': receipt['thread_id']} if receipt['thread_id'] else None)
                delivered = result is not None and getattr(result, 'success', False) is True
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # ambiguous network failure; retain barrier, no retry
            finally:
                # This entry is created only after the adapter has unwound, not
                # at claim time. Other consumers cannot retire an active send.
                pending = getattr(runner, '_delivery_recovery_completed', None)
                if pending is None:
                    pending = runner._delivery_recovery_completed = {}
                completed_key = (str(db_path), claim['request_id'])
                pending[completed_key] = claim
                settled = False
                try:
                    settled = settle_retry(db_path, claim, delivered=delivered)
                except (ValueError, sqlite3.Error, OSError):
                    pass  # uncertainty cleanup must not promote delivery
                if settled:
                    pending.pop(completed_key, None)
                else:
                    _reconcile_completed(runner, db_path)
            if settled and delivered:
                controller = getattr(runner, '_post_response_controller', None)
                if controller is not None:
                    controller.wake()
            if pending:
                return  # retry cleanup on a later poll before spending more claims
    finally:
        reset_hermes_home_override(token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('obligation_id')
    request = sub.add_parser('request-retry')
    request.add_argument('obligation_id')
    request.add_argument('--fingerprint', required=True)
    request.add_argument('--operator', required=True)
    request.add_argument('--acknowledge-possible-duplicate', action='store_true')
    request.add_argument('--apply', action='store_true', help='authorize one resend by the owning gateway; may duplicate')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = inspect_obligation(args.db, args.obligation_id)
        else:
            result = request_retry(args.db, args.obligation_id, fingerprint=args.fingerprint,
                operator=args.operator, acknowledge_possible_duplicate=args.acknowledge_possible_duplicate,
                dry_run=not args.apply)
    except (ValueError, sqlite3.Error, OSError):
        # DB error text can include paths, triggers or private payload data.
        parser.exit(2, 'Recovery refused; inspect the exact obligation and review its owner/state.\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
