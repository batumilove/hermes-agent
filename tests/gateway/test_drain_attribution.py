from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from gateway.drain_attribution import (
    DrainAttributionRecorder,
    DrainAttributionWriteResult,
    DrainGeneration,
    collect_gateway_work,
    current_drain_generation,
    generation_is_current,
    record_snapshot_bounded,
)
from gateway.run import GatewayRunner
from tests.gateway.restart_test_helpers import make_restart_runner


def test_record_persists_generation_bound_exact_units_with_fsync_digest(tmp_path: Path):
    generation = DrainGeneration(
        pid=4242,
        process_start_ticks="987654",
        invocation_id="invocation-abc",
        instantiation_epoch="boot:pid1",
    )
    recorder = DrainAttributionRecorder(
        home=tmp_path,
        generation=generation,
        owner_probe=lambda expected: expected == generation,
    )

    result = recorder.record(
        phase="deadline_expired",
        counts={"agent": 1, "cron": 1, "api": 0},
        units=[
            {
                "unit_id": "agent:session-key",
                "category": "agent",
                "session_key": "agent:main:telegram:dm:1",
                "session_id": "session-1",
                "phase": "active",
            },
            {
                "unit_id": "cron:job-1:execution-1",
                "category": "cron",
                "job_id": "job-1",
                "execution_id": "execution-1",
                "phase": "running",
            },
        ],
    )

    assert result.status == "persisted"
    assert result.sequence == 1
    assert result.path is not None
    assert result.path.parent == tmp_path / "state" / "gateway-drain-attribution"
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600

    lines = result.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == 1
    assert payload["generation"] == {
        "pid": 4242,
        "process_start_ticks": "987654",
        "invocation_id": "invocation-abc",
        "instantiation_epoch": "boot:pid1",
    }
    assert payload["sequence"] == 1
    assert payload["phase"] == "deadline_expired"
    assert payload["counts"] == {"agent": 1, "cron": 1, "api": 0, "total": 2}
    assert [unit["unit_id"] for unit in payload["units"]] == [
        "agent:session-key",
        "cron:job-1:execution-1",
    ]
    assert payload["attribution_complete"] is True
    assert result.sha256 == payload["record_sha256"]


class _BlockingRecorder:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def record(self, **_kwargs) -> DrainAttributionWriteResult:
        self.entered.set()
        self.release.wait(timeout=5.0)
        return DrainAttributionWriteResult(status="persisted", sequence=1)


@pytest.mark.asyncio
async def test_bounded_record_returns_incomplete_without_waiting_for_stuck_fsync():
    recorder = _BlockingRecorder()
    started = time.monotonic()
    try:
        result = await record_snapshot_bounded(
            recorder,
            timeout_seconds=0.05,
            phase="deadline_expired",
            counts={"agent": 1, "cron": 0, "api": 0},
            units=[{"unit_id": "agent:one", "category": "agent"}],
        )
    finally:
        recorder.release.set()

    assert recorder.entered.is_set()
    assert time.monotonic() - started < 0.5
    assert result.status == "attribution_incomplete"
    assert result.error == "write_timeout"


def test_timed_out_writer_does_not_raise_after_event_loop_closes(monkeypatch):
    recorder = _BlockingRecorder()
    thread_errors = []
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: thread_errors.append(args.exc_value),
    )
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            record_snapshot_bounded(
                recorder,
                timeout_seconds=0.01,
                phase="pre_timeout",
                counts={"agent": 1, "cron": 0, "api": 0},
                units=[{"unit_id": "agent:one", "category": "agent"}],
            )
        )
    finally:
        loop.close()
    assert result.error == "write_timeout"

    recorder.release.set()
    writers = [
        thread
        for thread in threading.enumerate()
        if thread.name == "gateway-drain-attribution-write"
    ]
    for writer in writers:
        writer.join(timeout=1.0)
    assert thread_errors == []


