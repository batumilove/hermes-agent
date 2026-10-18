"""A daemon conversation boundary ends its context engine after retained cleanup settles."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("boundary", [False, True])
def test_context_engine_boundary_survives_failed_provider_release(boundary):
    from gateway.run import GatewayRunner
    runner = GatewayRunner.__new__(GatewayRunner)
    messages = [{"role": "user", "content": "preserve this transcript"}]
    engine = SimpleNamespace(on_session_end=Mock())
    release = Mock(side_effect=[False, True])
    def release_memory(*, commit=False, messages=None):
        return release()
    agent = SimpleNamespace(session_id="owned", _session_messages=messages,
                            context_compressor=engine, _memory_manager=None,
                            release_memory_provider_for_cache_evict=release_memory,
                            release_clients=Mock())
    runner._retain_failed_evicted_agent_cleanup(agent, "route", boundary=boundary)
    assert runner._commit_then_release_soft(agent, "route") is False
    engine.on_session_end.assert_not_called()
    assert agent._session_messages == messages
    assert runner._retry_failed_evicted_agent_cleanups() == 1
    if boundary:
        engine.on_session_end.assert_called_once_with("owned", messages)
    else:
        engine.on_session_end.assert_not_called()
