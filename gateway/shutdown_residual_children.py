"""Bounded cgroup-v2 evidence at the gateway's final runtime-identity boundary.

Only process identity and executable basename are persisted. This collector
never terminates a process or changes runtime state; its caller bounds the
wall-clock budget off the event loop.
"""
from __future__ import annotations

import logging
import os
import time
from itertools import islice
from pathlib import Path
from typing import Any, Optional

from gateway.shutdown_forensics import _read_proc_field

logger = logging.getLogger(__name__)


def _default_gateway_remaining_pids() -> list[int]:
    """Discover remaining processes in our own cgroup (Linux only).

    Bounded discovery: derives the unified (cgroup-v2) path from the
    ``0::/path`` entry of ``/proc/self/cgroup`` and performs ONE direct
    read of ``/sys/fs/cgroup/<path>/cgroup.procs``.  No global /proc
    enumeration, no subprocesses.  Pure stdlib, best-effort, never
    raises.  Returns ``[]`` on non-v2 systems or any read failure.
    """
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            lines = fh.read(4096).splitlines()
    except (OSError, ValueError):
        return []
    v2_path = ""
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "" and parts[2]:
            v2_path = parts[2]
            break
    if not v2_path:
        # No unified (v2) membership → bounded discovery unavailable.
        return []
    try:
        with open(
            f"/sys/fs/cgroup{v2_path}/cgroup.procs", encoding="utf-8"
        ) as fh:
            procs_text = fh.read(4096)
    except (OSError, ValueError):
        return []
    if len(procs_text) == 4096 and not procs_text.endswith("\n"):
        procs_text = procs_text.rsplit("\n", 1)[0]
    pids: list[int] = []
    for token in procs_text.split():
        if token.isdigit():
            pids.append(int(token))
    return pids


def _registry_registered_pids() -> Optional[set]:
    """PIDs currently registered in ``tools.process_registry`` (guarded import).

    Returns ``None`` when the registry can't be consulted (import failure,
    unexpected API shape, or an internal error) — callers treat that as
    "unknown" rather than "not registered".  Never raises, never blocks on
    subprocesses: it only reads the in-memory session table.
    """
    try:  # guarded local import — never at module import time
        from tools.process_registry import process_registry
    except Exception:
        logger.debug("Residual process registry lookup failed", exc_info=True)
        return None
    try:
        sessions = process_registry.list_sessions()
    except Exception:
        logger.debug("Residual process registry lookup failed", exc_info=True)
        return None
    pids = set()
    try:
        for session in sessions or []:
            pid = getattr(session, "pid", None)
            if isinstance(pid, int) and pid > 0:
                pids.add(pid)
    except Exception:
        logger.debug("Residual process registry lookup failed", exc_info=True)
        return None
    return pids


def _redacted_command_description(pid: int) -> Optional[str]:
    """Privacy-safe command descriptor for a residual pid.

    Returns the *executable basename* only — never raw argv, arguments,
    environment, or full paths (an executable path could embed a username
    or user-content directory).  ``[redacted]`` when even the basename
    can't be safely determined.  Never raises.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            data = fh.read(4096)
    except (OSError, ValueError):
        data = b""
    if data:
        executable = data.split(b"\x00", 1)[0]
        if executable:
            basename = os.path.basename(executable.decode("utf-8", errors="replace"))
            # Squeeze whitespace so an executable name containing spaces can
            # never smuggle what looks like raw argv into the record.
            basename = "".join(basename.split())
            if basename:
                return basename[:64]
    # Kernel threads / gone pids: fall back to the Name field or opaque marker.
    name = _read_proc_field(pid, "Name")
    if name:
        return name[:64]
    return "[redacted]"


def _process_start_ticks(pid: int) -> int | None:
    """Bounded Linux stat read; field22 follows the parenthesized command."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read(4096).rsplit(")", 1)[1].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def _residual_process_record(pid: int, registered: set | None) -> dict[str, Any]:
    record: dict[str, Any] = {"pid": pid}
    ppid = _read_proc_field(pid, "PPid")
    if ppid is not None:
        try:
            record["ppid"] = int(ppid)
        except ValueError:
            logger.debug("Residual process parent ID is invalid: %s", pid)
    for key, limit in (("name", 64), ("state", 32)):
        value = _read_proc_field(pid, key.title())
        if value is not None:
            record[key] = value[:limit]
    start = _process_start_ticks(pid)
    if start is not None:
        record["start_ticks"] = start
    command = _redacted_command_description(pid)
    if command is not None:
        record["command"] = command
    if registered is not None:
        record["registered"] = pid in registered
    return record


def _registered_pids(probe) -> set | None:
    try:
        return probe()
    except Exception:
        logger.debug("Residual process registry probe failed", exc_info=True)
        return None


def capture_residual_children(
    *,
    phase: str,
    deadline_remaining: float,
    remaining_pids_fn=None,
    registered_pids_fn=None,
    artifact_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Write at most 64 residual members, atomically, under the owning profile.

    Discovery reads the current cgroup-v2 membership file once and excludes
    the gateway itself. Registry failure is unknown membership, not absence.
    Any discovery/persistence error returns None so shutdown can continue.
    The caller enforces its remaining aggregate deadline off the event loop.
    """
    try:
        self_pid = os.getpid()
        discover = remaining_pids_fn or _default_gateway_remaining_pids
        residual = [pid for pid in islice(discover() or [], 65) if isinstance(pid, int) and pid != self_pid]
        if not residual:
            return None
        registered = _registered_pids(registered_pids_fn or _registry_registered_pids)
        children = [_residual_process_record(pid, registered) for pid in residual[:64]]
        if artifact_path is None:
            from hermes_constants import get_hermes_home
            artifact_path = get_hermes_home() / "logs" / "gateway-shutdown-children.json"
        snapshot = {
            "ts": time.time(), "phase": str(phase)[:64],
            "deadline_remaining_s": deadline_remaining, "self_pid": self_pid, "children": children,
        }
        from utils import atomic_json_write
        atomic_json_write(artifact_path, snapshot, default=str)
        return snapshot
    except Exception:
        logger.debug("Residual shutdown evidence unavailable", exc_info=True)
        return None
