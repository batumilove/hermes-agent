"""Protected RED contracts for the 2026-08-15 drain/termination incident.

These contracts intentionally cover the ownership and supervisor boundaries that
were absent from the failed generation.  They do not prescribe the internal
registry or watchdog implementation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.platforms.base import MessageEvent, MessageType
from gateway.wake import deliver_wake
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


def test_runner_binds_loop_freeze_backstop_to_supervisor_deadline():
    """The lifetime freeze backstop must be configured before systemd SIGKILL."""

    runner, _adapter = make_restart_runner()
    runner.config.loop_watchdog = True
    runner._loop_floor_timer_handle = None
    runner._loop_liveness_watchdog = None
    runner._systemd_timeout_stop_s = 90.0
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    watchdog = MagicMock()
    watchdog.is_alive.return_value = True

    with (
        patch("gateway.run._arm_loop_floor_timer", return_value=MagicMock()),
        patch(
            "gateway.run.start_loop_liveness_watchdog", return_value=watchdog
        ) as start_watchdog,
    ):
        runner._start_loop_liveness_guards(loop)

    start_watchdog.assert_called_once()
    assert start_watchdog.call_args.args == (loop,)
    hard_deadline_s = start_watchdog.call_args.kwargs.get("hard_deadline_s")
    assert hard_deadline_s is not None
    assert 0.0 < hard_deadline_s <= 85.0


def test_active_work_count_includes_detached_delegations(monkeypatch):
    """The maintenance-drain count must own detached/background agent work."""

    runner, _adapter = make_restart_runner()
    monkeypatch.setattr("tools.async_delegation.active_count", lambda: 2)

    assert runner._active_work_count() == 2


@pytest.mark.asyncio
async def test_drain_waits_for_detached_delegation_to_settle(monkeypatch):
    """Quiescence is not reached while detached work remains live."""

    runner, _adapter = make_restart_runner()
    live = {"count": 1}
    monkeypatch.setattr(
        "tools.async_delegation.active_count", lambda: live["count"]
    )

    drain_task = asyncio.create_task(runner._drain_active_agents(0.5))
    await asyncio.sleep(0.03)
    assert not drain_task.done(), "drain returned while detached work was still live"

    live["count"] = 0
    _snapshot, timed_out = await drain_task

    assert timed_out is False


@pytest.mark.asyncio
async def test_drain_times_out_when_only_detached_delegation_remains(monkeypatch):
    """Detached work alone keeps the aggregate drain non-quiescent."""

    runner, _adapter = make_restart_runner()
    monkeypatch.setattr("tools.async_delegation.active_count", lambda: 1)

    _snapshot, timed_out = await runner._drain_active_agents(0.02)

    assert timed_out is True


@pytest.mark.asyncio
async def test_async_completion_wake_is_marked_as_internal_new_work():
    """The producer identifies completion wakes that re-enter the agent loop."""

    captured = []

    class PushAdapter:
        supports_async_delivery = True

        async def handle_message(self, event):
            captured.append(event)

    await deliver_wake(
        PushAdapter(),
        text="[background completion] continue the agent turn",
        source=make_restart_source(),
    )

    assert len(captured) == 1
    assert captured[0].internal is True
    assert captured[0].metadata.get("drain_admission") == "new_agent_turn"


@pytest.mark.asyncio
async def test_external_drain_refuses_marked_internal_new_agent_turn():
    """Internal completion provenance cannot bypass maintenance admission closure."""

    runner, _adapter = make_restart_runner()
    runner._external_drain_active = True
    runner._handle_message_with_agent = AsyncMock(return_value="agent turn started")
    event = MessageEvent(
        text="[background completion] continue the agent turn",
        message_type=MessageType.TEXT,
        source=make_restart_source(),
        message_id="internal-completion",
        internal=True,
        metadata={"drain_admission": "new_agent_turn"},
    )

    result = await runner._handle_message(event)

    assert result is not None
    assert "draining" in result.lower()
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_drain_does_not_broadly_refuse_unmarked_internal_event():
    """Authorization bypass alone is not a maintenance-admission classification."""

    runner, _adapter = make_restart_runner()
    runner._external_drain_active = True
    runner._handle_message_with_agent = AsyncMock(return_value="internal event handled")
    event = MessageEvent(
        text="[internal control event]",
        message_type=MessageType.TEXT,
        source=make_restart_source(),
        message_id="internal-control",
        internal=True,
    )

    result = await runner._handle_message(event)

    assert result == "internal event handled"
    runner._handle_message_with_agent.assert_awaited_once()
