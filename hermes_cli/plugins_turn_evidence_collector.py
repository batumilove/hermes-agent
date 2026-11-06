"""Bounded owning collector for per-turn delivery evidence.

One collector owns one terminal turn. It binds the turn to the exact producer
identity, profile and callback-registration generation, creates original Receipts
carrying that producer binding, retains each Receipt's terminal-proof reader in
the reviewed turn-evidence custody, and only after verified quiescence collects a
fresh in-process proof and issues a single-use authenticated transfer envelope.

Authority model: the signed proof is never cached or caller-mutable — issue time
re-reads a fresh proof and re-verifies every predicate again after signing.
Observed abandonment or boundary failure latches the authority dead permanently.
collect() is a report: it returns what was collected and never resurrects a
refused transfer. Disposed readers are released from custody at the next collector
call or when the collector is dropped. Nothing here queries a backend, holds
credentials, or activates anything.

Trust boundary: quiescence, abandonment and Receipt validity are observed states
of in-process objects. The collector refuses on every observation and latches
durably, but a host mutating registration, Receipts or context internals inside
the final instruction window — or toggling a private context flag without any
collector call ever observing it — is outside this contract, the same in-process
consumer trust the reviewed core relies on.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable

from . import plugins_turn_boundary as boundary

_MAX_TRACES = 16  # custody per-turn reader bound AND the total trace-ledger bound
_DEFAULT_RETENTION_SECONDS = 300.0
_MAX_COLLECT_DEADLINE_SECONDS = 10.0
_MAX_READ_TIMEOUT_SECONDS = 1.0
_POLL_SECONDS = 0.01
_TRANSFER_SCHEMA = 1
_TRANSFER_KIND = "turn_evidence_transfer"
_MIN_KEY_BYTES = 32
_MAX_FRESHNESS_SECONDS = 3600.0
_MAX_CLOCK_SKEW_SECONDS = 5.0
_MAX_CONSUMED_NONCES = 4096

_DEL_RELEASE_SECONDS = 5.0
_DEFERRED_LOCK = threading.Lock()
_DEFERRED_RELEASE: list[Any] = []

_COLLECTED = "collected"
_TRANSFERRED = "transferred"
_UNVERIFIED = "unverified"
_UNKNOWN = "unknown"
_ACTIVE_STATES = ("open", "retained", "collected")


def _canonical(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _turn_sha256(turn_id: str) -> str:
    return hashlib.sha256(turn_id.encode()).hexdigest()


def _registration_sha256(generation) -> str | None:
    if generation is None:
        return None
    count, hooks = generation
    shape = [[name, len(callbacks)] for name, callbacks in hooks]
    return hashlib.sha256(_canonical({"generation": count, "hooks": shape})).hexdigest()


def _check_key(key: Any) -> bytes:
    if not isinstance(key, (bytes, bytearray)) or len(key) < _MIN_KEY_BYTES:
        raise ValueError(f"transfer key must be at least {_MIN_KEY_BYTES} raw bytes")
    return bytes(key)


def _proof_copy(proof: Any) -> Any:
    """Hand out copies only: a returned proof must never alias the signing authority."""
    if not isinstance(proof, dict):
        return proof
    copied = dict(proof)
    if isinstance(copied.get("producer"), dict):
        copied["producer"] = dict(copied["producer"])
    return copied


def _defer_release(handles) -> None:
    with _DEFERRED_LOCK:
        _DEFERRED_RELEASE.extend(handles)


def _deferred_after_fork_child():
    # Parent threads and their locks do not survive fork; the reviewed core already
    # reset custody in the child, so inherited deferred handles are void there.
    global _DEFERRED_LOCK, _DEFERRED_RELEASE
    _DEFERRED_LOCK = threading.Lock()
    _DEFERRED_RELEASE = []


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_deferred_after_fork_child)


def _drain_deferred_release() -> None:
    """Retry handles a destructor could not release because custody was busy."""
    with _DEFERRED_LOCK:
        pending, _DEFERRED_RELEASE[:] = list(_DEFERRED_RELEASE), []
    if not pending:
        return
    if boundary._CUSTODY_LOCK.acquire(blocking=False):
        try:
            for handle in pending:
                held = boundary._RETAINED.get(handle)
                if held is not None and held[0] is not None:
                    del boundary._RETAINED[handle]
        finally:
            boundary._CUSTODY_LOCK.release()
    else:
        _defer_release(pending)


@dataclass
class _TraceEntry:
    receipt: Any = None
    handle: Any = None
    proof: dict | None = None
    status: str = "open"
    opened_at: float = 0.0


class TurnEvidenceCollector:
    """Owner of one terminal turn's Receipts, readers and post-quiescence proofs."""

    def __init__(self, manager, context, receipts: ModuleType, *, turn_id: str,
                 receipt_directory: str, key: bytes | None = None,
                 max_traces: int = _MAX_TRACES,
                 retention_seconds: float = _DEFAULT_RETENTION_SECONDS,
                 monotonic: Callable[[], float] = time.monotonic):
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 1024:
            raise ValueError("invalid turn binding")
        if not 1 <= max_traces <= _MAX_TRACES:
            raise ValueError("trace bound must stay within custody reader capacity")
        if not math.isfinite(retention_seconds) or retention_seconds <= 0:
            raise ValueError("retention must be finite and positive")
        self.manager = manager
        self.context = context
        self.receipts = receipts
        self.turn_id = turn_id
        self.turn_sha256 = _turn_sha256(turn_id)
        self.profile = manager.scope_key
        self.receipt_directory = receipt_directory
        self.key = None if key is None else _check_key(key)
        self.max_traces = max_traces
        self.retention_seconds = retention_seconds
        self._monotonic = monotonic
        self.producer = receipts.producer_identity()
        self._pid = os.getpid()
        self._boundary = None
        self._registration = None
        self._began = False
        self._authority_dead = False  # one-way: observed abandonment/boundary death
        self._lock = threading.Lock()
        self._traces: dict[str, _TraceEntry] = {}
        self._pending_release: list[Any] = []  # disposed handles awaiting custody release

    def binding(self) -> dict:
        """Exact collector identity a verifier must match: profile, turn digest, producer."""
        return {"profile": self.profile, "turn_sha256": self.turn_sha256,
                "producer": dict(self.producer)}

    def expected_binding(self, trace_id: str) -> dict:
        """Verifier-side pinning: collector identity plus the exact receipt and registration digests.

        Capture this BEFORE the transfer: after ``issue_transfer`` the collected proof
        digest is dropped and only the envelope carries it. A verifier may equally pin
        digests from its own independent record instead of trusting this helper.
        """
        if os.getpid() != self._pid:
            # Fork child: the inherited collector lock may be held forever by a parent
            # thread that no longer exists; never touch it, consistently with all other
            # collector methods. Pinning digests are unavailable on a foreign-process copy.
            return dict(self.binding(), trace_id=trace_id, receipt_sha256=None,
                        registration_sha256=self._registration)
        with self._lock:
            entry = self._traces.get(trace_id)
            digest = entry.proof.get("receipt_sha256") if entry is not None and entry.proof else None
        return dict(self.binding(), trace_id=trace_id, receipt_sha256=digest,
                    registration_sha256=self._registration)

    def attach_current_turn(self):
        """Adopt the host's open turn; never create or close a nested boundary."""
        # Reject fork copies before touching an inherited collector lock.
        if os.getpid() != self._pid:
            raise RuntimeError("collector changed process")
        scope = boundary._CURRENT.get()
        # Collector -> scope matches issue_transfer's lock order. Each contender
        # has its own scope, so locking only that scope cannot make admission unique.
        with self._lock:
            if self._began:
                raise RuntimeError("collector already began")
            if scope is None or scope.manager is not self.manager or scope.turn_id != self.turn_id:
                raise RuntimeError("no matching host turn")
            with scope.lock:
                if scope.closed or scope.pid != self._pid or scope.profile != self.profile:
                    raise RuntimeError("host turn admission closed")
                self._boundary = scope
                self._registration = _registration_sha256(scope.generation)
                self._began = True
        return self

    @contextmanager
    def begin_turn(self):
        """Own the terminal turn through the reviewed boundary; dispatch behavior is unchanged."""
        if os.getpid() != self._pid:
            raise RuntimeError("collector changed process")
        with self._lock:
            if self._began:
                raise RuntimeError("collector already began")
            self._began = True
        with boundary.turn_boundary(self.manager, self.turn_id) as scope:
            with self._lock:
                self._boundary = scope
                self._registration = _registration_sha256(scope.generation)
            yield scope

    def open_trace(self, trace_id: str, root_id: str):
        """Create an original Receipt bound to this collector's producer identity.

        Reopening a trace id disposes the prior entry: its Receipt is invalidated and
        dropped and its custody handle is queued for release at this or the next call.
        """
        if os.getpid() != self._pid:
            return None
        with self._lock:
            now = self._monotonic()
            self._expire_locked(now)
            prior = self._traces.get(trace_id)
            if prior is not None:
                self._dispose_locked(prior)
                del self._traces[trace_id]
            active = [t for t, e in self._traces.items() if e.status in _ACTIVE_STATES]
            while len(active) >= self.max_traces:
                evicted = self._traces[active.pop(0)]
                self._dispose_locked(evicted)
                evicted.status = "evicted"
            receipt = self.receipts.Receipt(self.receipt_directory, trace_id, root_id,
                                            self.turn_id, producer=dict(self.producer))
            self._traces[trace_id] = _TraceEntry(receipt=receipt, status="open", opened_at=now)
            self._prune_history_locked()
        self._release_pending()
        return receipt

    def retain(self, trace_id: str):
        """Retain the Receipt's terminal-proof reader in custody until turn closure.

        Retention is only valid while THIS collector's own boundary is the currently
        executing scope; a foreign turn's boundary never adopts another turn's reader.
        """
        if os.getpid() != self._pid:
            return None
        self._release_pending()
        with self._lock:
            entry = self._traces.get(trace_id)
            if entry is None or entry.status != "open" or entry.receipt is None:
                return None
            # boundary._CURRENT has no public accessor; ownership must not trust any
            # unrelated scope that happens to be executing on this thread.
            if self._boundary is None or boundary._CURRENT.get() is not self._boundary:
                entry.status = _UNVERIFIED
                entry.receipt = None
                return None
            handle = self.context.retain_turn_evidence(self._reader(entry))
            if handle is None:
                entry.status = _UNVERIFIED
                entry.receipt = None
                return None
            entry.handle = handle
            entry.status = "retained"
            return handle

    def collect(self, *, deadline_seconds: float = 2.0) -> dict[str, dict]:
        """Report collected terminal proofs after quiescence; bounded wait, no indefinite join.

        This is a report, not an authority action: observed abandonment or boundary
        failure latches the collector's authority dead permanently (a later restore of
        context internals cannot resurrect a transfer) without falsifying what was
        already collected.
        """
        if not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= _MAX_COLLECT_DEADLINE_SECONDS:
            raise ValueError("deadline must be finite and bounded")
        if os.getpid() != self._pid:
            return {t: {"status": _UNKNOWN, "detail": "collector process changed"}
                    for t in self._traces}
        deadline = self._monotonic() + deadline_seconds
        with self._lock:
            self._expire_locked(self._monotonic())
        self._release_pending()
        dead = self._authority_dead
        if self._boundary is not None and (
                bool(self.context._load_abandoned) or self._quiescence() == _UNVERIFIED):
            self._authority_dead = True
            dead = True
        with self._lock:
            snapshots = {}
            for trace_id, entry in self._traces.items():
                report = self._result(entry)
                if dead and report["status"] in ("collected", "pending"):
                    report = {"status": _UNVERIFIED,
                              "detail": "authority dead: abandonment or boundary failure"}
                snapshots[trace_id] = report
        pending = [t for t, r in snapshots.items() if r["status"] == "pending"]
        while pending and self._monotonic() < deadline:
            state = self._quiescence()
            if state == _UNVERIFIED:
                self._authority_dead = True
                for trace_id in pending:
                    snapshots[trace_id] = {"status": _UNVERIFIED, "detail": "boundary failed"}
                pending.clear()
                break
            if state != "ready":
                time.sleep(_POLL_SECONDS)
                continue
            trace_id = pending[0]
            with self._lock:
                entry = self._traces.get(trace_id)
                if entry is None or entry.status != "retained":
                    # A concurrent action changed this trace's disposition: report it,
                    # never the stale pending snapshot taken before the change.
                    snapshots[trace_id] = (self._result(entry) if entry is not None else
                                           {"status": _UNVERIFIED, "detail": "trace removed"})
                    pending.pop(0)
                    continue
                read = self.context.collect_turn_evidence(
                    entry.handle, timeout=min(_MAX_READ_TIMEOUT_SECONDS,
                                              max(deadline - self._monotonic(), 0.001)))
                if read.status == _COLLECTED:
                    entry.status = _COLLECTED
                    entry.proof = _proof_copy(read.value)
                    snapshots[trace_id] = {"status": _COLLECTED, "proof": _proof_copy(entry.proof)}
                elif read.status == "pending":
                    continue
                else:
                    entry.status = _UNVERIFIED
                    entry.receipt = None
                    snapshots[trace_id] = {"status": _UNVERIFIED, "detail": "custody rejected the read"}
                if self.context._load_abandoned:
                    self._authority_dead = True  # abandonment observed during the read
                pending.pop(0)
        for trace_id in pending:
            snapshots[trace_id] = {"status": _UNKNOWN, "detail": "quiescence not reached within deadline"}
        if self._authority_dead:
            # Last read: a death observed at any point of this call — including after a
            # custody read or set by another thread mid-call — voids every authority claim.
            for trace_id, report in snapshots.items():
                if report["status"] in (_COLLECTED, "pending"):
                    snapshots[trace_id] = {"status": _UNVERIFIED,
                                           "detail": "authority died during this call"}
        return snapshots

    def issue_transfer(self, trace_id: str, *, now: float | None = None):
        """Issue the single-use authenticated post-quiescence proof transfer for one trace.

        The signed authority is a FRESH proof re-read from the retained Receipt, and
        every authority predicate is re-verified AFTER the signature exists: any
        observable change between the first check and the final check refuses the
        release and permanently latches the authority dead.
        """
        if os.getpid() != self._pid:
            return None
        if self._authority_dead:
            with self._lock:
                entry = self._traces.get(trace_id)
                if entry is not None and entry.status in _ACTIVE_STATES:
                    entry.status = _UNVERIFIED
                    entry.proof = None
                    entry.receipt = None
            return None
        state = self._quiescence()
        abandoned = bool(self.context._load_abandoned)
        if self._boundary is not None and (abandoned or state == _UNVERIFIED):
            # The authority is dead: latch durably and never sign.
            self._authority_dead = True
            with self._lock:
                entry = self._traces.get(trace_id)
                if entry is not None and entry.status in _ACTIVE_STATES:
                    entry.status = _UNVERIFIED
                    entry.proof = None
                    entry.receipt = None
            return None
        if state != "ready":
            return None  # still quiescing; no disposition change
        with self._lock:
            self._expire_locked(self._monotonic())
        self._release_pending()
        with self._lock:
            entry = self._traces.get(trace_id)
            if entry is None:
                return None
            if entry.status == _TRANSFERRED:
                raise RuntimeError("transfer already issued for this trace")
            if entry.status != _COLLECTED or entry.proof is None or entry.receipt is None:
                return None
            if self.key is None:
                raise RuntimeError("collector has no transfer key")
            fresh = entry.receipt.terminal_proof()
            if fresh.get("valid") is not True:
                self._authority_dead = True
                entry.status = _UNVERIFIED
                entry.receipt = None
                entry.proof = None
                return None
            try:
                snapshot = self.receipts.read_receipt(self.receipt_directory, trace_id)
            except (OSError, ValueError):
                entry.status = _UNKNOWN
                entry.receipt = None
                entry.proof = None
                return None
            payload = {"schema": _TRANSFER_SCHEMA, "kind": _TRANSFER_KIND,
                       "trace_id": trace_id, "turn_sha256": self.turn_sha256,
                       "profile": self.profile, "producer": dict(self.producer),
                       "receipt_sha256": fresh.get("receipt_sha256"),
                       "registration_sha256": self._registration,
                       "issued_at": time.time() if now is None else now,
                       "nonce": secrets.token_hex(16), "proof": _proof_copy(fresh),
                       "snapshot": snapshot}
            signature = hmac.new(self.key, _canonical(payload), hashlib.sha256).hexdigest()
            # Final re-verification with the signature in hand: the authority must
            # still be intact, or the envelope is discarded and the authority dies.
            final = entry.receipt.terminal_proof()
            if (self._quiescence() != "ready" or self.context._load_abandoned
                    or final.get("valid") is not True
                    or final.get("receipt_sha256") != fresh.get("receipt_sha256")
                    or final.get("producer") != fresh.get("producer")
                    or self._authority_dead):
                self._authority_dead = True
                entry.status = _UNVERIFIED
                entry.receipt = None
                entry.proof = None
                return None
            entry.status = _TRANSFERRED
            entry.proof = _proof_copy(fresh)
            entry.receipt = None
            entry.handle = None
            return dict(payload, signature=signature)

    def __del__(self):
        # A dropped collector abandons its readers: release their custody slots with a
        # bounded wait for a busy custody lock and a deferred retry fallback. In-flight
        # reads (custody owner None) stay charged until their own reader exits. Never
        # raises, including at interpreter teardown.
        try:
            _drain_deferred_release()
            handles = list(self._pending_release) + [
                e.handle for e in self._traces.values() if e.handle is not None]
            if not handles:
                return
            deadline = time.monotonic() + _DEL_RELEASE_SECONDS
            while True:
                if boundary._CUSTODY_LOCK.acquire(blocking=False):
                    try:
                        self._release_handles_locked(handles)
                    finally:
                        boundary._CUSTODY_LOCK.release()
                    return
                if time.monotonic() >= deadline:
                    _defer_release(handles)
                    return
                time.sleep(0.005)
        except Exception:  # health: allow BLE001,S110 -- __del__ must never raise or log at interpreter teardown
            pass

    def _reader(self, entry: _TraceEntry) -> Callable[[], Any]:
        """Custody reader closed over the entry alone: a disposed or replaced trace can
        never yield a valid authority, and the closure pins neither the collector nor
        a dropped Receipt."""
        producer = dict(self.producer)

        def read():
            receipt = entry.receipt
            if entry.status == "retained" and receipt is not None:
                return receipt.terminal_proof()
            return {"receipt_sha256": None, "valid": False, "producer": producer,
                    "issued_at": time.time()}
        return read

    def _dispose_locked(self, entry: _TraceEntry) -> None:
        if entry.receipt is not None:
            entry.receipt.invalidate()
        entry.receipt = None
        entry.proof = None
        if entry.handle is not None:
            self._pending_release.append(entry.handle)
            entry.handle = None

    def _release_handles_locked(self, handles) -> None:
        # Caller holds the custody lock. In-flight reads (custody owner None) keep their
        # charged slot until the reader's own exit pops it.
        for handle in handles:
            held = boundary._RETAINED.get(handle)
            if held is not None and held[0] is not None and held[1] is self._boundary:
                del boundary._RETAINED[handle]

    def _release_pending(self) -> None:
        _drain_deferred_release()
        if os.getpid() != self._pid:
            return  # fork child: the inherited collector lock may never be free
        if self._boundary is not None and self._quiescence() == "pending":
            return  # mid-turn deferral: released post-quiescence or at destruction
        with self._lock:
            pending, self._pending_release = self._pending_release, []
        if not pending:
            return
        with boundary._CUSTODY_LOCK:
            self._release_handles_locked(pending)

    def _expire_locked(self, now: float) -> None:
        for entry in self._traces.values():
            if entry.status in _ACTIVE_STATES and now - entry.opened_at > self.retention_seconds:
                self._dispose_locked(entry)
                entry.status = "expired"

    def _prune_history_locked(self) -> None:
        # Bounded ledger: at most _MAX_TRACES entries total. Oldest terminal entries
        # are pruned first; their custody handles are queued for release.
        if len(self._traces) <= _MAX_TRACES:
            return
        for key in list(self._traces):
            if len(self._traces) <= _MAX_TRACES:
                break
            entry = self._traces[key]
            if entry.status in _ACTIVE_STATES:
                continue
            self._dispose_locked(entry)
            del self._traces[key]

    def _quiescence(self) -> str:
        scope = self._boundary
        if scope is None or os.getpid() != self._pid:
            return _UNVERIFIED
        with scope.lock:
            return scope.ready()

    def _result(self, entry: _TraceEntry) -> dict:
        if entry.status == _COLLECTED:
            return {"status": _COLLECTED, "proof": _proof_copy(entry.proof)}
        if entry.status == _TRANSFERRED:
            return {"status": _TRANSFERRED}
        if entry.status == "retained":
            return {"status": "pending"}
        if entry.status in ("evicted", "expired", _UNKNOWN):
            return {"status": _UNKNOWN, "detail": f"retention ended: {entry.status}"}
        return {"status": _UNVERIFIED, "detail": f"trace disposition: {entry.status}"}


