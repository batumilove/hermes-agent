"""Shutdown forensics — capture context when the gateway receives SIGTERM/SIGINT.

The gateway's ``shutdown_signal_handler`` runs synchronously inside the
asyncio event loop.  We can't safely block it for long, but we DO want a
durable record of who/what triggered the shutdown so that "the gateway
keeps dying" incidents can be diagnosed after the fact.

This module exposes :func:`snapshot_shutdown_context`, a fast (<10ms),
non-blocking probe that returns a structured dict the signal handler can
log immediately, plus :func:`spawn_async_diagnostic`, a fire-and-forget
``ps`` walk that runs as a detached subprocess so it can't block teardown
even if /proc is wedged.

Anything that needs to wait (e.g. shelling out to ``ps aux``) belongs in
the async helper, never in the synchronous probe.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


_SIGNAL_NAME_BY_NUM: Dict[int, str] = {}
for _name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGQUIT", "SIGUSR1", "SIGUSR2"):
    _val = getattr(signal, _name, None)
    if _val is not None:
        _SIGNAL_NAME_BY_NUM[int(_val)] = _name


def _signal_name(sig: Any) -> str:
    """Return a human-readable signal name (or ``str(sig)`` as fallback)."""
    if sig is None:
        return "UNKNOWN"
    try:
        sig_int = int(sig)
    except (TypeError, ValueError):
        return str(sig)
    return _SIGNAL_NAME_BY_NUM.get(sig_int, f"signal#{sig_int}")


def _read_proc_field(pid: int, key: str) -> Optional[str]:
    """Read a single field from /proc/<pid>/status.  Linux only; None elsewhere."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    return line.split(":", 1)[1].strip()
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return None


