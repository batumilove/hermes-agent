#!/usr/bin/env python3
"""Run a small repeatable context-efficiency canary batch.

The batch intentionally uses an isolated Hermes profile and forced toolsets. It
prints JSONL line counts before/after, appended telemetry events, and the
bounded advisor report so operators can inspect route-advisor behavior without
ad-hoc shell glue.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "tdai-canary"
DEFAULT_LOG_RELATIVE = "logs/context_efficiency-canary.jsonl"


@dataclass(frozen=True)
class CanaryCase:
    name: str
    family: str
    toolsets: tuple[str, ...]
    prompt: str


STABLE_CASES: tuple[CanaryCase, ...] = (
    CanaryCase(
        name="session-search",
        family="session_search",
        toolsets=("session_search",),
        prompt=(
            "Use the session_search tool to search for the exact phrase "
            "context route advisor telemetry, then answer in one short sentence."
        ),
    ),
    CanaryCase(
        name="durable-memory",
        family="durable_memory",
        toolsets=("memory",),
        prompt=(
            "Use a durable memory or Honcho memory tool to look up user preference "
            "context, then answer with one short sentence."
        ),
    ),
    CanaryCase(
        name="web-search",
        family="web",
        toolsets=("web",),
        prompt=(
            "Use the web_search tool to search current Hermes Agent docs URL for "
            "configuration, then answer with one short sentence."
        ),
    ),
    CanaryCase(
        name="file-search",
        family="file",
        toolsets=("file",),
        prompt=(
            "Use the search_files tool to find where context_efficiency_report is "
            "implemented under /home/ubuntu/.hermes/hermes-agent, then answer with one short sentence."
        ),
    ),
)

EXPERIMENTAL_CASES: tuple[CanaryCase, ...] = (
    CanaryCase(
        name="current-session-lcm",
        family="current_session_lcm",
        toolsets=("context_engine",),
        prompt=(
            "Use the lcm_status or lcm_grep tool to inspect current session LCM "
            "context, then answer with one short sentence."
        ),
    ),
)

CASES: tuple[CanaryCase, ...] = STABLE_CASES + EXPERIMENTAL_CASES


def profile_home(profile: str) -> Path:
    if profile == "default":
        return Path.home() / ".hermes"
    return Path.home() / ".hermes" / "profiles" / profile


def resolve_log_path(profile: str, log_path: str | None) -> Path:
    candidate = Path(log_path or DEFAULT_LOG_RELATIVE).expanduser()
    if not candidate.is_absolute():
        candidate = profile_home(profile) / candidate
    return candidate


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def read_appended(path: Path, before_count: int) -> list[dict[str, object]]:
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if idx <= before_count or not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def select_cases(names: Iterable[str], *, include_experimental: bool = False) -> list[CanaryCase]:
    wanted = [name.strip() for name in names if name.strip()]
    default_cases = CASES if include_experimental else STABLE_CASES
    if not wanted or wanted == ["all"]:
        return list(default_cases)
    by_name = {case.name: case for case in CASES}
    by_family = {case.family: case for case in CASES}
    selected: list[CanaryCase] = []
    unknown: list[str] = []
    for item in wanted:
        case = by_name.get(item) or by_family.get(item)
        if case is None:
            unknown.append(item)
        elif case not in selected:
            selected.append(case)
    if unknown:
        raise SystemExit(f"Unknown case/family: {', '.join(unknown)}")
    return selected


def hermes_binary() -> str:
    local = REPO_ROOT / "venv" / "bin" / "hermes"
    return str(local) if local.exists() else "hermes"


def run_case(case: CanaryCase, *, profile: str, timeout: int, dry_run: bool) -> dict[str, object]:
    cmd = [
        hermes_binary(),
        "--profile",
        profile,
        "chat",
        "-Q",
        "-q",
        case.prompt,
        "--toolsets",
        ",".join(case.toolsets),
    ]
    if dry_run:
        return {"case": case.name, "family": case.family, "command": cmd, "skipped": True}
    env = os.environ.copy()
    env["HERMES_PROFILE"] = profile
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "case": case.name,
        "family": case.family,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip()[-1000:],
        "stderr": proc.stderr.strip()[-2000:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run forced context-route canary prompts and show appended telemetry.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="Hermes profile to run; default tdai-canary")
    parser.add_argument("--log-path", default=None, help="Telemetry JSONL path; defaults to profile logs/context_efficiency-canary.jsonl")
    parser.add_argument("--case", action="append", default=[], help="Case name or family to run; repeatable; default stable cases")
    parser.add_argument("--include-experimental", action="store_true", help="Include experimental cases such as LCM when --case is omitted/all")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per Hermes one-shot")
    parser.add_argument("--report-limit", type=int, default=20, help="Number of recent events for final report")
    parser.add_argument("--dry-run", action="store_true", help="Print selected commands without running Hermes")
    args = parser.parse_args(argv)

    log_path = resolve_log_path(args.profile, args.log_path)
    cases = select_cases(args.case or ["all"], include_experimental=args.include_experimental)
    before = count_lines(log_path)
    print(f"profile={args.profile}")
    print(f"log_path={log_path}")
    print(f"lines_before={before}")
    print(f"cases={','.join(case.name for case in cases)}")

    results = []
    for case in cases:
        print(f"\n## case={case.name} family={case.family} toolsets={','.join(case.toolsets)}")
        result = run_case(case, profile=args.profile, timeout=args.timeout, dry_run=args.dry_run)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    after = count_lines(log_path)
    print(f"\nlines_after={after}")
    print(f"lines_delta={after - before}")
    appended = read_appended(log_path, before)
    print("\n## appended_events")
    for event in appended:
        compact = {
            "route": event.get("route"),
            "route_family": event.get("route_family"),
            "advisor_family": event.get("advisor_family"),
            "advisor_match": event.get("advisor_match"),
            "advisor_reason": event.get("advisor_reason"),
            "is_error": event.get("is_error"),
            "session_id": event.get("session_id"),
        }
        print(json.dumps(compact, ensure_ascii=False, sort_keys=True))

    if not args.dry_run:
        print("\n## report")
        report_cmd = [
            sys.executable,
            "-m",
            "agent.context_efficiency_report",
            str(log_path),
            "--limit",
            str(args.report_limit),
        ]
        proc = subprocess.run(
            report_cmd,
            cwd=str(REPO_ROOT),
            env={**os.environ, "HERMES_PROFILE": args.profile},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        print(proc.stdout.strip())
        if proc.returncode != 0:
            print(proc.stderr.strip(), file=sys.stderr)
            return proc.returncode

    failures = [r for r in results if not r.get("skipped") and r.get("returncode") != 0]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
