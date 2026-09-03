import asyncio
import subprocess
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gateway.run as gateway_run
from gateway.config import HomeChannel, Platform
from gateway.platforms.base import MessageEvent
from gateway.restart import GATEWAY_SERVICE_RESTART_EXIT_CODE
from gateway.session import build_session_key
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


@pytest.mark.asyncio
async def test_cancel_background_tasks_cancels_inflight_message_processing():
    _runner, adapter = make_restart_runner()
    release = asyncio.Event()

    async def block_forever(_event):
        await release.wait()
        return None

    adapter.set_message_handler(block_forever)
    event = MessageEvent(text="work", source=make_restart_source(), message_id="1")

    await adapter.handle_message(event)
    await asyncio.sleep(0)

    session_key = build_session_key(event.source)
    assert session_key in adapter._active_sessions
    assert adapter._background_tasks

    await adapter.cancel_background_tasks()

    assert adapter._background_tasks == set()
    assert adapter._active_sessions == {}
    assert adapter._pending_messages == {}


def test_cleanup_agent_resources_reaps_stale_aux_clients():
    runner, _adapter = make_restart_runner()
    agent = MagicMock()

    with patch("agent.auxiliary_client.cleanup_stale_async_clients") as cleanup_mock:
        runner._cleanup_agent_resources(agent)

    agent.shutdown_memory_provider.assert_called_once()
    agent.close.assert_called_once()
    cleanup_mock.assert_called_once()


def test_cron_provider_stop_cannot_override_gateway_exit_code(caplog):
    provider = MagicMock()
    provider.stop.side_effect = SystemExit(GATEWAY_SERVICE_RESTART_EXIT_CODE)

    gateway_run._stop_cron_provider(provider)

    provider.stop.assert_called_once_with()
    assert f"attempted to exit the gateway with code {GATEWAY_SERVICE_RESTART_EXIT_CODE}; ignoring" in caplog.text


@pytest.mark.asyncio
async def test_gateway_stop_interrupts_running_agents_and_cancels_adapter_tasks():
    runner, adapter = make_restart_runner()
    runner._pending_messages = {"session": "pending text"}
    runner._pending_approvals = {"session": {"command": "rm -rf /tmp/x"}}
    runner._restart_drain_timeout = 0.0

    release = asyncio.Event()

    async def block_forever(_event):
        await release.wait()
        return None

    adapter.set_message_handler(block_forever)
    event = MessageEvent(text="work", source=make_restart_source(), message_id="1")
    await adapter.handle_message(event)
    await asyncio.sleep(0)

    disconnect_mock = AsyncMock()
    adapter.disconnect = disconnect_mock

    session_key = build_session_key(event.source)
    running_agent = MagicMock()
    runner._running_agents = {session_key: running_agent}
    # Simulate the agent exiting once interrupted so stop()'s 5s
    # interrupt-deadline poll loop returns immediately.
    running_agent.interrupt.side_effect = lambda *a, **k: runner._running_agents.clear()

    with (
        patch("gateway.status.remove_pid_file"),
        patch("gateway.status.write_runtime_status"),
        patch("agent.auxiliary_client.shutdown_cached_clients") as shutdown_cached_clients,
    ):
        await runner.stop()

    running_agent.interrupt.assert_called_once_with("Gateway shutting down")
    disconnect_mock.assert_awaited_once()
    shutdown_cached_clients.assert_called_once()
    assert runner.adapters == {}
    assert runner._running_agents == {}
    assert runner._pending_messages == {}
    assert runner._pending_approvals == {}
    assert runner._shutdown_event.is_set() is True


