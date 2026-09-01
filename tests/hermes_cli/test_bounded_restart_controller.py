from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes_cli.bounded_restart import (
    BoundedRestartController,
    ControllerBlocked,
    Manifest,
    Occupancy,
    ServiceIdentity,
    SourceIdentity,
    main,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@dataclass
class FakeDrain:
    ops: "FakeOps"
    token: str

    def write_request(self):
        self.ops.events.append("drain:write")
        return {"owner_token": self.token}

    def refresh_request(self):
        self.ops.events.append("drain:refresh")
        return {"owner_token": self.token}

    def clear_request(self):
        self.ops.events.append("drain:clear")
        return True

    def release(self):
        self.ops.events.append("drain:release")


class FakeOps:
    def __init__(self, *, occupancy=None, replacements=None):
        self.wall = NOW
        self.mono = 0.0
        self.events: list[str] = []
        self.old = ServiceIdentity(
            pid=101,
            invocation_id="old-invocation",
            proc_start_ticks="1001",
            active_state="active",
            sub_state="running",
            control_group="/user.slice/hermes.service",
            restart_policy="always",
            kill_mode="control-group",
        )
        self.current_service = self.old
        self.source = SourceIdentity(
            head="a" * 40,
            tree="b" * 40,
            branch="batumi/live-deploy",
            remote_head="a" * 40,
            clean=True,
        )
        self.occupancy = list(occupancy or [Occupancy(active_agents=0)])
        self.replacements = list(replacements or [])
        self.last_occupancy = self.occupancy[-1]

    def wall_now(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def sleep(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)

    def source_identity(self, manifest):
        self.events.append("source")
        return self.source

    def service_identity(self, manifest):
        self.events.append("service")
        if "restart:force-kill" in self.events and self.replacements:
            self.current_service = self.replacements.pop(0)
        return self.current_service

    def acquire_drain(self, manifest):
        self.events.append("drain:acquire")
        return FakeDrain(self, manifest.transaction_id)

    def sample_occupancy(self, manifest):
        self.events.append("occupancy")
        if self.occupancy:
            self.last_occupancy = self.occupancy.pop(0)
        return self.last_occupancy

    def graceful_restart(self, manifest):
        self.events.append("restart:graceful")
        self.current_service = replacement_identity()

    def prepare_interruption(self, manifest, occupancy):
        self.events.append("interruption:prepare")

    def force_kill(self, manifest, old):
        self.events.append("restart:force-kill")
        self.current_service = replace(old, active_state="inactive", sub_state="dead")

    def start(self, manifest):
        self.events.append("restart:start")
        self.current_service = replacement_identity()

    def health_check(self, manifest, service):
        self.events.append("health")
        return service.pid == 202 and service.active_state == "active"


def replacement_identity():
    return ServiceIdentity(
        pid=202,
        invocation_id="new-invocation",
        proc_start_ticks="2002",
        active_state="active",
        sub_state="running",
        control_group="/user.slice/hermes.service",
        restart_policy="always",
        kill_mode="control-group",
    )


def manifest_mapping(tmp_path: Path, controller_path: Path) -> dict:
    digest = hashlib.sha256(controller_path.read_bytes()).hexdigest()
    return {
        "schema": "hermes-bounded-handoff-force-restart/v1",
        "transaction_id": "txn-20260901-a",
        "controller": {"version": 1, "sha256": digest},
        "service": {
            "unit": "hermes-gateway.service",
            "scope": "user",
            "old_pid": 101,
            "old_invocation_id": "old-invocation",
            "old_proc_start_ticks": "1001",
            "old_control_group": "/user.slice/hermes.service",
        },
        "source": {
            "repo": str(tmp_path / "repo"),
            "head": "a" * 40,
            "tree": "b" * 40,
            "branch": "batumi/live-deploy",
            "remote": "myfork",
            "remote_ref": "refs/heads/batumi/live",
            "remote_head": "a" * 40,
            "require_clean": True,
        },
        "policy": {
            "handoff_deadline_seconds": 180,
            "sample_interval_seconds": 60,
            "stable_zero_samples": 2,
            "restart_budget": 1,
            "force_after_deadline": True,
            "replacement_wait_seconds": 5,
        },
        "authorization": {
            "actor": "operator",
            "approved_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            "scope": ["drain", "graceful_restart", "force_restart"],
        },
        "hermes_home": str(tmp_path / "hermes-home"),
        "evidence_root": str(tmp_path / "evidence"),
        "health_url": "http://127.0.0.1:8642/health",
    }


@pytest.fixture
def controller_path():
    import hermes_cli.bounded_restart as module

    return Path(module.__file__).resolve()


def load_manifest(tmp_path, controller_path, mutate=None):
    raw = manifest_mapping(tmp_path, controller_path)
    if mutate:
        mutate(raw)
    return Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)


def test_manifest_requires_exact_three_minute_force_policy(tmp_path, controller_path):
    raw = manifest_mapping(tmp_path, controller_path)
    raw["policy"]["handoff_deadline_seconds"] = 181
    with pytest.raises(ValueError, match="exactly 180"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)

    raw = manifest_mapping(tmp_path, controller_path)
    raw["policy"]["force_after_deadline"] = False
    with pytest.raises(ValueError, match="force_after_deadline"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)


def test_manifest_rejects_expired_or_incomplete_force_authorization(tmp_path, controller_path):
    raw = manifest_mapping(tmp_path, controller_path)
    raw["authorization"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="expired"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)

    raw = manifest_mapping(tmp_path, controller_path)
    raw["authorization"]["scope"].remove("force_restart")
    with pytest.raises(ValueError, match="force_restart"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)


def test_controller_hash_can_be_printed_without_a_manifest(capsys):
    assert main(["--print-controller-sha256"]) == 0
    output = capsys.readouterr().out.strip()
    assert len(output) == 64
    assert all(character in "0123456789abcdef" for character in output)


def test_dry_run_performs_preflight_without_drain_or_restart(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()
    result = BoundedRestartController(manifest, ops).run(execute=False)

    assert result["state"] == "PREFLIGHT_PASS"
    assert "source" in ops.events and "service" in ops.events
    assert not any(event.startswith("drain:") for event in ops.events)
    assert not any(event.startswith("restart:") for event in ops.events)


def test_stable_zero_before_deadline_uses_one_graceful_restart(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps(
        occupancy=[
            Occupancy(active_agents=1, cron_runs=0, cgroup_pids=(101, 111)),
            Occupancy(active_agents=0, cron_runs=0, cgroup_pids=(101,)),
            Occupancy(active_agents=0, cron_runs=0, cgroup_pids=(101,)),
        ]
    )
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["restart_mode"] == "GRACEFUL"
    assert result["handoff_within_180s"] == "PASS"
    assert result["shutdown_cleanliness"] == "CLEAN"
    assert ops.events.count("restart:graceful") == 1
    assert "restart:force-kill" not in ops.events
    assert result["activation_health"] == "PASS"


def test_busy_at_deadline_force_kills_cgroup_once_and_accepts_auto_restart(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cron_runs=1, cgroup_pids=(101, 111, 112))
    ops = FakeOps(
        occupancy=[busy, busy, busy, busy],
        replacements=[replacement_identity()],
    )
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["restart_mode"] == "FORCED"
    assert result["handoff_within_180s"] == "TIMEOUT"
    assert result["shutdown_cleanliness"] == "UNCLEAN"
    assert result["remaining_occupancy"]["active_agents"] == 1
    assert ops.events.count("restart:force-kill") == 1
    assert "restart:start" not in ops.events
    assert result["old_cgroup_gone"] == "PASS"
    assert result["activation_health"] == "PASS"


def test_force_path_starts_unit_once_when_restart_policy_does_not_replace_it(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111))
    inactive = replace(
        FakeOps().old,
        active_state="inactive",
        sub_state="dead",
        restart_policy="no",
    )
    ops = FakeOps(occupancy=[busy] * 4, replacements=[inactive, inactive])
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["restart_mode"] == "FORCED"
    assert ops.events.count("restart:force-kill") == 1
    assert ops.events.count("restart:start") == 1
    assert result["activation_health"] == "PASS"


def test_detached_worker_alone_reaches_deadline_and_is_prepared_for_interruption(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    detached = Occupancy(
        active_agents=0,
        detached_workers=1,
        cgroup_pids=(101, 333),
    )
    ops = FakeOps(occupancy=[detached] * 4, replacements=[replacement_identity()])
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["restart_mode"] == "FORCED"
    assert ops.events.count("interruption:prepare") == 1
    assert ops.events.count("restart:force-kill") == 1
    assert result["shutdown_cleanliness"] == "UNCLEAN"


def test_authorization_expiring_during_handoff_blocks_before_mutation(
    tmp_path, controller_path
):
    manifest = load_manifest(
        tmp_path,
        controller_path,
        lambda raw: raw["authorization"].update(
            expires_at=(NOW + timedelta(seconds=120)).isoformat()
        ),
    )
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111))
    ops = FakeOps(occupancy=[busy] * 4)

    with pytest.raises(ControllerBlocked, match="authorization expired"):
        BoundedRestartController(manifest, ops).run(execute=True)
    assert "restart:graceful" not in ops.events
    assert "restart:force-kill" not in ops.events


def test_preflight_checkpoint_can_resume_without_spending_a_second_budget(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()
    controller = BoundedRestartController(manifest, ops)
    preflight = controller.run(execute=False)
    assert preflight["restart_budget_consumed"] == 0

    result = controller.run(execute=True)
    assert result["restart_mode"] == "GRACEFUL"
    assert ops.events.count("restart:graceful") == 1


def test_source_drift_at_commit_point_blocks_before_restart(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111))
    ops = FakeOps(occupancy=[busy] * 4)
    calls = 0

    def source_identity(_manifest):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ops.source
        return replace(ops.source, head="c" * 40)

    ops.source_identity = source_identity
    with pytest.raises(ControllerBlocked, match="source identity drift"):
        BoundedRestartController(manifest, ops).run(execute=True)

    assert "restart:graceful" not in ops.events
    assert "restart:force-kill" not in ops.events


def test_pid_start_identity_drift_blocks_wrong_generation_kill(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111))
    drifted = replace(FakeOps().old, proc_start_ticks="reused-pid")
    ops = FakeOps(occupancy=[busy] * 4)
    calls = 0

    def service_identity(manifest):
        nonlocal calls
        calls += 1
        ops.events.append("service")
        return ops.old if calls == 1 else drifted

    ops.service_identity = service_identity

    with pytest.raises(ControllerBlocked, match="service identity drift"):
        BoundedRestartController(manifest, ops).run(execute=True)
    assert "restart:force-kill" not in ops.events


