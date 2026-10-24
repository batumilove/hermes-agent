"""Write admission, phase attribution and upstream-safe transaction retries."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from typing import Callable, Optional, TypeVar

logger = logging.getLogger("hermes_state")
T = TypeVar("T")
from hermes_state_admission import (
    _SESSION_DB_LIVE_REGISTRY,
    SessionDBWriteAdmission,
    SessionDBWriteAdmissionClosedError,
    _AdmissionToken,
    _current_session_key_for_admission,
)
from hermes_state_errors import (
    _DISK_IO_ERROR_MARKER,
    SessionCompressionInProgressError,
    is_malformed_db_error,
    is_sqlite_lock_error,
)
from hermes_state_lockowners import log_write_lock_holders


class SessionWriteMixin:
    _SLOW_WRITE_WARN_S = 1.0
    _SLOW_LOCK_WAIT_WARN_S = 0.25
    _SLOW_LOCK_HOLD_WARN_S = 1.0

    @contextmanager
    def _attributed_lock(self, operation_label: str, *, holder_kind: str):
        """Hold the instance mutex with bounded, content-free attribution."""
        with self._lock.hold(operation_label, holder_kind) as diagnostics:
            yield diagnostics

    @contextmanager
    def _attributed_write_lock(self, operation_label: str):
        """Hold the writer mutex while preserving the existing diagnostics API."""
        with self._attributed_lock(operation_label, holder_kind="write") as diagnostics:
            yield diagnostics

    def _execute_write(
        self,
        fn: Callable[[sqlite3.Connection], T],
        patience_s: Optional[float] = None,
        *,
        operation: Optional[str] = None,
        items: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> T:
        """Execute a write transaction with BEGIN IMMEDIATE and jitter retry.

        *fn* receives the connection and should perform INSERT/UPDATE/DELETE
        statements.  The caller must NOT call ``commit()`` — that's handled
        here after *fn* returns.

        BEGIN IMMEDIATE acquires the WAL write lock at transaction start
        (not at commit time), so lock contention surfaces immediately.
        On ``database is locked``, we release the Python lock, sleep a
        random jitter, and retry — breaking the convoy pattern that
        SQLite's built-in deterministic backoff creates.

        *patience_s* is the total time budget for lock retries (default
        ``_WRITE_PATIENCE_S``).  Transcript-critical writes pass
        ``_TRANSCRIPT_WRITE_PATIENCE_S`` so a sibling process holding the
        lock for a legitimate long operation (VACUUM, TRUNCATE checkpoint,
        pre-bounded-merge FTS optimize from an older still-running
        install) exhausts routine writers' patience without destroying a
        user turn.  Jitter starts small (20-150ms) for fast reclaim on
        millisecond contention and backs off to 250ms-1s once the lock has
        been held longer than ``_WRITE_RETRY_SLOW_AFTER_S``.

        Returns whatever *fn* returns.
        """
        if patience_s is None:
            patience_s = self._WRITE_PATIENCE_S
        caller_name = getattr(fn, "__name__", type(fn).__name__)
        if operation is None and caller_name == "_do":
            qualname = getattr(fn, "__qualname__", "")
            enclosing = qualname.rsplit(".<locals>.", 1)[0]
            inferred_operation = (
                enclosing.rsplit(".", 1)[-1] if enclosing else caller_name
            )
        else:
            inferred_operation = caller_name
        operation_name = str(operation or inferred_operation)
        operation_label = re.sub(r"[\r\n\t]+", " ", operation_name)[:96]
        reason_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason or "-"))[:64]
        # Gateway-wide write admission (P0-A part 2): hold a slot on the
        # shared per-path controller for the ENTIRE call — lock retries,
        # BEGIN..commit, and post-commit checkpoint work all happen inside
        # one admission. Queue-full raises SessionDBWriteAdmissionFullError
        # (a sqlite3.OperationalError subclass carrying retry_after_s) so
        # agent-serving endpoints can answer 429 + Retry-After without ever
        # reaching the disk. Skipped for read_only handles (no writes to
        # admit) and when admission was already re-entered by a nested write
        # in this thread (the outer write is the admitted unit).
        if self.read_only:
            admission_ctx = nullcontext(None)
        else:
            try:
                admission_ctx = self._write_admission().acquire(
                    session_key=_current_session_key_for_admission(),
                    operation=operation_label,
                )
            except SessionDBWriteAdmissionClosedError:
                # Drain in progress: endpoint admission already fails fast;
                # the persistence layer stays fail-open so shutdown flushes
                # and straggler writes never lose data to a fairness bound.
                admission_ctx = nullcontext(None)
        call_started = time.monotonic()
        with admission_ctx as admission_token:
            admission_diag = (
                admission_token.diagnostics()
                if isinstance(admission_token, _AdmissionToken)
                else None
            )
            return self._execute_write_admitted(
                fn,
                patience_s,
                operation_label,
                reason_label,
                items,
                caller_name,
                call_started,
                admission_diag,
            )

    def _prepare_admitted_writer(self) -> None:
        self._raise_if_db_replaced()
        if self._conn is None:
            self._reopen_after_close_locked(context="write")

    def _execute_write_admitted(
        self,
        fn,
        patience_s,
        operation_label,
        reason_label,
        items,
        caller_name,
        call_started,
        admission_diag,
    ):
        deadline = call_started + patience_s
        # Set on the first compression-busy collision so the short wait is
        # measured from then, not from the start of the write.
        compression_deadline: Optional[float] = None
        ioerr_begin_retried = False
        attempt = 0
        sqlite_retry_wait = 0.0
        admission_diag = admission_diag or {}
        admission_wait = float(admission_diag.get("wait", 0.0))
        admission_queue_depth = int(admission_diag.get("queue_depth", 0))
        admission_owner_operation = str(admission_diag.get("owner_operation", "-"))
        admission_owner_age_s = float(admission_diag.get("owner_age_s", 0.0))
        admission_owner_transitions = int(admission_diag.get("owner_transitions", 0))
        admission_last_owner_operation = str(
            admission_diag.get("last_owner_operation", "-")
        )
        admission_holder = str(admission_diag.get("holder", "none"))

        # Transient engine-level error observed on contended WAL appends.
        # SQLite's exception class varies by build: some versions surface it
        # as InterfaceError, which is a sibling of DatabaseError. Keep the
        # policy message-scoped so unrelated sqlite3 errors still fail fast.
        def _is_no_more_rows(exc: sqlite3.Error) -> bool:
            return "no more rows available" in str(exc).lower()

        def _sleep_before_retry() -> bool:
            nonlocal sqlite_retry_wait, attempt
            sleep_started = time.monotonic()
            slept = self._sleep_before_write_retry(deadline, patience_s)
            if slept:
                sqlite_retry_wait += time.monotonic() - sleep_started
                attempt += 1
            return slept

        while True:
            self._raise_if_db_corrupt(storage=True)
            fn_started = False
            try:
                write_started = time.monotonic()
                with self._attributed_write_lock(operation_label) as lock_diag:
                    (
                        lock_wait,
                        queue_depth,
                        owner_operation,
                        owner_age_s,
                        owner_transitions,
                        last_owner_operation,
                        lock_holder,
                        owner_kind,
                        last_owner_kind,
                    ) = lock_diag
                    self._prepare_admitted_writer()
                    transaction_started = time.monotonic()
                    begin_started = time.monotonic()
                    self._conn.execute("BEGIN IMMEDIATE")
                    begin_wait = time.monotonic() - begin_started
                    try:
                        callback_started = time.monotonic()
                        fn_started = True
                        result = fn(self._conn)
                        callback_time = time.monotonic() - callback_started
                        commit_started = time.monotonic()
                        self._conn.commit()
                        commit_time = time.monotonic() - commit_started
                        transaction_time = time.monotonic() - transaction_started
                        # Keep cadence accounting inside the same serialized
                        # critical section as the successful write.  The
                        # maintenance work itself remains outside this lock.
                        self._write_count += 1
                        pending_checkpoint = (
                            self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0
                        )
                        pending_fts_merge = (
                            self._write_count % self._FTS_MERGE_EVERY_N_WRITES == 0
                        )
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except (sqlite3.Error, OSError, RuntimeError) as rollback_exc:
                            logger.debug("SessionDB rollback failed: %s", rollback_exc)
                        raise
                total_time = time.monotonic() - write_started
                elapsed_total = time.monotonic() - call_started
                if (
                    lock_wait >= self._SLOW_LOCK_WAIT_WARN_S
                    or elapsed_total >= self._SLOW_WRITE_WARN_S
                ):
                    logger.warning(
                        "SessionDB write latency: caller=%s operation=%s "
                        "reason=%s items=%s outcome=write lock_wait=%.3fs sqlite_retry_wait=%.3fs "
                        "begin_wait=%.3fs callback=%.3fs commit=%.3fs "
                        "txn=%.3fs total=%.3fs elapsed=%.3fs attempt=%d pid=%d "
                        "thread_id=%d db_instance=%s instance_queue_depth=%d "
                        "instance_owner_operation=%s instance_owner_kind=%s "
                        "instance_owner_age_s=%.3f instance_owner_transitions=%d "
                        "instance_last_owner_operation=%s instance_last_owner_kind=%s "
                        "instance_lock_holder=%s admission_wait=%.3fs "
                        "admission_queue_depth=%d admission_owner_operation=%s "
                        "admission_owner_age_s=%.3f admission_owner_transitions=%d "
                        "admission_last_owner_operation=%s admission_holder=%s "
                        "live_instances_total=%d",
                        caller_name,
                        operation_label,
                        reason_label,
                        items if items is not None else "-",
                        lock_wait,
                        sqlite_retry_wait,
                        begin_wait,
                        callback_time,
                        commit_time,
                        transaction_time,
                        total_time,
                        elapsed_total,
                        attempt + 1,
                        os.getpid(),
                        threading.get_ident(),
                        self._write_instance_id,
                        queue_depth,
                        owner_operation,
                        owner_kind,
                        owner_age_s,
                        owner_transitions,
                        last_owner_operation,
                        last_owner_kind,
                        lock_holder,
                        admission_wait,
                        admission_queue_depth,
                        admission_owner_operation,
                        admission_owner_age_s,
                        admission_owner_transitions,
                        admission_last_owner_operation,
                        admission_holder,
                        len(_SESSION_DB_LIVE_REGISTRY),
                    )
                # Success — periodic best-effort checkpoint + FTS merge.
                # The checkpoint call must stay OUTSIDE the writer-lock
                # scope above: invoking it inside held the instance mutex
                # for the whole checkpoint fsync (writer convoy, 2026-08-23
                # lock storm). _try_wal_checkpoint is now lock-free and
                # time-throttled; schedule it after the write lock is
                # released.
                if pending_fts_merge:
                    self._try_incremental_merge_fts()
                if pending_checkpoint:
                    self._try_wal_checkpoint()
                return result
            except SessionCompressionInProgressError:
                # A live foreign compression lock is transient: the compressor
                # publishes in a couple of seconds. Without any wait, a steer
                # that lands mid-compression aborts the user's turn as
                # session_persistence_failed and sends the operator hunting
                # disk space that was never the problem (#75083).
                #
                # The budget is _COMPRESSION_BUSY_WAIT_S, not the write-lock
                # patience: the lease is a correctness boundary, so a writer
                # still locked out after a short wait must be refused rather
                # than left to land a stale turn once a long-running or wedged
                # compression finally lets go.
                if compression_deadline is None:
                    compression_deadline = min(
                        time.monotonic() + self._COMPRESSION_BUSY_WAIT_S, deadline
                    )
                if self._sleep_before_write_retry(
                    compression_deadline, self._COMPRESSION_BUSY_WAIT_S
                ):
                    continue
                raise
            except sqlite3.Error as exc:
                # 'no more rows' is a transient engine error on contended WAL appends (some builds
                # raise it as InterfaceError, a sibling of DatabaseError): retry like locked/busy.
                if _is_no_more_rows(exc) and _sleep_before_retry():
                    continue
                err_msg = str(exc).lower()
                if isinstance(exc, sqlite3.OperationalError):
                    if is_sqlite_lock_error(exc):
                        if _sleep_before_retry():
                            continue
                        # Say what actually happened, not disk/permission damage. The holder goes to
                        # the log, not the message: classify_persistence_error() buckets by phrase and
                        # a holder's argv (a worktree named fix-corrupt-db) would flip the bucket.
                        log_write_lock_holders(self.db_path, patience_s)
                        raise sqlite3.OperationalError(
                            f"database is locked (another Hermes process held the "
                            f"state.db write lock for over {patience_s:.0f}s — "
                            "likely a long maintenance operation such as VACUUM, "
                            "a large WAL checkpoint, or an older pre-update "
                            "process; the database itself is healthy)"
                        ) from exc
                    if (
                        _DISK_IO_ERROR_MARKER in err_msg
                        and not fn_started
                        and not ioerr_begin_retried
                        and _sleep_before_retry()
                    ):
                        # Retry on the SAME connection: close()+reopen would cancel this process's
                        # POSIX locks for every sibling (howtocorrupt §2.2).
                        ioerr_begin_retried = True
                        continue
                    raise  # non-lock error, callback already ran, or patience exhausted
                if isinstance(exc, sqlite3.DatabaseError):
                    # An out-of-band replace surfaces as this same corruption class; in-file repair
                    # on a NEW generation amplifies the damage.
                    if (
                        "not a database" in err_msg
                        or is_malformed_db_error(exc)
                        or self._is_fts_write_corruption_error(exc)
                    ):
                        with self._lock:
                            self._raise_if_db_replaced()
                    # Corrupt FTS shadow tables fail every write via the sync triggers while canonical
                    # rows are intact: detach the derived indexes atomically and retry (never rebuild here).
                    if self._enter_fts_fail_open(
                        exc, deadline=deadline, patience_s=patience_s
                    ):
                        continue
                    # What survives both checks is structural damage: quarantine.
                    if self._is_structural_corruption_error(exc):
                        self._halt_db_corrupt(exc)
                raise

    def _write_admission(self) -> SessionDBWriteAdmission:
        """The shared per-path write admission controller for this db file."""
        return SessionDBWriteAdmission.for_path(self.db_path)

    def execute_write_admission_shutdown(self) -> None:
        """Shut down the shared per-path admission controller (drain-aware).

        Safe to call from any instance sharing the path; only the gateway's
        final drain (or tests) should call it — closing ONE SessionDB must
        not fence out its siblings on the same file.
        """
        SessionDBWriteAdmission.for_path(self.db_path).shutdown()
