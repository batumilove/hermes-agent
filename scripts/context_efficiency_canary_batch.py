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
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "tdai-canary"
DEFAULT_LOG_RELATIVE = "logs/context_efficiency-canary.jsonl"
SESSION_RE = re.compile(r"session_id:\s*([A-Za-z0-9_.:-]+)")


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

NATURAL_TOOLSETS = ("session_search", "memory", "web", "file", "context_engine")

NATURAL_CASES: tuple[CanaryCase, ...] = (
    CanaryCase(
        name="natural-past-decision",
        family="session_search",
        toolsets=NATURAL_TOOLSETS,
        prompt="What did we decide in the previous session about context route advisor telemetry? Answer briefly.",
    ),
    CanaryCase(
        name="natural-user-preference",
        family="durable_memory",
        toolsets=NATURAL_TOOLSETS,
        prompt="What user preference should guide how you report infrastructure verification results? Answer briefly.",
    ),
    CanaryCase(
        name="natural-current-lcm",
        family="current_session_lcm",
        toolsets=NATURAL_TOOLSETS,
        prompt="Check the current session LCM state and summarize it in one sentence.",
    ),
    CanaryCase(
        name="natural-current-docs",
        family="web",
        toolsets=NATURAL_TOOLSETS,
        prompt="Find the current Hermes Agent configuration docs URL and answer with just the URL.",
    ),
    CanaryCase(
        name="natural-repo-file",
        family="file",
        toolsets=NATURAL_TOOLSETS,
        prompt="Where in this repo is the context efficiency report implemented? Answer with the path only.",
    ),
    CanaryCase(
        name="natural-ambiguous-memory-session",
        family="session_search",
        toolsets=NATURAL_TOOLSETS,
        prompt="Where did we leave the memory routing evaluation, and what should happen next? Answer briefly.",
    ),
    CanaryCase(
        name="natural-ambiguous-current-repo-docs",
        family="file",
        toolsets=NATURAL_TOOLSETS,
        prompt="Check the local repo docs for context efficiency telemetry and summarize the relevant instruction.",
    ),
)

CASES: tuple[CanaryCase, ...] = STABLE_CASES + EXPERIMENTAL_CASES
ALL_CASES: tuple[CanaryCase, ...] = CASES + NATURAL_CASES


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


def extract_session_id(result: dict[str, object]) -> str:
    stderr = str(result.get("stderr") or "")
    match = SESSION_RE.search(stderr)
    return match.group(1) if match else ""


def summarize_case_outcome(case: CanaryCase, result: dict[str, object], events: list[dict[str, object]]) -> dict[str, object]:
    route_families: dict[str, int] = {}
    advisor_families: dict[str, int] = {}
    routes: dict[str, int] = {}
    errors = 0
    mismatches = 0
    for event in events:
        route_family = str(event.get("route_family") or "unknown")
        advisor_family = str(event.get("advisor_family") or "unknown")
        route = str(event.get("route") or "unknown")
        route_families[route_family] = route_families.get(route_family, 0) + 1
        advisor_families[advisor_family] = advisor_families.get(advisor_family, 0) + 1
        routes[route] = routes.get(route, 0) + 1
        errors += 1 if event.get("is_error") else 0
        mismatches += 1 if event.get("advisor_match") is False else 0

    expected_events = [event for event in events if event.get("route_family") == case.family]
    unexpected_families = sorted(family for family in route_families if family != case.family)
    return {
        "case": case.name,
        "prompt": case.prompt,
        "expected_family": case.family,
        "toolsets": list(case.toolsets),
        "session_id": extract_session_id(result),
        "returncode": result.get("returncode"),
        "answer_excerpt": str(result.get("stdout") or "")[:1000],
        "event_count": len(events),
        "route_families": route_families,
        "advisor_families": advisor_families,
        "routes": routes,
        "errors": errors,
        "advisor_mismatches": mismatches,
        "expected_family_events": len(expected_events),
        "unexpected_families": unexpected_families,
        "route_family_ok": bool(expected_events) and not unexpected_families,
        "needs_review": bool(result.get("returncode") != 0 or errors or mismatches or not expected_events or unexpected_families),
        "review_note": "manual outcome review required before adaptive routing promotion",
    }


def summarize_batch_run(*, profile: str, cases: list[CanaryCase], results: list[dict[str, object]], appended: list[dict[str, object]], log_path: Path, before: int, after: int, natural: bool) -> dict[str, object]:
    events_by_session: dict[str, list[dict[str, object]]] = {}
    for event in appended:
        events_by_session.setdefault(str(event.get("session_id") or ""), []).append(event)
    case_summaries = []
    for case, result in zip(cases, results):
        case_summaries.append(summarize_case_outcome(case, result, events_by_session.get(extract_session_id(result), [])))
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "natural": natural,
        "log_path": str(log_path),
        "lines_before": before,
        "lines_after": after,
        "lines_delta": after - before,
        "case_count": len(case_summaries),
        "event_count": len(appended),
        "mismatch_event_count": sum(1 for event in appended if event.get("advisor_match") is False),
        "review_case_count": sum(1 for item in case_summaries if item.get("needs_review")),
        "cases": case_summaries,
    }


def default_run_summary_path(profile: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return profile_home(profile) / "runs" / "context-route" / f"{stamp}.json"


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_cases(names: Iterable[str], *, include_experimental: bool = False, natural: bool = False) -> list[CanaryCase]:
    wanted = [name.strip() for name in names if name.strip()]
    default_cases = NATURAL_CASES if natural else (CASES if include_experimental else STABLE_CASES)
    if not wanted or wanted == ["all"]:
        return list(default_cases)
    by_name = {case.name: case for case in ALL_CASES}
    by_family = {case.family: case for case in ALL_CASES}
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
    parser.add_argument("--natural", action="store_true", help="Run representative unforced prompts with all context toolsets instead of forced-route smoke cases")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per Hermes one-shot")
    parser.add_argument("--report-limit", type=int, default=20, help="Number of recent events for final report")
    parser.add_argument("--write-run-summary", nargs="?", const="auto", default=None, help="Write prompt-level outcome summary JSON; optional path or auto profile runs/context-route/<timestamp>.json")
    parser.add_argument("--dry-run", action="store_true", help="Print selected commands without running Hermes")
    args = parser.parse_args(argv)

    log_path = resolve_log_path(args.profile, args.log_path)
    cases = select_cases(args.case or ["all"], include_experimental=args.include_experimental, natural=args.natural)
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

    if args.write_run_summary and not args.dry_run:
        summary_path = default_run_summary_path(args.profile) if args.write_run_summary == "auto" else Path(args.write_run_summary).expanduser()
        if not summary_path.is_absolute():
            summary_path = Path.cwd() / summary_path
        run_summary = summarize_batch_run(
            profile=args.profile,
            cases=cases,
            results=results,
            appended=appended,
            log_path=log_path,
            before=before,
            after=after,
            natural=args.natural,
        )
        write_json(summary_path, run_summary)
        print("\n## run_summary")
        print(f"path={summary_path}")
        print(f"cases={run_summary['case_count']} events={run_summary['event_count']} review_cases={run_summary['review_case_count']} mismatches={run_summary['mismatch_event_count']}")

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
