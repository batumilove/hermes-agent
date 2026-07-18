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


class TestCloseUsesTruncate:
    """close() should still use TRUNCATE to shrink WAL on shutdown."""

    def test_close_uses_truncate_mode(self, db):
        """TRUNCATE at close is safe — no concurrent writers during shutdown."""
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
        assert len(truncate_calls) == 1, (
            f"Expected 1 TRUNCATE checkpoint at close, got {len(truncate_calls)}"
        )

    def test_close_logs_debug_on_failure(self, db, caplog):
        """Failed TRUNCATE at close logs debug (not warning — close is best-effort)."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        db._conn = mock_conn

        with caplog.at_level(logging.DEBUG):
            db.close()

        assert any("WAL checkpoint (TRUNCATE) at close failed" in r.message for r in caplog.records), (
            f"Expected debug log about TRUNCATE failure at close, got: {caplog.text}"
        )


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

