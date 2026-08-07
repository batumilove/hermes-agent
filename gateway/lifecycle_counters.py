"""Process-local gateway lifecycle counters.

A small, thread-safe, process-local counter that records aggregate lifecycle
events for the gateway's agent cache, executor, threads, and tasks.  It exists
solely to feed ``/agents --diagnostics`` — a sorted-JSON snapshot an operator
can read to understand cache pressure, eviction mix, and cleanup health
WITHOUT exposing any session key, chat id, user id, file path, or message
content.

Design constraints (enforced by tests in ``test_lifecycle_counters.py``):

* **No persistence or outbound telemetry.** Collection reuses the existing
  local shared-metrics opt-in at construction time; counters then live only in
  process memory and are lost on restart. No SQLite, files, or network writes.
* **No identifiers.** Recording methods accept a ``session_key`` argument for
  ergonomic call-site symmetry with the instrumented paths, but that value is
  deliberately ignored — it NEVER reaches the snapshot.  Only aggregate counts
  and class/type names are stored.
* **Thread-safe.** A single ``threading.Lock`` guards all mutations and the
  snapshot read.  Locks are held for the minimum work (increment an int / copy
  a shallow dict) so the cache lock ordering in ``run.py`` is preserved — this
  module never acquires the agent-cache lock and is safe to call while holding
  it.
* **getattr-safe.** ``GatewayRunner`` instances built via ``object.__new__``
  (test fixtures) have no ``_lifecycle_counters``; the instrumented paths use
  ``getattr(self, "_lifecycle_counters", None)`` and skip recording when it is
  ``None``.  The counter's own recording methods are also safe to call on a
  bare instance — they never depend on external state.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

__all__ = ["GatewayLifecycleCounters"]

# Bump when the snapshot shape changes in a backwards-incompatible way.
_SCHEMA_VERSION = 1

_CACHE_FLOW_EVENTS = frozenset(
    ("hit", "miss_absent", "miss_signature", "miss_invalidated", "created")
)
_EVENT_KEYS = frozenset(
    (
        *_CACHE_FLOW_EVENTS,
        "cache_event_unknown",
        "unknown",
        "evict_explicit",
        "evict_cap",
        "evict_idle",
        "evict_stale_self_heal",
        "evict_cross_process",
        "evict_unknown",
        "soft_release_calls",
        "soft_release_success",
        "soft_release_failure",
        "hard_cleanup_calls",
        "hard_cleanup_success",
        "hard_cleanup_failure",
        "agent_close_attempt",
        "agent_close_success",
        "agent_close_failure",
        "shutdown_cache_clear_count",
        "shutdown_cache_clear_total",
    )
)


def _type_name(obj: Any) -> str:
    """Return a stable class/type name for *obj* — never an id or repr.

    Uses ``type(obj).__name__`` so the bare class name is recorded without any
    enclosing-scope qualifier.  No instance state is included.
    """
    t = type(obj)
    return getattr(t, "__name__", None) or "unknown"


class GatewayLifecycleCounters:
    """Thread-safe, process-local lifecycle counter.

    Construct with no arguments.  Call the ``record_*`` / ``set_*`` helpers from
    the instrumented gateway paths, and ``snapshot()`` to get a plain dict for
    ``/agents --diagnostics``.
    """

    def __init__(
        self, *, enabled: Optional[bool] = None, gc_enabled: bool = False
    ) -> None:
        if enabled is None:
            enabled = False
        self._enabled = bool(enabled)
        self._lock = threading.Lock()
        self._seq = 0
        # agent_cache.events: flat int counters keyed by event name.
        self._events: Dict[str, int] = {}
        # agent_cache size / config mirror (set via set_agent_cache_size).
        self._cache_entries: int = 0
        self._cache_max_size: Optional[int] = None
        self._cache_idle_ttl: Optional[float] = None
        # context_engines: by-type counts (class/type name -> count).
        self._ce_by_type: Dict[str, int] = {}
        self._ce_total: int = 0
        # Aggregate executor mirror only; never retain the executor object.
        self._executor_present = False
        self._executor_shutdown = False
        self._executor_threads = 0
        self._executor_queued = 0
        # Task / agents-running mirrors (set via setters).
        self._tasks_background: int = 0
        self._tasks_cleanup: int = 0
        self._agents_running: int = 0
        # Optional and explicitly controlled; never enabled by an environment
        # variable or merely by enabling the lifecycle counters.
        self._gc_enabled = self._enabled and bool(gc_enabled)

    @classmethod
    def from_config(cls) -> "GatewayLifecycleCounters":
        """Construct from the existing local shared-metrics opt-in policy."""
        try:
            from hermes_cli.config import read_raw_config_readonly

            config = read_raw_config_readonly() or {}
        except Exception:
            enabled = False
        else:
            telemetry = config.get("telemetry") if isinstance(config, dict) else None
            shared_metrics = (
                telemetry.get("shared_metrics")
                if isinstance(telemetry, dict)
                else None
            )
            enabled = (
                isinstance(shared_metrics, dict)
                and shared_metrics.get("enabled") is True
            )
        return cls(enabled=enabled)

    # ------------------------------------------------------------------
    # Low-level increment (internal)
    # ------------------------------------------------------------------
    def incr(self, group: str, key: str, amount: int = 1) -> None:
        """Increment a nested ``group -> key`` int counter.

        ``group`` is currently always ``"agent_cache.events"``; kept generic so
        future groups don't require an API change.
        """
        if not self._enabled or amount == 0:
            return
        with self._lock:
            self._seq += 1
            if group == "agent_cache.events":
                bounded_key = key if key in _EVENT_KEYS else "unknown"
                self._events[bounded_key] = self._events.get(bounded_key, 0) + amount
            # Other groups could be added here without breaking the API.

    # ------------------------------------------------------------------
    # Cache-flow events
    # ------------------------------------------------------------------
    def record_cache_event(
        self, event: str, session_key: Optional[str] = None
    ) -> None:
        """Record an agent-cache flow event.

        ``event`` is one of: ``hit``, ``miss_absent``, ``miss_signature``,
        ``miss_invalidated``, ``created``. Unknown values map to one bounded
        counter. ``session_key`` is accepted for call-site symmetry but is
        deliberately IGNORED — it never reaches the snapshot (privacy).
        """
        bounded_event = event if event in _CACHE_FLOW_EVENTS else "cache_event_unknown"
        self.incr("agent_cache.events", bounded_event)

    # ------------------------------------------------------------------
    # Evictions
    # ------------------------------------------------------------------
    _EVICTION_KINDS = (
        "explicit",
        "cap",
        "idle",
        "stale_self_heal",
        "cross_process",
    )

    def record_eviction(
        self, kind: str, session_key: Optional[str] = None
    ) -> None:
        """Record a cache eviction of the given *kind*.

        ``kind`` is one of: ``explicit``, ``cap``, ``idle``,
        ``stale_self_heal``, ``cross_process``.  Unknown kinds are recorded
        under ``evict_unknown`` so we never silently drop a signal.
        ``session_key`` is ignored (privacy).
        """
        key = f"evict_{kind}" if kind in self._EVICTION_KINDS else "evict_unknown"
        self.incr("agent_cache.events", key)

    # ------------------------------------------------------------------
    # Soft release (cap / idle / xproc eviction cleanup)
    # ------------------------------------------------------------------
    def record_soft_release(
        self, success: bool, session_key: Optional[str] = None
    ) -> None:
        """Record a soft-release (``release_clients``) call."""
        self.incr("agent_cache.events", "soft_release_calls")
        if success:
            self.incr("agent_cache.events", "soft_release_success")
        else:
            self.incr("agent_cache.events", "soft_release_failure")

    # ------------------------------------------------------------------
    # Hard cleanup (``_cleanup_agent_resources``)
    # ------------------------------------------------------------------
    def record_hard_cleanup(
        self,
        attempted: bool,
        success: Optional[bool] = None,
        session_key: Optional[str] = None,
    ) -> None:
        """Record a full-cleanup attempt and, when known, its outcome."""
        if attempted:
            self.incr("agent_cache.events", "hard_cleanup_calls")
        if success is True:
            self.incr("agent_cache.events", "hard_cleanup_success")
        elif success is False:
            self.incr("agent_cache.events", "hard_cleanup_failure")

    # ------------------------------------------------------------------
    # agent.close() attempt / success / failure
    # ------------------------------------------------------------------
    def record_agent_close(
        self,
        attempted: bool,
        success: bool,
        session_key: Optional[str] = None,
    ) -> None:
        """Record an ``agent.close()`` attempt and its outcome."""
        if attempted:
            self.incr("agent_cache.events", "agent_close_attempt")
            if success:
                self.incr("agent_cache.events", "agent_close_success")
            else:
                self.incr("agent_cache.events", "agent_close_failure")

    # ------------------------------------------------------------------
    # Shutdown cache-clear
    # ------------------------------------------------------------------
    def record_shutdown_cache_clear(self, cleared: int) -> None:
        """Record that the shutdown path cleared *cleared* cache entries."""
        if not self._enabled:
            return
        with self._lock:
            self._seq += 1
            self._events["shutdown_cache_clear_count"] = (
                self._events.get("shutdown_cache_clear_count", 0) + 1
            )
            self._events["shutdown_cache_clear_total"] = (
                self._events.get("shutdown_cache_clear_total", 0) + max(0, cleared)
            )

    # ------------------------------------------------------------------
    # Context-engine cache (by type name only)
    # ------------------------------------------------------------------
    def record_context_engine(self, engine: Any) -> None:
        """Record a cached context engine by its class/type name only."""
        if not self._enabled:
            return
        name = _type_name(engine)
        with self._lock:
            self._seq += 1
            self._ce_by_type[name] = self._ce_by_type.get(name, 0) + 1
            self._ce_total += 1

    # ------------------------------------------------------------------
    # Size / config mirrors
    # ------------------------------------------------------------------
    def set_agent_cache_size(
        self,
        entries: int,
        max_size: Optional[int] = None,
        idle_ttl_seconds: Optional[float] = None,
    ) -> None:
        """Mirror the current agent-cache entry count and config."""
        if not self._enabled:
            return
        with self._lock:
            self._seq += 1
            self._cache_entries = int(entries)
            if max_size is not None:
                self._cache_max_size = int(max_size)
            if idle_ttl_seconds is not None:
                self._cache_idle_ttl = float(idle_ttl_seconds)

    def set_executor(self, executor: Any) -> None:
        """Mirror aggregate executor gauges without retaining the executor."""
        if not self._enabled:
            return
        present = executor is not None
        shutdown = False
        threads = 0
        queued = 0
        if present:
            try:
                shutdown = bool(getattr(executor, "_shutdown", False))
            except Exception:
                pass
            try:
                threads = int(getattr(executor, "_max_workers", 0))
            except Exception:
                pass
            try:
                queue = getattr(executor, "_work_queue", None)
                if queue is not None and hasattr(queue, "qsize"):
                    queued = int(queue.qsize())
            except Exception:
                pass
        with self._lock:
            self._seq += 1
            self._executor_present = present
            self._executor_shutdown = shutdown
            self._executor_threads = threads
            self._executor_queued = queued

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def _executor_snapshot(self) -> Dict[str, Any]:
        """Return the aggregate executor mirror — never an object reference."""
        with self._lock:
            return {
                "present": self._executor_present,
                "shutdown": self._executor_shutdown,
                "threads": self._executor_threads,
                "queued": self._executor_queued,
            }

    def _gc_snapshot(self) -> Dict[str, Any]:
        """Optional gc stats — evaluated lazily on snapshot, never raises."""
        if not self._gc_enabled:
            return {}
        try:
            import gc

            return {
                "counts": list(gc.get_count()),
                "stats": [dict(s) for s in gc.get_stats()],
            }
        except Exception:
            return {}

    def _threads_snapshot(self) -> Dict[str, Any]:
        """Active thread count (best-effort)."""
        try:
            return {"active": threading.active_count()}
        except Exception:
            return {"active": 0}

    def _tasks_snapshot(self) -> Dict[str, Any]:
        """Background/cleanup task counts.

        These are populated by the gateway via ``set_task_counts`` if available;
        otherwise they default to 0.  We do NOT inspect asyncio loops here
        (that would require the loop and could race).
        """
        with self._lock:
            return {
                "background_active": self._tasks_background,
                "cleanup_active": self._tasks_cleanup,
            }

    def set_task_counts(
        self, background_active: int = 0, cleanup_active: int = 0
    ) -> None:
        """Mirror the current background / cleanup task counts."""
        if not self._enabled:
            return
        with self._lock:
            self._seq += 1
            self._tasks_background = int(background_active)
            self._tasks_cleanup = int(cleanup_active)

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep-ish copy of the current counter state.

        The returned dict is fully isolated from the live counter — mutating it
        cannot corrupt subsequent snapshots.  Thread-safe.
        """
        with self._lock:
            seq = self._seq
            events_copy = dict(self._events)
            ce_by_type_copy = dict(self._ce_by_type)
            entries = self._cache_entries
            max_size = self._cache_max_size
            idle_ttl = self._cache_idle_ttl
            ce_total = self._ce_total
            agents_running = self._agents_running
            executor = {
                "present": self._executor_present,
                "shutdown": self._executor_shutdown,
                "threads": self._executor_threads,
                "queued": self._executor_queued,
            }
            tasks = {
                "background_active": self._tasks_background,
                "cleanup_active": self._tasks_cleanup,
            }

        snap: Dict[str, Any] = {
            "enabled": self._enabled,
            "schema_version": _SCHEMA_VERSION,
            "monotonic_seq": seq,
            "agent_cache": {
                "entries": entries,
                "max_size": max_size,
                "idle_ttl_seconds": idle_ttl,
                "events": events_copy,
            },
            "agents": {
                "running": agents_running,
            },
            "context_engines": {
                "cached_total": ce_total,
                "by_type": ce_by_type_copy,
            },
            "executor": executor,
            "threads": self._threads_snapshot(),
            "tasks": tasks,
        }
        gc_snap = self._gc_snapshot()
        if gc_snap:
            snap["gc"] = gc_snap
        return snap

    # ------------------------------------------------------------------
    # Agents-running mirror (set by the gateway)
    # ------------------------------------------------------------------
    def set_agents_running(self, count: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._seq += 1
            self._agents_running = int(count)
