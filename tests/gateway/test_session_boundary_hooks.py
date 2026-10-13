"""Explicit session finalization and permanent-session resource eviction boundaries.

Automatic expiry fixtures were retired when sessions became durable until an explicit reset.
"""
import threading
from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource, SessionStore


@pytest.mark.asyncio
async def test_explicit_finalize_reaches_plugin_and_returns_messages(monkeypatch):
    from agent.async_utils import run_sync_in_detached_daemon_thread
    from hermes_cli import plugins

    calls = []
    def plugin_hook(name, **kwargs):
        calls.append((name, kwargs))
        return ["Session finalized"]
    async def async_plugin_hook(name, **kwargs):
        return plugin_hook(name, **kwargs)
    monkeypatch.setattr(plugins, "invoke_hook", plugin_hook)
    monkeypatch.setattr(plugins, "ainvoke_hook", async_plugin_hook)
    runner = object.__new__(GatewayRunner)
    runner._run_release_in_profile_scope = lambda fn, args, _key: fn(*args)
    async def off_loop(fn, *args):
        return await run_sync_in_detached_daemon_thread(fn, *args)
    runner._run_housekeeping_in_executor = off_loop

    messages = await runner._finalize_session_off_loop(
        session_id="session-explicit", platform="gateway", reason="shutdown")

    assert messages == ["Session finalized"]
    assert calls == [("on_session_finalize", {
        "session_id": "session-explicit", "platform": "gateway", "reason": "shutdown"})]


def test_cache_eviction_keeps_durable_route_and_conversation_model(tmp_path):
    from run_agent import AIAgent

    store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="42", user_id="u1", chat_type="dm")
    runner = object.__new__(GatewayRunner)
    runner.session_store = store
    runner._agent_cache_lock = threading.Lock()
    runner._running_agents = {}
    runner._lifecycle_counters = None
    try:
        entry = store.get_or_create_session(source)
        runner._last_resolved_model = {entry.session_key: "conversation-model", "other": "retain-other"}
        manager = MagicMock()
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._session_messages = [{"role": "user", "content": "retain conversation"}]
        agent.release_clients = MagicMock()
        runner._agent_cache = {entry.session_key: (agent, "signature")}
        runner._schedule_evicted_agent_soft_release = lambda a, key: runner._commit_then_release_soft(a, key)

        runner._evict_cached_agent(entry.session_key)

        assert entry.session_key not in runner._agent_cache
        assert store.get_or_create_session(source).session_id == entry.session_id
        assert runner._last_resolved_model[entry.session_key] == "conversation-model"
        assert runner._last_resolved_model["other"] == "retain-other"
        manager.on_session_end.assert_called_once_with([{"role": "user", "content": "retain conversation"}])
        manager.shutdown_all.assert_called_once_with()
        agent.release_clients.assert_called_once_with()
    finally:
        store._db_handle_cache.close_all(lambda db: db.close())
