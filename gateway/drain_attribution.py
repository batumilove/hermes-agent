"""Bounded, generation-scoped gateway drain attribution evidence.

Each snapshot is one compact JSONL record written to a per-process-generation
file under ``<HERMES_HOME>/state/gateway-drain-attribution``.  The writer is
independent of SessionDB/LCM so their lock contention cannot erase the only
identity evidence available before a forced shutdown.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


@dataclass(frozen=True)
class DrainGeneration:
    pid: int
    process_start_ticks: str
    invocation_id: str
    instantiation_epoch: str


def _process_start_ticks(pid: int) -> str:
    try:
        stat_text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        return stat_text.rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return ""


def current_drain_generation() -> DrainGeneration:
    try:
        from gateway.drain_control import current_instantiation_epoch

        epoch = current_instantiation_epoch()
    except Exception:
        epoch = ""
    return DrainGeneration(
        pid=os.getpid(),
        process_start_ticks=_process_start_ticks(os.getpid()),
        invocation_id=str(os.environ.get("INVOCATION_ID", "")),
        instantiation_epoch=epoch,
    )


def generation_is_current(generation: DrainGeneration, *, home: Path) -> bool:
    if generation.pid != os.getpid():
        return False
    current_ticks = _process_start_ticks(os.getpid())
    if generation.process_start_ticks and current_ticks != generation.process_start_ticks:
        return False
    current_invocation = str(os.environ.get("INVOCATION_ID", ""))
    if generation.invocation_id and current_invocation != generation.invocation_id:
        return False
    if generation.instantiation_epoch:
        try:
            from gateway.drain_control import current_instantiation_epoch

            if current_instantiation_epoch() != generation.instantiation_epoch:
                return False
        except Exception:
            return False
    lifecycle_path = Path(home) / "state" / "gateway.lifecycle.json"
    try:
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lifecycle = None
    if isinstance(lifecycle, dict) and lifecycle.get("phase") == "running":
        try:
            if int(lifecycle.get("pid")) != generation.pid:
                return False
        except (TypeError, ValueError):
            return False
    return True


@dataclass(frozen=True)
class DrainAttributionWriteResult:
    status: str
    sequence: int | None = None
    path: Path | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class GatewayWorkSnapshot:
    counts: dict[str, int]
    units: tuple[dict[str, Any], ...]
    attribution_complete: bool
    omissions: tuple[str, ...]


def _default_cron_provider() -> Sequence[Mapping[str, Any]]:
    try:
        from cron.scheduler import get_running_job_attribution

        return get_running_job_attribution()
    except (ImportError, AttributeError):
        try:
            from cron.scheduler import get_running_job_ids

            return tuple(
                {"job_id": job_id, "execution_id": None, "phase": "running"}
                for job_id in sorted(get_running_job_ids())
            )
        except Exception:
            return ()


def collect_gateway_work(
    runner: Any,
    *,
    pending_sentinel: Any,
    cron_provider: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
) -> GatewayWorkSnapshot:
    """Collect one identity record for every unit counted by gateway drain."""

    units: list[dict[str, Any]] = []
    omissions: list[str] = []
    running_agents = list(getattr(runner, "_running_agents", {}).items())
    running_ts = getattr(runner, "_running_agents_ts", {})
    generations = getattr(runner, "_session_run_generation", {})
    for session_key, agent in running_agents:
        pending = agent is pending_sentinel
        unit: dict[str, Any] = {
            "unit_id": f"agent:{session_key}",
            "category": "agent",
            "session_key": str(session_key),
            "phase": "pending_agent_creation" if pending else "active",
            "work_class": "user_turn_admission" if pending else "user_turn",
            "drain_blocking": True,
        }
        if session_key in running_ts:
            unit["started_monotonic"] = running_ts[session_key]
        if session_key in generations:
            unit["run_generation"] = generations[session_key]
        if not pending:
            session_id = getattr(agent, "session_id", None)
            if session_id:
                unit["session_id"] = str(session_id)
        units.append(unit)

    provider = cron_provider or _default_cron_provider
    try:
        cron_records = list(provider())
    except Exception as exc:
        cron_records = []
        omissions.append(f"cron_snapshot_failed:{type(exc).__name__}")
    for record in cron_records:
        job_id = str(record.get("job_id") or "unknown")
        execution_id = record.get("execution_id")
        if not execution_id:
            omissions.append(f"cron_execution_id_unavailable:{job_id}"[:128])
        units.append(
            {
                "unit_id": (
                    f"cron:{job_id}:{execution_id}"
                    if execution_id
                    else f"cron:{job_id}:unknown"
                ),
                "category": "cron",
                "job_id": job_id,
                "execution_id": str(execution_id) if execution_id else None,
                "phase": str(record.get("phase") or "running"),
                "work_class": "scheduled_job",
                "drain_blocking": True,
            }
        )

    api_count = 0
    api_snapshot_complete = True
    adapters = getattr(runner, "adapters", {})
    for adapter in getattr(adapters, "values", lambda: ())():
        helper = getattr(adapter, "active_agent_work_snapshot", None)
        if not callable(helper):
            continue
        try:
            snapshot = helper()
            api_count = max(0, int(snapshot.get("count", 0)))
            api_units = [dict(unit) for unit in snapshot.get("units", ())]
            for unit in api_units:
                unit.setdefault("drain_blocking", True)
                if "run_id" in unit:
                    unit.setdefault("work_class", "api_run")
                elif "request_id" in unit:
                    unit.setdefault("work_class", "api_request_admission")
                else:
                    unit.setdefault("work_class", "api_turn")
            units.extend(api_units)
            api_snapshot_complete = bool(snapshot.get("attribution_complete", False))
            omissions.extend(str(item)[:128] for item in snapshot.get("omissions", ()))
        except Exception as exc:
            api_snapshot_complete = False
            omissions.append(f"api_snapshot_failed:{type(exc).__name__}")
        break

    counts = {
        "agent": len(running_agents),
        "cron": len(cron_records),
        "api": api_count,
    }
    counts["total"] = sum(counts.values())
    unit_ids = [str(unit.get("unit_id") or "") for unit in units]
    if len(units) != counts["total"]:
        omissions.append(f"unit_count_mismatch:{len(units)}:{counts['total']}")
    if len(set(unit_ids)) != len(unit_ids):
        omissions.append("duplicate_unit_ids")
    complete = api_snapshot_complete and not omissions
    return GatewayWorkSnapshot(
        counts=counts,
        units=tuple(units),
        attribution_complete=complete,
        omissions=tuple(omissions),
    )


class DrainAttributionRecorder:
    """Append fsync'd attribution snapshots for exactly one gateway life."""

    def __init__(
        self,
        *,
        home: Path,
        generation: DrainGeneration,
        owner_probe: Callable[[DrainGeneration], bool],
        max_units: int = 256,
    ) -> None:
        self.home = Path(home)
        self.generation = generation
        self._owner_probe = owner_probe
        self._max_units = max(1, int(max_units))
        self._sequence = 0
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        identity = json.dumps(
            asdict(self.generation), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        suffix = hashlib.sha256(identity).hexdigest()[:16]
        return (
            self.home
            / "state"
            / "gateway-drain-attribution"
            / f"gateway-{self.generation.pid}-{suffix}.jsonl"
        )

    def _last_persisted_sequence(self, path: Path) -> int:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 65536), os.SEEK_SET)
                tail = handle.read()
        except OSError:
            return 0
        expected_generation = asdict(self.generation)
        for raw_line in reversed(tail.splitlines()):
            try:
                payload = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if payload.get("generation") != expected_generation:
                continue
            try:
                return max(0, int(payload.get("sequence", 0)))
            except (TypeError, ValueError):
                continue
        return 0

    def record(
        self,
        *,
        phase: str,
        counts: Mapping[str, int],
        units: Sequence[Mapping[str, Any]],
        attribution_complete: bool = True,
        omissions: Sequence[str] = (),
    ) -> DrainAttributionWriteResult:
        path = self.path
        with self._lock, _path_lock(path):
            if not self._owner_probe(self.generation):
                return DrainAttributionWriteResult(status="stale_generation")
            self._sequence = max(
                self._sequence,
                self._last_persisted_sequence(path),
            ) + 1
            normalized_counts = {
                "agent": max(0, int(counts.get("agent", 0))),
                "cron": max(0, int(counts.get("cron", 0))),
                "api": max(0, int(counts.get("api", 0))),
            }
            normalized_counts["total"] = sum(normalized_counts.values())
            normalized_units = [dict(unit) for unit in units[: self._max_units]]
            effective_omissions = [str(item)[:128] for item in omissions]
            truncated = max(0, len(units) - len(normalized_units))
            if truncated:
                effective_omissions.append(f"units_truncated:{truncated}")
            payload: dict[str, Any] = {
                "schema_version": 1,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "generation": asdict(self.generation),
                "sequence": self._sequence,
                "phase": str(phase)[:64],
                "counts": normalized_counts,
                "units": normalized_units,
                "attribution_complete": bool(attribution_complete) and not effective_omissions,
                "omissions": effective_omissions,
            }
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
            payload["record_sha256"] = digest
            line = (
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
                + "\n"
            ).encode("utf-8")
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                try:
                    os.fchmod(fd, 0o600)
                    written = 0
                    while written < len(line):
                        written += os.write(fd, line[written:])
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                return DrainAttributionWriteResult(
                    status="attribution_incomplete",
                    sequence=self._sequence,
                    path=path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            return DrainAttributionWriteResult(
                status="persisted",
                sequence=self._sequence,
                path=path,
                sha256=digest,
            )


async def record_snapshot_bounded(
    recorder: Any,
    *,
    timeout_seconds: float,
    **record_kwargs: Any,
) -> DrainAttributionWriteResult:
    """Run one evidence fsync off-loop without extending shutdown's deadline."""

    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    result_box: list[DrainAttributionWriteResult] = []

    def _write() -> None:
        try:
            result_box.append(recorder.record(**record_kwargs))
        except BaseException as exc:
            result_box.append(
                DrainAttributionWriteResult(
                    status="attribution_incomplete",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            try:
                loop.call_soon_threadsafe(done.set)
            except RuntimeError:
                # The bounded caller may have returned and its loop may close
                # while a timed-out daemon writer finishes. Evidence may still
                # persist; never turn that late completion into a thread crash.
                pass

    worker = threading.Thread(
        target=_write,
        name="gateway-drain-attribution-write",
        daemon=True,
    )
    worker.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=max(0.001, float(timeout_seconds)))
    except TimeoutError:
        return DrainAttributionWriteResult(
            status="attribution_incomplete",
            error="write_timeout",
        )
    return result_box[0]
