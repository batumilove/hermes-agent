"""Detach idle gateway agents and release them within the shutdown budget."""


async def cleanup_idle_cached_agents(runner, deadline) -> None:
    from gateway.run import _record_lifecycle

    cache_lock = getattr(runner, "_agent_cache_lock", None)
    cache = getattr(runner, "_agent_cache", None)
    if cache_lock is None or cache is None:
        return
    with cache_lock:
        idle_agents = list(cache.items())
        cache.clear()
    _record_lifecycle(
        getattr(runner, "_lifecycle_counters", None), "record_shutdown_cache_clear", len(idle_agents)
    )
    for key, entry in idle_agents:
        # Off-loop and deadline-bound: a wedged memory provider must not stall SIGTERM.
        await runner._cleanup_agent_resources_off_loop(
            entry[0] if isinstance(entry, tuple) else entry, context="shutdown idle-cache",
            session_key=key, timeout=runner._shutdown_remaining(deadline),
        )
