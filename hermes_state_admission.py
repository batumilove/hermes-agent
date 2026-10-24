"""Bounded FIFO writer admission and content-free ownership diagnostics."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import itertools
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
import weakref
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, TypeVar

if TYPE_CHECKING:
    from hermes_state import SessionDB
logger = logging.getLogger("hermes_state")
T = TypeVar("T")
_SESSION_DB_INSTANCE_IDS = itertools.count(1)
_SESSION_DB_LOCK_KINDS = frozenset(
    {"direct", "lifecycle", "maintenance", "read", "write"}
)


# ── Live-instance census (P0-A part 1) ──
#
# The 2026-08-19 gateway evidence (db_instance=1..137 in one process) was
# churn made invisible: nothing observed how many SessionDB handles were
# concurrently alive, so a leak could grow until fd exhaustion with no signal
# in between. The registry below is that signal. It holds only STRONG
# references to ids/paths/stack-digests — never to the SessionDB itself — so
# registration cannot extend an instance's lifetime (the same rule the
# attributed-lock wrapper follows); ``close()`` deregisters, and an instance
# whose owner forgot ``close()`` stays visible until GC clears the weakref,
# which is exactly the population an operator needs to see.
_SESSION_DB_LIVE_REGISTRY: dict[int, dict[str, Any]] = {}
_SESSION_DB_LIVE_REGISTRY_LOCK = threading.Lock()


def _session_db_creation_stack_digest() -> str:
    """Short stable digest of the caller's creation stack (diagnostic only).

    The census exists to answer "WHO is holding live handles"; an instance id
    alone cannot say that. A digest (not raw text) keeps the answer compact
    and free of source paths/code detail while still grouping instances that
    share one construction site.
    """
    try:
        stack = traceback.extract_stack()
        # Drop hermes_state frames so the digest tracks the CONSTRUCTION SITE
        # (run_agent / cron / gateway method), not SessionDB.__init__ itself.
        site = traceback.format_list(stack[:-1])[-4:]
        raw = "".join(site)
    except (RuntimeError, ValueError, OSError):
        raw = "unavailable"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]


def session_db_live_census() -> dict[str, Any]:
    """Snapshot of every live SessionDB instance in this process.

    Returns ``{"total": N, "by_path": {path: [entry, ...]}}`` where each
    entry is ``{"instance_id": str, "created_stack_digest": str}``. Closed
    instances never appear; ``total`` counts live instances only. Intended
    for diagnostics endpoints/logs (e.g. alongside slow-write warnings), not
    for correctness decisions — callers must not treat it as a lock.
    """
    with _SESSION_DB_LIVE_REGISTRY_LOCK:
        by_path: dict[str, list[dict[str, Any]]] = {}
        for entry in list(_SESSION_DB_LIVE_REGISTRY.values()):
            by_path.setdefault(entry["path"], []).append(
                {
                    "instance_id": entry["instance_id"],
                    "created_stack_digest": entry["created_stack_digest"],
                }
            )
        return {"total": len(_SESSION_DB_LIVE_REGISTRY), "by_path": by_path}


def _session_db_register_live(instance: SessionDB) -> None:
    try:
        path_key = str(getattr(instance, "db_path", "") or "")
        entry = {
            "path": path_key,
            "instance_id": getattr(instance, "_write_instance_id", "?"),
            "created_stack_digest": _session_db_creation_stack_digest(),
            "weak": weakref.ref(instance, _session_db_registry_drop),
        }
        with _SESSION_DB_LIVE_REGISTRY_LOCK:
            _SESSION_DB_LIVE_REGISTRY[id(instance)] = entry
    except Exception:
        logger.debug("SessionDB live-registry register failed", exc_info=True)


def _session_db_registry_drop(ref: Any) -> None:
    """weakref finalizer: purge the entry when an unclosed instance is GC'd."""
    # The dict is keyed by id(instance); we cannot recover the key from the
    # dead ref, so purge by identity scan. This runs on GC, not on hot paths.
    with _SESSION_DB_LIVE_REGISTRY_LOCK:
        for key, entry in list(_SESSION_DB_LIVE_REGISTRY.items()):
            if entry.get("weak") is ref:
                del _SESSION_DB_LIVE_REGISTRY[key]
                break


