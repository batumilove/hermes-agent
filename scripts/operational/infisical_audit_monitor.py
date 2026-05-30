#!/usr/bin/env python3
"""Monitor Hermes Infisical secret-injection audit JSONL without exposing values.

Reads ~/.hermes/audit/secrets/YYYY-MM-DD.jsonl by default and emits a JSON
summary plus optional Prometheus textfile metrics. The monitor intentionally
reports counts and secret *name counts* only; it does not print secret values and
it does not print the secret_names arrays by default.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_EXPECTED_WRAPPERS = {"hermes-infisical-run", "hermes-infisical-env"}
DEFAULT_EXPECTED_PATHS = {
    "/hermes/providers",
    "/hermes/secrets",
    "/hermes/proxmox_intel",
    "/hermes/browser",
    "/hermes/voice",
}
DEFAULT_STATE = Path.home() / ".hermes/state/infisical-audit-monitor-last.json"
DEFAULT_METRICS = Path.home() / ".hermes/state/prometheus/infisical_audit.prom"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def today_log_path() -> Path:
    return Path.home() / ".hermes/audit/secrets" / (utc_now().strftime("%Y-%m-%d") + ".jsonl")


def csv_set(raw: str | None, default: set[str]) -> set[str]:
    if raw is None or raw.strip() == "":
        return set(default)
    return {p.strip() for p in raw.split(",") if p.strip()}


def parse_ts(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value).timestamp()
    except Exception:
        return None


def prom_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metric_line(name: str, labels: dict[str, str] | None, value: int | float) -> str:
    if labels:
        rendered = ",".join(f'{k}="{prom_label(str(v))}"' for k, v in sorted(labels.items()))
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def write_metrics(path: Path, summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines += [
        "# HELP hermes_infisical_audit_ok 1 if the Infisical audit log passed monitor checks.",
        "# TYPE hermes_infisical_audit_ok gauge",
        metric_line("hermes_infisical_audit_ok", None, 1 if summary["ok"] else 0),
        "# HELP hermes_infisical_audit_events_today_total Count of Infisical audit events seen in today's log.",
        "# TYPE hermes_infisical_audit_events_today_total gauge",
        metric_line("hermes_infisical_audit_events_today_total", None, summary["events_today"]),
        "# HELP hermes_infisical_audit_last_event_timestamp_seconds Unix timestamp of the newest audit event.",
        "# TYPE hermes_infisical_audit_last_event_timestamp_seconds gauge",
        metric_line("hermes_infisical_audit_last_event_timestamp_seconds", None, summary.get("last_event_epoch") or 0),
        "# HELP hermes_infisical_audit_file_mode_ok 1 if audit file mode is 0600.",
        "# TYPE hermes_infisical_audit_file_mode_ok gauge",
        metric_line("hermes_infisical_audit_file_mode_ok", None, 1 if summary.get("mode") == "0600" else 0),
        "# HELP hermes_infisical_audit_parse_errors_total JSON parse errors in today's audit log.",
        "# TYPE hermes_infisical_audit_parse_errors_total gauge",
        metric_line("hermes_infisical_audit_parse_errors_total", None, summary["parse_errors"]),
    ]
    for result, count in sorted(summary["by_result"].items()):
        lines.append(metric_line("hermes_infisical_audit_events_by_result_total", {"result": result}, count))
    for key, count in sorted(summary["by_wrapper_result_path"].items()):
        wrapper, result, secret_path = key.split("\t", 2)
        lines.append(metric_line("hermes_infisical_audit_events_total", {"wrapper": wrapper, "result": result, "secret_path": secret_path}, count))
    for item in summary["unexpected_paths"]:
        lines.append(metric_line("hermes_infisical_audit_unexpected_path", {"secret_path": item}, 1))
    for item in summary["unexpected_wrappers"]:
        lines.append(metric_line("hermes_infisical_audit_unexpected_wrapper", {"wrapper": item}, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def monitor(args: argparse.Namespace) -> dict[str, Any]:
    audit_path = Path(args.log).expanduser() if args.log else today_log_path()
    expected_wrappers = csv_set(args.expected_wrappers, DEFAULT_EXPECTED_WRAPPERS)
    expected_paths = csv_set(args.expected_paths, DEFAULT_EXPECTED_PATHS)
    max_age_seconds = int(args.max_age_seconds)

    now = utc_now().timestamp()
    summary: dict[str, Any] = {
        "ok": True,
        "generated_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file": str(audit_path),
        "exists": audit_path.exists(),
        "mode": None,
        "size_bytes": 0,
        "events_today": 0,
        "last_event_ts": None,
        "last_event_epoch": None,
        "last_event_age_seconds": None,
        "by_result": {},
        "by_wrapper": {},
        "by_path": {},
        "by_wrapper_result_path": {},
        "unexpected_paths": [],
        "unexpected_wrappers": [],
        "non_success_recent": [],
        "parse_errors": 0,
        "errors": [],
        "warnings": [],
    }

    if not audit_path.exists():
        summary["ok"] = False
        summary["errors"].append("audit_file_missing")
        return summary

    st = audit_path.stat()
    mode = stat.S_IMODE(st.st_mode)
    summary["mode"] = f"{mode:04o}"
    summary["size_bytes"] = st.st_size
    if mode != 0o600:
        summary["ok"] = False
        summary["errors"].append("audit_file_mode_not_0600")

    by_result: Counter[str] = Counter()
    by_wrapper: Counter[str] = Counter()
    by_path: Counter[str] = Counter()
    by_wrp: Counter[str] = Counter()
    unexpected_paths: set[str] = set()
    unexpected_wrappers: set[str] = set()
    last_epoch: float | None = None
    last_ts: str | None = None
    non_success_recent: list[dict[str, Any]] = []

    with audit_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                summary["parse_errors"] += 1
                continue
            summary["events_today"] += 1
            wrapper = str(obj.get("wrapper") or "unknown")
            result = str(obj.get("result") or "unknown")
            secret_path = str(obj.get("secret_path") or "unknown")
            by_result[result] += 1
            by_wrapper[wrapper] += 1
            by_path[secret_path] += 1
            by_wrp[f"{wrapper}\t{result}\t{secret_path}"] += 1
            if wrapper not in expected_wrappers:
                unexpected_wrappers.add(wrapper)
            if secret_path not in expected_paths:
                unexpected_paths.add(secret_path)
            epoch = parse_ts(obj.get("ts"))
            if epoch is not None and (last_epoch is None or epoch > last_epoch):
                last_epoch = epoch
                last_ts = obj.get("ts")
            if result != "success" and len(non_success_recent) < 20:
                # No secret_names here; only count of names for privacy.
                non_success_recent.append({
                    "line": line_no,
                    "ts": obj.get("ts"),
                    "wrapper": wrapper,
                    "secret_path": secret_path,
                    "target": obj.get("target"),
                    "result": result,
                    "secret_name_count": len(obj.get("secret_names") or []),
                })

    summary["by_result"] = dict(sorted(by_result.items()))
    summary["by_wrapper"] = dict(sorted(by_wrapper.items()))
    summary["by_path"] = dict(sorted(by_path.items()))
    summary["by_wrapper_result_path"] = dict(sorted(by_wrp.items()))
    summary["unexpected_paths"] = sorted(unexpected_paths)
    summary["unexpected_wrappers"] = sorted(unexpected_wrappers)
    summary["non_success_recent"] = non_success_recent[-20:]
    summary["last_event_ts"] = last_ts
    summary["last_event_epoch"] = int(last_epoch) if last_epoch is not None else None
    if last_epoch is not None:
        summary["last_event_age_seconds"] = int(now - last_epoch)

    if summary["parse_errors"]:
        summary["ok"] = False
        summary["errors"].append("audit_json_parse_errors")
    if unexpected_paths:
        summary["ok"] = False
        summary["errors"].append("unexpected_secret_path")
    if unexpected_wrappers:
        summary["ok"] = False
        summary["errors"].append("unexpected_wrapper")
    if any(k != "success" for k in by_result):
        summary["ok"] = False
        summary["errors"].append("non_success_audit_events")
    if summary["events_today"] == 0:
        summary["warnings"].append("no_events_today")
    if last_epoch is not None and max_age_seconds > 0 and now - last_epoch > max_age_seconds:
        summary["warnings"].append("last_event_older_than_threshold")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Monitor Hermes Infisical audit JSONL without exposing secret values")
    ap.add_argument("--log", help="audit JSONL path; default is today's UTC log")
    ap.add_argument("--expected-paths", default=os.environ.get("INFISICAL_AUDIT_EXPECTED_PATHS"), help="comma-separated allowlist of secret paths")
    ap.add_argument("--expected-wrappers", default=os.environ.get("INFISICAL_AUDIT_EXPECTED_WRAPPERS"), help="comma-separated allowlist of wrappers")
    ap.add_argument("--max-age-seconds", default=os.environ.get("INFISICAL_AUDIT_MAX_AGE_SECONDS", "86400"), help="warn if newest event is older than this; 0 disables")
    ap.add_argument("--metrics-file", default=os.environ.get("INFISICAL_AUDIT_METRICS_FILE", str(DEFAULT_METRICS)), help="write Prometheus textfile metrics here; use 'off' to disable")
    ap.add_argument("--state-file", default=os.environ.get("INFISICAL_AUDIT_STATE_FILE", str(DEFAULT_STATE)), help="write last JSON summary here; use 'off' to disable")
    ap.add_argument("--fail-on-alert", action="store_true", help="exit 2 when ok=false; default exits 0 for cron/Hermes summarizers")
    args = ap.parse_args()

    summary = monitor(args)
    metrics_file = str(args.metrics_file).strip().lower()
    if metrics_file not in {"", "off", "none", "false", "0"}:
        write_metrics(Path(args.metrics_file).expanduser(), summary)
    state_file = str(args.state_file).strip().lower()
    if state_file not in {"", "off", "none", "false", "0"}:
        p = Path(args.state_file).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        p.chmod(0o600)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 2 if args.fail_on_alert and not summary["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
