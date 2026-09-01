"""Manifest-driven, externally supervised Hermes gateway restart controller.

The controller is deliberately separate from ``hermes-gateway.service``.  It
closes admission through the existing owned drain contract, gives active work
one fixed 180-second handoff window, then performs exactly one graceful or
cgroup-wide forced systemd restart.  Per-update identities and authorization
live in a sealed JSON manifest; this module contains no deployment-specific
values.

Run directly with::

    python -m hermes_cli.bounded_restart --manifest transaction.json --execute

Omit ``--execute`` for an identity-only preflight.  Linux user-systemd is the
only live mutation backend in v1; the state machine is dependency-injected so
its deadline, fencing, and crash-recovery behavior can be tested without
systemd or a production ``HERMES_HOME``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

SCHEMA = "hermes-bounded-handoff-force-restart/v1"
RESULT_SCHEMA = "hermes-bounded-handoff-force-restart-result/v1"
HANDOFF_DEADLINE_SECONDS = 180
CONTROLLER_VERSION = 1
_REQUIRED_AUTHORIZATION = frozenset({"drain", "graceful_restart", "force_restart"})


class ControllerBlocked(RuntimeError):
    """A safety or identity fence blocked mutation."""


class ControllerFailed(RuntimeError):
    """The authorized transaction mutated state but failed verification."""


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _parse_datetime(value: Any, field: str) -> datetime:
    raw = _require_str(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class SourceIdentity:
    head: str
    tree: str
    branch: str
    remote_head: str
    clean: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "tree": self.tree,
            "branch": self.branch,
            "remote_head": self.remote_head,
            "clean": self.clean,
        }


@dataclass(frozen=True)
class ServiceIdentity:
    pid: int
    invocation_id: str
    proc_start_ticks: str
    active_state: str
    sub_state: str
    control_group: str
    restart_policy: str
    kill_mode: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "invocation_id": self.invocation_id,
            "proc_start_ticks": self.proc_start_ticks,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "control_group": self.control_group,
            "restart_policy": self.restart_policy,
            "kill_mode": self.kill_mode,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ServiceIdentity":
        return cls(
            pid=int(raw.get("pid", 0)),
            invocation_id=str(raw.get("invocation_id", "")),
            proc_start_ticks=str(raw.get("proc_start_ticks", "")),
            active_state=str(raw.get("active_state", "")),
            sub_state=str(raw.get("sub_state", "")),
            control_group=str(raw.get("control_group", "")),
            restart_policy=str(raw.get("restart_policy", "")),
            kill_mode=str(raw.get("kill_mode", "")),
        )


@dataclass(frozen=True)
class Occupancy:
    active_agents: int = 0
    cron_runs: int = 0
    api_runs: int = 0
    detached_workers: int = 0
    cgroup_pids: tuple[int, ...] = ()

    def quiet_for(self, main_pid: int) -> bool:
        extra_pids = tuple(pid for pid in self.cgroup_pids if pid != main_pid)
        return (
            self.active_agents == 0
            and self.cron_runs == 0
            and self.api_runs == 0
            and self.detached_workers == 0
            and not extra_pids
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "active_agents": self.active_agents,
            "cron_runs": self.cron_runs,
            "api_runs": self.api_runs,
            "detached_workers": self.detached_workers,
            "cgroup_pids": list(self.cgroup_pids),
        }


@dataclass(frozen=True)
class Manifest:
    transaction_id: str
    controller_sha256: str
    service_unit: str
    service_scope: str
    old_pid: int
    old_invocation_id: str
    old_proc_start_ticks: str
    old_control_group: str
    repo: Path
    expected_source: SourceIdentity
    remote: str
    remote_ref: str
    require_clean: bool
    handoff_deadline_seconds: int
    sample_interval_seconds: int
    stable_zero_samples: int
    restart_budget: int
    force_after_deadline: bool
    replacement_wait_seconds: int
    authorization_actor: str
    approved_at: datetime
    expires_at: datetime
    authorization_scope: frozenset[str]
    hermes_home: Path
    evidence_root: Path
    health_url: str

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        controller_path: Path,
        now: datetime | None = None,
    ) -> "Manifest":
        if raw.get("schema") != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        controller = _require_mapping(raw.get("controller"), "controller")
        if _require_int(controller.get("version"), "controller.version", minimum=1) != CONTROLLER_VERSION:
            raise ValueError(f"controller.version must be {CONTROLLER_VERSION}")
        expected_hash = _require_str(controller.get("sha256"), "controller.sha256")
        if len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
            raise ValueError("controller.sha256 must be a lowercase SHA-256")
        actual_hash = _sha256(controller_path)
        if actual_hash != expected_hash:
            raise ValueError("controller SHA-256 does not match executable bytes")

        service = _require_mapping(raw.get("service"), "service")
        source = _require_mapping(raw.get("source"), "source")
        policy = _require_mapping(raw.get("policy"), "policy")
        authorization = _require_mapping(raw.get("authorization"), "authorization")

        deadline = _require_int(
            policy.get("handoff_deadline_seconds"),
            "policy.handoff_deadline_seconds",
            minimum=1,
        )
        if deadline != HANDOFF_DEADLINE_SECONDS:
            raise ValueError("handoff deadline must be exactly 180 seconds")
        force = policy.get("force_after_deadline")
        if force is not True:
            raise ValueError("policy.force_after_deadline must be true")
        budget = _require_int(policy.get("restart_budget"), "policy.restart_budget", minimum=1)
        if budget != 1:
            raise ValueError("policy.restart_budget must be exactly 1")

        approved_at = _parse_datetime(authorization.get("approved_at"), "authorization.approved_at")
        expires_at = _parse_datetime(authorization.get("expires_at"), "authorization.expires_at")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if expires_at <= current:
            raise ValueError("authorization expired")
        scope_raw = authorization.get("scope")
        if not isinstance(scope_raw, list) or not all(isinstance(item, str) for item in scope_raw):
            raise ValueError("authorization.scope must be a string array")
        scope = frozenset(scope_raw)
        missing = sorted(_REQUIRED_AUTHORIZATION - scope)
        if missing:
            raise ValueError("authorization.scope missing " + ", ".join(missing))

        head = _require_str(source.get("head"), "source.head")
        tree = _require_str(source.get("tree"), "source.tree")
        remote_head = _require_str(source.get("remote_head"), "source.remote_head")
        for field, value in (("source.head", head), ("source.tree", tree), ("source.remote_head", remote_head)):
            if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{field} must be a lowercase 40-character Git object ID")

        hermes_home = Path(_require_str(raw.get("hermes_home"), "hermes_home")).expanduser().resolve()
        evidence_root = Path(_require_str(raw.get("evidence_root"), "evidence_root")).expanduser().resolve()
        if hermes_home == Path("/"):
            raise ValueError("hermes_home cannot be /")
        if evidence_root == Path("/"):
            raise ValueError("evidence_root cannot be /")
        return cls(
            transaction_id=_require_str(raw.get("transaction_id"), "transaction_id"),
            controller_sha256=expected_hash,
            service_unit=_require_str(service.get("unit"), "service.unit"),
            service_scope=_require_str(service.get("scope"), "service.scope"),
            old_pid=_require_int(service.get("old_pid"), "service.old_pid", minimum=1),
            old_invocation_id=_require_str(service.get("old_invocation_id"), "service.old_invocation_id"),
            old_proc_start_ticks=_require_str(service.get("old_proc_start_ticks"), "service.old_proc_start_ticks"),
            old_control_group=_require_str(service.get("old_control_group"), "service.old_control_group"),
            repo=Path(_require_str(source.get("repo"), "source.repo")).expanduser().resolve(),
            expected_source=SourceIdentity(
                head=head,
                tree=tree,
                branch=_require_str(source.get("branch"), "source.branch"),
                remote_head=remote_head,
                clean=bool(source.get("require_clean", True)),
            ),
            remote=_require_str(source.get("remote"), "source.remote"),
            remote_ref=_require_str(source.get("remote_ref"), "source.remote_ref"),
            require_clean=source.get("require_clean") is not False,
            handoff_deadline_seconds=deadline,
            sample_interval_seconds=_require_int(policy.get("sample_interval_seconds"), "policy.sample_interval_seconds", minimum=1),
            stable_zero_samples=_require_int(policy.get("stable_zero_samples"), "policy.stable_zero_samples", minimum=1),
            restart_budget=budget,
            force_after_deadline=force,
            replacement_wait_seconds=_require_int(policy.get("replacement_wait_seconds"), "policy.replacement_wait_seconds", minimum=1),
            authorization_actor=_require_str(authorization.get("actor"), "authorization.actor"),
            approved_at=approved_at,
            expires_at=expires_at,
            authorization_scope=scope,
            hermes_home=hermes_home,
            evidence_root=evidence_root,
            health_url=_require_str(raw.get("health_url"), "health_url"),
        )


class Operations(Protocol):
    def wall_now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def source_identity(self, manifest: Manifest) -> SourceIdentity: ...
    def service_identity(self, manifest: Manifest) -> ServiceIdentity: ...
    def acquire_drain(self, manifest: Manifest) -> Any: ...
    def sample_occupancy(self, manifest: Manifest) -> Occupancy: ...
    def graceful_restart(self, manifest: Manifest) -> None: ...
    def prepare_interruption(self, manifest: Manifest, occupancy: Occupancy) -> None: ...
    def force_kill(self, manifest: Manifest, old: ServiceIdentity) -> None: ...
    def start(self, manifest: Manifest) -> None: ...
    def health_check(self, manifest: Manifest, service: ServiceIdentity) -> bool: ...


class BoundedRestartController:
    def __init__(self, manifest: Manifest, operations: Operations):
        self.manifest = manifest
        self.ops = operations
        self.result_path = manifest.evidence_root / "controller-result.json"

    def _base_result(self) -> dict[str, Any]:
        return {
            "schema": RESULT_SCHEMA,
            "transaction_id": self.manifest.transaction_id,
            "controller_sha256": self.manifest.controller_sha256,
            "state": "INITIAL",
            "source_identity": "BLOCKED",
            "handoff_signal": "UNSUPPORTED",
            "handoff_within_180s": "BLOCKED",
            "restart_mode": "NOT_RUN",
            "restart_budget_consumed": 0,
            "old_cgroup_gone": "BLOCKED",
            "shutdown_cleanliness": "UNKNOWN",
            "activation_health": "BLOCKED",
            "runtime_acceptance": "BLOCKED",
            "interrupted_work_resumable": "BLOCKED",
            "overall": "BLOCKED",
            "samples": [],
        }

    def _write(self, result: dict[str, Any]) -> None:
        _atomic_json_write(self.result_path, result)

    def _load_existing(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.result_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise ControllerBlocked("existing controller result is unreadable") from exc
        if not isinstance(raw, dict) or raw.get("schema") != RESULT_SCHEMA:
            raise ControllerBlocked("existing controller result has the wrong schema")
        if raw.get("transaction_id") != self.manifest.transaction_id:
            raise ControllerBlocked("evidence root belongs to another transaction")
        return raw

    def _assert_authorized(self) -> None:
        if self.ops.wall_now().astimezone(timezone.utc) >= self.manifest.expires_at:
            raise ControllerBlocked("authorization expired before mutation")

    def _assert_source(self, actual: SourceIdentity) -> None:
        expected = self.manifest.expected_source
        if (
            actual.head != expected.head
            or actual.tree != expected.tree
            or actual.branch != expected.branch
            or actual.remote_head != expected.remote_head
            or (self.manifest.require_clean and not actual.clean)
        ):
            raise ControllerBlocked("source identity drift")

    def _assert_old_service(self, actual: ServiceIdentity) -> None:
        if (
            actual.pid != self.manifest.old_pid
            or actual.invocation_id != self.manifest.old_invocation_id
            or actual.proc_start_ticks != self.manifest.old_proc_start_ticks
            or actual.control_group != self.manifest.old_control_group
            or actual.active_state != "active"
        ):
            raise ControllerBlocked("service identity drift")
        if actual.kill_mode not in {"control-group", "mixed"}:
            raise ControllerBlocked("service KillMode is not cgroup-safe")

    @staticmethod
    def _is_replacement(old: ServiceIdentity, current: ServiceIdentity) -> bool:
        return (
            current.pid > 0
            and current.active_state == "active"
            and current.sub_state == "running"
            and (
                current.pid != old.pid
                or current.invocation_id != old.invocation_id
                or current.proc_start_ticks != old.proc_start_ticks
            )
        )

    def _verify_replacement(
        self,
        result: dict[str, Any],
        old: ServiceIdentity,
        *,
        allow_start: bool,
    ) -> dict[str, Any]:
        deadline = self.ops.monotonic() + self.manifest.replacement_wait_seconds
        current = self.ops.service_identity(self.manifest)
        while not self._is_replacement(old, current) and self.ops.monotonic() < deadline:
            self.ops.sleep(1)
            current = self.ops.service_identity(self.manifest)
        if not self._is_replacement(old, current) and allow_start:
            self.ops.start(self.manifest)
            deadline = self.ops.monotonic() + self.manifest.replacement_wait_seconds
            current = self.ops.service_identity(self.manifest)
            while not self._is_replacement(old, current) and self.ops.monotonic() < deadline:
                self.ops.sleep(1)
                current = self.ops.service_identity(self.manifest)
        if not self._is_replacement(old, current):
            result.update(state="ACTIVATION_FAILED", activation_health="FAIL", overall="FAIL")
            self._write(result)
            raise ControllerFailed("replacement gateway generation not observed")
        result["new_service"] = current.to_mapping()
        result["old_cgroup_gone"] = "PASS"
        if not self.ops.health_check(self.manifest, current):
            result.update(state="HEALTH_FAILED", activation_health="FAIL", overall="FAIL")
            self._write(result)
            raise ControllerFailed("replacement gateway failed health check")
        result.update(
            state="VERIFIED",
            activation_health="PASS",
            runtime_acceptance="PASS",
            interrupted_work_resumable="PASS",
            overall="PASS",
        )
        self._write(result)
        return result

    def _recover_committed(self, existing: dict[str, Any]) -> dict[str, Any]:
        old_raw = existing.get("old_service")
        if not isinstance(old_raw, Mapping):
            raise ControllerBlocked("committed result lacks old service identity")
        old = ServiceIdentity.from_mapping(old_raw)
        actual_source = self.ops.source_identity(self.manifest)
        self._assert_source(actual_source)
        current = self.ops.service_identity(self.manifest)
        if not self._is_replacement(old, current):
            raise ControllerBlocked("restart budget consumed but replacement is not proven")
        result = dict(existing)
        result["recovered_after_restart_commit"] = True
        result["source_identity"] = "PASS"
        return self._verify_replacement(result, old, allow_start=False)

    def run(self, *, execute: bool) -> dict[str, Any]:
        existing = self._load_existing()
        if existing and int(existing.get("restart_budget_consumed", 0)) >= 1:
            return self._recover_committed(existing)
        if existing and existing.get("state") not in {
            "PREFLIGHT_PASS",
            "HANDOFF",
            "PREPARED",
        }:
            raise ControllerBlocked("transaction evidence is not safely resumable")

        result = self._base_result()
        try:
            self._assert_authorized()
            source = self.ops.source_identity(self.manifest)
            self._assert_source(source)
            old = self.ops.service_identity(self.manifest)
            self._assert_old_service(old)
            result.update(
                state="PREFLIGHT_PASS",
                source_identity="PASS",
                source=source.to_mapping(),
                old_service=old.to_mapping(),
            )
            self._write(result)
            if not execute:
                return result

            drain = self.ops.acquire_drain(self.manifest)
            drain_written = False
            try:
                drain.write_request()
                drain_written = True
                result["state"] = "HANDOFF"
                result["handoff_started_at"] = self.ops.wall_now().isoformat()
                self._write(result)
                start = self.ops.monotonic()
                deadline = start + self.manifest.handoff_deadline_seconds
                stable = 0
                last = Occupancy()
                while True:
                    last = self.ops.sample_occupancy(self.manifest)
                    elapsed = min(
                        self.manifest.handoff_deadline_seconds,
                        max(0.0, self.ops.monotonic() - start),
                    )
                    result["samples"].append(
                        {
                            "elapsed_seconds": elapsed,
                            **last.to_mapping(),
                        }
                    )
                    self._write(result)
                    if last.quiet_for(old.pid):
                        stable += 1
                        if stable >= self.manifest.stable_zero_samples:
                            mode = "GRACEFUL"
                            break
                    else:
                        stable = 0
                    if self.ops.monotonic() >= deadline:
                        mode = "FORCED"
                        break
                    drain.refresh_request()
                    self.ops.sleep(
                        min(
                            self.manifest.sample_interval_seconds,
                            deadline - self.ops.monotonic(),
                        )
                    )

                self._assert_authorized()
                final_source = self.ops.source_identity(self.manifest)
                self._assert_source(final_source)
                final_old = self.ops.service_identity(self.manifest)
                self._assert_old_service(final_old)
                result["remaining_occupancy"] = last.to_mapping()
                result["restart_mode"] = mode
                result["handoff_within_180s"] = "PASS" if mode == "GRACEFUL" else "TIMEOUT"
                result["shutdown_cleanliness"] = "CLEAN" if mode == "GRACEFUL" else "UNCLEAN"
                result["state"] = "RESTART_COMMITTED"
                result["restart_budget_consumed"] = 1
                self._write(result)

                if mode == "GRACEFUL":
                    self.ops.graceful_restart(self.manifest)
                else:
                    self.ops.prepare_interruption(self.manifest, last)
                    self.ops.force_kill(self.manifest, final_old)
                result = self._verify_replacement(
                    result,
                    final_old,
                    allow_start=(mode == "FORCED"),
                )
                if drain_written:
                    drain.clear_request()
                    drain_written = False
                return result
            finally:
                # Before the restart commit, cancellation is safe and restores
                # admission.  After commit, preserve the marker unless the new
                # generation was verified and the owner cleared it above.
                if drain_written and result.get("restart_budget_consumed", 0) == 0:
                    try:
                        drain.clear_request()
                    except Exception:
                        pass
                drain.release()
        except (ControllerBlocked, ControllerFailed):
            raise
        except Exception as exc:
            result.update(state="FAILED", overall="FAIL", error=f"{type(exc).__name__}: {exc}")
            self._write(result)
            raise


class SystemdOperations:
    """Linux user-systemd and local-filesystem implementation."""

    def __init__(self) -> None:
        if sys.platform != "linux":
            raise ControllerBlocked("bounded restart v1 requires Linux")

    def wall_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    @staticmethod
    def _run(command: list[str], *, timeout: float = 10, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise ControllerBlocked(
                f"command failed ({result.returncode}): {' '.join(command)}: {result.stderr.strip()}"
            )
        return result

    def _systemctl(self, manifest: Manifest) -> list[str]:
        if manifest.service_scope != "user":
            raise ControllerBlocked("bounded restart v1 supports only user systemd units")
        return ["systemctl", "--user"]

    @staticmethod
    def _proc_start_ticks(pid: int) -> str:
        if pid <= 0:
            return ""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            return stat.rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return ""

    def source_identity(self, manifest: Manifest) -> SourceIdentity:
        repo = manifest.repo
        def git(*args: str) -> str:
            return self._run(["git", "-C", str(repo), *args], timeout=30).stdout.strip()

        remote_output = self._run(
            ["git", "-C", str(repo), "ls-remote", manifest.remote, manifest.remote_ref],
            timeout=30,
        ).stdout.strip().splitlines()
        exact = [line.split() for line in remote_output if len(line.split()) == 2 and line.split()[1] == manifest.remote_ref]
        if len(exact) != 1:
            raise ControllerBlocked("remote ref did not resolve to exactly one object")
        return SourceIdentity(
            head=git("rev-parse", "HEAD"),
            tree=git("rev-parse", "HEAD^{tree}"),
            branch=git("branch", "--show-current"),
            remote_head=exact[0][0],
            clean=not bool(git("status", "--porcelain=v1")),
        )

    def service_identity(self, manifest: Manifest) -> ServiceIdentity:
        properties = [
            "MainPID",
            "InvocationID",
            "ActiveState",
            "SubState",
            "ControlGroup",
            "Restart",
            "KillMode",
        ]
        command = self._systemctl(manifest) + [
            "show",
            manifest.service_unit,
            *[f"--property={name}" for name in properties],
            "--no-pager",
        ]
        result = self._run(command)
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        try:
            pid = int(values.get("MainPID", "0"))
        except ValueError:
            pid = 0
        return ServiceIdentity(
            pid=pid,
            invocation_id=values.get("InvocationID", ""),
            proc_start_ticks=self._proc_start_ticks(pid),
            active_state=values.get("ActiveState", ""),
            sub_state=values.get("SubState", ""),
            control_group=values.get("ControlGroup", ""),
            restart_policy=values.get("Restart", ""),
            kill_mode=values.get("KillMode", ""),
        )

    def acquire_drain(self, manifest: Manifest) -> Any:
        from gateway.drain_control import acquire_drain_ownership

        return acquire_drain_ownership(
            principal="bounded-handoff-force-restart",
            home=manifest.hermes_home,
            suppress_notification=False,
            owner_token=manifest.transaction_id,
        )

    @staticmethod
    def _count_running_cron(home: Path) -> int:
        database = home / "cron" / "executions.db"
        if not database.is_file():
            return 0
        try:
            uri = f"file:{database}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM executions "
                    "WHERE status IN ('claimed', 'running')"
                ).fetchone()
            return int(row[0]) if row else 0
        except (sqlite3.Error, OSError) as exc:
            raise ControllerBlocked("cannot read cron running executions") from exc

    @staticmethod
    def _cgroup_pids(control_group: str) -> tuple[int, ...]:
        if not control_group.startswith("/") or ".." in Path(control_group).parts:
            raise ControllerBlocked("unsafe systemd ControlGroup path")
        path = Path("/sys/fs/cgroup") / control_group.lstrip("/") / "cgroup.procs"
        try:
            return tuple(sorted(int(line) for line in path.read_text(encoding="utf-8").splitlines()))
        except FileNotFoundError:
            return ()
        except (OSError, ValueError) as exc:
            raise ControllerBlocked("cannot read service cgroup membership") from exc

    def sample_occupancy(self, manifest: Manifest) -> Occupancy:
        home = manifest.hermes_home
        active_agents = 0
        state_path = home / "gateway_state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                active_agents = int(state.get("active_agents", 0) or 0)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ControllerBlocked("cannot read gateway occupancy") from exc
        service = self.service_identity(manifest)
        return Occupancy(
            active_agents=active_agents,
            cron_runs=self._count_running_cron(home),
            cgroup_pids=self._cgroup_pids(service.control_group),
        )

    def graceful_restart(self, manifest: Manifest) -> None:
        self._run(self._systemctl(manifest) + ["restart", manifest.service_unit], timeout=30)

    def prepare_interruption(self, manifest: Manifest, occupancy: Occupancy) -> None:
        # This receipt is intentionally written before SIGKILL.  The current
        # gateway has no steering verb, so handoff_signal remains UNSUPPORTED;
        # durable session state plus the replacement's session-store health are
        # the resumability proof available in controller v1.
        _atomic_json_write(
            manifest.evidence_root / "interruption-intent.json",
            {
                "schema": "hermes-bounded-handoff-interruption/v1",
                "transaction_id": manifest.transaction_id,
                "prepared_at": self.wall_now().isoformat(),
                "occupancy": occupancy.to_mapping(),
                "handoff_signal": "UNSUPPORTED",
            },
        )

    def force_kill(self, manifest: Manifest, old: ServiceIdentity) -> None:
        current = self.service_identity(manifest)
        if current.pid != old.pid or current.proc_start_ticks != old.proc_start_ticks:
            raise ControllerBlocked("service generation changed before force kill")
        self._run(
            self._systemctl(manifest)
            + ["kill", "--kill-whom=all", "--signal=SIGKILL", manifest.service_unit],
            timeout=30,
        )

    def start(self, manifest: Manifest) -> None:
        self._run(self._systemctl(manifest) + ["start", manifest.service_unit], timeout=30)

    def health_check(self, manifest: Manifest, service: ServiceIdentity) -> bool:
        try:
            with urllib.request.urlopen(manifest.health_url, timeout=5) as response:
                body = response.read(1024 * 1024)
                if response.status != 200:
                    return False
            payload = json.loads(body)
            return isinstance(payload, dict) and payload.get("status") == "ok"
        except Exception:
            return False


def _load_manifest(path: Path, *, now: datetime | None = None) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read manifest: {exc}") from exc
    return Manifest.from_mapping(raw, controller_path=Path(__file__).resolve(), now=now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--execute", action="store_true", help="perform the authorized drain/restart")
    parser.add_argument("--print-controller-sha256", action="store_true")
    args = parser.parse_args(argv)
    if args.print_controller_sha256:
        print(_sha256(Path(__file__).resolve()))
        return 0
    if args.manifest is None:
        parser.error("--manifest is required unless --print-controller-sha256 is used")
    try:
        manifest = _load_manifest(args.manifest)
        result = BoundedRestartController(manifest, SystemdOperations()).run(execute=args.execute)
    except (ValueError, ControllerBlocked, ControllerFailed) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
