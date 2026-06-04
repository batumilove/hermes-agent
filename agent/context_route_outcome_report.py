"""Aggregate prompt-level context route run-summary JSON files."""

from __future__ import annotations

import argparse
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1


def expand_inputs(patterns: Iterable[str]) -> list[Path]:
    """Expand one or more JSON paths/globs into existing files."""
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        expanded = [Path(item).expanduser() for item in glob.glob(str(Path(pattern).expanduser()))]
        if not expanded:
            expanded = [Path(pattern).expanduser()]
        for path in expanded:
            if path.is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(resolved)
    return sorted(paths)


def load_run_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported schema_version={data.get('schema_version')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"{path}: expected cases list")
    return data


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _case_name(case: dict[str, Any]) -> str:
    return str(case.get("case") or case.get("name") or "unknown")


def _expected_family(case: dict[str, Any]) -> str:
    return str(case.get("expected_family") or "unknown")


def _family_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(k): _as_int(v) for k, v in value.items()}


def summarize_runs(runs: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    cases: list[tuple[Path, dict[str, Any]]] = []
    for source, run in runs:
        for case in run.get("cases") or []:
            if isinstance(case, dict):
                cases.append((source, case))

    total_cases = len(cases)
    ok_count = sum(1 for _, case in cases if case.get("route_family_ok") is True)
    acceptable_count = sum(1 for _, case in cases if case.get("route_family_acceptable", case.get("route_family_ok")) is True)
    outcome_ok_count = sum(1 for _, case in cases if case.get("outcome_ok", case.get("route_family_ok")) is True)
    no_telemetry_expected_tool_count = sum(1 for _, case in cases if case.get("no_telemetry_expected_tool") is True)
    timeout_count = sum(1 for _, case in cases if case.get("timed_out") is True or case.get("returncode") == 124)
    failure_count = sum(1 for _, case in cases if case.get("returncode") not in (0, None, 124) and case.get("timed_out") is not True)

    unexpected_counter: Counter[str] = Counter()
    tool_error_counter: Counter[str] = Counter()
    family_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cases": 0,
            "events": 0,
            "mismatch_events": 0,
            "needs_review_cases": 0,
            "route_family_ok_cases": 0,
            "route_family_acceptable_cases": 0,
            "outcome_ok_cases": 0,
            "no_telemetry_expected_tool_cases": 0,
            "timeout_cases": 0,
            "failure_cases": 0,
            "unexpected_families": {},
            "tool_errors": {},
        }
    )
    repeated: dict[str, list[dict[str, Any]]] = defaultdict(list)
    needs_review_cases: list[dict[str, Any]] = []

    for source, case in cases:
        name = _case_name(case)
        expected = _expected_family(case)
        event_count = _as_int(case.get("event_count"))
        mismatches = _as_int(case.get("advisor_mismatches"))
        errors = _as_int(case.get("errors"))
        needs_review = bool(case.get("needs_review"))
        route_family_ok = case.get("route_family_ok") is True
        route_family_acceptable = case.get("route_family_acceptable", case.get("route_family_ok")) is True
        outcome_ok = case.get("outcome_ok", case.get("route_family_ok")) is True
        no_telemetry_expected_tool = case.get("no_telemetry_expected_tool") is True
        timed_out = case.get("timed_out") is True or case.get("returncode") == 124
        failed = case.get("returncode") not in (0, None, 124) and case.get("timed_out") is not True

        stats = family_stats[expected]
        stats["cases"] += 1
        stats["events"] += event_count
        stats["mismatch_events"] += mismatches
        stats["needs_review_cases"] += 1 if needs_review else 0
        stats["route_family_ok_cases"] += 1 if route_family_ok else 0
        stats["route_family_acceptable_cases"] += 1 if route_family_acceptable else 0
        stats["outcome_ok_cases"] += 1 if outcome_ok else 0
        stats["no_telemetry_expected_tool_cases"] += 1 if no_telemetry_expected_tool else 0
        stats["timeout_cases"] += 1 if timed_out else 0
        stats["failure_cases"] += 1 if failed else 0

        if needs_review:
            needs_review_cases.append(
                {
                    "case": name,
                    "source": str(source),
                    "expected_family": expected,
                    "session_id": str(case.get("session_id") or ""),
                    "acceptable_families": list(case.get("acceptable_families") or [expected]),
                    "route_family_acceptable": route_family_acceptable,
                    "outcome_ok": outcome_ok,
                    "no_telemetry_expected_tool": no_telemetry_expected_tool,
                    "timed_out": timed_out,
                }
            )

        for family in case.get("unexpected_families") or []:
            family_text = str(family)
            unexpected_counter[family_text] += 1
            stats["unexpected_families"][family_text] = stats["unexpected_families"].get(family_text, 0) + 1

        if errors:
            for route, count in _family_counts(case.get("routes")).items():
                tool_error_counter[route] += count
                stats["tool_errors"][route] = stats["tool_errors"].get(route, 0) + count

        repeated[name].append(
            {
                "route_family_ok": route_family_ok,
                "route_family_acceptable": route_family_acceptable,
                "outcome_ok": outcome_ok,
                "timed_out": timed_out,
                "failed": failed,
                "needs_review": needs_review,
            }
        )

    expected_family_stats: dict[str, dict[str, Any]] = {}
    for family, stats in sorted(family_stats.items()):
        case_count = stats["cases"]
        expected_family_stats[family] = {
            **stats,
            "route_family_ok_rate": round(stats["route_family_ok_cases"] / case_count, 4) if case_count else 0.0,
            "route_family_acceptable_rate": round(stats["route_family_acceptable_cases"] / case_count, 4) if case_count else 0.0,
            "outcome_ok_rate": round(stats["outcome_ok_cases"] / case_count, 4) if case_count else 0.0,
        }

    case_stability: dict[str, dict[str, Any]] = {}
    for name, items in sorted(repeated.items()):
        if len(items) < 2:
            continue
        ok_values = [bool(item["route_family_ok"]) for item in items]
        outcome_values = [bool(item["outcome_ok"]) for item in items]
        review_values = [bool(item["needs_review"]) for item in items]
        case_stability[name] = {
            "runs": len(items),
            "route_family_ok_values": ok_values,
            "outcome_ok_values": outcome_values,
            "needs_review_values": review_values,
            "stable_route_family_ok": len(set(ok_values)) == 1,
            "stable_outcome_ok": len(set(outcome_values)) == 1,
            "stable_needs_review": len(set(review_values)) == 1,
        }

    event_count = sum(_as_int(run.get("event_count")) for _, run in runs)
    mismatch_event_count = sum(_as_int(run.get("mismatch_event_count")) for _, run in runs)
    review_case_count = sum(_as_int(run.get("review_case_count")) for _, run in runs)

    return {
        "schema_version": SCHEMA_VERSION,
        "sources": [str(path) for path, _ in runs],
        "run_count": len(runs),
        "case_count": total_cases,
        "event_count": event_count,
        "mismatch_event_count": mismatch_event_count,
        "review_case_count": review_case_count,
        "route_family_ok_rate": round(ok_count / total_cases, 4) if total_cases else 0.0,
        "route_family_acceptable_rate": round(acceptable_count / total_cases, 4) if total_cases else 0.0,
        "outcome_ok_rate": round(outcome_ok_count / total_cases, 4) if total_cases else 0.0,
        "route_family_acceptable_count": acceptable_count,
        "outcome_ok_count": outcome_ok_count,
        "no_telemetry_expected_tool_count": no_telemetry_expected_tool_count,
        "timeout_count": timeout_count,
        "failure_count": failure_count,
        "needs_review_cases": needs_review_cases,
        "unexpected_families": dict(sorted(unexpected_counter.items())),
        "tool_errors": dict(tool_error_counter.most_common()),
        "expected_family_stats": expected_family_stats,
        "case_stability": case_stability,
    }