def _session_db_unregister_live(instance: SessionDB) -> None:
    with _SESSION_DB_LIVE_REGISTRY_LOCK:
        _SESSION_DB_LIVE_REGISTRY.pop(id(instance), None)


class _SessionDBAttributedLock:
    """A ``threading.Lock``-compatible mutex with content-free holder telemetry."""

    def __init__(self, owner: SessionDB) -> None:
        # Avoid turning the lock into a new SessionDB retention root. The owner
        # already owns this wrapper; a strong back-reference would create a
        # cycle and delay connection cleanup when callers omit close().
        self._owner_ref = weakref.ref(owner)
        self._raw = threading.Lock()
        self._active: Optional[dict[str, Any]] = None

    def _owner(self) -> SessionDB:
        owner = self._owner_ref()
        if owner is None:  # pragma: no cover - wrapper is normally owner-reachable
            raise RuntimeError("SessionDB lock owner is no longer available")
        return owner

    @staticmethod
    def _clean_operation(operation: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(operation))[:96] or "unknown"

    @staticmethod
    def _infer_kind(operation: str) -> str:
        lowered = operation.lower()
        if operation == "_read_ctx" or lowered.startswith(
            ("get_", "list_", "load_", "search_", "find_", "count_", "has_", "is_")
        ):
            return "read"
        if any(
            token in lowered
            for token in ("checkpoint", "fts", "optimize", "rebuild", "vacuum")
        ):
            return "maintenance"
        if any(token in lowered for token in ("close", "open", "init", "shutdown")):
            return "lifecycle"
        return "direct"

    def _acquire(
        self,
        operation: str,
        holder_kind: str,
        blocking: bool = True,
        timeout: float = -1,
    ) -> tuple[bool, Optional[tuple[float, int, str, float, int, str, str, str, str]]]:
        owner = self._owner()
        operation_label = self._clean_operation(operation)
        kind = holder_kind if holder_kind in _SESSION_DB_LOCK_KINDS else "direct"
        wait_started = time.monotonic()
        with owner._write_diag_lock:
            owner._write_waiter_count += 1
            queue_depth = owner._write_waiter_count
            owner_operation = owner._write_owner_operation or "-"
            owner_kind = owner._write_owner_kind or "-"
            owner_generation = owner._write_owner_generation
            owner_age_s = (
                max(0.0, wait_started - owner._write_owner_started)
                if owner._write_owner_started is not None
                else 0.0
            )

        try:
            if timeout == -1:
                acquired = self._raw.acquire(blocking)
            else:
                acquired = self._raw.acquire(blocking, timeout)
        except BaseException:
            with owner._write_diag_lock:
                owner._write_waiter_count -= 1
            raise
        if not acquired:
            with owner._write_diag_lock:
                owner._write_waiter_count -= 1
            return False, None

        acquired_at = time.monotonic()
        lock_wait = acquired_at - wait_started
        with owner._write_diag_lock:
            owner._write_waiter_count -= 1
            owner_transitions = owner._write_owner_generation - owner_generation
            last_owner_operation = (
                owner._write_last_owner_operation if owner_transitions > 0 else None
            ) or "-"
            last_owner_kind = (
                owner._write_last_owner_kind if owner_transitions > 0 else None
            ) or "-"
            if owner_operation != "-" or owner_transitions > 0:
                lock_holder = "attributed"
            elif lock_wait >= 0.001:
                lock_holder = "unattributed"
            else:
                lock_holder = "none"
            owner._write_owner_generation += 1
            owner._write_last_owner_operation = operation_label
            owner._write_last_owner_kind = kind
            owner._write_owner_operation = operation_label
            owner._write_owner_kind = kind
            owner._write_owner_started = acquired_at
            self._active = {
                "operation": operation_label,
                "holder_kind": kind,
                "wait": lock_wait,
                "hold_started": acquired_at,
                "queue_depth": queue_depth,
                "thread_id": threading.get_ident(),
            }
        return True, (
            lock_wait,
            queue_depth,
            owner_operation,
            owner_age_s,
            owner_transitions,
            last_owner_operation,
            lock_holder,
            owner_kind,
            last_owner_kind,
        )

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        operation = sys._getframe(1).f_code.co_name
        acquired, _ = self._acquire(
            operation,
            self._infer_kind(operation),
            blocking=blocking,
            timeout=timeout,
        )
        return acquired

    def release(self) -> None:
        owner = self._owner()
        released_at = time.monotonic()
        with owner._write_diag_lock:
            active = self._active
            if active is not None:
                self._active = None
                owner._write_owner_operation = None
                owner._write_owner_kind = None
                owner._write_owner_started = None
        self._raw.release()
        if active is None or active["holder_kind"] == "write":
            return
        hold_s = released_at - active["hold_started"]
        if (
            active["wait"] < owner._SLOW_LOCK_WAIT_WARN_S
            and hold_s < owner._SLOW_LOCK_HOLD_WARN_S
        ):
            return
        logger.warning(
            "SessionDB lock latency: operation=%s holder_kind=%s wait=%.3fs "
            "hold=%.3fs pid=%d thread_id=%d db_instance=%s queue_depth=%d",
            active["operation"],
            active["holder_kind"],
            active["wait"],
            hold_s,
            os.getpid(),
            active["thread_id"],
            owner._write_instance_id,
            active["queue_depth"],
        )

    def locked(self) -> bool:
        return self._raw.locked()

    def __enter__(self) -> _SessionDBAttributedLock:
        operation = sys._getframe(1).f_code.co_name
        acquired, _ = self._acquire(operation, self._infer_kind(operation))
        if not acquired:  # pragma: no cover - blocking acquisition cannot do this
            raise RuntimeError("failed to acquire SessionDB lock")
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        self.release()
        return False

    @contextmanager
    def hold(self, operation: str, holder_kind: str):
        acquired, diagnostics = self._acquire(operation, holder_kind)
        if not acquired:  # pragma: no cover - blocking acquisition cannot do this
            raise RuntimeError("failed to acquire SessionDB lock")
        try:
            assert diagnostics is not None
            yield diagnostics
        finally:
            self.release()


