"""Aggregate lifecycle diagnostics for the explicit /agents diagnostics option."""
import json

def agents_diagnostics(self, event):
    from gateway.run import _AGENT_CACHE_IDLE_TTL_SECS, _AGENT_CACHE_MAX_SIZE
    get_args = getattr(event, "get_command_args", None)
    if not callable(get_args) or get_args().strip() != "--diagnostics":
        return None
    counters = getattr(self, "_lifecycle_counters", None)
    if counters is None:
        from gateway.lifecycle_counters import GatewayLifecycleCounters
        counters = GatewayLifecycleCounters()
    cache = getattr(self, "_agent_cache", None)
    cache_lock = getattr(self, "_agent_cache_lock", None)
    if cache is not None:
        if cache_lock is not None:
            with cache_lock:
                cache_size = len(cache)
        else:
            cache_size = len(cache)
        counters.set_agent_cache_size(
            cache_size,
            max_size=_AGENT_CACHE_MAX_SIZE,
            idle_ttl_seconds=_AGENT_CACHE_IDLE_TTL_SECS,
        )
    counters.set_agents_running(len(getattr(self, "_running_agents", {}) or {}))
    background = getattr(self, "_background_tasks", set()) or set()
    cleanup = getattr(self, "_deferred_agent_cleanup_tasks", set()) or set()
    counters.set_task_counts(
        sum(1 for task in background if not task.done()),
        sum(1 for task in cleanup if not task.done()),
    )
    counters.set_executor(getattr(self, "_executor", None))
    return json.dumps(counters.snapshot(), sort_keys=True)
