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
    ControllerFailed,
    ForceKillCommandFailed,
    Manifest,
    Occupancy,
    ServiceIdentity,
    SourceIdentity,
    SystemdOperations,
    main,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@dataclass
class FakeLifecycleLease:
    ops: "FakeOps"

    def release(self):
        self.ops.events.append("lifecycle:release")


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
    def __init__(
        self,
        *,
        occupancy=None,
        replacements=None,
        old_cgroup_gone=True,
        runtime_acceptance=None,
        force_kill_error: ControllerBlocked | None = None,
    ):
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
            n_restarts=7,
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
        self.cgroup_gone = old_cgroup_gone
        self.force_kill_error = force_kill_error
        self.acceptance = dict(
            runtime_acceptance
            or {
                "platforms": "PASS",
                "scheduler": "PASS",
                "session_store": "PASS",
                "resumability": "PASS",
                "drain_marker": "PASS",
            }
        )

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

    def acquire_lifecycle(self, manifest):
        self.events.append("lifecycle:acquire")
        return FakeLifecycleLease(self)

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
        if self.force_kill_error is not None:
            raise self.force_kill_error

    def start(self, manifest):
        self.events.append("restart:start")
        self.current_service = replacement_identity()

    def health_check(self, manifest, service):
        self.events.append("health")
        return service.pid == 202 and service.active_state == "active"

    def old_cgroup_members_gone(self, manifest, old_pids):
        self.events.append("cgroup:verify")
        return self.cgroup_gone

    def runtime_acceptance(self, manifest, service):
        self.events.append("acceptance")
        return dict(self.acceptance)


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
        n_restarts=7,
    )