_SESSION_DB_WRITE_ADMISSIONS: dict[str, SessionDBWriteAdmission] = {}
_SESSION_DB_WRITE_ADMISSIONS_LOCK = threading.Lock()

# Defaults: one active write transaction per db file, queue up to 64 waiters
# before rejecting. SQLite exposes one writer slot per database file, so the
# process-wide admission boundary uses that same granularity: independent
# SessionDB wrappers queue before BEGIN IMMEDIATE rather than convoying inside
# SQLite's busy wait. Queue-full becomes 429 + Retry-After at agent-serving
# endpoints (see gateway/write_admission.py).
_SESSION_DB_ADMISSION_DEFAULT_CAPACITY = 1
_SESSION_DB_ADMISSION_DEFAULT_QUEUE_LIMIT = 64
_SESSION_DB_ADMISSION_RETRY_AFTER_S = 1.0


class SessionDBWriteAdmissionFullError(sqlite3.OperationalError):
    """Write rejected because the per-database admission queue is full.

    Subclasses ``sqlite3.OperationalError`` so every existing write path that
    already handles sqlite errors can carry it unchanged; carries
    ``retry_after_s`` / ``queue_depth`` for HTTP 429 + Retry-After mapping at
    agent-serving endpoints.
    """

    retry_after_s: float
    queue_depth: int

    def __init__(self, message: str, *, retry_after_s: float, queue_depth: int):
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.queue_depth = queue_depth


class SessionDBWriteAdmissionClosedError(sqlite3.OperationalError):
    """Write rejected because the admission controller was shut down."""


