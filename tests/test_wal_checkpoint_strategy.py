"""Tests for SessionDB WAL checkpoint strategy (issue #45383).

Verifies that periodic checkpoints use PASSIVE mode (safe for large DBs)
while close() and pre-VACUUM paths still use TRUNCATE.
"""

import sqlite3
import logging
from unittest.mock import MagicMock, patch

import pytest

from hermes_state import SessionDB, _CheckpointCoordinator


@pytest.fixture()
def db(tmp_path):
    """Create a SessionDB with a temp database file."""
    db_path = tmp_path / "test_state.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    try:
        session_db.close()
    except Exception:
        pass


class TestTryWalCheckpointPassive:
    """_try_wal_checkpoint() should use PASSIVE mode for periodic use."""

    def test_checkpoint_uses_passive_mode(self, db):
        """PASSIVE checkpoint does not require exclusive lock — safe for large DBs."""
        # Capture the real connection's execute before mocking
        real_conn = db._conn
        execute_calls = []

        def tracking_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return real_conn.execute(sql, *args, **kwargs)

        # sqlite3.Connection.execute is read-only (C extension) — replace _conn
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = tracking_execute
        mock_conn.fetchone.return_value = None
        db._conn = mock_conn

        db._try_wal_checkpoint()

        passive_calls = [c for c in execute_calls if "wal_checkpoint(PASSIVE)" in c]
        truncate_calls = [c for c in execute_calls if "wal_checkpoint(TRUNCATE)" in c]
        assert len(passive_calls) == 1, (
            f"Expected 1 PASSIVE checkpoint call, got {len(passive_calls)}"
        )
        assert len(truncate_calls) == 0, (
            "Periodic checkpoint should NOT use TRUNCATE"
        )

    def test_checkpoint_logs_warning_on_failure(self, db, caplog):
        """Failed PASSIVE checkpoint logs a warning instead of silent pass."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        db._conn = mock_conn

        with caplog.at_level(logging.WARNING):
            db._try_wal_checkpoint()

        assert any("WAL checkpoint (PASSIVE) failed" in r.message for r in caplog.records), (
            f"Expected warning log about PASSIVE checkpoint failure, got: {caplog.text}"
        )

    def test_checkpoint_returns_result_on_success(self, db):
        """Successful PASSIVE checkpoint does not raise."""
        db._try_wal_checkpoint()


class TestCloseDoesNotCheckpoint:
    """Per-instance close must not checkpoint a WAL shared across processes."""

    def test_close_does_not_truncate_wal(self, db):
        """Instance close must not TRUNCATE a WAL shared with other processes."""
        real_conn = db._conn
        execute_calls = []

        def tracking_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return real_conn.execute(sql, *args, **kwargs)

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = tracking_execute
        db._conn = mock_conn

        db.close()

        truncate_calls = [c for c in execute_calls if "wal_checkpoint(TRUNCATE)" in c]
        assert truncate_calls == []

    def test_close_skips_truncate_while_same_path_has_another_owner(self, tmp_path):
        """Closing one gateway SessionDB must not checkpoint under live peers."""
        db_path = tmp_path / "shared-close-truncate.db"
        first = SessionDB(db_path=db_path)
        second = SessionDB(db_path=db_path)
        real_conn = first._conn
        execute_calls = []

        def tracking_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return real_conn.execute(sql, *args, **kwargs)

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = tracking_execute
        first._conn = mock_conn
        try:
            first.close()
            assert not any("wal_checkpoint(TRUNCATE)" in sql for sql in execute_calls)
        finally:
            second.close()

class TestCheckpointCoordinator:
    """Checkpoint coordinator safety: separate connection, no write-lock hold,
    singleton per SessionDB, joined on close.
    """

    def test_coordinator_runs_on_dedicated_connection(self, db, monkeypatch):
        """Coordinator's sqlite connection is distinct from SessionDB._conn."""
        import time as _time

        monkeypatch.setattr(db, "_CHECKPOINT_INTERVAL_S", 0.05)
        db._checkpoint_coordinator.stop(timeout=1.0)
        db._checkpoint_coordinator = _CheckpointCoordinator(
            db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
        )
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("sess_conn", "test", _time.time()),
        ))
        _time.sleep(0.15)
        assert db._checkpoint_coordinator._conn is not None
        assert db._checkpoint_coordinator._conn is not db._conn

    def test_coordinator_does_not_hold_write_lock(self, db, monkeypatch):
        """Checkpoint executes without SessionDB._lock being held."""
        import time as _time
        from unittest.mock import MagicMock

        monkeypatch.setattr(db, "_CHECKPOINT_INTERVAL_S", 0.05)
        db._checkpoint_coordinator.stop(timeout=1.0)
        db._checkpoint_coordinator = _CheckpointCoordinator(
            db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
        )

        lock_held_during_checkpoint = []
        original_checkpoint = db._checkpoint_coordinator._run_checkpoint

        def instrumented_checkpoint():
            lock_held_during_checkpoint.append(db._lock.locked())
            return original_checkpoint()

        db._checkpoint_coordinator._run_checkpoint = instrumented_checkpoint

        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("sess_lock", "test", _time.time()),
        ))
        _time.sleep(0.15)
        assert lock_held_during_checkpoint, "coordinator should have run at least once"
        assert not any(lock_held_during_checkpoint), (
            "SessionDB._lock must not be held while coordinator runs checkpoint"
        )

    def test_single_coordinator_per_instance(self, db, monkeypatch):
        """Starting the coordinator twice should not create a second thread."""
        monkeypatch.setattr(db, "_CHECKPOINT_INTERVAL_S", 1.0)
        db._checkpoint_coordinator.stop(timeout=1.0)
        db._checkpoint_coordinator = _CheckpointCoordinator(
            db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
        )
        first = db._checkpoint_coordinator.start()
        second = db._checkpoint_coordinator.start()
        assert first is True
        assert second is False

    def test_stop_serializes_with_concurrent_start(self, tmp_path):
        """A new coordinator thread cannot start until the old one has joined."""
        import threading
        import time as _time

        coordinator = _CheckpointCoordinator(tmp_path / "stop-start.db", 0.01, 2.0)
        checkpoint_entered = threading.Event()
        release_checkpoint = threading.Event()

        def blocking_checkpoint():
            checkpoint_entered.set()
            release_checkpoint.wait(timeout=2.0)

        coordinator._run_checkpoint = blocking_checkpoint
        coordinator.start()
        assert checkpoint_entered.wait(timeout=1.0)

        stop_thread = threading.Thread(target=coordinator.stop)
        stop_thread.start()
        _time.sleep(0.05)

        start_done = threading.Event()
        start_result = []

        def restart():
            start_result.append(coordinator.start())
            start_done.set()

        start_thread = threading.Thread(target=restart)
        start_thread.start()
        try:
            assert not start_done.wait(timeout=0.05)
        finally:
            release_checkpoint.set()
            stop_thread.join(timeout=1.0)
            start_thread.join(timeout=1.0)
            coordinator.stop(timeout=1.0)
        assert start_result == [True]

    def test_coordinator_connection_can_be_closed_by_stopper(self, tmp_path, monkeypatch):
        """The joined coordinator connection must be safely closable by stop()."""
        connect_kwargs = []
        fake_conn = MagicMock()

        def fake_connect(*args, **kwargs):
            connect_kwargs.append(kwargs)
            return fake_conn

        monkeypatch.setattr(sqlite3, "connect", fake_connect)
        coordinator = _CheckpointCoordinator(tmp_path / "thread-close.db", 60.0, 2.0)
        assert coordinator._ensure_conn() is fake_conn
        assert connect_kwargs[0]["check_same_thread"] is False

    def test_final_release_keeps_registry_when_stop_times_out(self, tmp_path, monkeypatch):
        """A still-running coordinator cannot be replaced under a new owner."""
        import hermes_state

        key, coordinator = hermes_state._acquire_checkpoint_coordinator(
            tmp_path / "stop-timeout.db", 60.0, 2.0
        )
        monkeypatch.setattr(coordinator, "stop", lambda timeout=10.0: False)
        try:
            assert hermes_state._release_checkpoint_coordinator(key, coordinator) is False
            assert hermes_state._CHECKPOINT_COORDINATORS[key] == (coordinator, 0)
        finally:
            hermes_state._CHECKPOINT_COORDINATORS.pop(key, None)

    def test_close_is_single_release_under_concurrency(self, tmp_path, monkeypatch):
        """Two concurrent close calls release one registry owner exactly once."""
        import threading
        import time as _time
        import hermes_state

        db = SessionDB(tmp_path / "double-close.db")
        real_release = hermes_state._release_checkpoint_coordinator
        release_entered = threading.Event()
        allow_release = threading.Event()
        calls = []
        calls_lock = threading.Lock()

        def blocking_release(*args, **kwargs):
            with calls_lock:
                calls.append(1)
                first = len(calls) == 1
            if first:
                release_entered.set()
                allow_release.wait(timeout=2.0)
            return real_release(*args, **kwargs)

        monkeypatch.setattr(hermes_state, "_release_checkpoint_coordinator", blocking_release)
        first = threading.Thread(target=db.close)
        second = threading.Thread(target=db.close)
        first.start()
        assert release_entered.wait(timeout=1.0)
        second.start()
        _time.sleep(0.05)
        allow_release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)
        assert calls == [1]

    def test_close_cannot_be_followed_by_late_coordinator_start(self, tmp_path):
        """A successful write racing close cannot restart an orphan coordinator."""
        import threading
        import time as _time

        db = SessionDB(tmp_path / "write-close.db")
        coordinator = db._checkpoint_coordinator
        original_start = coordinator.start
        start_entered = threading.Event()
        allow_start = threading.Event()

        def delayed_start():
            start_entered.set()
            allow_start.wait(timeout=2.0)
            return original_start()

        coordinator.start = delayed_start
        writer = threading.Thread(target=lambda: db._execute_write(lambda conn: None))
        writer.start()
        assert start_entered.wait(timeout=1.0)
        closer = threading.Thread(target=db.close)
        closer.start()
        _time.sleep(0.05)
        allow_start.set()
        writer.join(timeout=1.0)
        closer.join(timeout=1.0)
        assert db._conn is None
        assert coordinator._thread is None or not coordinator._thread.is_alive()

    def test_coordinator_waits_for_interval_before_first_checkpoint(self, tmp_path):
        """The first ordinary write must not trigger an immediate checkpoint."""
        import threading

        coordinator = _CheckpointCoordinator(tmp_path / "deferred.db", 0.15, 2.0)
        ran = threading.Event()
        coordinator._run_checkpoint = ran.set
        try:
            coordinator.start()
            assert not ran.wait(timeout=0.05)
            assert ran.wait(timeout=0.30)
        finally:
            coordinator.stop(timeout=1.0)

    def test_session_dbs_for_same_path_share_one_coordinator(self, tmp_path):
        """Many gateway SessionDB objects must not create a checkpoint storm."""
        db_path = tmp_path / "shared.db"
        first = SessionDB(db_path=db_path)
        second = SessionDB(db_path=db_path)
        try:
            assert first._checkpoint_coordinator is second._checkpoint_coordinator
        finally:
            first.close()
            second.close()

    def test_closing_one_session_db_keeps_shared_coordinator_for_other(self, tmp_path):
        """A shared coordinator stops only after the final owner closes."""
        db_path = tmp_path / "shared-close.db"
        first = SessionDB(db_path=db_path)
        second = SessionDB(db_path=db_path)
        coordinator = first._checkpoint_coordinator
        coordinator.start()
        thread = coordinator._thread
        assert thread is not None and thread.is_alive()

        first.close()
        assert thread.is_alive()

        second.close()
        assert not thread.is_alive()

    def test_coordinator_joined_on_close(self, db, monkeypatch):
        """close() must join the coordinator thread."""
        import time as _time

        monkeypatch.setattr(db, "_CHECKPOINT_INTERVAL_S", 1.0)
        db._checkpoint_coordinator.stop(timeout=1.0)
        db._checkpoint_coordinator = _CheckpointCoordinator(
            db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
        )
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("sess_close", "test", _time.time()),
        ))
        thread = db._checkpoint_coordinator._thread
        assert thread is not None
        db.close()
        assert not thread.is_alive(), "coordinator thread should be joined on close"

    def test_no_inline_checkpoint_in_execute_write(self, db, monkeypatch):
        """_execute_write must not call _try_wal_checkpoint inline."""
        import time as _time

        call_count = [0]
        original = db._try_wal_checkpoint

        def counting_checkpoint():
            call_count[0] += 1
            return original()

        db._try_wal_checkpoint = counting_checkpoint
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("sess_inline", "test", _time.time()),
        ))
        # The coordinator is independent of _try_wal_checkpoint; inline calls
        # from _execute_write are expected to be gone.
        assert call_count[0] == 0, (
            "_execute_write should not trigger inline _try_wal_checkpoint"
        )


class TestCheckpointFrequency:
    """Periodic checkpoint is now driven by a dedicated background coordinator,
    not inline on every N writes.  This test guards the new behavior.
    """

    def test_checkpoint_runs_on_coordinator_interval(self, db, monkeypatch):
        """The coordinator runs PASSIVE checkpoints on its own connection and interval."""
        import time as _time
        from unittest.mock import patch

        # Short interval so the test doesn't wait a full minute.
        monkeypatch.setattr(db, "_CHECKPOINT_INTERVAL_S", 0.05)
        # Recreate coordinator with the new interval (don't mutate the running one).
        db._checkpoint_coordinator.stop(timeout=1.0)
        db._checkpoint_coordinator = _CheckpointCoordinator(
            db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
        )

        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("sess_0", "test", _time.time()),
        ))
        # Wait long enough for the coordinator to tick at least once.
        _time.sleep(0.15)
        # The coordinator must have its own connection, distinct from db._conn.
        assert db._checkpoint_coordinator._conn is not None
        assert db._checkpoint_coordinator._conn is not db._conn

