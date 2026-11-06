"""Gateway lifecycle transitions preserve their optional observer bookkeeping."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.fallback_observers import _fallback_chain_exhausted
from gateway.lifecycle_counters import GatewayLifecycleCounters
from gateway.run import GatewayRunner
from gateway.run_shutdown import GatewayShutdownMixin


def test_refresh_rearms_exhaustion_observer_for_the_restarted_chain(monkeypatch):
    observed = []
    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda _name: True)
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook", lambda name, **event: observed.append((name, event))
    )
    chain = [{"provider": "openrouter", "model": "fallback"}]
    agent = SimpleNamespace(
        _fallback_chain=chain, _fallback_model=chain[0], _fallback_index=1,
        _fallback_activated=False, _rate_limited_until=0,
    )
    assert _fallback_chain_exhausted(agent, None) is False
    GatewayRunner._apply_fallback_chain_to_agent(agent, chain)
    assert agent._fallback_index == 0
    assert _fallback_chain_exhausted(agent, None) is False
    assert _fallback_chain_exhausted(agent, None) is False
    assert [name for name, _event in observed] == ["on_fallback_chain_exhausted"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("observer_raises", [False, True], ids=["aggregate", "broken-observer"])
async def test_shutdown_counts_detached_cache_before_cleanup_and_ignores_observer_failure(observer_raises):
    counters = GatewayLifecycleCounters(enabled=True)
    lock = threading.Lock()
    cache = {"first": (object(),), "second": (object(),)}
    cleaned = []

    async def await_phase(operation, **_kwargs):
        return await operation

    async def cleanup(agent, *, session_key, **_kwargs):
        assert not cache
        assert lock.acquire(blocking=False), "diagnostics must run outside the cache ownership lock"
        lock.release()
        if not observer_raises:
            events = counters.snapshot()["agent_cache"]["events"]
            assert events["shutdown_cache_clear_count"] == 1
            assert events["shutdown_cache_clear_total"] == 2
        cleaned.append((session_key, agent))

    if observer_raises:
        def refuse(_cleared):
            raise RuntimeError("observer unavailable")
        counters.record_shutdown_cache_clear = refuse
    runner = SimpleNamespace(
        _restart_requested=False, _agent_cache_lock=lock, _agent_cache=cache,
        _lifecycle_counters=counters, _finalize_shutdown_agents=AsyncMock(return_value=True),
        _await_shutdown_phase=await_phase, _cleanup_agent_resources_off_loop=cleanup,
        _shutdown_remaining=lambda deadline: max(0, deadline - time.monotonic()),
        adapters={}, _profile_adapters={},
    )
    ctx = GatewayShutdownMixin._StopContext(deferred_count=lambda: 0)
    ctx.started_at = time.monotonic()
    ctx.active_agents = []
    ctx.cleanup_deadline = time.monotonic() + 10
    ctx.teardown_deadline = ctx.cleanup_deadline
    await GatewayShutdownMixin._stop_finalize_agents_and_adapters(runner, ctx)
    assert [key for key, _agent in cleaned] == ["first", "second"]
