"""Overlapping same-session turns keep independent native Relay stacks."""
from agent import relay_runtime
from tests.agent.test_relay_llm import relay_turn as _relay_turn
relay_turn = _relay_turn


def test_real_binding_turns_finish_out_of_order_without_orphans(relay_turn):
    relay, first = relay_turn
    second = relay_runtime.SESSION_COORDINATOR.begin_turn(first.lease, turn_id="second", task_id="task-second")
    host, session = first.lease.host, first.lease.session
    def top(turn):
        return host.run_in_session(session, relay_runtime._current_top, relay, scope_context=turn.context)
    assert first.relay_enabled and second.relay_enabled
    assert relay_runtime._same_handle(top(first), first.handle)
    assert relay_runtime._same_handle(top(second), second.handle)
    relay_runtime.SESSION_COORDINATOR.end_turn(first, outcome="success")
    assert relay_runtime._same_handle(top(second), second.handle)
    assert relay_runtime.active_turn() is second
    relay_runtime.SESSION_COORDINATOR.end_turn(second, outcome="success")
    assert relay_runtime.active_turn() is None
    assert not relay_runtime.SESSION_COORDINATOR.has_active_turn(profile_key=first.lease.profile_key, session_id=first.lease.session_id)
    assert relay_runtime._same_handle(host.run_in_session(session, relay_runtime._current_top, relay), session.handle)


def test_managed_stream_uses_inherited_loop_without_closing_it(relay_turn):
    import asyncio
    import threading
    from agent import relay_llm

    _relay, turn = relay_turn
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def run_loop():
        loop.call_soon(started.set)
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    assert started.wait(2)
    token = relay_runtime.bind_sync_bridge_loop(loop)
    chunk = {"choices": [{"delta": {"content": "answer"}, "finish_reason": None}]}
    try:
        stream = relay_llm.stream(
            {"model": "m", "messages": []}, lambda _request: iter([chunk]),
            session_id=turn.lease.session_id, name="test-provider", model_name="m",
            finalizer=lambda: {"choices": [{"message": {"content": "answer"}}]},
        )
        assert stream._loop is loop
        assert not stream._owns_loop
        assert list(stream) == [chunk]
        assert loop.is_running() and not loop.is_closed()
    finally:
        relay_runtime.reset_sync_bridge_loop(token)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        assert not thread.is_alive()
        loop.close()