@pytest.mark.asyncio
async def test_gateway_stop_settles_completion_batch_before_adapter_disconnect():
    runner, adapter = make_restart_runner()
    runner._completion_notification_batch_window = 3600
    event = {
        "session_id": "shutdown-batch",
        "started_at": 1.0,
        "session_key": "telegram:dm:123456:u1",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123456",
        "user_id": "u1",
        "exit_code": 0,
        "output": "done",
    }
    call_order: list[str] = []
    original_cancel = runner._cancel_process_completion_batch_tasks

    async def _tracked_cancel():
        call_order.append("batch_cancel_start")
        await original_cancel()
        call_order.append("batch_cancel_done")

    async def _disconnect():
        call_order.append("disconnect")

    runner._cancel_process_completion_batch_tasks = _tracked_cancel
    adapter.disconnect = _disconnect
    pending = asyncio.create_task(
        runner._enqueue_process_completion_notification("completion", event)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert runner._completion_notification_batch_flush_tasks

    with patch("gateway.status.remove_pid_file"), patch("gateway.status.write_runtime_status"):
        await runner.stop()

    assert await asyncio.wait_for(pending, timeout=1.0) is False
    assert call_order == ["batch_cancel_start", "batch_cancel_done", "disconnect"]
    assert runner._completion_notification_batch_flush_tasks == set()


@pytest.mark.asyncio
async def test_planned_service_exit_issues_no_restart_of_its_own(monkeypatch):
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    runner._restart_requested = True
    runner._restart_via_service = True
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail(
            f"planned service exit must not spawn a restart helper: {args}"
        ),
    )

    with patch("gateway.status.remove_pid_file"), patch("gateway.status.write_runtime_status"):
        await runner.stop()

    assert runner._exit_code == GATEWAY_SERVICE_RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_gateway_stop_bounds_atomic_resume_marker_batch():
    """A wedged batch write must not bypass the aggregate shutdown deadline."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.05
    runner._finalize_shutdown_agents = AsyncMock()
    adapter.disconnect = AsyncMock()

    running_agent = MagicMock()
    runner._running_agents = {"session": running_agent}
    running_agent.interrupt.side_effect = lambda *_args: runner._running_agents.clear()

    marker_started = asyncio.Event()
    release_marker = asyncio.Event()

    async def stuck_mark_resume_pending_batch(*_args, **_kwargs):
        marker_started.set()
        await release_marker.wait()
        return ["session"]

    async_store = MagicMock()
    async_store._store = runner.session_store
    async_store.mark_resume_pending_batch = stuck_mark_resume_pending_batch
    runner._async_session_store = async_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        stop_task = asyncio.create_task(
            runner.stop(restart=True, service_restart=True)
        )
        await asyncio.wait_for(marker_started.wait(), timeout=1.0)
        await asyncio.wait_for(stop_task, timeout=2.0)

    running_agent.interrupt.assert_called_once_with("Gateway restarting")
    adapter.disconnect.assert_awaited_once()
    assert runner._shutdown_event.is_set() is True

    marker_tasks = list(getattr(runner, "_shutdown_resume_marker_tasks", set()))
    release_marker.set()
    if marker_tasks:
        await asyncio.gather(*marker_tasks)


@pytest.mark.asyncio
async def test_gateway_stop_persists_all_resume_markers_in_one_batch():
    """Many interrupted turns must not degrade into per-session point writes."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.05
    runner._finalize_shutdown_agents = AsyncMock()
    adapter.disconnect = AsyncMock()

    session_keys = [f"session-{index:02d}" for index in range(14)]
    running_agent = MagicMock()
    runner._running_agents = {key: running_agent for key in session_keys}
    running_agent.interrupt.side_effect = lambda *_args: runner._running_agents.clear()

    async_store = MagicMock()
    async_store._store = runner.session_store
    async_store.mark_resume_pending_batch = AsyncMock(return_value=session_keys)
    async_store.mark_resume_pending = AsyncMock()
    runner._async_session_store = async_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await asyncio.wait_for(
            runner.stop(restart=True, service_restart=True),
            timeout=2.0,
        )

    assert async_store.mark_resume_pending_batch.await_count == 2
    for call in async_store.mark_resume_pending_batch.await_args_list:
        assert call.args == (session_keys, "restart_timeout")
    async_store.mark_resume_pending.assert_not_awaited()
    running_agent.interrupt.assert_called_with("Gateway restarting")
    adapter.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_stop_clears_late_atomic_marker_after_graceful_drain():
    """A timed-out batch that commits late must be cleared after clean drain."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.05
    runner._finalize_shutdown_agents = AsyncMock()
    adapter.disconnect = AsyncMock()

    running_agent = MagicMock()
    runner._running_agents = {"session": running_agent}

    marker_started = asyncio.Event()
    release_marker = asyncio.Event()
    marker_written = asyncio.Event()
    resume_pending: set[str] = set()

    async def mark_resume_pending_batch(session_keys, _reason):
        marker_started.set()
        await release_marker.wait()
        resume_pending.update(session_keys)
        marker_written.set()
        return list(session_keys)

    async def clear_resume_pending(session_key):
        await marker_written.wait()
        resume_pending.discard(session_key)

    async_store = MagicMock()
    async_store._store = runner.session_store
    async_store.mark_resume_pending_batch = mark_resume_pending_batch
    async_store.clear_resume_pending = clear_resume_pending
    runner._async_session_store = async_store

    async def finish_gracefully_after_marker_timeout():
        await marker_started.wait()
        await asyncio.sleep(0.06)
        release_marker.set()
        runner._running_agents.clear()

    finisher = asyncio.create_task(finish_gracefully_after_marker_timeout())
    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await asyncio.wait_for(
            runner.stop(restart=True, service_restart=True),
            timeout=2.0,
        )
    await finisher

    assert resume_pending == set()
    running_agent.interrupt.assert_not_called()
    adapter.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_stop_logs_exact_resume_marker_batch_counts(caplog):
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.05
    runner._finalize_shutdown_agents = AsyncMock()
    adapter.disconnect = AsyncMock()

    session_keys = ["session-a", "session-b", "session-c"]
    running_agent = MagicMock()
    runner._running_agents = {key: running_agent for key in session_keys}
    running_agent.interrupt.side_effect = lambda *_args: runner._running_agents.clear()

    async_store = MagicMock()
    async_store._store = runner.session_store
    async_store.mark_resume_pending_batch = AsyncMock(return_value=session_keys[:2])
    runner._async_session_store = async_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ), caplog.at_level("INFO", logger="gateway.run"):
        await asyncio.wait_for(
            runner.stop(restart=True, service_restart=True),
            timeout=2.0,
        )

    assert "candidates=3 persisted=2 missing=1" in caplog.text
    assert "omitted session keys: session-c" in caplog.text


@pytest.mark.asyncio
async def test_gateway_stop_scopes_counts_when_reusing_broader_batch(caplog):
    """A post-timeout subset must not count keys from its reused pre-drain batch."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.05
    runner._finalize_shutdown_agents = AsyncMock()
    adapter.disconnect = AsyncMock()

    session_keys = ["session-a", "session-b", "session-c"]
    running_agent = MagicMock()
    runner._running_agents = {key: running_agent for key in session_keys}
    running_agent.interrupt.side_effect = lambda *_args: runner._running_agents.clear()

    release_marker = asyncio.Event()

    async def delayed_batch(keys, _reason):
        await release_marker.wait()
        return list(keys)

    async def timed_out_drain(*_args, **_kwargs):
        runner._running_agents.pop("session-c")
        asyncio.get_running_loop().call_later(0.01, release_marker.set)
        return list(session_keys), True

    async_store = MagicMock()
    async_store._store = runner.session_store
    async_store.mark_resume_pending_batch = AsyncMock(side_effect=delayed_batch)
    runner._async_session_store = async_store
    runner._drain_active_agents = AsyncMock(side_effect=timed_out_drain)

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ), caplog.at_level("INFO", logger="gateway.run"):
        await asyncio.wait_for(
            runner.stop(restart=True, service_restart=True),
            timeout=2.0,
        )

    assert async_store.mark_resume_pending_batch.await_count == 1
    assert "candidates=2 persisted=2 missing=0" in caplog.text