def _read_proc_cmdline(pid: int) -> Optional[str]:
    """Read /proc/<pid>/cmdline as a printable string.  Linux only; None elsewhere."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            data = fh.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if not data:
        return None
    # cmdline uses NUL separators
    return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _proc_summary(pid: int) -> Dict[str, Any]:
    """Compact /proc/<pid> snapshot: pid, ppid, state, uid, cmdline.

    Best-effort.  Missing fields are simply omitted rather than raising.
    """
    summary: Dict[str, Any] = {"pid": pid}
    if pid <= 0:
        return summary
    name = _read_proc_field(pid, "Name")
    if name is not None:
        summary["name"] = name
    state = _read_proc_field(pid, "State")
    if state is not None:
        summary["state"] = state
    ppid = _read_proc_field(pid, "PPid")
    if ppid is not None:
        try:
            summary["ppid"] = int(ppid)
        except ValueError:
            pass
    uid = _read_proc_field(pid, "Uid")
    if uid is not None:
        # "real effective saved fs"
        summary["uid"] = uid.split()[0] if uid else uid
    cmdline = _read_proc_cmdline(pid)
    if cmdline:
        # Truncate aggressively — these can be 4KB
        summary["cmdline"] = cmdline[:300]
    return summary


def snapshot_shutdown_context(received_signal: Any = None) -> Dict[str, Any]:
    """Fast (<10ms) snapshot of who/what is asking us to shut down.

    Captures:

    * The signal number/name (so SIGINT vs SIGTERM is visible)
    * Our own PID/ppid + parent process info from /proc (Linux)
    * Whether systemd is our parent (``ppid==1`` or ``INVOCATION_ID`` set)
    * Whether takeover/planned-stop markers exist (consumed lazily by the caller)
    * /proc/self limits + load average (1-min)
    * Wall-clock and monotonic timestamps for cross-correlating later phases

    Pure stdlib, never raises, never blocks on subprocesses.
    """
    now = time.time()
    monotonic = time.monotonic()
    pid = os.getpid()
    ppid = os.getppid()

    ctx: Dict[str, Any] = {
        "ts": now,
        "ts_monotonic": monotonic,
        "signal": _signal_name(received_signal),
        "signal_num": int(received_signal) if received_signal is not None else None,
        "pid": pid,
        "ppid": ppid,
        "parent": _proc_summary(ppid),
        "self": _proc_summary(pid),
    }

    # systemd context.  If we were started by a systemd unit, INVOCATION_ID
    # is set in our env.  ppid==1 (init) is also a strong signal that
    # systemd reaped+forwarded the SIGTERM.
    invocation_id = os.environ.get("INVOCATION_ID")
    if invocation_id:
        ctx["systemd_invocation_id"] = invocation_id
    journal_stream = os.environ.get("JOURNAL_STREAM")
    if journal_stream:
        ctx["systemd_journal_stream"] = journal_stream
    ctx["under_systemd"] = bool(invocation_id) or ppid == 1

    # Load average — high load points the finger at "something else
    # crushing the box" rather than "external killer".
    try:
        ctx["loadavg_1m"] = os.getloadavg()[0]
    except (OSError, AttributeError):
        pass

    # /proc/self/status TracerPid: nonzero means a debugger / strace is
    # attached.  Useful when "phantom SIGKILL" turns out to be a manual
    # gdb session.
    try:
        tracer = _read_proc_field(pid, "TracerPid")
        if tracer is not None and tracer != "0":
            ctx["tracer_pid"] = int(tracer) if tracer.isdigit() else tracer
            ctx["tracer"] = _proc_summary(int(tracer)) if tracer.isdigit() else None
    except (TypeError, ValueError):
        pass

    # Race-detection hint: did somebody recently start a sibling gateway
    # with --replace?  We can't see the new process directly here, but if
    # there's a takeover marker on disk that DOESN'T name us, that's a
    # smoking gun for "another --replace instance is killing us".
    # Filenames mirror gateway.status (._TAKEOVER_MARKER_FILENAME /
    # _PLANNED_STOP_MARKER_FILENAME); we use string literals here so the
    # signal-handler path stays import-light.
    try:
        hermes_home_str = os.environ.get("HERMES_HOME")
        if hermes_home_str:
            takeover_path = Path(hermes_home_str) / ".gateway-takeover.json"
            if takeover_path.exists():
                try:
                    raw = takeover_path.read_text(encoding="utf-8")
                    ctx["takeover_marker"] = raw[:300]
                    ctx["takeover_marker_for_self"] = (
                        f'"target_pid": {pid}' in raw
                        or f"'target_pid': {pid}" in raw
                    )
                except OSError:
                    pass
            planned_stop_path = Path(hermes_home_str) / ".gateway-planned-stop.json"
            if planned_stop_path.exists():
                try:
                    raw = planned_stop_path.read_text(encoding="utf-8")
                    ctx["planned_stop_marker"] = raw[:300]
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 — never raise from a signal handler
        pass

    return ctx


def spawn_async_diagnostic(
    log_path: Path,
    signal_name: str,
    *,
    timeout_seconds: float = 5.0,
) -> Optional[int]:
    """Fire-and-forget ``ps``-style snapshot written to ``log_path``.

    Runs as a detached subprocess so it can't block the asyncio event loop
    or compete with platform teardown.  The subprocess uses its own
    ``timeout`` so a wedged ``ps`` still self-cleans within
    ``timeout_seconds``.

    Returns the subprocess PID on success, ``None`` on failure.  Never
    raises.

    We deliberately avoid ``subprocess.run(["ps", "aux"])`` from inside the
    signal handler (the pre-existing pattern): on a busy host with hundreds
    of processes, ``ps aux`` can take >2s to walk /proc, during which the
    asyncio loop is frozen and adapter teardown can't begin.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    # Inline shell so we don't have to ship a helper script.  bash -c is
    # available on every POSIX target we support; on Windows we just skip
    # the snapshot (the platform doesn't ship ps anyway).
    if sys.platform == "win32":
        return None

    script = (
        f"echo '=== shutdown diagnostic @ {signal_name} ==='; "
        "echo '--- date ---'; date -u +%Y-%m-%dT%H:%M:%SZ; "
        "echo '--- ps auxf (top 60 by cpu) ---'; "
        "ps auxf --sort=-pcpu 2>/dev/null | head -60; "
        "echo '--- pstree of self ---'; "
        f"pstree -plau {os.getpid()} 2>/dev/null | head -40 || true; "
        "echo '--- /proc/loadavg ---'; "
        "cat /proc/loadavg 2>/dev/null || true; "
        "echo '--- recent dmesg (oom/killed) ---'; "
        "dmesg -T 2>/dev/null | tail -20 || journalctl --user -n 20 --no-pager 2>/dev/null | tail -20 || true; "
        "echo '=== end ==='"
    )

    try:
        # Open the log file in append mode and let the subprocess inherit.
        # We use os.O_APPEND so concurrent diagnostics from rapid signals
        # don't trample each other.
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        return None

    try:
        # Detach from our process group so the subprocess survives even
        # if systemd kills our cgroup with KillMode=control-group (which
        # would also reap us anyway, but defense in depth).  Without
        # start_new_session, a SIGKILL on our cgroup takes the diag down
        # before it can flush.
        proc = subprocess.Popen(
            ["timeout", f"{timeout_seconds:.0f}", "bash", "-c", script],
            stdout=fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except (FileNotFoundError, OSError):
        try:
            os.close(fd)
        except OSError:
            pass
        return None
    finally:
        # Subprocess inherited the fd; we can drop our handle.
        try:
            os.close(fd)
        except OSError:
            pass

    return proc.pid


def format_context_for_log(ctx: Dict[str, Any]) -> str:
    """Render a shutdown context dict as a single, scannable log line."""
    sig = ctx.get("signal", "?")
    parent = ctx.get("parent") or {}
    parent_cmd = parent.get("cmdline", "(unknown)")
    parent_name = parent.get("name") or "?"
    parent_pid = parent.get("pid") or "?"
    under_systemd = "yes" if ctx.get("under_systemd") else "no"
    load = ctx.get("loadavg_1m")
    load_str = f"{load:.2f}" if isinstance(load, (int, float)) else "?"
    extras: List[str] = []
    if ctx.get("takeover_marker") is not None:
        for_self = ctx.get("takeover_marker_for_self")
        extras.append(
            f"takeover_marker_present={'self' if for_self else 'other'}"
        )
    if ctx.get("planned_stop_marker") is not None:
        extras.append("planned_stop_marker_present=yes")
    if ctx.get("tracer_pid"):
        extras.append(f"tracer_pid={ctx['tracer_pid']}")
    extras_str = (" " + " ".join(extras)) if extras else ""
    # Parent cmdline is the most useful single signal — log it prominently.
    return (
        f"signal={sig} "
        f"under_systemd={under_systemd} "
        f"parent_pid={parent_pid} "
        f"parent_name={parent_name} "
        f"loadavg_1m={load_str}"
        f"{extras_str} "
        f"parent_cmdline={parent_cmd!r}"
    )


def context_as_json(ctx: Dict[str, Any]) -> str:
    """JSON-serialise a context dict for structured ingestion.  Never raises."""
    try:
        return json.dumps(ctx, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return "{}"


def _default_gateway_remaining_pids() -> List[int]:
    """Discover remaining processes in our own cgroup (Linux only).

    Bounded discovery: derives the unified (cgroup-v2) path from the
    ``0::/path`` entry of ``/proc/self/cgroup`` and performs ONE direct
    read of ``/sys/fs/cgroup/<path>/cgroup.procs``.  No global /proc
    enumeration, no subprocesses.  Pure stdlib, best-effort, never
    raises.  Returns ``[]`` on non-v2 systems or any read failure.
    """
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            lines = [line.strip() for line in fh if line.strip()]
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
            procs_text = fh.read()
    except (OSError, ValueError):
        return []
    pids: List[int] = []
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
    except Exception:  # noqa: BLE001
        return None
    try:
        sessions = process_registry.list_sessions()
    except Exception:  # noqa: BLE001
        return None
    pids = set()
    try:
        for session in sessions or []:
            pid = getattr(session, "pid", None)
            if isinstance(pid, int) and pid > 0:
                pids.add(pid)
    except Exception:  # noqa: BLE001
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
            data = fh.read()
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


def capture_residual_children(
    *,
    phase: str,
    deadline_remaining: float,
    remaining_pids_fn=None,
    registered_pids_fn=None,
    artifact_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Capture a bounded, privacy-safe snapshot of residual child processes.

    Runs at the *final* shutdown boundary — right before the runtime PID
    file / gateway lock are released — so the artifact records exactly
    which processes would be left behind in the gateway cgroup.

    Design constraints (all mandatory):

    * **Bounded & best-effort** — pure /proc reads, no subprocesses, no
      terminal calls, no blocking.  Every failure is swallowed; this must
      never delay teardown or cause it to fail.
    * **Privacy** — records only pid/ppid/name/state/start ticks plus a
      *redacted* command description (executable basename or opaque
      marker).  Never raw argv, environment, credentials, or user content.
    * **Atomic persistence** — written via ``utils.atomic_json_write``
      under ``<HERMES_HOME>/logs/gateway-shutdown-children.json`` so the
      file is never partially written.

    Args:
        phase: shutdown phase label supplied by the caller (run.py).
        deadline_remaining: seconds left in the shutdown deadline budget.
        remaining_pids_fn: injectable pid-discovery callable (defaults to
            cgroup-based discovery; used by tests).
        registered_pids_fn: injectable registry-probe callable returning
            registered pids (defaults to a guarded process_registry read;
            ``None`` return means "unknown").
        artifact_path: explicit artifact destination (defaults to
            ``$HERMES_HOME/logs/gateway-shutdown-children.json``).

    Returns:
        The written snapshot dict, or ``None`` when nothing remained, the
        environment is unsupported, or any step failed.  Never raises.
    """
    try:
        self_pid = os.getpid()
        if remaining_pids_fn is None:
            remaining_pids_fn = _default_gateway_remaining_pids
        try:
            all_pids = list(remaining_pids_fn() or [])
        except Exception:  # noqa: BLE001 — wedged /proc must not fail teardown
            return None
        residual = [pid for pid in all_pids if isinstance(pid, int) and pid != self_pid]
        if not residual:
            return None

        registered = None
        if registered_pids_fn is None:
            registered_pids_fn = _registry_registered_pids
        try:
            registered = registered_pids_fn()
        except Exception:  # noqa: BLE001
            registered = None

        children: List[Dict[str, Any]] = []
        for pid in residual[:64]:  # bounded — no unbounded pid lists
            record: Dict[str, Any] = {"pid": pid}
            ppid = _read_proc_field(pid, "PPid")
            if ppid is not None:
                try:
                    record["ppid"] = int(ppid)
                except ValueError:
                    pass
            name = _read_proc_field(pid, "Name")
            if name is not None:
                record["name"] = name[:64]
            state = _read_proc_field(pid, "State")
            if state is not None:
                record["state"] = state[:32]
            try:
                from gateway.status import get_process_start_time

                start = get_process_start_time(pid)
            except Exception:  # noqa: BLE001
                start = None
            if start is not None:
                record["start_ticks"] = start
            command = _redacted_command_description(pid)
            if command is not None:
                record["command"] = command
            if registered is not None:
                record["registered"] = pid in registered
            children.append(record)

        if not children:
            return None

        if artifact_path is None:
            hermes_home = os.environ.get("HERMES_HOME")
            if not hermes_home:
                return None
            artifact_path = Path(hermes_home) / "logs" / "gateway-shutdown-children.json"

        snapshot: Dict[str, Any] = {
            "ts": time.time(),
            "phase": str(phase)[:64],
            "deadline_remaining_s": deadline_remaining,
            "self_pid": self_pid,
            "children": children,
        }
        from utils import atomic_json_write

        atomic_json_write(artifact_path, snapshot, default=str)
        return snapshot
    except Exception:  # noqa: BLE001 — never fail teardown
        return None


def check_systemd_timing_alignment(drain_timeout: float) -> Optional[Dict[str, Any]]:
    """At startup, sanity-check that systemd's TimeoutStopSec >= drain_timeout.

    When the gateway is run under a stale systemd unit file (e.g. the user
    upgraded hermes-agent but never re-ran ``hermes setup`` to regenerate
    the unit), ``TimeoutStopSec`` can be smaller than the configured
    ``restart_drain_timeout``.  Result: SIGTERM arrives, the drain starts,
    and systemd SIGKILLs the cgroup mid-drain — looks like a phantom kill
    in the journal because the journal only logs ``code=killed status=9``.

    Returns ``None`` when the alignment is fine OR we can't determine it
    (not running under systemd, ``systemctl`` unavailable, etc.).  Returns
    a dict with ``timeout_stop_sec`` + ``drain_timeout`` + ``mismatch``
    bool when we have data to report.

    Best-effort.  Never raises.
    """
    invocation_id = os.environ.get("INVOCATION_ID")
    if not invocation_id:
        return None  # Not running under systemd (or at least not directly)

    # Try to identify our unit name and ask systemctl for its config.
    unit_name: Optional[str] = None
    try:
        # /proc/self/cgroup gives us "0::/user.slice/.../hermes-gateway.service"
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            for line in fh:
                # systemd cgroup line ends with the unit name
                if ".service" in line:
                    parts = line.strip().split("/")
                    for p in reversed(parts):
                        if p.endswith(".service"):
                            unit_name = p
                            break
                    if unit_name:
                        break
    except (OSError, FileNotFoundError):
        pass
    if not unit_name:
        return None

    # Query systemctl for TimeoutStopUSec.  Use --user OR system depending
    # on which manager actually owns the unit.  Try user first since
    # that's the common case for hermes.
    timeout_us: Optional[int] = None
    for flag in (["--user"], []):
        try:
            result = subprocess.run(
                ["systemctl", *flag, "show", unit_name, "--property=TimeoutStopUSec"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=2.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode != 0:
            continue
        # Output: "TimeoutStopUSec=1min 30s" or "TimeoutStopUSec=90000000"
        for line in result.stdout.splitlines():
            if line.startswith("TimeoutStopUSec="):
                value = line.split("=", 1)[1].strip()
                # Try numeric microseconds first
                if value.isdigit():
                    timeout_us = int(value)
                else:
                    timeout_us = parse_systemd_duration_to_us(value)
                if timeout_us is not None:
                    break
        if timeout_us is not None:
            break

    if timeout_us is None:
        return None

    timeout_stop_sec = timeout_us / 1_000_000.0
    # systemd needs headroom for: post-interrupt kill, adapter disconnect,
    # SessionDB close, file unlinks, etc.  30s matches the unit-template
    # constant in hermes_cli/gateway.py.
    headroom = 30.0
    expected = drain_timeout + headroom
    return {
        "unit": unit_name,
        "timeout_stop_sec": timeout_stop_sec,
        "drain_timeout": drain_timeout,
        "expected_min": expected,
        "mismatch": timeout_stop_sec < expected,
    }


def parse_systemd_duration_to_us(raw: str) -> Optional[int]:
    """Parse 'TimeoutStopUSec=1min 30s' / '90s' style values to microseconds.

    systemd accepts a wide grammar; we cover the common cases (s, ms, min,
    h) and return None on anything unexpected.  Never raises.

    Public: also consumed by hermes_cli.gateway's restart-wait sizing.
    """
    if not raw:
        return None
    units = {
        "us": 1,
        "ms": 1_000,
        "s": 1_000_000,
        "sec": 1_000_000,
        "min": 60_000_000,
        "h": 3_600_000_000,
        "hr": 3_600_000_000,
    }
    total_us = 0
    token = ""
    digits = ""
    for ch in raw + " ":
        if ch.isdigit() or ch == ".":
            if token:
                # End previous unit, start new number
                multiplier = units.get(token.lower())
                if multiplier is None or not digits:
                    return None
                try:
                    total_us += int(float(digits) * multiplier)
                except ValueError:
                    return None
                digits = ""
                token = ""
            digits += ch
        elif ch.isalpha():
            token += ch
        elif digits and token:
            multiplier = units.get(token.lower())
            if multiplier is None:
                return None
            try:
                total_us += int(float(digits) * multiplier)
            except ValueError:
                return None
            digits = ""
            token = ""
        elif digits and not token:
            # Bare number = seconds (rare but valid)
            try:
                total_us += int(float(digits) * 1_000_000)
            except ValueError:
                return None
            digits = ""
    return total_us if total_us > 0 else None


# Backward-compat private alias (pre-promotion name).
_parse_systemd_duration_to_us = parse_systemd_duration_to_us
