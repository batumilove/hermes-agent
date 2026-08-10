import asyncio
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from hermes_state import SessionDB


def _logged_seconds(message, field):
    match = re.search(rf"(?:^| ){re.escape(field)}=([0-9]+\.[0-9]+)s(?: |$)", message)
    assert match is not None, f"missing {field}= duration in {message!r}"
    return float(match.group(1))


class _DelayedPhaseConnection:
    """Real SQLite connection proxy with deterministic per-phase delays."""

    def __init__(self, wrapped, *, begin_s, commit_s):
        self._wrapped = wrapped
        self._begin_s = begin_s
        self._commit_s = commit_s

    def execute(self, sql, *args, **kwargs):
        if sql == "BEGIN IMMEDIATE":
            time.sleep(self._begin_s)
        return self._wrapped.execute(sql, *args, **kwargs)

    def commit(self):
        time.sleep(self._commit_s)
        return self._wrapped.commit()

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class _RetryOnceConnection:
    """Real SQLite connection proxy that reports one synthetic busy attempt."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._begin_attempts = 0

    def execute(self, sql, *args, **kwargs):
        if sql == "BEGIN IMMEDIATE":
            self._begin_attempts += 1
            if self._begin_attempts == 1:
                raise sqlite3.OperationalError("database is locked")
        return self._wrapped.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def test_execute_write_warns_when_waiting_for_session_db_lock(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    def insert_session(conn):
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("slow-session", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._execute_write(insert_session)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "SessionDB write latency" in messages
    assert "caller=insert_session" in messages
    assert "operation=insert_session" in messages
    assert "lock_wait=" in messages
    assert "txn=" in messages
    db.close()


def test_execute_write_splits_transaction_phases_and_identifies_runtime(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0
    db._conn = _DelayedPhaseConnection(  # type: ignore[assignment]
        db._conn, begin_s=0.04, commit_s=0.03
    )

    def insert_session(conn):
        time.sleep(0.02)
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("phase-session", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._execute_write(insert_session, operation="phase_probe")

    message = next(
        record.getMessage()
        for record in caplog.records
        if "operation=phase_probe" in record.getMessage()
    )
    assert "begin_wait=" in message
    assert "callback=" in message
    assert "commit=" in message
    assert "outcome=write" in message
    assert f"pid={os.getpid()}" in message
    assert f"thread_id={threading.get_ident()}" in message
    assert "db_instance=" in message
    assert str(tmp_path) not in message
    begin_s = _logged_seconds(message, "begin_wait")
    callback_s = _logged_seconds(message, "callback")
    commit_s = _logged_seconds(message, "commit")
    transaction_s = _logged_seconds(message, "txn")
    assert begin_s >= 0.03
    assert callback_s >= 0.015
    assert commit_s >= 0.02
    phase_sum = begin_s + callback_s + commit_s
    assert phase_sum - 0.003 <= transaction_s <= phase_sum + 0.250
    db.close()


def test_execute_write_logs_reason_without_changing_operation(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.replace_gateway_routing_entries(
            {"telegram:1": '{"session_id":"s1"}'},
            reason="suspend_recently_active",
        )

    message = next(
        record.getMessage()
        for record in caplog.records
        if "operation=replace_gateway_routing_entries" in record.getMessage()
    )
    assert "operation=replace_gateway_routing_entries " in message
    assert "reason=suspend_recently_active " in message
    assert "items=1 " in message
    db.close()


def test_execute_write_attributes_lock_wait_to_observed_owner(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0
    holder_entered = threading.Event()
    release_holder = threading.Event()

    def hold_writer(conn):
        holder_entered.set()
        assert release_holder.wait(timeout=5)

    def wait_for_writer(conn):
        conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("waiter", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(
                db._execute_write,
                hold_writer,
                operation="hold_writer",
            )
            assert holder_entered.wait(timeout=2)
            waiter = pool.submit(
                db._execute_write,
                wait_for_writer,
                operation="wait_for_writer",
            )
            time.sleep(0.05)
            release_holder.set()
            holder.result(timeout=2)
            waiter.result(timeout=2)

    messages = [record.getMessage() for record in caplog.records]
    waiter_log = next(
        message for message in messages if "operation=wait_for_writer" in message
    )
    assert "instance_queue_depth=1" in waiter_log
    assert "instance_owner_operation=hold_writer" in waiter_log
    assert "instance_owner_age_s=" in waiter_log
    assert "instance_owner_transitions=0" in waiter_log
    assert "instance_lock_holder=attributed" in waiter_log
    db.close()


def test_execute_write_attributes_fallback_read_holder(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0
    db._wal_active = False
    holder_entered = threading.Event()
    release_holder = threading.Event()

    def hold_fallback_read():
        with db._read_ctx() as conn:
            assert conn is not None
            conn.execute("SELECT 1").fetchone()
            holder_entered.set()
            assert release_holder.wait(timeout=5)

    def wait_for_read_holder(conn):
        conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("read-waiter", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(hold_fallback_read)
            assert holder_entered.wait(timeout=2)
            waiter = pool.submit(
                db._execute_write,
                wait_for_read_holder,
                operation="wait_for_read_holder",
            )
            time.sleep(0.05)
            release_holder.set()
            holder.result(timeout=2)
            waiter.result(timeout=2)

    message = next(
        record.getMessage()
        for record in caplog.records
        if "operation=wait_for_read_holder" in record.getMessage()
    )
    assert "instance_owner_operation=_read_ctx" in message
    assert "instance_owner_kind=read" in message
    assert "instance_owner_transitions=0" in message
    assert "instance_lock_holder=attributed" in message
    db.close()


def test_execute_write_counts_attributed_owner_transitions_while_queued(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0
    first_entered = threading.Event()
    release_first = threading.Event()
    entered = {name: threading.Event() for name in ("second", "third")}
    release = {name: threading.Event() for name in ("second", "third")}

    def first_writer(conn):
        first_entered.set()
        assert release_first.wait(timeout=5)

    def queued_writer(name):
        def _write(conn):
            entered[name].set()
            assert release[name].wait(timeout=5)
            conn.execute(
                "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                (name, "test", time.time()),
            )

        return _write

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(
                db._execute_write, first_writer, operation="first_writer"
            )
            assert first_entered.wait(timeout=2)
            queued = {
                name: pool.submit(
                    db._execute_write,
                    queued_writer(name),
                    operation=f"{name}_writer",
                )
                for name in ("second", "third")
            }
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with db._write_diag_lock:
                    if db._write_waiter_count == 2:
                        break
                time.sleep(0.005)
            else:
                raise AssertionError("queued writers did not both reach the instance lock")

            release_first.set()
            first.result(timeout=2)
            deadline = time.monotonic() + 2
            winner = None
            while time.monotonic() < deadline:
                winner = next(
                    (name for name, event in entered.items() if event.is_set()), None
                )
                if winner is not None:
                    break
                time.sleep(0.005)
            assert winner is not None
            loser = "third" if winner == "second" else "second"
            release[winner].set()
            queued[winner].result(timeout=2)
            assert entered[loser].wait(timeout=2)
            release[loser].set()
            queued[loser].result(timeout=2)

    loser_log = next(
        record.getMessage()
        for record in caplog.records
        if f"operation={loser}_writer" in record.getMessage()
    )
    transitions = re.search(r"instance_owner_transitions=([0-9]+)", loser_log)
    assert transitions is not None
    assert int(transitions.group(1)) >= 1
    assert f"instance_last_owner_operation={winner}_writer" in loser_log
    assert "instance_lock_holder=attributed" in loser_log
    db.close()


def test_replace_messages_logs_item_count_and_current_write_outcome(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0
    replacement = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.replace_messages("s1", replacement)
        db.replace_messages("s1", replacement)

    messages = [
        record.getMessage()
        for record in caplog.records
        if "operation=replace_messages" in record.getMessage()
    ]
    assert len(messages) == 2
    assert "items=3" in messages[0]
    assert "outcome=write" in messages[0]
    assert "items=3" in messages[1]
    # Current protected head intentionally performs both rewrites; telemetry
    # must describe that behavior rather than reintroduce the superseded
    # branch's no-op optimization as part of a diagnostics-only candidate.
    assert "outcome=write" in messages[1]
    db.close()


def test_execute_write_infers_enclosing_session_operation(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.set_expiry_finalized("s1")

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "caller=_do" in messages
    assert "operation=set_expiry_finalized" in messages
    db.close()


def test_execute_write_reports_retry_elapsed_and_uses_it_for_warning(
    tmp_path, caplog, monkeypatch
):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._conn = _RetryOnceConnection(db._conn)  # type: ignore[assignment]
    db._SLOW_WRITE_WARN_S = 0.015
    db._SLOW_LOCK_WAIT_WARN_S = 60.0
    monkeypatch.setattr("hermes_state.random.uniform", lambda *_args: 0.03)

    def insert_session(conn):
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("retried-session", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._execute_write(insert_session)

    message = next(
        record.getMessage()
        for record in caplog.records
        if "operation=insert_session" in record.getMessage()
    )
    elapsed_s = _logged_seconds(message, "elapsed")
    total_s = _logged_seconds(message, "total")
    sqlite_retry_wait_s = _logged_seconds(message, "sqlite_retry_wait")
    assert "attempt=2" in message
    assert sqlite_retry_wait_s >= 0.025
    assert elapsed_s >= 0.025
    assert elapsed_s > total_s
    db.close()


def test_nonwrite_lock_logs_content_free_holder_latency(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_LOCK_HOLD_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        with db._attributed_lock("maintenance_probe", holder_kind="maintenance"):
            time.sleep(0.01)

    message = next(
        record.getMessage()
        for record in caplog.records
        if "SessionDB lock latency" in record.getMessage()
    )
    assert "operation=maintenance_probe" in message
    assert "holder_kind=maintenance" in message
    assert "wait=" in message
    assert "hold=" in message
    assert f"pid={os.getpid()}" in message
    assert f"thread_id={threading.get_ident()}" in message
    assert "db_instance=" in message
    assert str(tmp_path) not in message
    db.close()


def test_attributed_lock_clears_owner_after_exception_and_cancellation(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")

    for error_type in (RuntimeError, asyncio.CancelledError):
        try:
            with db._attributed_lock("failing_lifecycle", holder_kind="lifecycle"):
                raise error_type("expected")
        except error_type as exc:
            assert str(exc) == "expected"
        else:
            raise AssertionError(f"expected {error_type.__name__}")

        with db._write_diag_lock:
            assert db._write_waiter_count == 0
            assert db._write_owner_operation is None
            assert db._write_owner_kind is None
            assert db._write_owner_started is None
        assert db._lock.acquire(timeout=0.1)
        db._lock.release()
    db.close()


def test_passive_checkpoint_is_attributed_as_maintenance(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_LOCK_HOLD_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._try_wal_checkpoint()

    message = next(
        record.getMessage()
        for record in caplog.records
        if "SessionDB lock latency" in record.getMessage()
    )
    assert "operation=_try_wal_checkpoint" in message
    assert "holder_kind=maintenance" in message
    db.close()


def test_incremental_fts_merge_is_attributed_as_maintenance(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_LOCK_HOLD_WARN_S = 0.0
    db._fts_enabled = True

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._try_incremental_merge_fts()

    messages = [
        record.getMessage()
        for record in caplog.records
        if "SessionDB lock latency" in record.getMessage()
    ]
    assert any("operation=_merge_fts_incrementally" in message for message in messages)
    assert any("holder_kind=maintenance" in message for message in messages)
    db.close()