@pytest.mark.asyncio
async def test_in_chat_restart_skips_home_shutdown_even_with_active_session():
    runner, adapter = make_restart_runner()
    source = make_restart_source(thread_id="42")
    session_key = build_session_key(source)
    runner._running_agents = {session_key: MagicMock()}
    runner._cache_session_source(session_key, source)
    restart_source = make_restart_source(thread_id="42")
    restart_source.message_id = "restart-command"
    runner._restart_requested = True
    runner._restart_command_source = restart_source
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-chat",
        name="Telegram Home",
    )

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent_calls) == 1
    chat_id, message, metadata = adapter.sent_calls[0]
    assert chat_id == source.chat_id
    assert "Gateway restarting" in message
    assert metadata["telegram_reply_to_message_id"] == "restart-command"


@pytest.mark.asyncio
async def test_gateway_stop_kills_tool_subprocesses_before_adapter_disconnect_on_timeout(monkeypatch):
    """On drain timeout, tool subprocesses must be killed BEFORE adapter
    disconnect so systemd's TimeoutStopSec doesn't SIGKILL the cgroup with
    bash/sleep children still attached (#8202)."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.01  # force timeout path

    call_order: list[str] = []

    def _fake_kill_all(task_id=None):
        call_order.append("kill_all")
        return 2

    def _fake_cleanup_envs():
        call_order.append("cleanup_environments")

    def _fake_cleanup_browsers():
        call_order.append("cleanup_browsers")

    async def _disconnect():
        call_order.append("disconnect")

    # Patch the module-level names the stop() helper imports lazily.
    import tools.process_registry as _pr
    import tools.terminal_tool as _tt
    import tools.browser_tool as _bt
    monkeypatch.setattr(_pr.process_registry, "kill_all", _fake_kill_all)
    monkeypatch.setattr(_tt, "cleanup_all_environments", _fake_cleanup_envs)
    monkeypatch.setattr(_bt, "cleanup_all_browsers", _fake_cleanup_browsers)

    adapter.disconnect = _disconnect

    runner._running_agents = {"session": MagicMock()}
    runner._running_agents["session"].interrupt.side_effect = (
        lambda *a, **k: runner._running_agents.clear()
    )

    with patch("gateway.status.remove_pid_file"), patch("gateway.status.write_runtime_status"):
        await runner.stop()

    # First kill_all must precede the first disconnect.  (Both the eager
    # post-interrupt cleanup and the final catch-all call _kill_tool_
    # subprocesses, so we expect kill_all to appear twice total.)
    assert "kill_all" in call_order
    assert "disconnect" in call_order
    first_kill = call_order.index("kill_all")
    first_disconnect = call_order.index("disconnect")
    assert first_kill < first_disconnect, (
        f"Tool subprocesses must be killed before adapter disconnect on "
        f"drain timeout, got order: {call_order}"
    )
    # Defense-in-depth final cleanup still runs.
    assert call_order.count("kill_all") >= 2


@pytest.mark.asyncio
async def test_gateway_stop_bounds_blocking_agent_interrupt_fanout():
    """One blocking agent interrupt must not hold the shutdown event loop."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.0
    runner._shutdown_interrupt_timeout_secs = lambda: 0.05
    runner._finalize_shutdown_agents = AsyncMock()
    adapter.disconnect = AsyncMock()

    interrupt_started = threading.Event()
    event_times: dict[str, float] = {}
    running_agent = MagicMock()

    def block_interrupt(_reason):
        interrupt_started.set()
        event_times["interrupt_started"] = time.monotonic()
        time.sleep(1.0)
        event_times["interrupt_returned"] = time.monotonic()
        runner._running_agents.clear()

    async def event_loop_heartbeat():
        while not interrupt_started.is_set():
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        event_times["heartbeat"] = time.monotonic()

    running_agent.interrupt.side_effect = block_interrupt
    runner._running_agents = {"blocked-session": running_agent}

    heartbeat_task = asyncio.create_task(event_loop_heartbeat())
    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()
    await heartbeat_task

    assert interrupt_started.is_set()
    assert "heartbeat" in event_times
    assert (
        "interrupt_returned" not in event_times
        or event_times["heartbeat"] < event_times["interrupt_returned"]
    )
    adapter.disconnect.assert_awaited_once()
    assert runner._shutdown_event.is_set() is True


