"""Retained Relay overlap isolation and asynchronous subscriber shutdown contracts."""
import contextvars
import threading

import pytest

from agent import relay_runtime
from hermes_cli import lifecycle
from hermes_cli.observability import relay_shared_metrics
from tests.hermes_cli.test_relay_shared_metrics_runtime import direct_runtime as _direct_runtime


@pytest.fixture(name="direct_runtime")
def metrics_runtime(tmp_path, monkeypatch):
    yield from _direct_runtime.__wrapped__(tmp_path, monkeypatch)

def test_concurrent_turns_close_in_independent_scope_contexts(
    direct_runtime,
):
    coordinator = relay_runtime.SESSION_COORDINATOR
    profile_key = relay_runtime.current_profile_key()
    lease = coordinator.acquire_conversation(
        profile_key=profile_key,
        session_id="shared-session",
        platform="cli",
    )
    first = coordinator.begin_turn(lease, turn_id="first", task_id="first-task")
    second = coordinator.begin_turn(
        lease,
        turn_id="second",
        task_id="second-task",
    )

    assert first.relay_enabled is True
    assert first.handle is not None
    assert second.relay_enabled is True
    assert second.handle is not None
    assert first.context is not second.context
    assert first.handle != second.handle
    assert relay_runtime.resolve_execution_context("shared-session") == (
        lease.host, lease.session, second.handle,
    )

    coordinator.end_turn(first, outcome="success")
    coordinator.end_turn(second, outcome="success")
    coordinator.release_conversation(lease)
    coordinator.finalize_conversation(
        profile_key=profile_key,
        session_id="shared-session",
    )

    turn_closes = [
        event
        for event in direct_runtime.events
        if event[0] == "scope.pop" and event[1] in {first.handle, second.handle}
    ]
    assert [event[1] for event in turn_closes] == [first.handle, second.handle]
    assert not [
        event
        for event in direct_runtime.events
        if event[0] == "scope.pop.rejected"
    ]



def test_concurrent_turn_creates_metrics_under_its_own_turn(direct_runtime):
    coordinator = relay_runtime.SESSION_COORDINATOR
    profile_key = relay_runtime.current_profile_key()
    lease = coordinator.acquire_conversation(
        profile_key=profile_key,
        session_id="shared-session",
        platform="cli",
    )
    first = coordinator.begin_turn(lease, turn_id="first", task_id="first-task")
    second = coordinator.begin_turn(lease, turn_id="second", task_id="second-task")

    relay_shared_metrics.observe_lifecycle(
        "pre_llm_call",
        session_id="shared-session",
        task_id="second-task",
        platform="cli",
    )
    relay_shared_metrics.observe_lifecycle(
        "pre_api_request",
        session_id="shared-session",
        task_id="second-task",
        api_request_id="second-request",
        platform="cli",
    )

    assert second.relay_enabled is True
    [task_start] = [
        event for event in direct_runtime.events
        if event[0] == "scope.push" and event[1] == relay_shared_metrics.TASK_SCOPE
    ]
    assert task_start[3]["handle"] == second.handle
    [model_start] = [event for event in direct_runtime.events if event[0] == "llm.call"]
    assert model_start[3]["handle"][1] == relay_shared_metrics.TASK_SCOPE
    relay_shared_metrics.finish_task_run(
        session_id="shared-session", task_id="second-task",
        platform="cli", result={"completed": True},
    )

    coordinator.end_turn(first, outcome="success")
    coordinator.end_turn(second, outcome="success")
    coordinator.release_conversation(lease)



def test_overlapping_turn_survives_first_close_and_inherited_context_is_gated_after_close(direct_runtime):
    coordinator = relay_runtime.SESSION_COORDINATOR
    profile_key = relay_runtime.current_profile_key()
    lease = coordinator.acquire_conversation(
        profile_key=profile_key,
        session_id="shared-session",
        platform="cli",
    )
    first = coordinator.begin_turn(lease, turn_id="first", task_id="first-task")
    second = coordinator.begin_turn(lease, turn_id="second", task_id="second-task")
    inherited = contextvars.copy_context()

    coordinator.end_turn(first, outcome="success")

    assert relay_runtime.current_turn() is second
    assert inherited.run(relay_runtime.current_turn) is second
    assert relay_runtime.relay_instrumentation_enabled()
    assert inherited.run(relay_runtime.relay_instrumentation_enabled)
    assert relay_runtime.resolve_execution_context("shared-session") == (
        lease.host, lease.session, second.handle,
    )

    relay_shared_metrics.observe_lifecycle(
        "pre_llm_call",
        session_id="shared-session",
        task_id="second-task",
        platform="cli",
    )
    inherited.run(
        relay_shared_metrics.observe_lifecycle,
        "pre_api_request",
        session_id="shared-session",
        task_id="second-task",
        api_request_id="second-request",
        platform="cli",
    )

    [task_start] = [
        event for event in direct_runtime.events
        if event[0] == "scope.push" and event[1] == relay_shared_metrics.TASK_SCOPE
    ]
    assert task_start[3]["handle"] == second.handle
    assert len([event for event in direct_runtime.events if event[0] == "llm.call"]) == 1
    relay_shared_metrics.finish_task_run(
        session_id="shared-session", task_id="second-task",
        platform="cli", result={"completed": True},
    )

    coordinator.end_turn(second, outcome="success")
    assert relay_runtime.current_turn() is None
    assert inherited.run(relay_runtime.current_turn) is second
    assert not inherited.run(relay_runtime.relay_instrumentation_enabled)
    coordinator.release_conversation(lease)



