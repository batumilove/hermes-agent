#!/usr/bin/env python3
"""Run Bumblebee read-only scans and print only actionable findings/errors.

Designed for Hermes cron no_agent=True: empty stdout means silent success.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from collections import Counter

HOME = pathlib.Path.home()
BUMBLEBEE = HOME / ".local/bin/bumblebee"
BASE = HOME / ".local/share/bumblebee"
CATALOG_DIR = BASE / "catalogs"
SCAN_DIR = BASE / "scans"
STATE_PATH = BASE / "watchdog-state.json"
PROJECT_ROOTS = [
    HOME / ".hermes/hermes-agent",
    HOME / "hermes-skills",
    HOME / "obsidian-scratchpad",
]
TIMEOUT_SECONDS = 300
MAX_DURATION = "5m"


def load_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except Exception:
        return default


def save_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def run_scan(label: str, args: list[str]) -> dict:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = SCAN_DIR / f"{label}-{ts}.ndjson"
    err = SCAN_DIR / f"{label}-{ts}.stderr"
    cmd = [str(BUMBLEBEE), "scan", *args, "--exposure-catalog", str(CATALOG_DIR), "--findings-only", "--max-duration", MAX_DURATION]
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, stdout=stdout, stderr=stderr, text=True, timeout=TIMEOUT_SECONDS)
    records = []
    for line in out.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            records.append({"record_type": "invalid", "raw": line[:500]})
    findings = [r for r in records if r.get("record_type") == "finding"]
    summaries = [r for r in records if r.get("record_type") == "scan_summary"]
    stderr_text = err.read_text(errors="replace")
    return {
        "label": label,
        "cmd": cmd,
        "returncode": proc.returncode,
        "out": str(out),
        "err": str(err),
        "records": records,
        "findings": findings,
        "summary": summaries[-1] if summaries else None,
        "stderr": stderr_text,
    }


def finding_key(f: dict) -> str:
    raw = json.dumps(f, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def compact_finding(f: dict) -> str:
    # Keep resilient against schema changes.
    exposure = f.get("exposure") or f.get("catalog") or {}
    component = f.get("component") or f.get("package") or f
    eco = component.get("ecosystem") or f.get("ecosystem") or exposure.get("ecosystem") or "unknown"
    name = component.get("name") or component.get("normalized_name") or f.get("name") or exposure.get("name") or "unknown"
    version = component.get("version") or f.get("version") or exposure.get("version") or "unknown"
    source = component.get("source_path") or f.get("source_path") or f.get("path") or "unknown path"
    exp_id = exposure.get("id") or f.get("exposure_id") or f.get("catalog_id") or f.get("record_id") or "unknown exposure"
    return f"- `{eco}` `{name}` `{version}` — {exp_id} — `{source}`"


def main() -> int:
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    if not BUMBLEBEE.exists():
        print(f"Bumblebee watchdog error: binary missing at {BUMBLEBEE}")
        return 1
    if not CATALOG_DIR.exists() or not any(CATALOG_DIR.glob("*.json")):
        print(f"Bumblebee watchdog error: no exposure catalogs in {CATALOG_DIR}")
        return 1

    scans = []
    try:
        scans.append(run_scan("findings-baseline", ["--profile", "baseline"]))
        roots = [p for p in PROJECT_ROOTS if p.exists()]
        if roots:
            root_args = ["--profile", "project"]
            for p in roots:
                root_args += ["--root", str(p)]
            scans.append(run_scan("findings-project", root_args))
    except subprocess.TimeoutExpired:
        print(f"Bumblebee watchdog error: scan timed out after {TIMEOUT_SECONDS}s")
        return 1
    except Exception as e:
        print(f"Bumblebee watchdog error: {type(e).__name__}: {e}")
        return 1

    errors = [s for s in scans if s["returncode"] != 0]
    if errors:
        print("Bumblebee watchdog scan failed:")
        for s in errors:
            print(f"- {s['label']}: exit {s['returncode']} stdout={s['out']} stderr={s['err']}")
            if s["stderr"]:
                print(s["stderr"][:2000])
        return 1

    state = load_json(STATE_PATH, {"seen": []})
    seen = set(state.get("seen", []))
    all_findings = []
    for s in scans:
        for f in s["findings"]:
            all_findings.append((s["label"], finding_key(f), f))
    new = [(label, key, f) for label, key, f in all_findings if key not in seen]

    state["seen"] = sorted(seen | {key for _, key, _ in all_findings})[-5000:]
    state["last_run_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["last_counts"] = {
        s["label"]: {
            "findings": len(s["findings"]),
            "files_considered": (s["summary"] or {}).get("files_considered"),
            "suppressed_packages": (s["summary"] or {}).get("package_records_suppressed"),
            "duration_ms": (s["summary"] or {}).get("duration_ms"),
            "out": s["out"],
            "err": s["err"],
        }
        for s in scans
    }
    save_json(STATE_PATH, state)

    if not new:
        return 0

    counts = Counter(label for label, _, _ in new)
    host = socket.gethostname()
    print(f"Bumblebee found {len(new)} new exposure finding(s) on `{host}`:")
    for label, _, f in new[:25]:
        print(f"{compact_finding(f)} [{label}]")
    if len(new) > 25:
        print(f"…and {len(new)-25} more. Latest scan dir: `{SCAN_DIR}`")
    print("\nScan artifacts:")
    for s in scans:
        print(f"- {s['label']}: `{s['out']}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
