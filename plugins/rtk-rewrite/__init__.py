"""Hermes plugin adapter for RTK command rewriting.

All rewrite logic lives in RTK's Rust ``rtk rewrite`` command; this module only
bridges Hermes ``pre_tool_call`` payloads to that command and fails open.

The plugin also emits lightweight Prometheus textfile metrics so runtime RTK
rewrite behavior can be scraped and graphed without adding a new server process.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

ACCEPTED_REWRITE_RETURN_CODES = {0, 3}
EXPECTED_PASSTHROUGH_RETURN_CODES = {1, 2}

_rtk_available: bool | None = None
_rtk_missing_warned = False
_METRICS_LOCK = threading.Lock()
_METRICS: dict[str, Any] = {
    "attempts": 0,
    "rewrites": 0,
    "passthrough": 0,
    "failures": 0,
    "timeouts": 0,
    "last_event_timestamp": 0.0,
    "events_by_command": {},
}


def register(ctx: Any) -> None:
    """Register the Hermes pre-tool callback when RTK is available."""
    available = _check_rtk()
    _write_metrics(available=available)
    if not available:
        return
    ctx.register_hook("pre_tool_call", _pre_tool_call)


def _check_rtk() -> bool:
    """Return whether the ``rtk`` binary is in PATH, warning once when missing."""
    global _rtk_available, _rtk_missing_warned

    if _rtk_available is None:
        _rtk_available = shutil.which("rtk") is not None

    if not _rtk_available and not _rtk_missing_warned:
        _warn("rtk binary not found in PATH; Hermes hook not registered")
        _rtk_missing_warned = True

    return _rtk_available


def _pre_tool_call(tool_name: str | None = None, args: dict[str, Any] | None = None, **_kwargs: Any) -> None:
    """Rewrite mutable Hermes terminal command args when RTK provides a change.

    The hook is intentionally fail-open: any unsupported payload, RTK failure,
    timeout, or unexpected exception leaves the original command unchanged.
    """
    try:
        if tool_name != "terminal" or not isinstance(args, dict):
            return

        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return

        command_family = _command_family(command)
        _record_metric(command_family, "attempt")
        try:
            result = subprocess.run(
                ["rtk", "rewrite", command],
                shell=False,
                timeout=2,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            _record_metric(command_family, "timeout")
            _warn("rtk rewrite timed out")
            return

        if result.returncode not in ACCEPTED_REWRITE_RETURN_CODES:
            if result.returncode not in EXPECTED_PASSTHROUGH_RETURN_CODES:
                _record_metric(command_family, "failure")
                details = f"rtk rewrite failed with exit {result.returncode}"
                stderr = result.stderr.strip()
                if stderr:
                    details = f"{details}: {stderr}"
                _warn(details)
            else:
                _record_metric(command_family, "passthrough")
            return

        rewritten = result.stdout.strip()
        if rewritten and rewritten != command:
            args["command"] = rewritten
            _record_metric(command_family, "rewrite")
        else:
            _record_metric(command_family, "passthrough")
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        _warn(str(exc))
        return


def _command_family(command: str) -> str:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        parts = command.strip().split()
    if not parts:
        return "unknown"
    first = Path(parts[0]).name or "unknown"
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in first)
    return safe[:64] or "unknown"


def _record_metric(command_family: str, event: str) -> None:
    with _METRICS_LOCK:
        _METRICS["last_event_timestamp"] = time.time()
        if event == "attempt":
            _METRICS["attempts"] += 1
        elif event == "rewrite":
            _METRICS["rewrites"] += 1
        elif event == "passthrough":
            _METRICS["passthrough"] += 1
        elif event == "failure":
            _METRICS["failures"] += 1
        elif event == "timeout":
            _METRICS["timeouts"] += 1
        by_command = _METRICS["events_by_command"]
        key = (command_family, event)
        by_command[key] = by_command.get(key, 0) + 1
    _write_metrics(available=_check_rtk())


def _metrics_file() -> Path:
    override = os.environ.get("HERMES_RTK_METRICS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return get_hermes_home() / "state" / "prometheus" / "rtk_rewrite.prom"


def _write_metrics(*, available: bool) -> None:
    try:
        path = _metrics_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _METRICS_LOCK:
            snapshot = {
                "attempts": _METRICS["attempts"],
                "rewrites": _METRICS["rewrites"],
                "passthrough": _METRICS["passthrough"],
                "failures": _METRICS["failures"],
                "timeouts": _METRICS["timeouts"],
                "last_event_timestamp": _METRICS["last_event_timestamp"],
                "events_by_command": dict(_METRICS["events_by_command"]),
            }
        lines = [
            "# HELP hermes_rtk_binary_available Whether the rtk binary was available when the plugin last checked.",
            "# TYPE hermes_rtk_binary_available gauge",
            f"hermes_rtk_binary_available {1 if available else 0}",
            "# HELP hermes_rtk_rewrite_attempts_total Terminal commands sent to rtk rewrite.",
            "# TYPE hermes_rtk_rewrite_attempts_total counter",
            f"hermes_rtk_rewrite_attempts_total {snapshot['attempts']}",
            "# HELP hermes_rtk_rewrites_total Terminal commands rewritten to RTK equivalents.",
            "# TYPE hermes_rtk_rewrites_total counter",
            f"hermes_rtk_rewrites_total {snapshot['rewrites']}",
            "# HELP hermes_rtk_passthrough_total Commands RTK left unchanged or unsupported.",
            "# TYPE hermes_rtk_passthrough_total counter",
            f"hermes_rtk_passthrough_total {snapshot['passthrough']}",
            "# HELP hermes_rtk_failures_total Unexpected rtk rewrite failures.",
            "# TYPE hermes_rtk_failures_total counter",
            f"hermes_rtk_failures_total {snapshot['failures']}",
            "# HELP hermes_rtk_timeouts_total rtk rewrite timeout count.",
            "# TYPE hermes_rtk_timeouts_total counter",
            f"hermes_rtk_timeouts_total {snapshot['timeouts']}",
            "# HELP hermes_rtk_last_event_timestamp_seconds Unix timestamp of the last RTK rewrite plugin event.",
            "# TYPE hermes_rtk_last_event_timestamp_seconds gauge",
            f"hermes_rtk_last_event_timestamp_seconds {snapshot['last_event_timestamp']:.3f}",
            "# HELP hermes_rtk_rewrite_events_total RTK rewrite plugin events by command family and event.",
            "# TYPE hermes_rtk_rewrite_events_total counter",
        ]
        for (command, event), value in sorted(snapshot["events_by_command"].items()):
            lines.append(
                f'hermes_rtk_rewrite_events_total{{command="{_escape_label(command)}",event="{_escape_label(event)}"}} {value}'
            )
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(path)
    except Exception:
        # Metrics must never interfere with command execution.
        return


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _warn(message: str) -> None:
    print(f"rtk: hermes plugin warning: {message}", file=sys.stderr)
