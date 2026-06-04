"""Summaries for context-efficiency telemetry JSONL logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from agent.context_efficiency import DEFAULT_LOG_PATH
from hermes_constants import get_hermes_home


def resolve_input_path(path: str | None = None) -> Path:
    candidate = Path(path or DEFAULT_LOG_PATH).expanduser()
    if not candidate.is_absolute():
        candidate = get_hermes_home() / candidate
    return candidate


def load_events(path: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    if limit and limit > 0:
        lines = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def summarize_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    advisor_events: list[dict[str, Any]] = []
    for event in events:
        total += 1
        grouped[str(event.get("route") or "unknown")].append(event)
        if event.get("advisor_family"):
            advisor_events.append(event)

    routes: list[dict[str, Any]] = []
    for route, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        durations = [float(e.get("duration_s") or 0.0) for e in items]
        result_chars = [int(e.get("result_chars") or 0) for e in items]
        errors = sum(1 for e in items if e.get("is_error"))
        sessions = {str(e.get("session_id") or "") for e in items if e.get("session_id")}
        route_advisor_events = [e for e in items if e.get("advisor_family")]
        advisor_mismatches = sum(1 for e in route_advisor_events if e.get("advisor_match") is False)
        routes.append(
            {
                "route": route,
                "calls": len(items),
                "errors": errors,
                "error_rate": round(errors / len(items), 4) if items else 0.0,
                "avg_duration_s": round(mean(durations), 3) if durations else 0.0,
                "avg_result_chars": round(mean(result_chars), 1) if result_chars else 0.0,
                "sessions": len(sessions),
                "advisor_mismatches": advisor_mismatches,
                "advisor_mismatch_rate": round(advisor_mismatches / len(route_advisor_events), 4) if route_advisor_events else 0.0,
            }
        )

    family_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in advisor_events:
        family_items[str(event.get("advisor_family"))].append(event)

    advisor_by_family: dict[str, dict[str, Any]] = {}
    for family, items in sorted(family_items.items()):
        mismatches = [e for e in items if e.get("advisor_match") is False]
        advisor_by_family[family] = {
            "events": len(items),
            "mismatches": len(mismatches),
            "mismatch_rate": round(len(mismatches) / len(items), 4) if items else 0.0,
            "routes": dict(Counter(str(e.get("route") or "unknown") for e in mismatches).most_common(5)),
        }

    total_mismatches = sum(1 for e in advisor_events if e.get("advisor_match") is False)
    advisor = {
        "events": len(advisor_events),
        "mismatches": total_mismatches,
        "mismatch_rate": round(total_mismatches / len(advisor_events), 4) if advisor_events else 0.0,
        "by_family": advisor_by_family,
    }
    return {"events": total, "advisor": advisor, "routes": routes}


def format_summary(summary: dict[str, Any]) -> str:
    lines = [f"Context efficiency telemetry: {summary.get('events', 0)} event(s)"]
    advisor = summary.get("advisor") or {}
    if advisor.get("events"):
        lines.append(
            "Advisor: events={events}, mismatches={mismatches} ({mismatch_rate})".format(**advisor)
        )
        for family, row in sorted((advisor.get("by_family") or {}).items()):
            routes = row.get("routes") or {}
            route_text = ",".join(f"{route}:{count}" for route, count in routes.items()) or "none"
            lines.append(
                f"  - advisor_family={family}: events={row.get('events', 0)}, "
                f"mismatches={row.get('mismatches', 0)}, top_actual_routes={route_text}"
            )
    routes = summary.get("routes") or []
    if not routes:
        lines.append("No route events found.")
        return "\n".join(lines)
    for row in routes:
        advisor_suffix = ""
        if row.get("advisor_mismatches"):
            advisor_suffix = ", advisor_mismatches={advisor_mismatches} ({advisor_mismatch_rate})".format(**row)
        lines.append(
            ("- {route}: calls={calls}, errors={errors}, avg_duration={avg_duration_s}s, "
             "avg_result_chars={avg_result_chars}, sessions={sessions}" + advisor_suffix).format(**row)
        )
    return "\n".join(lines)


def build_report(path: str | None = None, *, limit: int | None = None) -> str:
    p = resolve_input_path(path)
    events = load_events(p, limit=limit)
    header = f"Source: {p}"
    return header + "\n" + format_summary(summarize_events(events))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize Hermes context-efficiency telemetry JSONL logs.")
    parser.add_argument("path", nargs="?", help="Telemetry JSONL path; defaults to HERMES_HOME/logs/context_efficiency.jsonl")
    parser.add_argument("--limit", type=int, default=None, help="Only summarize the last N events")
    args = parser.parse_args(argv)
    print(build_report(args.path, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
