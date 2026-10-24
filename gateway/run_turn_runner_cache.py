"""Cache owner transfer and per-turn aggregate cache diagnostics."""
from contextlib import suppress
import logging
logger = logging.getLogger("gateway.run")

def _pop_cached_agent_for_eviction(self):
    """Evict under the lock but DEFER release (release_clients can block on memory-provider /
    socket teardown while the idle sweeper waits on this lock). The turn rebuilds a fresh agent, so
    the caller does a SOFT release that keeps sandbox / browser / bg processes."""
    from gateway.run import _AGENT_PENDING_SENTINEL
    candidate = self._runner._agent_cache.get(self._ctx.session_key)
    candidate_agent = candidate[0] if isinstance(candidate, tuple) and candidate else None
    if candidate_agent is not None and candidate_agent is not _AGENT_PENDING_SENTINEL:
        self._runner._retain_failed_evicted_agent_cleanup(candidate_agent, self._ctx.session_key)
    evicted = self._runner._agent_cache.pop(self._ctx.session_key, None)
    agent = evicted[0] if isinstance(evicted, tuple) and evicted else None
    return agent if agent and agent is not _AGENT_PENDING_SENTINEL else None


def _lookup_cached_agent(self, sig, cache_lock, cache, max_iterations, peek_sid, dead, msg_count):
    ctx = self._ctx
    out = self._CachedAgentLookup()
    if not (cache_lock and cache is not None):
        return out
    with cache_lock:
        cached = cache.get(ctx.session_key)
        if cached and cached[1] != sig:
            out.evicted = self._pop_cached_agent_for_eviction()
            out.cache_event = "miss_signature"
            out.eviction_kind = "signature"
            return out
        if not cached:
            return out
        # cached[2] = message_count at cache time (stale when a second process appended rows);
        # cached[3] = the session_id the snapshot was taken for.
        cached_mc = cached[2] if len(cached) > 2 else None
        cached_sid = cached[3] if len(cached) > 3 else None
        # Same session_key, other conversation: the counts track DIFFERENT DB rows, so the
        # comparison is meaningless — REUSE rather than bust the prompt cache on every switch.
        sid_mismatch = cached_sid is not None and ctx.session_id is not None and cached_sid != ctx.session_id
        # Re-validate the outside-lock dead-session peek against the tuple read under THIS lock:
        # a stale "dead" verdict must never be applied to a different (possibly live) agent.
        if sid_mismatch and dead and cached_sid == peek_sid:
            out.cache_event = "miss_invalidated"
            out.eviction_kind = "stale_self_heal"
            logger.info(
                "Agent cache invalidated for session %s: "
                "cached agent's session_id %s is ended in "
                "state.db (stale self-heal artifact, "
                "#54878 x #54947) — discarding instead of "
                "reusing across the routing recovery", ctx.session_key, cached_sid,
            )
        elif not sid_mismatch and cached_mc is not None and msg_count is not None and msg_count != cached_mc:
            out.cache_event = "miss_invalidated"
            out.eviction_kind = "cross_process"
            logger.info(
                "Agent cache invalidated for session %s: "
                "message_count changed (%s -> %s), "
                "possible cross-process write", ctx.session_key, cached_mc, msg_count,
            )
        else:
            out.agent = cached[0]
            # Refresh LRU order so cap enforcement evicts truly-oldest entries.
            if hasattr(cache, "move_to_end"):
                with suppress(KeyError):
                    cache.move_to_end(ctx.session_key)
            self._runner._init_cached_agent_for_turn(out.agent, ctx._interrupt_depth)
            # Cached agent may have been created with old config.
            out.agent.max_iterations = max_iterations
            logger.debug("Reusing cached agent for session %s", ctx.session_key)
            out.reused = True
            out.cache_event = "hit"
            return out
        out.evicted = self._pop_cached_agent_for_eviction()
    return out


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
