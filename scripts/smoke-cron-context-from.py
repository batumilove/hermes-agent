#!/usr/bin/env python3
"""Live smoke test for Hermes cron context_from chaining.

Creates temporary cron jobs through the installed Hermes cronjob tool, runs them,
verifies that the dependent job receives the source job's latest output via
context_from, and removes the temporary jobs on exit.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

TOKEN = "SOURCE_TOKEN_CONTEXT_FROM_SMOKE_AUTOMATED"
OK = "CONTEXT_FROM_OK"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test Hermes cron context_from; creates temporary cron jobs, "
            "runs them, verifies injected context, and removes temporary jobs."
        )
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="seconds to wait for each cron output file (default: 180)",
    )
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help="keep cron output artifacts for inspection (default: leave them in place; removal is not implemented to avoid deleting useful logs)",
    )
    parser.add_argument(
        "--hermes-home",
        type=Path,
        default=Path.home() / ".hermes",
        help="Hermes home containing cron/output (default: ~/.hermes)",
    )
    return parser.parse_args()


def run_python(code: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout)


def cronjob_call(payload: dict) -> dict:
    code = f"""
import json
from tools.cronjob_tools import cronjob
print(cronjob(**{payload!r}))
"""
    return run_python(code)


def wait_for_output(hermes_home: Path, job_id: str, needle: str | None, timeout: int) -> Path:
    out_dir = hermes_home / "cron" / "output" / job_id
    deadline = time.time() + timeout
    while time.time() < deadline:
        files = sorted(out_dir.glob("*.md"))
        if files:
            latest = files[-1]
            text = latest.read_text(encoding="utf-8", errors="replace")
            if needle is None or needle in text:
                return latest
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for cron output for {job_id}")


def main() -> int:
    args = parse_args()
    source_id = None
    dependent_id = None

    try:
        source = cronjob_call(
            {
                "action": "create",
                "name": "context-from-source-smoke-auto",
                "prompt": f"Reply exactly with this token and nothing else:\n{TOKEN}",
                "schedule": "0 0 1 1 *",
                "repeat": 999,
                "deliver": "local",
                "enabled_toolsets": [],
            }
        )
        if not source.get("success"):
            raise RuntimeError(source)
        source_id = source["job_id"]

        cronjob_call({"action": "run", "job_id": source_id})
        source_file = wait_for_output(args.hermes_home, source_id, TOKEN, args.timeout)

        dependent = cronjob_call(
            {
                "action": "create",
                "name": "context-from-dependent-smoke-auto",
                "prompt": (
                    f"If the injected context contains {TOKEN}, reply exactly {OK}. "
                    "Otherwise reply exactly CONTEXT_FROM_MISSING."
                ),
                "schedule": "0 0 1 1 *",
                "repeat": 999,
                "deliver": "local",
                "enabled_toolsets": [],
                "context_from": [source_id],
            }
        )
        if not dependent.get("success"):
            raise RuntimeError(dependent)
        dependent_id = dependent["job_id"]

        cronjob_call({"action": "run", "job_id": dependent_id})
        dependent_file = wait_for_output(args.hermes_home, dependent_id, OK, args.timeout)
        dependent_text = dependent_file.read_text(encoding="utf-8", errors="replace")
        if f"## Output from job '{source_id}'" not in dependent_text or TOKEN not in dependent_text:
            raise AssertionError("dependent output did not include injected context block")

        print("context_from smoke test passed")
        print(f"source_job={source_id} source_output={source_file}")
        print(f"dependent_job={dependent_id} dependent_output={dependent_file}")
        if not args.keep_output:
            print("note: output artifacts are retained intentionally for cron audit logs")
        return 0
    finally:
        for job_id in (dependent_id, source_id):
            if job_id:
                try:
                    cronjob_call({"action": "remove", "job_id": job_id})
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    print(f"warning: failed to remove temporary job {job_id}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