def manifest_mapping(tmp_path: Path, controller_path: Path) -> dict:
    digest = hashlib.sha256(controller_path.read_bytes()).hexdigest()
    return {
        "schema": "hermes-bounded-handoff-force-restart/v1",
        "transaction_id": "txn-20260901-a",
        "controller": {"version": 7, "sha256": digest},
        "service": {
            "unit": "hermes-gateway.service",
            "scope": "user",
            "old_pid": 101,
            "old_invocation_id": "old-invocation",
            "old_proc_start_ticks": "1001",
            "old_control_group": "/user.slice/hermes.service",
            "old_n_restarts": 7,
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
        "acceptance": {
            "expected_platforms": ["telegram", "api_server", "buzz"],
        },
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


def test_forced_restart_rejects_more_than_one_systemd_restart(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    duplicate = replace(replacement_identity(), n_restarts=9)
    ops = FakeOps(
        occupancy=[Occupancy(active_agents=1)] * 4,
        replacements=[duplicate],
    )
    controller = BoundedRestartController(manifest, ops)

    with pytest.raises(ControllerFailed, match="restart count"):
        controller.run(execute=True)

    result = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert result["restart_count"] == "FAIL"
    assert ops.events.count("restart:force-kill") == 1
    assert ops.events.count("restart:start") == 0


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


def test_dry_run_performs_preflight_without_drain_restart_or_evidence(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()
    result = BoundedRestartController(manifest, ops).run(execute=False)

    assert result["state"] == "PREFLIGHT_PASS"
    assert "source" in ops.events and "service" in ops.events
    assert not any(event.startswith("drain:") for event in ops.events)
    assert not any(event.startswith("restart:") for event in ops.events)
    assert "lifecycle:acquire" not in ops.events
    assert not manifest.evidence_root.exists()


def test_dry_run_failure_does_not_create_evidence(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)

    class FailingSourceOps(FakeOps):
        def source_identity(self, manifest):
            del manifest
            raise TimeoutError("remote proof timed out")

    with pytest.raises(TimeoutError, match="remote proof timed out"):
        BoundedRestartController(manifest, FailingSourceOps()).run(execute=False)

    assert not manifest.evidence_root.exists()


def test_dry_run_refuses_committed_recovery_without_changing_evidence(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    result_path = Path(manifest.evidence_root) / "controller-result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "schema": "hermes-bounded-handoff-force-restart-result/v1",
                "transaction_id": manifest.transaction_id,
                "manifest_sha256": manifest.manifest_sha256,
                "state": "RESTART_COMMITTED",
                "restart_budget_consumed": 1,
                "old_service": FakeOps().old.to_mapping(),
            }
        ),
        encoding="utf-8",
    )
    before = result_path.read_bytes()
    ops = FakeOps()
    ops.current_service = replacement_identity()

    with pytest.raises(
        ControllerBlocked, match="committed transaction requires execute-mode recovery"
    ):
        BoundedRestartController(manifest, ops).run(execute=False)

    assert result_path.read_bytes() == before
    assert "restart:graceful" not in ops.events
    assert "restart:force-kill" not in ops.events
    assert "restart:start" not in ops.events


def test_dry_run_never_recovers_or_rewrites_a_committed_transaction(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    root = Path(manifest.evidence_root)
    root.mkdir(parents=True)
    result_path = root / "controller-result.json"
    original = json.dumps(
        {
            "schema": "hermes-bounded-handoff-force-restart-result/v1",
            "transaction_id": manifest.transaction_id,
            "manifest_sha256": manifest.manifest_sha256,
            "state": "RESTART_COMMITTED",
            "restart_budget_consumed": 1,
            "old_service": FakeOps().old.to_mapping(),
        }
    )
    result_path.write_text(original, encoding="utf-8")
    ops = FakeOps()

    with pytest.raises(ControllerBlocked, match="requires execute-mode recovery"):
        BoundedRestartController(manifest, ops).run(execute=False)

    assert result_path.read_text(encoding="utf-8") == original
    assert ops.events == []


def test_execute_holds_lifecycle_lease_before_drain_and_releases_it(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()

    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["overall"] == "PASS"
    assert ops.events.index("lifecycle:acquire") < ops.events.index("drain:acquire")
    assert ops.events[-1] == "lifecycle:release"


def test_execute_releases_lifecycle_lease_when_preflight_blocks(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()
    ops.source = replace(ops.source, head="c" * 40)

    with pytest.raises(ControllerBlocked, match="source identity drift"):
        BoundedRestartController(manifest, ops).run(execute=True)

    assert ops.events == ["lifecycle:acquire", "source", "lifecycle:release"]


def test_execute_preserves_primary_failure_when_lifecycle_release_also_fails(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()
    ops.source = replace(ops.source, head="c" * 40)

    class BrokenRelease(FakeLifecycleLease):
        def release(self):
            raise RuntimeError("release-tampered")

    ops.acquire_lifecycle = lambda manifest: BrokenRelease(ops)
    with pytest.raises(
        ControllerBlocked,
        match="source identity drift.*release-tampered",
    ) as caught:
        BoundedRestartController(manifest, ops).run(execute=True)

    assert isinstance(caught.value.__cause__, ControllerBlocked)


def test_execute_releases_lifecycle_lease_on_unexpected_error(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps()

    def fail_source(_manifest):
        raise ValueError("unexpected-probe")

    ops.source_identity = fail_source
    with pytest.raises(ValueError, match="unexpected-probe"):
        BoundedRestartController(manifest, ops).run(execute=True)

    assert ops.events == ["lifecycle:acquire", "lifecycle:release"]


def test_systemd_lifecycle_lease_binds_exact_candidate_provenance(
    tmp_path, controller_path, monkeypatch
):
    import gateway.lifecycle_lease as lifecycle_lease

    manifest = load_manifest(tmp_path, controller_path)
    captured = {}
    sentinel = object()

    def fake_acquire(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(lifecycle_lease, "acquire_lifecycle_lease", fake_acquire)
    operations = SystemdOperations()
    monkeypatch.setattr(operations, "wall_now", lambda: NOW)

    assert operations.acquire_lifecycle(manifest) is sentinel
    assert captured == {
        "home": manifest.hermes_home,
        "owner_token": manifest.transaction_id,
        "purpose": "bounded-restart",
        "provenance": {
            "source_head": manifest.expected_source.head,
            "source_tree": manifest.expected_source.tree,
            "artifact_sha256": manifest.manifest_sha256,
            "evidence_id": manifest.transaction_id,
        },
        "expires_at": manifest.expires_at,
        "now": NOW,
    }


def test_systemd_lifecycle_lease_real_filesystem_seam(tmp_path, controller_path, monkeypatch):
    manifest = load_manifest(tmp_path, controller_path)
    operations = SystemdOperations()
    monkeypatch.setattr(operations, "wall_now", lambda: NOW)

    first = operations.acquire_lifecycle(manifest)
    try:
        with pytest.raises(ControllerBlocked, match="lifecycle lease is busy"):
            operations.acquire_lifecycle(manifest)
    finally:
        first.release()

    retry = operations.acquire_lifecycle(manifest)
    retry.release()


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


def test_force_only_observation_can_never_trigger_early_graceful_restart(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    unknown = Occupancy(active_agents=0, cgroup_pids=(101,), force_only=True)
    ops = FakeOps(
        occupancy=[unknown, unknown, unknown, unknown],
        replacements=[replace(replacement_identity(), n_restarts=8)],
    )

    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert ops.mono == 180
    assert result["restart_mode"] == "FORCED"
    assert result["handoff_within_180s"] == "TIMEOUT"
    assert result["remaining_occupancy"]["force_only"] is True
    assert "restart:graceful" not in ops.events
    assert ops.events.count("restart:force-kill") == 1


def test_bootstrap_manifest_cannot_gracefully_restart_even_if_operations_report_quiet(
    tmp_path, controller_path
):
    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": "c" * 40,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    quiet = Occupancy(active_agents=0, cgroup_pids=(101,))
    ops = FakeOps(
        occupancy=[quiet, quiet, quiet, quiet],
        replacements=[replace(replacement_identity(), n_restarts=8)],
    )

    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert ops.mono == 180
    assert result["restart_mode"] == "FORCED"
    assert "restart:graceful" not in ops.events
    assert ops.events.count("restart:force-kill") == 1


def test_busy_at_deadline_force_kills_cgroup_once_and_accepts_auto_restart(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cron_runs=1, cgroup_pids=(101, 111, 112))
    ops = FakeOps(
        occupancy=[busy, busy, busy, busy],
        replacements=[replace(replacement_identity(), n_restarts=8)],
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


def test_force_kill_nonzero_after_old_generation_gone_reconciles_auto_restart(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111, 112))
    ops = FakeOps(
        occupancy=[busy] * 4,
        replacements=[replace(replacement_identity(), n_restarts=8)],
        force_kill_error=ForceKillCommandFailed(
            "command failed (1): systemctl kill: Invalid argument"
        ),
    )

    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["state"] == "VERIFIED"
    assert result["force_kill_command_error"].endswith("Invalid argument")
    assert result["force_kill_reconciled"] is True
    assert result["restart_count"] == "PASS"
    assert result["old_cgroup_gone"] == "PASS"
    assert result["activation_health"] == "PASS"
    assert result["runtime_acceptance"] == "PASS"
    assert ops.events.count("restart:force-kill") == 1
    assert "restart:start" not in ops.events
    assert ops.events.count("drain:clear") == 1


def test_force_kill_nonzero_retries_transient_replacement_health_before_blocking(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111, 112))

    class DelayedHealthOps(FakeOps):
        def __init__(self):
            super().__init__(
                occupancy=[busy] * 4,
                replacements=[replace(replacement_identity(), n_restarts=8)],
                force_kill_error=ForceKillCommandFailed(
                    "command failed (1): systemctl kill: Invalid argument"
                ),
            )
            self.health_results = [False, True]

        def health_check(self, manifest, service):
            self.events.append("health")
            assert service.pid == 202
            return self.health_results.pop(0)

    ops = DelayedHealthOps()
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["state"] == "VERIFIED"
    assert result["force_kill_reconciled"] is True
    assert result["activation_health"] == "PASS"
    assert ops.events.count("health") == 2
    assert ops.events.count("restart:force-kill") == 1
    assert "restart:start" not in ops.events
    assert ops.events.count("drain:clear") == 1


def test_force_kill_nonzero_permanent_health_failure_stays_bounded_and_blocked(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111, 112))

    class UnhealthyOps(FakeOps):
        def __init__(self):
            super().__init__(
                occupancy=[busy] * 4,
                replacements=[replace(replacement_identity(), n_restarts=8)],
                force_kill_error=ForceKillCommandFailed(
                    "command failed (1): systemctl kill: Invalid argument"
                ),
            )

        def health_check(self, manifest, service):
            self.events.append("health")
            assert service.pid == 202
            return False

    ops = UnhealthyOps()
    with pytest.raises(ControllerBlocked, match="verification did not pass"):
        BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert ops.mono == 185
    assert ops.events.count("health") == 6
    assert ops.events.count("restart:force-kill") == 1
    assert "restart:start" not in ops.events
    assert "drain:clear" not in ops.events
    assert saved["force_kill_reconciled"] is False
    assert saved["activation_health"] == "FAIL"
    assert saved["overall"] == "BLOCKED"


def test_force_kill_precommand_identity_block_is_not_reconciled(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111, 112))
    ops = FakeOps(
        occupancy=[busy] * 4,
        replacements=[replace(replacement_identity(), n_restarts=8)],
        force_kill_error=ControllerBlocked(
            "service generation changed before force kill"
        ),
    )

    with pytest.raises(ControllerBlocked, match="generation changed before force kill"):
        BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert saved["restart_budget_consumed"] == 1
    assert "force_kill_reconciled" not in saved
    assert saved["activation_health"] == "BLOCKED"
    assert saved["overall"] == "BLOCKED"
    assert ops.events.count("restart:force-kill") == 1
    assert "restart:start" not in ops.events
    assert ops.events.count("drain:clear") == 0


def test_systemd_force_kill_types_only_subprocess_failure_as_reconcilable(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    old = ServiceIdentity(
        pid=101,
        invocation_id="old-invocation",
        proc_start_ticks="1001",
        active_state="active",
        sub_state="running",
        control_group="/user.slice/hermes.service",
        restart_policy="always",
        kill_mode="control-group",
        n_restarts=7,
    )
    ops = SystemdOperations()
    monkeypatch.setattr(ops, "service_identity", lambda unused: old)

    def fail_command(command, *, timeout, check=True):
        raise ControllerBlocked("command failed (1): systemctl kill: Invalid argument")

    monkeypatch.setattr(ops, "_run", fail_command)

    with pytest.raises(ForceKillCommandFailed, match="Invalid argument"):
        ops.force_kill(manifest, old)


def test_force_kill_nonzero_with_unverified_replacement_stays_unreconciled(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111, 112))
    ops = FakeOps(
        occupancy=[busy] * 4,
        replacements=[replace(replacement_identity(), n_restarts=8)],
        old_cgroup_gone=False,
        force_kill_error=ForceKillCommandFailed(
            "command failed (1): systemctl kill: Invalid argument"
        ),
    )

    with pytest.raises(ControllerBlocked, match="verification did not pass"):
        BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert saved["force_kill_reconciled"] is False
    assert saved["old_cgroup_gone"] == "FAIL"
    assert saved["overall"] == "BLOCKED"
    assert ops.events.count("drain:clear") == 0


def test_force_kill_nonzero_without_replacement_blocks_and_preserves_drain(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111, 112))
    ops = FakeOps(
        occupancy=[busy] * 4,
        force_kill_error=ForceKillCommandFailed(
            "command failed (1): systemctl kill: Invalid argument"
        ),
    )

    with pytest.raises(ControllerBlocked, match="kill command failed and replacement"):
        BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert saved["state"] == "RESTART_COMMITTED"
    assert saved["restart_budget_consumed"] == 1
    assert saved["force_kill_reconciled"] is False
    assert saved["activation_health"] == "BLOCKED"
    assert saved["overall"] == "BLOCKED"
    assert ops.events.count("restart:force-kill") == 1
    assert "restart:start" not in ops.events
    assert "drain:clear" not in ops.events


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
    ops = FakeOps(occupancy=[detached] * 4, replacements=[replace(replacement_identity(), n_restarts=8)])
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
                "manifest_sha256": manifest.manifest_sha256,
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
    ops = FakeOps(occupancy=[busy] * 4, replacements=[replace(replacement_identity(), n_restarts=8)])
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


def test_runtime_acceptance_must_prove_every_required_surface(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps(
        runtime_acceptance={
            "platforms": "PASS",
            "scheduler": "FAIL",
            "session_store": "PASS",
            "resumability": "PASS",
        }
    )

    with pytest.raises(Exception, match="runtime acceptance"):
        BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert saved["activation_health"] == "PASS"
    assert saved["runtime_acceptance"] == "FAIL"
    assert saved["overall"] == "FAIL"


def test_runtime_acceptance_retries_transient_control_socket_unavailability(
    tmp_path, controller_path
):
    manifest = load_manifest(
        tmp_path,
        controller_path,
        mutate=lambda raw: raw["policy"].update(stable_zero_samples=1),
    )

    class FlakyAcceptanceOps(FakeOps):
        def __init__(self):
            super().__init__()
            self.acceptance_attempts = 0

        def runtime_acceptance(self, manifest, service):
            self.acceptance_attempts += 1
            if self.acceptance_attempts == 1:
                raise ControllerBlocked("live gateway occupancy is unavailable")
            return super().runtime_acceptance(manifest, service)

    ops = FlakyAcceptanceOps()
    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["runtime_acceptance"] == "PASS"
    assert ops.acceptance_attempts == 2
    assert ops.mono == 1


def test_old_cgroup_members_must_be_proven_gone(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    ops = FakeOps(old_cgroup_gone=False)

    with pytest.raises(Exception, match="old cgroup"):
        BoundedRestartController(manifest, ops).run(execute=True)

    saved = json.loads(
        (Path(manifest.evidence_root) / "controller-result.json").read_text()
    )
    assert saved["old_cgroup_gone"] == "FAIL"
    assert saved["activation_health"] == "PASS"
    assert saved["overall"] == "FAIL"


def test_systemd_occupancy_blocks_when_live_gateway_status_is_missing(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = SystemdOperations()
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, _verb: None)
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: FakeOps().old)
    monkeypatch.setattr(ops, "_count_running_cron", lambda _home: 0)
    monkeypatch.setattr(ops, "_cgroup_pids", lambda _cgroup: (101,))

    with pytest.raises(ControllerBlocked, match="live gateway occupancy"):
        ops.sample_occupancy(manifest)


def test_systemd_occupancy_uses_live_attributed_counts(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = SystemdOperations()
    service = FakeOps().old
    payloads = {
        "identify": {"pid": service.pid, "code_sha": manifest.expected_source.head},
        "status": {
            "pid": service.pid,
            "active_agents": 10,
            "occupancy": {
                "foreground_agents": 2,
                "cron_runs": 1,
                "api_runs": 3,
                "detached_workers": 4,
                "total": 10,
            },
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)
    monkeypatch.setattr(ops, "_count_running_cron", lambda _home: 2)
    monkeypatch.setattr(ops, "_cgroup_pids", lambda _cgroup: (101, 111))

    assert ops.sample_occupancy(manifest) == Occupancy(
        active_agents=2,
        cron_runs=2,
        api_runs=3,
        detached_workers=4,
        cgroup_pids=(101, 111),
    )


def test_systemd_occupancy_blocks_on_missing_attribution(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = SystemdOperations()
    service = FakeOps().old
    payloads = {
        "identify": {"pid": service.pid, "code_sha": manifest.expected_source.head},
        "status": {
            "pid": service.pid,
            "active_agents": 0,
            "occupancy": {"foreground_agents": 0, "cron_runs": 0, "total": 0},
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)

    with pytest.raises(ControllerBlocked, match="malformed"):
        ops.sample_occupancy(manifest)


def test_manifest_bootstrap_force_only_requires_explicit_scope(tmp_path, controller_path):
    raw = manifest_mapping(tmp_path, controller_path)
    raw["bootstrap"] = {
        "mode": "legacy-force-only",
        "old_code_sha": "c" * 40,
    }

    with pytest.raises(ValueError, match="bootstrap_force_only"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)


def test_manifest_bootstrap_rejects_target_generation_as_legacy(tmp_path, controller_path):
    raw = manifest_mapping(tmp_path, controller_path)
    raw["bootstrap"] = {
        "mode": "legacy-force-only",
        "old_code_sha": raw["source"]["head"],
    }
    raw["authorization"]["scope"].append("bootstrap_force_only")

    with pytest.raises(ValueError, match="must differ from target"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)


def test_systemd_bootstrap_uses_pid_bound_legacy_status_but_never_declares_quiet(
    tmp_path, controller_path, monkeypatch
):
    old_code_sha = "c" * 40

    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": old_code_sha,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    ops = SystemdOperations()
    service = FakeOps().old
    payloads = {
        "identify": {"pid": service.pid, "code_sha": old_code_sha},
        "status": {
            "pid": service.pid,
            "answering_pid": service.pid,
            "code_sha": old_code_sha,
            "gateway_state": "running",
            "active_agents": 0,
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)
    monkeypatch.setattr(ops, "_count_running_cron", lambda _home: 0)
    monkeypatch.setattr(ops, "_cgroup_pids", lambda _cgroup: (service.pid,))

    observed = ops.sample_occupancy(manifest)

    assert observed == Occupancy(
        active_agents=0,
        cron_runs=0,
        api_runs=0,
        detached_workers=0,
        cgroup_pids=(service.pid,),
        force_only=True,
    )
    assert observed.quiet_for(service.pid) is False


def test_systemd_bootstrap_repeated_samples_accept_owned_running_to_draining_transition(
    tmp_path, controller_path, monkeypatch
):
    old_code_sha = "c" * 40

    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": old_code_sha,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    ops = SystemdOperations()
    service = FakeOps().old
    status = {
        "pid": service.pid,
        "answering_pid": service.pid,
        "code_sha": old_code_sha,
        "gateway_state": "running",
        "active_agents": 5,
    }
    payloads = {
        "identify": {"pid": service.pid, "code_sha": old_code_sha},
        "status": status,
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)
    monkeypatch.setattr(ops, "_count_running_cron", lambda _home: 3)
    monkeypatch.setattr(ops, "_cgroup_pids", lambda _cgroup: (service.pid, 111))

    running = ops.sample_occupancy(manifest)

    manifest.hermes_home.mkdir(parents=True)
    (manifest.hermes_home / ".drain_request.json").write_text(
        json.dumps(
            {
                "action": "drain",
                "principal": "bounded-handoff-force-restart",
                "owner_token": manifest.transaction_id,
            }
        ),
        encoding="utf-8",
    )
    status["gateway_state"] = "draining"

    draining = ops.sample_occupancy(manifest)

    assert running == draining == Occupancy(
        active_agents=5,
        cron_runs=3,
        api_runs=0,
        detached_workers=0,
        cgroup_pids=(service.pid, 111),
        force_only=True,
    )


def test_systemd_bootstrap_rejects_draining_status_owned_by_another_transaction(
    tmp_path, controller_path, monkeypatch
):
    old_code_sha = "c" * 40

    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": old_code_sha,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    manifest.hermes_home.mkdir(parents=True)
    (manifest.hermes_home / ".drain_request.json").write_text(
        json.dumps({"action": "drain", "owner_token": "foreign-transaction"}),
        encoding="utf-8",
    )
    ops = SystemdOperations()
    service = FakeOps().old
    payloads = {
        "identify": {"pid": service.pid, "code_sha": old_code_sha},
        "status": {
            "pid": service.pid,
            "answering_pid": service.pid,
            "code_sha": old_code_sha,
            "gateway_state": "draining",
            "active_agents": 5,
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)

    with pytest.raises(ControllerBlocked, match="owned drain marker"):
        ops.sample_occupancy(manifest)


def test_systemd_bootstrap_rejects_unknown_gateway_state(
    tmp_path, controller_path, monkeypatch
):
    old_code_sha = "c" * 40

    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": old_code_sha,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    ops = SystemdOperations()
    service = FakeOps().old
    payloads = {
        "identify": {"pid": service.pid, "code_sha": old_code_sha},
        "status": {
            "pid": service.pid,
            "answering_pid": service.pid,
            "code_sha": old_code_sha,
            "gateway_state": "stopped",
            "active_agents": 2,
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)

    with pytest.raises(ControllerBlocked, match="malformed"):
        ops.sample_occupancy(manifest)


def test_systemd_bootstrap_rejects_old_code_identity_drift(
    tmp_path, controller_path, monkeypatch
):
    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": "c" * 40,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    ops = SystemdOperations()
    service = FakeOps().old
    payloads = {
        "identify": {"pid": service.pid, "code_sha": "d" * 40},
        "status": {
            "pid": service.pid,
            "answering_pid": service.pid,
            "code_sha": "d" * 40,
            "gateway_state": "running",
            "active_agents": 0,
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])
    monkeypatch.setattr(ops, "service_identity", lambda _manifest: service)

    with pytest.raises(ControllerBlocked, match="old code identity"):
        ops.sample_occupancy(manifest)


def test_systemd_cgroup_membership_missing_blocks_instead_of_becoming_empty(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    ops = SystemdOperations()
    missing = tmp_path / "missing-cgroup.procs"
    monkeypatch.setattr(ops, "_cgroup_procs_path", lambda _group: missing)

    with pytest.raises(ControllerBlocked, match="cgroup membership"):
        ops._cgroup_pids(manifest.old_control_group)


def test_systemd_runtime_acceptance_binds_every_surface_to_replacement(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    service = replacement_identity()
    ops = SystemdOperations()
    payloads = {
        "identify": {"pid": service.pid, "code_sha": manifest.expected_source.head},
        "status": {
            "pid": service.pid,
            "gateway_state": "running",
            "platforms": {
                name: {
                    "state": "connected",
                    "writer_pid": service.pid,
                    "needs_attention": False,
                }
                for name in manifest.expected_platforms
            },
            "scheduler": {"status": "running", "writer_pid": service.pid},
            "session_store": {"status": "ok", "writer_pid": service.pid},
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])

    assert ops.runtime_acceptance(manifest, service) == {
        "platforms": "PASS",
        "scheduler": "PASS",
        "session_store": "PASS",
        "resumability": "PASS",
        "drain_marker": "PASS",
    }


def test_bootstrap_runtime_acceptance_still_rejects_old_code_identity(
    tmp_path, controller_path, monkeypatch
):
    old_code_sha = "c" * 40

    def mutate(raw):
        raw["bootstrap"] = {
            "mode": "legacy-force-only",
            "old_code_sha": old_code_sha,
        }
        raw["authorization"]["scope"].append("bootstrap_force_only")

    manifest = load_manifest(tmp_path, controller_path, mutate=mutate)
    service = replacement_identity()
    ops = SystemdOperations()
    payloads = {
        "identify": {"pid": service.pid, "code_sha": old_code_sha},
        "status": {
            "pid": service.pid,
            "gateway_state": "running",
            "platforms": {
                name: {"state": "connected", "writer_pid": service.pid}
                for name in manifest.expected_platforms
            },
            "scheduler": {"status": "running", "writer_pid": service.pid},
            "session_store": {"status": "ok", "writer_pid": service.pid},
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])

    assert set(ops.runtime_acceptance(manifest, service).values()) == {"FAIL"}


def test_systemd_runtime_acceptance_rejects_stale_scheduler_writer(
    tmp_path, controller_path, monkeypatch
):
    manifest = load_manifest(tmp_path, controller_path)
    service = replacement_identity()
    ops = SystemdOperations()
    payloads = {
        "identify": {"pid": service.pid, "code_sha": manifest.expected_source.head},
        "status": {
            "pid": service.pid,
            "gateway_state": "running",
            "platforms": {
                name: {"state": "connected", "writer_pid": service.pid}
                for name in manifest.expected_platforms
            },
            "scheduler": {"status": "running", "writer_pid": 101},
            "session_store": {"status": "ok", "writer_pid": service.pid},
        },
    }
    monkeypatch.setattr(ops, "_query_gateway", lambda _manifest, verb: payloads[verb])

    acceptance = ops.runtime_acceptance(manifest, service)
    assert acceptance["scheduler"] == "FAIL"


def test_existing_evidence_rejects_manifest_identity_change(tmp_path, controller_path):
    manifest = load_manifest(tmp_path, controller_path)
    root = Path(manifest.evidence_root)
    root.mkdir(parents=True)
    (root / "controller-result.json").write_text(
        json.dumps(
            {
                "schema": "hermes-bounded-handoff-force-restart-result/v1",
                "transaction_id": manifest.transaction_id,
                "manifest_sha256": "0" * 64,
                "state": "PREFLIGHT_PASS",
                "restart_budget_consumed": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ControllerBlocked, match="manifest changed"):
        BoundedRestartController(manifest, FakeOps()).run(execute=False)


def test_handoff_resume_preserves_original_deadline_and_samples(
    tmp_path, controller_path
):
    manifest = load_manifest(tmp_path, controller_path)
    root = Path(manifest.evidence_root)
    root.mkdir(parents=True)
    original_deadline = NOW + timedelta(seconds=30)
    (root / "controller-result.json").write_text(
        json.dumps(
            {
                "schema": "hermes-bounded-handoff-force-restart-result/v1",
                "transaction_id": manifest.transaction_id,
                "manifest_sha256": manifest.manifest_sha256,
                "controller_sha256": manifest.controller_sha256,
                "state": "HANDOFF",
                "restart_budget_consumed": 0,
                "old_service": FakeOps().old.to_mapping(),
                "samples": [{"elapsed_seconds": 150, "active_agents": 1}],
                "handoff_started_at": (NOW - timedelta(seconds=150)).isoformat(),
                "handoff_deadline_at": original_deadline.isoformat(),
            }
        )
    )
    busy = Occupancy(active_agents=1, cgroup_pids=(101, 111))
    ops = FakeOps(occupancy=[busy] * 4, replacements=[replace(replacement_identity(), n_restarts=8)])

    result = BoundedRestartController(manifest, ops).run(execute=True)

    assert result["restart_mode"] == "FORCED"
    assert ops.mono <= 30
    assert result["samples"][0]["elapsed_seconds"] == 150


def test_health_url_rejects_non_http_schemes(tmp_path, controller_path):
    raw = manifest_mapping(tmp_path, controller_path)
    raw["health_url"] = "file:///etc/passwd"
    with pytest.raises(ValueError, match="http"):
        Manifest.from_mapping(raw, controller_path=controller_path, now=NOW)