class _AdmissionTicket:
    """One registered write intent: queue entry + grant state.

    State machine (transitions only under the controller lock):

        queued ──grant──▶ granted ──release──▶ done
           │
           └──cancel──▶ cancelled

    ``event`` is set on every terminal-ish transition (grant, cancel,
    shutdown) so ``_AdmissionToken.__enter__`` can park on it.
    """

    __slots__ = (
        "cancelled",
        "event",
        "granted",
        "granted_at",
        "holder",
        "last_owner_operation",
        "operation",
        "owner_age_s",
        "owner_generation",
        "owner_operation",
        "owner_transitions",
        "queue_depth",
        "registered_at",
        "released",
        "seq",
        "session_key",
    )

    def __init__(self, seq: int, session_key: Optional[str], operation: str):
        self.seq = seq
        self.session_key = session_key
        self.operation = operation
        self.event = threading.Event()
        self.granted = False
        self.released = False
        self.cancelled = False
        self.registered_at = time.monotonic()
        self.granted_at: Optional[float] = None
        self.queue_depth = 0
        self.owner_operation = "-"
        self.owner_age_s = 0.0
        self.owner_generation = 0
        self.owner_transitions = 0
        self.last_owner_operation = "-"
        self.holder = "none"


_ASYNC_ADMISSION_OWNERS = contextvars.ContextVar(
    "state_db_async_admission_owners", default=()
)


class _AdmissionToken:
    """Public handle returned by ``SessionDBWriteAdmission.acquire``.

    ``acquire()`` only REGISTERS the intent (it never blocks; queue-full
    raises immediately so a caller can answer 429 without spawning work).
    Blocking for the grant happens in ``__enter__``; the slot is released on
    ALL exit paths (body return, body raise, cancel, shutdown) because
    ``__exit__`` always calls ``release()``.
    """

    __slots__ = (
        "_async_scope",
        "_controller",
        "_depth_taken",
        "_passthrough",
        "_ticket",
    )

    def __init__(self, controller: SessionDBWriteAdmission, ticket: _AdmissionTicket):
        self._controller = controller
        self._ticket = ticket
        self._passthrough = False
        self._depth_taken = False
        self._async_scope = None

    def __enter__(self) -> _AdmissionToken:
        if self._passthrough or self._ticket is None:
            return self
        ticket = self._ticket
        while not ticket.granted:
            if ticket.cancelled or self._controller._closed:
                raise SessionDBWriteAdmissionClosedError(
                    "SessionDB write admission shut down before this write was granted"
                )
            ticket.event.wait(timeout=0.5)
        local = self._controller._thread_depth
        local.depth = getattr(local, "depth", 0) + 1
        self._depth_taken = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False

    async def __aenter__(self) -> _AdmissionToken:
        if self._passthrough or self._ticket is None:
            return self
        ticket = self._ticket
        try:
            while not ticket.granted:
                if ticket.cancelled or self._controller._closed:
                    raise SessionDBWriteAdmissionClosedError(
                        "SessionDB write admission shut down before this write was granted"
                    )
                await asyncio.sleep(0.01)
            # Thread-local depth would admit EVERY unrelated task sharing this loop.
            # Context-local ownership also follows the handler's offloaded DB work.
            self._async_scope = _ASYNC_ADMISSION_OWNERS.set(
                (*_ASYNC_ADMISSION_OWNERS.get(), self)
            )
            return self
        except BaseException:
            self.cancel()
            self.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._async_scope is not None:
            _ASYNC_ADMISSION_OWNERS.reset(self._async_scope)
            self._async_scope = None
        self.release()
        return False

    def release(self) -> None:
        if self._depth_taken:
            local = self._controller._thread_depth
            local.depth = max(0, getattr(local, "depth", 1) - 1)
            self._depth_taken = False
        if self._passthrough or self._ticket is None:
            return
        self._controller.release(self._ticket)

    def cancel(self) -> bool:
        """Abandon a still-queued ticket; returns False if already granted."""
        if self._passthrough or self._ticket is None:
            return False
        return self._controller.cancel(self._ticket)

    def diagnostics(self) -> Optional[dict[str, Any]]:
        """Return the immutable admission-wait snapshot after grant."""
        if self._passthrough or self._ticket is None:
            return None
        ticket = self._ticket
        granted_at = ticket.granted_at
        return {
            "wait": max(
                0.0, (granted_at or ticket.registered_at) - ticket.registered_at
            ),
            "queue_depth": ticket.queue_depth,
            "owner_operation": ticket.owner_operation,
            "owner_age_s": ticket.owner_age_s,
            "owner_transitions": ticket.owner_transitions,
            "last_owner_operation": ticket.last_owner_operation,
            "holder": ticket.holder,
        }