# ---------------------------------------------------------------------------
# gateway_state persistence on shutdown (issue #42675)
#
# On Docker/s6, container_boot.py only auto-starts gateways whose last
# persisted gateway_state was "running". An unexpected external signal
# (the SIGTERM s6/Docker sends on `docker compose up --force-recreate`,
# OOM, bare kill) must NOT persist "stopped" — otherwise the gateway
# stays down after every container restart. An operator-initiated stop
# writes a planned-stop marker first, so it is NOT signal-initiated and
# DOES persist "stopped", respecting the explicit intent.
# ---------------------------------------------------------------------------


def _persisted_states(runner) -> list:
    """All gateway_state values passed to _update_runtime_status, in order."""
    states = []
    for call in runner._update_runtime_status.call_args_list:
        args, kwargs = call
        state = kwargs.get("gateway_state", args[0] if args else None)
        states.append(state)
    return states


def _stopped_state_persisted(runner) -> bool:
    """True iff _update_runtime_status was called with gateway_state='stopped'."""
    return "stopped" in _persisted_states(runner)


@pytest.mark.asyncio
async def test_signal_initiated_shutdown_persists_running_not_stopped(tmp_path, monkeypatch):
    """Unexpected SIGTERM (container restart / OOM / kill) must persist
    gateway_state=running — NOT stopped, and NOT leave the mid-shutdown
    'draining' marker — so container_boot auto-starts on next boot (#42675)."""
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    runner._signal_initiated_shutdown = True  # set by handler on unmarked signal

    with patch("gateway.status.remove_pid_file"), patch("gateway.status.write_runtime_status"):
        await runner.stop()

    assert not _stopped_state_persisted(runner), (
        "signal-initiated shutdown must NOT persist gateway_state=stopped"
    )
    # The FINAL terminal write must be 'running' so container_boot's
    # _AUTOSTART_STATES check passes (it only auto-starts 'running').
    assert _persisted_states(runner)[-1] == "running", (
        f"final state must be 'running', got: {_persisted_states(runner)}"
    )


# ── #42126: zombie PID must be treated as dead in _pid_exists ────────────────
# Under systemd Restart=always, the old gateway becomes a zombie (still in the
# process table, not yet reaped) when the replacement starts. _pid_exists must
# report it dead so --replace proceeds instead of waiting on it and aborting
# with exit 1 (a silent crash loop).


def test_pid_exists_zombie_via_psutil_returns_false(monkeypatch):
    """The live path is psutil. psutil.pid_exists() returns True for a zombie,
    so _pid_exists must additionally check Process.status() == STATUS_ZOMBIE."""
    import sys
    import types

    from gateway import status

    fake_psutil = types.SimpleNamespace()
    fake_psutil.STATUS_ZOMBIE = "zombie"

    class NoSuchProcess(Exception):
        pass

    class PsutilError(Exception):
        pass

    fake_psutil.NoSuchProcess = NoSuchProcess
    fake_psutil.Error = PsutilError

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def status(self):
            return "zombie"

    fake_psutil.Process = _Proc
    # Without the zombie guard, this True would make the caller treat the
    # zombie as a live gateway.
    fake_psutil.pid_exists = lambda pid: True

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert status._pid_exists(4242) is False