def test_restart_budget_checkpoint_prevents_second_restart_after_crash(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    root = Path(manifest.evidence_root)
    root.mkdir(parents=True)
    (root / "controller-result.json").write_text(
        json.dumps(
            {
                "schema": "hermes-bounded-handoff-force-restart-result/v1",
                "transaction_id": manifest.transaction_id,
                "state": "RESTART_COMMITTED",
                "restart_budget_consumed": 1,
                "old_service": FakeOps().old.to_mapping(),
            }
        ),
        encoding="utf-8",
    )
    ops = FakeOps()
    ops.current_service = replacement_identity()
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["state"] == "VERIFIED"
    assert result["recovered_after_restart_commit"] is True
    assert "restart:graceful" not in ops.events
    assert "restart:force-kill" not in ops.events
    assert "restart:start" not in ops.events


def test_forced_result_is_durable_and_separates_unclean_shutdown_from_health(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=2, cron_runs=1, cgroup_pids=(101, 111))
    ops = FakeOps(occupancy=[busy] * 4, replacements=[replacement_identity()])
    result = BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved == result
    assert saved["restart_budget_consumed"] == 1
    assert saved["shutdown_cleanliness"] == "UNCLEAN"
    assert saved["activation_health"] == "PASS"
    assert saved["overall"] == "PASS"