class SessionDBWriteAdmission:
    """Bounded, FIFO, per-session-ordered write admission for one db path.

    Guarantees:

    * FIFO fairness — waiters are granted in ticket order whenever eligible
      (a same-session waiter fenced behind its predecessor does not block
      other sessions' waiters).
    * Per-session ordering — a session's later write is granted only after
      its earlier write COMPLETES (release), not merely after it is admitted.
    * Bounded queue — beyond ``queue_limit`` waiting intents, ``acquire``
      raises ``SessionDBWriteAdmissionFullError`` (the load-shedding signal
      mapped to 429 + Retry-After by agent-serving endpoints).
    * Cancellation — an abandoned queued waiter is removed and consumes no
      slot.
    * Slot release on all exit paths — granted slots are freed by
      ``release()`` whether the body returned, raised, or the process is
      draining.
    * Drain-aware shutdown — after ``shutdown()`` new acquires fail fast
      with ``SessionDBWriteAdmissionClosedError``, queued waiters are
      cancelled (woken with the same error), and in-flight slots release
      normally (shutdown never orphans them).
    """

    def __init__(
        self,
        capacity: int = _SESSION_DB_ADMISSION_DEFAULT_CAPACITY,
        queue_limit: int = _SESSION_DB_ADMISSION_DEFAULT_QUEUE_LIMIT,
    ):
        self._capacity = max(1, int(capacity))
        self._queue_limit = max(0, int(queue_limit))
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        # Global FIFO of not-yet-granted tickets.
        self._waiters: deque[_AdmissionTicket] = deque()
        # Per-session arrival order of UNRELEASED tickets (queued or granted):
        # the head of each session deque is that session's non-overtake
        # fence. A later same-session ticket is grantable only when it IS the
        # head, i.e. every earlier same-session write has completed.
        self._session_order: dict[str, deque[_AdmissionTicket]] = {}
        self._in_flight = 0
        self._closed = False
        self._active: dict[int, _AdmissionTicket] = {}
        self._owner_generation = 0
        self._last_owner_operation: Optional[str] = None
        # Per-thread count of granted slots held (reentrancy guard — see
        # _AdmissionToken.__enter__).
        self._thread_depth = threading.local()

    # -- acquisition --------------------------------------------------------

    def acquire(
        self,
        session_key: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> _AdmissionToken:
        """Register a write intent; returns a token context manager.

        Never blocks: if the intent cannot be granted immediately it is
        queued, and once ``queue_limit`` intents are already waiting this
        raises ``SessionDBWriteAdmissionFullError`` (``retry_after_s`` set)
        or ``SessionDBWriteAdmissionClosedError`` after shutdown. The wait
        for the grant happens in ``_AdmissionToken.__enter__``.
        """
        inherited_owner = any(
            owner._controller is self
            and owner._ticket is not None
            and owner._ticket.granted
            and not owner._ticket.released
            for owner in _ASYNC_ADMISSION_OWNERS.get()
        )
        if getattr(self._thread_depth, "depth", 0) > 0 or inherited_owner:
            # Reentrant: this thread already holds a granted slot (a write
            # callback performing another SessionDB write). The OUTERMOST
            # write is the admitted unit of disk work; a nested intent must
            # not register a queue ticket — a later promote could grant it
            # with no one left to release it (capacity leak).
            token = _AdmissionToken(self, None)
            token._passthrough = True
            return token
        with self._lock:
            if self._closed:
                raise SessionDBWriteAdmissionClosedError(
                    "SessionDB write admission is shut down for this database"
                )
            operation_label = _SessionDBAttributedLock._clean_operation(
                operation or "unknown"
            )
            ticket = _AdmissionTicket(next(self._seq), session_key, operation_label)
            ticket.queue_depth = len(self._waiters) + 1
            ticket.owner_generation = self._owner_generation
            if self._active:
                owner = next(iter(self._active.values()))
                ticket.owner_operation = owner.operation
                ticket.owner_age_s = max(
                    0.0,
                    ticket.registered_at - (owner.granted_at or ticket.registered_at),
                )
                ticket.holder = "attributed"
            session_queue = None
            if session_key is not None:
                session_queue = self._session_order.setdefault(session_key, deque())
                session_queue.append(ticket)
            if self._in_flight < self._capacity and (
                session_queue is None or session_queue[0] is ticket
            ):
                self._grant_locked(ticket)
            else:
                if len(self._waiters) >= self._queue_limit:
                    # Undo the session-order registration before rejecting.
                    if session_key is not None:
                        self._discard_session_ticket_locked(session_key, ticket)
                    depth = len(self._waiters)
                    raise SessionDBWriteAdmissionFullError(
                        f"SessionDB write admission queue full for this database "
                        f"(waiting={depth} >= queue_limit={self._queue_limit}, "
                        f"in_flight={self._in_flight}/{self._capacity}); "
                        f"retry after {_SESSION_DB_ADMISSION_RETRY_AFTER_S:.1f}s",
                        retry_after_s=_SESSION_DB_ADMISSION_RETRY_AFTER_S,
                        queue_depth=depth,
                    )
                self._waiters.append(ticket)
        return _AdmissionToken(self, ticket)

    def _grant_locked(self, ticket: _AdmissionTicket) -> None:
        ticket.owner_transitions = self._owner_generation - ticket.owner_generation
        if ticket.owner_transitions > 0:
            ticket.last_owner_operation = self._last_owner_operation or "-"
            ticket.holder = "attributed"
        ticket.granted_at = time.monotonic()
        self._owner_generation += 1
        self._last_owner_operation = ticket.operation
        self._active[ticket.seq] = ticket
        ticket.granted = True
        self._in_flight += 1
        ticket.event.set()

    def _discard_session_ticket_locked(
        self, session_key: str, ticket: _AdmissionTicket
    ) -> None:
        session_queue = self._session_order.get(session_key)
        if session_queue is None:
            return
        try:
            session_queue.remove(ticket)
        except ValueError:
            pass
        if not session_queue:
            # Session deques must not accumulate for the life of the process
            # (session keys churn); drop them as soon as they empty.
            del self._session_order[session_key]

    # -- release / cancel ---------------------------------------------------

    def release(self, ticket: _AdmissionTicket) -> None:
        """Complete a ticket: free its slot (if granted) and promote waiters."""
        promote = False
        with self._lock:
            if ticket.released or ticket.cancelled:
                return
            if not ticket.granted:
                # release() on a still-queued ticket is an abandonment:
                # treat as cancellation so it never holds a queue slot.
                self._cancel_locked(ticket)
                return
            ticket.released = True
            self._in_flight -= 1
            self._active.pop(ticket.seq, None)
            if ticket.session_key is not None:
                self._discard_session_ticket_locked(ticket.session_key, ticket)
            promote = True
        if promote:
            self._promote()

    def cancel(self, ticket: _AdmissionTicket) -> bool:
        """Abandon a queued ticket; False if it was already granted/released."""
        with self._lock:
            if ticket.granted or ticket.cancelled or ticket.released:
                return False
            self._cancel_locked(ticket)
            return True

    def _cancel_locked(self, ticket: _AdmissionTicket) -> None:
        ticket.cancelled = True
        try:
            self._waiters.remove(ticket)
        except ValueError:
            pass
        if ticket.session_key is not None:
            self._discard_session_ticket_locked(ticket.session_key, ticket)
        ticket.event.set()

    def _promote(self) -> None:
        """Grant waiters in FIFO order while capacity and fences allow.

        A waiter fenced behind an earlier same-session ticket is skipped
        (not removed) so it does not block OTHER sessions' later waiters —
        that skip is what makes the queue FIFO-fair across sessions while
        still non-overtaking within one session.
        """
        with self._lock:
            if self._closed:
                return
            for ticket in list(self._waiters):
                if self._in_flight >= self._capacity:
                    break
                if ticket.cancelled or ticket.granted or ticket.released:
                    self._waiters.remove(ticket)
                    continue
                if ticket.session_key is not None:
                    session_queue = self._session_order.get(ticket.session_key)
                    if session_queue is None or session_queue[0] is not ticket:
                        continue  # fenced behind an earlier same-session write
                self._waiters.remove(ticket)
                self._grant_locked(ticket)

    # -- shutdown / introspection -------------------------------------------

    def shutdown(self) -> None:
        """Drain-aware shutdown: reject new intents, cancel queued waiters.

        In-flight tickets are NOT cancelled — their holders release normally
        (``release()`` keeps working after shutdown), so a shutdown never
        orphans a slot or a writer mid-transaction.
        """
        with self._lock:
            self._closed = True
            pending = list(self._waiters)
            self._waiters.clear()
            for ticket in pending:
                ticket.cancelled = True
                if ticket.session_key is not None:
                    self._discard_session_ticket_locked(ticket.session_key, ticket)
                ticket.event.set()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "in_flight": self._in_flight,
                "capacity": self._capacity,
                "waiting": len(self._waiters),
                "queue_limit": self._queue_limit,
                "closed": self._closed,
            }

    def _override_for_test(
        self,
        capacity: Optional[int] = None,
        queue_limit: Optional[int] = None,
    ) -> None:
        """Test-only reconfiguration (also used by deploy-gate verification)."""
        with self._lock:
            if capacity is not None:
                self._capacity = max(1, int(capacity))
            if queue_limit is not None:
                self._queue_limit = max(0, int(queue_limit))

    @classmethod
    def for_path(cls, db_path) -> SessionDBWriteAdmission:
        """Return the shared controller for a database file path.

        Keyed by the resolved absolute path so every SessionDB instance in
        this process that points at the same file — the gateway's long-lived
        handle, per-cron ephemerals, tool ephemerals, subagent children —
        shares ONE controller (the "gateway-wide" in gateway-wide cap; the
        same rule applies inside any multi-instance process).
        """
        key = str(Path(db_path).resolve())
        with _SESSION_DB_WRITE_ADMISSIONS_LOCK:
            admission = _SESSION_DB_WRITE_ADMISSIONS.get(key)
            if admission is None:
                admission = cls(
                    capacity=_SESSION_DB_ADMISSION_DEFAULT_CAPACITY,
                    queue_limit=_SESSION_DB_ADMISSION_DEFAULT_QUEUE_LIMIT,
                )
                _SESSION_DB_WRITE_ADMISSIONS[key] = admission
            return admission


def _current_session_key_for_admission() -> Optional[str]:
    """Best-effort session key for write-admission ordering.

    Prefers the session ContextVar (task-local, correct for concurrent
    gateway turns), falls back to the process env var (CLI single-session
    case). Never raises: ordering is best-effort when identity is unknown.
    """
    value = ""
    try:
        from gateway.session_context import get_session_env  # stdlib-only module

        value = get_session_env("HERMES_SESSION_KEY", "")
    except (ImportError, AttributeError, RuntimeError, TypeError):
        value = os.environ.get("HERMES_SESSION_KEY", "")
    return (value or "").strip() or None
