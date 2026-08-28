from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gateway.run import GatewayRunner
from tests.gateway.restart_test_helpers import make_restart_runner


class _ActiveAgent:
    def __init__(self, session_id: str = "session-active") -> None:
        self.session_id = session_id

    def interrupt(self, _reason: str) -> None:
        return None

    def set_final_messages(self, _messages) -> None:
        return None


@pytest.fixture
def lifecycle_runner(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("INVOCATION_ID", "lifecycle-invocation")
    monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
    runner, _ = make_restart_runner()
    runner.adapters = {}
    runner.scheduler = None
    runner._notify_restart_state = lambda _state: None
    runner._shutdown_owned_tool_subprocesses = lambda: asyncio.sleep(0)
    runner._reconcile_running_agents = lambda **_kwargs: {}
    runner._session_db = None
    runner._db = None
    return runner


def _phase_rows(home: Path) -> list[dict]:
    paths = list((home / "state" / "gateway-drain-attribution").glob("*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_gateway_stop_records_clean_exit_only_after_zero_work(
    tmp_path: Path, lifecycle_runner, monkeypatch
):
    runner = lifecycle_runner
    monkeypatch.setattr(
        runner,
        "_drain_active_agents",
        lambda _timeout, **_kwargs: asyncio.sleep(0, result=({}, False)),
    )

    await GatewayRunner.stop(runner)

    assert [row["phase"] for row in _phase_rows(tmp_path)] == ["clean_exit"]
    assert (tmp_path / ".clean_shutdown").exists()


@pytest.mark.asyncio
async def test_gateway_stop_records_interrupt_forced_forward_and_incomplete(
    tmp_path: Path, lifecycle_runner, monkeypatch
):
    runner = lifecycle_runner
    agent = _ActiveAgent()
    runner._running_agents = {"agent:main:test:blocked": agent}
    runner._running_agents_ts = {"agent:main:test:blocked": 3.0}
    runner._session_run_generation = {"agent:main:test:blocked": 2}
    monkeypatch.setattr(
        runner,
        "_drain_active_agents",
        lambda _timeout, **_kwargs: asyncio.sleep(0, result=({}, True)),
    )

    await GatewayRunner.stop(runner)

    rows = _phase_rows(tmp_path)
    phases = [row["phase"] for row in rows]
    assert phases == [
        "interrupt_start",
        "interrupt_settled",
        "forced_forward",
        "cleanup_incomplete",
    ]
    forced = rows[phases.index("forced_forward")]
    assert forced["counts"]["agent"] == 1
    assert forced["units"][0]["session_id"] == "session-active"
    assert not (tmp_path / ".clean_shutdown").exists()


@pytest.mark.asyncio
async def test_gateway_stop_never_attempts_clean_exit_after_budget_exhaustion(
    lifecycle_runner, monkeypatch
):
    runner = lifecycle_runner
    monkeypatch.setattr(
        runner,
        "_drain_active_agents",
        lambda _timeout, **_kwargs: asyncio.sleep(0, result=({}, False)),
    )
    runner._SHUTDOWN_TAIL_RESERVE_S = 999.0
    phases = []

    async def _record(phase: str, *, deadline: float):
        phases.append(phase)
        from gateway.drain_attribution import DrainAttributionWriteResult

        return DrainAttributionWriteResult(
            status="attribution_incomplete",
            error="shutdown_deadline_exhausted",
        )

    monkeypatch.setattr(runner, "_record_drain_attribution", _record)

    await GatewayRunner.stop(runner)

    assert "clean_exit" not in phases
    assert phases[-1] == "cleanup_incomplete"


@pytest.mark.asyncio
async def test_attribution_write_timeout_preserves_shutdown_tail(
    lifecycle_runner, monkeypatch
):
    from gateway.drain_attribution import DrainAttributionWriteResult

    observed_timeouts = []

    async def _capture_timeout(_recorder, *, timeout_seconds: float, **_kwargs):
        observed_timeouts.append(timeout_seconds)
        return DrainAttributionWriteResult(status="persisted")

    monkeypatch.setattr(
        "gateway.drain_attribution.record_snapshot_bounded",
        _capture_timeout,
    )
    loop = asyncio.get_running_loop()

    await GatewayRunner._record_drain_attribution(
        lifecycle_runner,
        "interrupt_start",
        deadline=loop.time() + 10.0,
    )

    assert observed_timeouts == [GatewayRunner._DRAIN_ATTRIBUTION_WRITE_TIMEOUT_S]
    assert observed_timeouts[0] <= 0.05


@pytest.mark.asyncio
async def test_gateway_stop_propagates_attribution_cancellation(
    lifecycle_runner, monkeypatch
):
    runner = lifecycle_runner
    monkeypatch.setattr(
        runner,
        "_drain_active_agents",
        lambda _timeout, **_kwargs: asyncio.sleep(0, result=({}, False)),
    )

    async def _cancelled_record(_phase: str, *, deadline: float):
        del deadline
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "_record_drain_attribution", _cancelled_record)

    with pytest.raises(asyncio.CancelledError):
        await GatewayRunner.stop(runner)


@pytest.mark.asyncio
async def test_shutdown_watchdog_snapshot_omits_work_identifiers(
    lifecycle_runner, monkeypatch
):
    from gateway.drain_attribution import DrainAttributionWriteResult

    runner = lifecycle_runner
    monkeypatch.setattr(
        runner,
        "_drain_active_agents",
        lambda _timeout, **_kwargs: asyncio.sleep(0, result=({}, False)),
    )
    detailed_work = {
        "counts": {"agent": 1, "cron": 1, "api": 1, "total": 3},
        "units": [
            {"kind": "agent", "session_id": "private-session"},
            {"kind": "cron", "job_id": "private-job"},
            {"kind": "api", "request_id": "private-request"},
        ],
        "attribution_complete": True,
        "omissions": [],
    }
    monkeypatch.setattr(
        runner,
        "_shutdown_work_attribution_snapshot",
        lambda: detailed_work,
    )

    async def _record(_phase: str, *, deadline: float):
        del deadline
        return DrainAttributionWriteResult(status="persisted")

    monkeypatch.setattr(runner, "_record_drain_attribution", _record)
    captured = {}

    def _capture_watchdog(_delay, *, snapshot_fn, **_kwargs):
        captured["snapshot_fn"] = snapshot_fn

    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setattr("gateway.run.arm_shutdown_watchdog", _capture_watchdog)

    await GatewayRunner.stop(runner)

    watchdog_work = captured["snapshot_fn"]()["work_attribution"]
    assert watchdog_work == {
        "counts": detailed_work["counts"],
        "attribution_complete": True,
        "omissions": [],
    }