def build_outcome_report(patterns: Iterable[str]) -> dict[str, Any]:
    paths = expand_inputs(patterns)
    if not paths:
        joined = ", ".join(str(pattern) for pattern in patterns)
        raise ValueError(f"no input files matched: {joined}")
    runs = [(path, load_run_summary(path)) for path in paths]
    return summarize_runs(runs)


def format_text(summary: dict[str, Any]) -> str:
    lines = ["Context route outcome report"]
    lines.append(f"Sources: {len(summary.get('sources') or [])}")
    lines.append(f"Runs: {summary.get('run_count', 0)}")
    lines.append(
        "Cases: {case_count} | Events: {event_count} | Mismatch events: {mismatch_event_count} | Review cases: {review_case_count}".format(
            **summary
        )
    )
    lines.append(f"route_family_ok rate: {summary.get('route_family_ok_rate', 0.0)}")
    lines.append(f"route_family_acceptable rate: {summary.get('route_family_acceptable_rate', 0.0)}")
    lines.append(f"outcome_ok rate: {summary.get('outcome_ok_rate', 0.0)}")
    lines.append(f"no-telemetry expected-tool cases: {summary.get('no_telemetry_expected_tool_count', 0)}")
    lines.append(f"timeouts: {summary.get('timeout_count', 0)} | failures: {summary.get('failure_count', 0)}")

    needs_review = summary.get("needs_review_cases") or []
    lines.append(f"needs_review cases: {len(needs_review)}")
    for item in needs_review:
        lines.append(
            f"- {item.get('case')} expected_family={item.get('expected_family')} session={item.get('session_id')} source={item.get('source')}"
        )

    unexpected = summary.get("unexpected_families") or {}
    if unexpected:
        lines.append("Unexpected families: " + ", ".join(f"{k}:{v}" for k, v in unexpected.items()))
    else:
        lines.append("Unexpected families: none")

    tool_errors = summary.get("tool_errors") or {}
    if tool_errors:
        lines.append("Tool errors: " + ", ".join(f"{k}:{v}" for k, v in tool_errors.items()))
    else:
        lines.append("Tool errors: none")

    stats = summary.get("expected_family_stats") or {}
    if stats:
        lines.append("Per expected family:")
        for family, row in stats.items():
            lines.append(
                "- expected_family={family}: cases={cases}, events={events}, ok_rate={route_family_ok_rate}, acceptable_rate={route_family_acceptable_rate}, outcome_ok_rate={outcome_ok_rate}, review_cases={needs_review_cases}, timeouts={timeout_cases}, failures={failure_cases}, mismatches={mismatch_events}".format(
                    family=family, **row
                )
            )

    stability = summary.get("case_stability") or {}
    if stability:
        lines.append("Repeated case stability:")
        for name, row in stability.items():
            lines.append(
                f"- {name}: runs={row.get('runs')}, route_family_ok_stable={row.get('stable_route_family_ok')}, outcome_ok_stable={row.get('stable_outcome_ok')}, needs_review_stable={row.get('stable_needs_review')}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize context-route canary run-summary JSON files.")
    parser.add_argument("paths", nargs="+", help="One or more run-summary JSON paths/globs")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        summary = build_outcome_report(args.paths)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json_output:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
