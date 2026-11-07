"""In-process turn evidence custody; never a backend delivery verdict.

The owner closes admission; abandoned callback threads retain their activity leases.
An opaque handle authorizes a single fresh read after closure, not a cached proof.
Only synchronous evidence readers with no detached work are supported.
"""
from __future__ import annotations

import contextvars
import math
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginRegistration

logger = logging.getLogger("hermes_cli.plugins")

_CURRENT = contextvars.ContextVar("plugin_turn_boundary", default=None)
_MAX_RETAINED = 256
_MAX_PER_TURN = 16
_MAX_ACTIVITIES = 256
_RETENTION_SECONDS = 300.0
_RETAINED: dict[object, tuple] = {}
_CUSTODY_LOCK = threading.Lock()



def _after_fork_child():
    # Parent threads and their locks do not survive fork. Never touch inherited locks.
    global _RETAINED, _CUSTODY_LOCK
    _RETAINED = {}
    _CUSTODY_LOCK = threading.Lock()
    _CURRENT.set(None)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)


def reserve_current_activity():
    boundary = _CURRENT.get()
    if boundary is None:
        return None
    return admit_callback(boundary.manager, {"turn_id": boundary.turn_id})


def _generation(manager):
    try:
        return (manager._hook_evidence_generation,
                tuple((name, tuple(callbacks)) for name, callbacks in sorted(manager._hooks.items())))
    except RuntimeError:
        return None


@dataclass(frozen=True)
class EvidenceRead:
    """Local outcome, not serializable authority or proof of backend acceptance."""
    status: str
    value: Any = None


class TurnBoundary:
    def __init__(self, manager, turn_id):
        self.manager = manager
        self.turn_id = turn_id
        self.profile = manager.scope_key
        self.pid = os.getpid()
        self.created = time.monotonic()
        self.lock = threading.Lock()
        self.pending = 0
        self.retained = 0
        self.closed = False
        self.failed = False
        self.generation = _generation(manager)
        self.failed = self.generation is None

    def fail(self):
        if self.pid != os.getpid():
            return
        with self.lock:
            self.failed = True

    def enter(self, manager, payload):
        if self.pid != os.getpid():
            return False
        with self.lock:
            matches = (manager is self.manager and manager.scope_key == self.profile
                       and payload.get("turn_id", self.turn_id) == self.turn_id)
            if not matches:
                self.failed = True
                return None  # unrelated profile/turn work keeps its existing dispatch behavior
            if (self.closed or self.pid != os.getpid() or self.pending >= _MAX_ACTIVITIES):
                self.failed = True
                return False if self.retained else None
            self.pending += 1
            return True

    def leave(self):
        if self.pid != os.getpid():
            return
        with self.lock:
            self.pending -= 1

    def close(self, normal):
        if self.pid != os.getpid():
            return
        with self.lock:
            self.closed = True
            self.failed |= not normal

    def ready(self):
        # Caller holds lock. The captured callable objects pin registration identity.
        if (self.failed or self.pid != os.getpid()
                or time.monotonic() - self.created > _RETENTION_SECONDS
                or _generation(self.manager) != self.generation):
            return "unverified"
        return "ready" if self.closed and self.pending == 0 else "pending"


@contextmanager
def turn_boundary(manager, turn_id):
    """Host-owned turn admission. A copied context retains this exact, eventually closed scope."""
    boundary = TurnBoundary(manager, turn_id)
    token = _CURRENT.set(boundary)
    normal = False
    try:
        yield boundary
        normal = True
    finally:
        boundary.close(normal)
        _CURRENT.reset(token)


def admit_callback(manager, payload):
    boundary = _CURRENT.get()
    if boundary is None:
        return None
    admitted = boundary.enter(manager, payload)
    return boundary if admitted is True else admitted


def fail_current():
    boundary = _CURRENT.get()
    if boundary is not None:
        boundary.fail()


def _expire_locked():
    now = time.monotonic()
    for handle, (owner, boundary, _) in list(_RETAINED.items()):
        if owner is not None and (now - boundary.created > _RETENTION_SECONDS or boundary.pid != os.getpid()):
            del _RETAINED[handle]