def test_new_recorder_continues_sequence_for_same_generation(tmp_path: Path):
    generation = DrainGeneration(7, "ticks", "inv", "epoch")
    first = DrainAttributionRecorder(
        home=tmp_path,
        generation=generation,
        owner_probe=lambda _expected: True,
    )
    second = DrainAttributionRecorder(
        home=tmp_path,
        generation=generation,
        owner_probe=lambda _expected: True,
    )

    assert first.record(
        phase="drain_start",
        counts={"agent": 0, "cron": 0, "api": 0},
        units=[],
    ).sequence == 1
    assert second.record(
        phase="deadline_expired",
        counts={"agent": 0, "cron": 0, "api": 0},
        units=[],
    ).sequence == 2


def test_unit_cap_preserves_counts_and_marks_attribution_incomplete(tmp_path: Path):
    generation = DrainGeneration(8, "ticks", "inv", "epoch")
    recorder = DrainAttributionRecorder(
        home=tmp_path,
        generation=generation,
        owner_probe=lambda _expected: True,
        max_units=2,
    )

    result = recorder.record(
        phase="deadline_expired",
        counts={"agent": 3, "cron": 0, "api": 0},
        units=[
            {"unit_id": "agent:1", "category": "agent"},
            {"unit_id": "agent:2", "category": "agent"},
            {"unit_id": "agent:3", "category": "agent"},
        ],
    )

    payload = json.loads(result.path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["counts"]["total"] == 3
    assert len(payload["units"]) == 2
    assert payload["attribution_complete"] is False
    assert payload["omissions"] == ["units_truncated:1"]


class _Agent:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _ApiAdapter:
    def active_agent_work_snapshot(self):
        return {
            "count": 2,
            "units": [
                {"unit_id": "api:run:r-1", "category": "api", "run_id": "r-1"},
                {"unit_id": "api:request:q-1", "category": "api", "request_id": "q-1"},
            ],
            "attribution_complete": True,
            "omissions": [],
        }


class _Runner:
    def __init__(self, pending) -> None:
        self._running_agents = {
            "agent:main:telegram:dm:1": _Agent("session-1"),
            "agent:main:telegram:dm:2": pending,
        }
        self._running_agents_ts = {
            "agent:main:telegram:dm:1": 10.5,
            "agent:main:telegram:dm:2": 11.5,
        }
        self._session_run_generation = {
            "agent:main:telegram:dm:1": 3,
            "agent:main:telegram:dm:2": 4,
        }
        self.adapters = {"api_server": _ApiAdapter()}


def test_collect_gateway_work_reconciles_each_counted_unit_once():
    pending = object()
    runner = _Runner(pending)

    snapshot = collect_gateway_work(
        runner,
        pending_sentinel=pending,
        cron_provider=lambda: [
            {
                "job_id": "job-1",
                "execution_id": "execution-1",
                "phase": "running",
            }
        ],
    )

    assert snapshot.counts == {"agent": 2, "cron": 1, "api": 2, "total": 5}
    assert len(snapshot.units) == 5
    assert len({unit["unit_id"] for unit in snapshot.units}) == 5
    assert snapshot.attribution_complete is True
    assert snapshot.omissions == ()
    active = next(unit for unit in snapshot.units if unit["unit_id"].endswith(":1"))
    assert active["session_id"] == "session-1"
    assert active["run_generation"] == 3
    assert active["started_monotonic"] == 10.5
    assert active["work_class"] == "user_turn"
    assert active["drain_blocking"] is True
    pending_unit = next(unit for unit in snapshot.units if unit["unit_id"].endswith(":2"))
    assert pending_unit["phase"] == "pending_agent_creation"
    assert pending_unit["work_class"] == "user_turn_admission"
    cron_unit = next(unit for unit in snapshot.units if unit["category"] == "cron")
    assert cron_unit["work_class"] == "scheduled_job"
    assert cron_unit["drain_blocking"] is True


def test_generation_owner_probe_rejects_successor_lifecycle_claim(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "inv-current")
    generation = current_drain_generation()
    assert generation.pid == os.getpid()
    assert generation.process_start_ticks
    assert generation.invocation_id == "inv-current"

    lifecycle = tmp_path / "state" / "gateway.lifecycle.json"
    lifecycle.parent.mkdir(parents=True)
    lifecycle.write_text(
        json.dumps({"phase": "running", "pid": generation.pid}),
        encoding="utf-8",
    )
    assert generation_is_current(generation, home=tmp_path) is True

    lifecycle.write_text(
        json.dumps({"phase": "running", "pid": generation.pid + 1}),
        encoding="utf-8",
    )
    assert generation_is_current(generation, home=tmp_path) is False


def test_generation_owner_probe_rejects_changed_instantiation_epoch(
    tmp_path: Path, monkeypatch
):
    from gateway import drain_control

    monkeypatch.setattr(drain_control, "current_instantiation_epoch", lambda: "epoch-a")
    generation = current_drain_generation()
    assert generation.instantiation_epoch == "epoch-a"

    monkeypatch.setattr(drain_control, "current_instantiation_epoch", lambda: "epoch-b")
    assert generation_is_current(generation, home=tmp_path) is False


@pytest.mark.asyncio
async def test_runner_records_pre_timeout_snapshot_outside_session_databases(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("INVOCATION_ID", "runner-invocation")
    runner, _adapter = make_restart_runner()
    runner._running_agents = {
        "agent:main:telegram:dm:1": _Agent("session-runner-1")
    }
    runner._running_agents_ts = {"agent:main:telegram:dm:1": 12.5}
    runner._session_run_generation = {"agent:main:telegram:dm:1": 9}

    result = await GatewayRunner._record_drain_attribution(
        runner,
        "drain_start",
        deadline=asyncio.get_running_loop().time() + 1.0,
    )

    assert result.status == "persisted"
    assert result.path is not None
    payload = json.loads(result.path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["phase"] == "drain_start"
    assert payload["generation"]["invocation_id"] == "runner-invocation"
    assert payload["counts"] == {"agent": 1, "cron": 0, "api": 0, "total": 1}
    assert not (tmp_path / "state.db").exists()
    assert not (tmp_path / "lcm.db").exists()


@pytest.mark.asyncio
async def test_drain_wait_records_start_and_pre_timeout_exact_occupancy(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("INVOCATION_ID", "drain-wait-invocation")
    runner, _adapter = make_restart_runner()
    runner._running_agents = {
        "agent:main:telegram:dm:blocked": _Agent("session-blocked")
    }
    runner._running_agents_ts = {"agent:main:telegram:dm:blocked": 21.5}
    runner._session_run_generation = {"agent:main:telegram:dm:blocked": 11}
    runner._DRAIN_ATTRIBUTION_INTERVAL_S = 0.4

    _snapshot, timed_out = await GatewayRunner._drain_active_agents(runner, 1.5)

    assert timed_out is True
    evidence = list(
        (tmp_path / "state" / "gateway-drain-attribution").glob("*.jsonl")
    )
    assert len(evidence) == 1
    rows = [
        json.loads(line)
        for line in evidence[0].read_text(encoding="utf-8").splitlines()
    ]
    phases = [row["phase"] for row in rows]
    assert phases[0] == "drain_start"
    assert "drain_interval" in phases[1:-1]
    assert phases[-1] == "pre_timeout"
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert all(row["counts"]["total"] == 1 for row in rows)
    assert rows[-1]["units"] == [
        {
            "unit_id": "agent:agent:main:telegram:dm:blocked",
            "category": "agent",
            "session_key": "agent:main:telegram:dm:blocked",
            "session_id": "session-blocked",
            "phase": "active",
            "started_monotonic": 21.5,
            "run_generation": 11,
            "work_class": "user_turn",
            "drain_blocking": True,
        }
    ]
