"""Tests for SessionDB WAL checkpoint strategy (issue #45383).

Verifies that periodic checkpoints use PASSIVE mode (safe for large DBs)
while close() and pre-VACUUM paths still use TRUNCATE.
"""

import sqlite3
import logging
import tempfile
import time
from pathlib import Path
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
        """A new coordinator thread cannot start while the old one is alive."""
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

        # While the old thread is still alive, start() must refuse.
        assert coordinator.start() is False

        # Once the old thread exits, start() can restart cleanly.
        release_checkpoint.set()
        stop_thread.join(timeout=1.0)
        assert coordinator.start() is True
        coordinator.stop(timeout=1.0)

    def test_coordinator_connection_uses_thread_affinity(self, tmp_path, monkeypatch):
        """The coordinator connection is created with check_same_thread=True.

        The connection is opened and closed on the dedicated coordinator
        thread, so SQLite thread-affinity checks are safe.
        """
        connect_kwargs = []
        fake_conn = MagicMock()

        def fake_connect(*args, **kwargs):
            connect_kwargs.append(kwargs)
            return fake_conn

        monkeypatch.setattr(sqlite3, "connect", fake_connect)
        coordinator = _CheckpointCoordinator(tmp_path / "thread-close.db", 60.0, 2.0)
        assert coordinator._ensure_conn() is fake_conn
        assert connect_kwargs[0]["check_same_thread"] is True

    def test_coordinator_connection_closed_by_thread(self, tmp_path):
        """The coordinator thread itself closes its connection in _loop finally."""
        coordinator = _CheckpointCoordinator(tmp_path / "thread-close.db", 0.01, 2.0)
        coordinator.start()
        # Wait for the first iteration to open the connection.
        time.sleep(0.05)
        coordinator.stop(timeout=1.0)
        assert coordinator._thread is None
        assert coordinator._conn is None

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
        real_release = hermes_state._release_checkpoint_coordinator_with_state
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

        monkeypatch.setattr(hermes_state, "_release_checkpoint_coordinator_with_state", blocking_release)
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


def _fds_for_path(path: Path) -> list[str]:
    """Return currently open FD entries under /proc/self/fd pointing at *path*."""
    fds = []
    target = path.resolve()
    for fd_str in Path("/proc/self/fd").glob("*"):
        try:
            resolved = fd_str.resolve()
        except (OSError, ValueError):
            continue
        if resolved == target:
            fds.append(fd_str.name)
    return fds


class TestCheckpointCoordinatorNoFdLeak:
    """Starting the coordinator and closing SessionDB must not leak file descriptors."""

    def test_close_leaves_no_db_fds_after_coordinator_started(self, tmp_path):
        """Regression for P0 review: SessionDB.close() releases coordinator FDs."""
        db_path = tmp_path / "leak_state.db"
        db = SessionDB(db_path=db_path)
        db._CHECKPOINT_INTERVAL_S = 0.05
        db._checkpoint_coordinator.stop(timeout=1.0)
        db._checkpoint_coordinator = _CheckpointCoordinator(
            db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
        )

        # Force a write so the coordinator starts (lazily) and opens its own connection.
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("fd-leak", "test", time.time()),
        ))
        # Give the coordinator enough time to open its sqlite connection.
        time.sleep(0.15)

        before_close = _fds_for_path(db_path)
        assert before_close, "expected coordinator to have opened the database file"

        db.close()

        # After close, no state.db / wal / shm FDs may remain for this temp database.
        for suffix in ("", "-wal", "-shm"):
            residual = _fds_for_path(db_path.parent / (db_path.name + suffix))
            assert residual == [], (
                f"leaked FDs for {db_path.name}{suffix} after close: {residual}"
            )


class TestCheckpointCoordinatorStopTimeout:
    """stop(timeout) must not allow a new thread while the old one is alive."""

    def test_stop_timeout_does_not_forget_live_thread(self):
        """Regression for review BLOCK: a join timeout must not orphan the thread."""
        import threading as _threading

        db_path = Path(tempfile.mkdtemp()) / "timeout_state.db"
        c = _CheckpointCoordinator(db_path, 0.01, 2.0)
        entered = _threading.Event()
        release = _threading.Event()

        def blocked_checkpoint():
            entered.set()
            release.wait(timeout=5.0)

        c._run_checkpoint = blocked_checkpoint
        assert c.start() is True
        assert entered.wait(timeout=1.0), "coordinator thread did not enter _run_checkpoint"
        first_thread = c._thread
        assert first_thread is not None

        c.stop(timeout=0.01)

        # The old thread may still be alive (blocked) after the short timeout.
        # The coordinator must retain a reference to it so start() cannot launch
        # a second loop concurrently.
        assert c._thread is first_thread, (
            "stop() forgot the live thread before it exited"
        )
        assert c.start() is False, "start() must refuse while prior thread is alive"
        assert c._thread is first_thread, (
            "start() replaced the live thread reference"
        )

        release.set()
        first_thread.join(timeout=1.0)
        assert not first_thread.is_alive(), "first thread did not exit after release"

        # Once the old thread has exited, explicit restart is safe and allowed.
        assert c.start() is True
        second_thread = c._thread
        assert second_thread is not first_thread
        assert second_thread.is_alive()
        c.stop(timeout=1.0)
        assert not second_thread.is_alive()

    def test_stop_retries_join_until_thread_exits(self):
        """stop() on a stuck thread can be repeated; each call keeps joining the same thread."""
        import threading as _threading
        import tempfile as _tempfile

        db_path = Path(_tempfile.mkdtemp()) / "retry_state.db"
        c = _CheckpointCoordinator(db_path, 0.01, 2.0)
        entered = _threading.Event()
        release = _threading.Event()

        def blocked_checkpoint():
            entered.set()
            release.wait(timeout=5.0)

        c._run_checkpoint = blocked_checkpoint
        c.start()
        assert entered.wait(timeout=1.0)
        thread = c._thread

        c.stop(timeout=0.005)
        assert c._thread is thread, "first stop should still reference the live thread"
        c.stop(timeout=0.005)
        assert c._thread is thread, "second stop should still reference the same live thread"

        release.set()
        c.stop(timeout=1.0)
        assert not thread.is_alive()
        assert c._thread is None, "thread ref should be cleared once the thread exits"

    def test_stop_idempotent_after_thread_exits(self):
        """stop() after a clean shutdown is a no-op and does not resurrect a thread."""
        import tempfile as _tempfile

        db_path = Path(_tempfile.mkdtemp()) / "idempotent_state.db"
        c = _CheckpointCoordinator(db_path, 100.0, 2.0)
        c.start()
        thread = c._thread
        c.stop(timeout=1.0)
        assert c._thread is None
        # Another stop should be safe and leave thread None.
        c.stop(timeout=1.0)
        assert c._thread is None
        assert not thread.is_alive()