def retain_evidence(context, reader: Callable[[], Any]):
    """Retain the original reader on the currently executing turn; no lookup by caller IDs."""
    boundary = _CURRENT.get()
    if boundary is None or boundary.pid != os.getpid() or not callable(reader) or context._load_abandoned:
        return None
    with _CUSTODY_LOCK, boundary.lock:
        _expire_locked()
        if (boundary.closed or boundary.failed or boundary.pid != os.getpid()
                or context._manager is not boundary.manager
                or boundary.retained >= _MAX_PER_TURN or len(_RETAINED) >= _MAX_RETAINED):
            boundary.failed = True
            return None
        handle = object()
        _RETAINED[handle] = (context, boundary, reader)
        boundary.retained += 1
        return handle


def collect_evidence(context, handle, *, timeout=1.0):
    """One-shot bounded in-process transfer, only to the retaining context.

    Pending reads do not consume. A timeout consumes and invalidates; the reader may
    still run but its result is never reusable. No caller-supplied validity booleans.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
        raise ValueError("timeout must be finite and between zero and five seconds")
    with _CUSTODY_LOCK:
        _expire_locked()
        entry = _RETAINED.get(handle)
        if entry is None or entry[0] is not context:
            return EvidenceRead("unverified")
        _, boundary, reader = entry
        with boundary.lock:
            status = boundary.ready()
            if context._load_abandoned:
                status = "unverified"
            if status == "pending":
                return EvidenceRead(status)
            if status != "ready":
                del _RETAINED[handle]
                return EvidenceRead("unverified")
            # Keep custody while the reader runs so hung readers cannot create unbounded workers.
            _RETAINED[handle] = (None, boundary, reader)
    done = threading.Event()
    outcome = {}

    def read():
        try:
            outcome["value"] = reader()
        except BaseException:  # health: allow BLE001 -- plugin proof readers may raise arbitrary private errors; discard without exposing payloads
            boundary.fail()
        finally:
            with _CUSTODY_LOCK:
                _RETAINED.pop(handle, None)
            done.set()

    worker = threading.Thread(target=contextvars.copy_context().run, args=(read,), daemon=True)
    try:
        worker.start()
    except RuntimeError:
        boundary.fail()
        with _CUSTODY_LOCK:
            _RETAINED.pop(handle, None)
        return EvidenceRead("unverified")
    if not done.wait(timeout):
        boundary.fail()
        return EvidenceRead("unverified")
    with boundary.lock:
        if boundary.ready() != "ready" or context._load_abandoned:
            return EvidenceRead("unverified")
    return EvidenceRead("collected", outcome.get("value"))


class PluginTurnContextMixin:
    """Context evidence custody and hook-registration generation tracking."""

    def retain_turn_evidence(self, reader: Callable[[], Any]) -> Any:
        """Retain an original in-process evidence reader until this turn closes; None if unavailable."""
        return retain_evidence(self, reader)

    def collect_turn_evidence(self, handle: Any, *, timeout: float = 1.0) -> Any:
        """Read retained evidence once after quiescence; this is not backend acceptance."""
        return collect_evidence(self, handle, timeout=timeout)

    def _track_callback(
        self, kind: str, key: str, callback: Callable, mapping: dict[str, list[Callable]],
        valid: set[str],
    ) -> PluginRegistration:
        """Append ``callback`` under ``key`` (warning on unknown ``key``) and lease its removal."""
        if key not in valid:
            logger.warning("Plugin '%s' registered unknown %s '%s' (valid: %s)", self.manifest.name, kind,
                           key, ", ".join(sorted(valid)))
        if mapping is self._manager._hooks:
            self._manager._hook_evidence_generation += 1
        mapping.setdefault(key, []).append(callback)
        handle = self._track(kind, key, lambda: self._manager._remove_callback(mapping, key, callback))
        logger.debug("Plugin %s registered %s: %s", self.manifest.name, kind, key)
        return handle
