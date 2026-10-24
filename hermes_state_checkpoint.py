"""One-shot periodic WAL checkpoints outside the writer mutex."""

from __future__ import annotations

import contextvars
import logging
import sqlite3
import threading
import time
from typing import TypeVar

logger = logging.getLogger("hermes_state")
T = TypeVar("T")
import hermes_state_lockguard as _lockguard
from hermes_state_common import stat_db_file_identity
from hermes_state_dbfile import _connect_tracked_db


class SessionCheckpointMixin:
    _CHECKPOINT_MIN_INTERVAL_S = 60.0

    def _checkpoint_now(self) -> None:
        """Run one PASSIVE checkpoint WITHOUT holding the instance mutex.

        The periodic checkpoint used to run inside
        ``_attributed_lock(..., holder_kind="maintenance")`` — the same
        instance mutex every writer needs. PASSIVE never blocks at the
        SQLite level, so the mutex is not required for it, and holding it
        turned multi-second checkpoint fsyncs (cost scales with the main
        file size; 9+ GB in production) into a writer convoy: observed
        24-81s holds with queue_depth=10, writer waits of 29-49s, and a
        close() hold of 188.7s — enough to trip the gateway loop-liveness
        watchdog (exit 75, 2026-08-23, twice).

        Uses a dedicated short-lived connection: sqlite3 connections are
        not safe for cross-thread use, and this runs on the checkpoint
        worker thread, not the writer thread that owns ``self._conn``.
        """
        if self._quarantine_reason() is not None or self._conn is None:
            return
        identity = stat_db_file_identity(self.db_path)
        if identity != self._db_file_identity:
            return
        conn = None
        guard = {}
        try:
            conn = _connect_tracked_db(
                str(self.db_path),
                tracking_path=self.db_path,
                timeout=5.0,
                isolation_level=None,
            )
            guard = _lockguard.hold(self.db_path)
            if (
                stat_db_file_identity(self.db_path) != identity
                or self._quarantine_reason() is not None
            ):
                return
            result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            if result and result[1] > 0:
                logger.debug(
                    "WAL checkpoint: %d/%d pages checkpointed",
                    result[2],
                    result[1],
                )
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            logger.warning("WAL checkpoint (PASSIVE) failed: %s", exc)
        finally:
            _lockguard.release(guard)
            if conn is not None:
                try:
                    conn.close()
                except (sqlite3.Error, OSError, RuntimeError) as exc:
                    logger.debug("Checkpoint connection close failed: %s", exc)

    def _checkpoint_worker_once(self) -> None:
        """Run one checkpoint and retire the daemon worker.

        A permanent bound-method worker would retain ``self`` (and therefore
        a closed SessionDB) forever.  A one-shot worker retains the instance
        only for the bounded in-flight checkpoint and then clears its slot.
        """
        try:
            self._checkpoint_now()
        finally:
            with self._checkpoint_schedule_lock:
                if self._checkpoint_worker is threading.current_thread():
                    self._checkpoint_worker = None

    def _try_wal_checkpoint(self) -> None:
        """Throttled best-effort PASSIVE WAL checkpoint.  Never raises.

        Flushes committed WAL frames back into the main DB file without
        requiring an exclusive lock.  PASSIVE is safe for frequent
        periodic use because it does not block concurrent writers and
        cannot corrupt B-tree pages under I/O pressure.

        Throttled: at most one checkpoint per ``_CHECKPOINT_MIN_INTERVAL_S``
        so a write burst past the counter threshold cannot fire
        back-to-back checkpoints (each costing an fsync scaled to the main
        file size). The checkpoint itself runs in ``_checkpoint_now``
        WITHOUT the instance mutex — see that method for the convoy history.

        PASSIVE does not truncate the WAL file — it stays at its
        high-water mark. Explicit checkpoints on the shared ``state.db`` no
        longer truncate the WAL; it is bounded by ``journal_size_limit`` and
        the writer's natural post-checkpoint reset rather than by a TRUNCATE
        at every close or maintenance command.

        Previous TRUNCATE strategy caused B-tree corruption on large
        databases (65K+ pages) due to the exclusive-lock I/O pressure
        from checkpointing thousands of frames at once (issue #45383).
        """
        now = time.monotonic()
        # Never run the checkpoint synchronously on the calling writer's
        # thread: a PASSIVE checkpoint fsync costs seconds on a multi-GB
        # database (observed 24-81s), and the calling writer would pay that
        # latency directly. Scheduling is serialized so simultaneous writers
        # cannot start overlapping workers or race the throttle timestamp.
        with self._checkpoint_schedule_lock:
            last = self._last_checkpoint_monotonic
            if last is not None and (now - last) < self._CHECKPOINT_MIN_INTERVAL_S:
                return
            worker = self._checkpoint_worker
            if worker is not None and worker.is_alive():
                return
            self._last_checkpoint_monotonic = now
            worker = threading.Thread(
                target=contextvars.copy_context().run,
                args=(self._checkpoint_worker_once,),
                name=f"sessiondb-wal-checkpoint-{id(self):x}",
                daemon=True,
            )
            self._checkpoint_worker = worker
            try:
                worker.start()
            except (sqlite3.Error, OSError, RuntimeError) as exc:
                # Under thread/resource exhaustion, skipping a best-effort
                # checkpoint is safer than reintroducing an 80-second
                # synchronous stall on the writer/event-loop thread.
                if self._checkpoint_worker is worker:
                    self._checkpoint_worker = None
                self._last_checkpoint_monotonic = last
                logger.warning("WAL checkpoint worker start failed: %s", exc)