@pytest.mark.asyncio
async def test_async_session_close_uses_async_subscriber_barrier(direct_runtime):
    from agent import relay_runtime as core_relay_runtime

    runtime = core_relay_runtime.get_runtime()
    assert runtime is not None
    runtime.ensure_session({"session_id": "async-close"})

    await runtime.close_session_async({"session_id": "async-close"})

    assert runtime.get_session("async-close") is None
    assert ("subscribers.flush_async",) in direct_runtime.events
    assert ("subscribers.flush",) not in direct_runtime.events



@pytest.mark.asyncio
async def test_async_lifecycle_finalize_uses_async_shared_metrics_barrier(
    direct_runtime,
    monkeypatch,
):
    from agent import relay_runtime as core_relay_runtime

    runtime = relay_shared_metrics._get_runtime()
    assert runtime is not None
    runtime.ensure_session({"session_id": "async-lifecycle-finalize"})

    async def finalize_core(**_kwargs):
        return None

    monkeypatch.setattr(
        core_relay_runtime.SESSION_COORDINATOR,
        "finalize_conversation_async",
        finalize_core,
    )
    direct_runtime.events.clear()

    await lifecycle.finalize_session_async(
        session_id="async-lifecycle-finalize",
        platform="gateway",
    )

    assert runtime._sessions.get("async-lifecycle-finalize") is None
    assert ("subscribers.flush_async",) in direct_runtime.events
    assert ("subscribers.flush",) not in direct_runtime.events



def test_task_flush_worker_stops_when_runtime_deactivates(direct_runtime):
    runtime = relay_shared_metrics._get_runtime()
    assert runtime is not None
    event = {
        "session_id": "task-flush-worker-stop",
        "task_id": "task",
        "platform": "gateway",
    }
    assert runtime.start_task(event) is not None

    runtime.finish_task({**event, "completed": True})
    assert runtime._wait_for_task_flush(runtime._task_flush_requested)
    worker = runtime._task_flush_worker
    assert worker is not None and worker.is_alive()

    runtime.deactivate()

    assert not worker.is_alive()



def test_shutdown_does_not_overlap_a_stuck_task_flush(
    direct_runtime,
    monkeypatch,
):
    runtime = relay_shared_metrics._get_runtime()
    assert runtime is not None
    event = {
        "session_id": "task-flush-shutdown",
        "task_id": "task",
        "platform": "gateway",
    }
    assert runtime.start_task(event) is not None
    entered = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def blocking_flush() -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        entered.set()
        try:
            assert release.wait(5)
        finally:
            with lock:
                active -= 1

    direct_runtime.subscribers.flush = blocking_flush
    monkeypatch.setattr(
        runtime,
        "_stop_task_flush_worker",
        lambda timeout=5.0: type(runtime)._stop_task_flush_worker(runtime, timeout=0.05),
    )
    runtime.finish_task({**event, "completed": True})
    assert entered.wait(1)

    shutdown = threading.Thread(target=runtime.shutdown)
    shutdown.start()
    shutdown.join(0.5)

    assert not shutdown.is_alive()
    assert max_active == 1
    assert not any(event[0] == "subscribers.deregister" for event in direct_runtime.events)
    release.set()
    worker = runtime._task_flush_worker
    assert worker is not None
    worker.join(5)
    assert not worker.is_alive()
    assert max_active == 1



def test_deactivate_does_not_deregister_during_a_stuck_task_flush(
    direct_runtime,
    monkeypatch,
):
    runtime = relay_shared_metrics._get_runtime()
    assert runtime is not None
    event = {
        "session_id": "task-flush-deactivate",
        "task_id": "task",
        "platform": "gateway",
    }
    assert runtime.start_task(event) is not None
    entered = threading.Event()
    release = threading.Event()

    def blocking_flush() -> None:
        entered.set()
        assert release.wait(5)

    direct_runtime.subscribers.flush = blocking_flush
    monkeypatch.setattr(
        runtime,
        "_stop_task_flush_worker",
        lambda timeout=5.0: type(runtime)._stop_task_flush_worker(runtime, timeout=0.05),
    )
    runtime.finish_task({**event, "completed": True})
    assert entered.wait(1)

    runtime.deactivate()

    assert not any(event[0] == "subscribers.deregister" for event in direct_runtime.events)
    release.set()
    worker = runtime._task_flush_worker
    assert worker is not None
    worker.join(5)
    assert not worker.is_alive()



def test_runtime_replacement_waits_for_stuck_deactivation(
    direct_runtime,
    monkeypatch,
):
    runtime = relay_shared_metrics._get_runtime()
    assert runtime is not None
    profile_key = relay_runtime.current_profile_key()
    replacement_host = object()
    monkeypatch.setattr(runtime, "deactivate", lambda: False)

    replacement = relay_shared_metrics._get_runtime(host=replacement_host)

    assert replacement is None
    assert relay_shared_metrics._RUNTIMES[profile_key] is runtime
