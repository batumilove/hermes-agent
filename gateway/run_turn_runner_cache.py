"""Per-turn cache diagnostics after cache ownership locks are released."""

def _record_turn_cache_result(self, found, agent, cap_evicted):
    """Publish diagnostics only after releasing cache ownership locks."""
    from gateway.run import _record_lifecycle
    counters = getattr(self._runner, "_lifecycle_counters", None)
    if counters is None:
        return
    _record_lifecycle(counters, "record_cache_event", found.cache_event)
    if found.eviction_kind:
        _record_lifecycle(counters, "record_eviction", found.eviction_kind)
    if not found.reused:
        _record_lifecycle(counters, "record_cache_event", "created")
        for _ in range(cap_evicted if isinstance(cap_evicted, int) else 0):
            _record_lifecycle(counters, "record_eviction", "cap")
        engine = getattr(agent, "context_compressor", None)
        if engine is not None:
            _record_lifecycle(counters, "record_context_engine", engine)