class TransferVerifier:
    """Offline authenticated consumer: keyed provenance, exact binding, single use."""

    def __init__(self, key: bytes, *, freshness_seconds: float = 120.0,
                 clock: Callable[[], float] = time.time,
                 max_consumed_nonces: int = _MAX_CONSUMED_NONCES):
        self._key = _check_key(key)
        if not math.isfinite(freshness_seconds) or not 0 < freshness_seconds <= _MAX_FRESHNESS_SECONDS:
            raise ValueError("freshness must be finite and bounded")
        self._freshness = freshness_seconds
        self._clock = clock
        self._max_nonces = max_consumed_nonces
        self._lock = threading.Lock()
        self._consumed: set[str] = set()

    def verify(self, envelope, *, expected_binding: dict):
        """Authenticate, bind and consume once; returns the verified payload or None.

        ``expected_binding`` pins profile, turn digest, producer and — when supplied —
        the exact receipt digest and registration-generation digest.
        """
        if not isinstance(envelope, dict) or "signature" not in envelope:
            return None
        signature = envelope["signature"]
        if (not isinstance(signature, str) or len(signature) != 64
                or signature.strip("0123456789abcdef")):
            return None  # signatures are ASCII hex; anything else is malformed, not compared
        payload = {k: v for k, v in envelope.items() if k != "signature"}
        expected = hmac.new(self._key, _canonical(payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        now = self._clock()
        issued = payload.get("issued_at")
        if (payload.get("schema") != _TRANSFER_SCHEMA or payload.get("kind") != _TRANSFER_KIND
                or type(issued) not in (int, float) or not math.isfinite(issued)
                or issued > now + _MAX_CLOCK_SKEW_SECONDS or now - issued > self._freshness):
            return None
        proof = payload.get("proof")
        if not isinstance(proof, dict) or not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
            return None
        if (payload.get("trace_id") != expected_binding.get("trace_id")
                or payload.get("turn_sha256") != expected_binding.get("turn_sha256")
                or payload.get("profile") != expected_binding.get("profile")
                or payload.get("producer") != expected_binding.get("producer")
                or payload.get("receipt_sha256") != proof.get("receipt_sha256")
                or proof.get("producer") != payload.get("producer")):
            return None
        pinned_receipt = expected_binding.get("receipt_sha256")
        if pinned_receipt is not None and payload.get("receipt_sha256") != pinned_receipt:
            return None
        pinned_registration = expected_binding.get("registration_sha256")
        if pinned_registration is not None and payload.get("registration_sha256") != pinned_registration:
            return None
        with self._lock:
            if payload["nonce"] in self._consumed or len(self._consumed) >= self._max_nonces:
                return None
            self._consumed.add(payload["nonce"])
        return payload


def validate_backend(payload: dict, backend: dict, *, receipts: ModuleType, now=None):
    """Validate a verified transfer payload against a backend envelope via the original reconcile."""
    try:
        return receipts.reconcile(payload["snapshot"], backend,
                                  terminal_proof=payload["proof"], now=now)
    except (KeyError, TypeError, ValueError):
        return _UNVERIFIED
