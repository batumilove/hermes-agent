"""Summaries for context-efficiency telemetry JSONL logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from agent.context_efficiency import DEFAULT_LOG_PATH, route_family as classify_route_family
from hermes_constants import get_hermes_home


def resolve_input_path(path: str | None = None) -> Path:
    candidate = Path(path or DEFAULT_LOG_PATH).expanduser()
    if not candidate.is_absolute():
        candidate = get_hermes_home() / candidate
    return candidate


def _parse_timestamp(value: str | float | int | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _event_ts(event: dict[str, Any]) -> float:
    try:
        return float(event.get("ts") or 0.0)
    except Exception:
        return 0.0


def _route_family(event: dict[str, Any]) -> str:
    family = str(event.get("route_family") or "")
    if family:
        return family
    return classify_route_family(str(event.get("route") or ""))


def filter_events(
    events: Iterable[dict[str, Any]],
    *,
    since: str | float | int | None = None,
    family: str | None = None,
    mismatches_only: bool = False,
) -> list[dict[str, Any]]:
    """Filter telemetry events for reporting."""
    since_ts = _parse_timestamp(since)
    family_filter = str(family).strip() if family else ""
    filtered: list[dict[str, Any]] = []
    for event in events:
        if since_ts is not None and _event_ts(event) < since_ts:
            continue
        if family_filter and family_filter not in {
            _route_family(event),
            str(event.get("advisor_family") or ""),
        }:
            continue
        if mismatches_only and event.get("advisor_match") is not False:
            continue
        filtered.append(event)
    return filtered


def load_events(
    path: str | Path,
    *,
    limit: int | None = None,
    since: str | float | int | None = None,
    family: str | None = None,
    mismatches_only: bool = False,
) -> list[dict[str, Any]]:
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
    return filter_events(events, since=since, family=family, mismatches_only=mismatches_only)


def summarize_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    advisor_events: list[dict[str, Any]] = []
    for event in events:
        total += 1
        grouped[str(event.get("route") or "unknown")].append(event)
        grouped_families[_route_family(event)].append(event)
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

    families: list[dict[str, Any]] = []
    for route_family, items in sorted(grouped_families.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        errors = sum(1 for e in items if e.get("is_error"))
        route_advisor_events = [e for e in items if e.get("advisor_family")]
        advisor_mismatches = sum(1 for e in route_advisor_events if e.get("advisor_match") is False)
        families.append(
            {
                "route_family": route_family,
                "calls": len(items),
                "errors": errors,
                "error_rate": round(errors / len(items), 4) if items else 0.0,
                "routes": dict(Counter(str(e.get("route") or "unknown") for e in items).most_common(5)),
                "advisor_mismatches": advisor_mismatches,
                "advisor_mismatch_rate": round(advisor_mismatches / len(route_advisor_events), 4) if route_advisor_events else 0.0,
            }
        )

    mismatch_items = [e for e in advisor_events if e.get("advisor_match") is False]
    mismatch_sessions = []
    session_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in mismatch_items:
        session_items[str(event.get("session_id") or "unknown")].append(event)
    for session_id, items in sorted(session_items.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:10]:
        mismatch_sessions.append(
            {
                "session_id": session_id,
                "events": len(items),
                "advisor_families": dict(Counter(str(e.get("advisor_family") or "unknown") for e in items).most_common(5)),
                "route_families": dict(Counter(_route_family(e) for e in items).most_common(5)),
                "routes": dict(Counter(str(e.get("route") or "unknown") for e in items).most_common(5)),
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
        "mismatch_sessions": mismatch_sessions,
    }
    return {"events": total, "advisor": advisor, "families": families, "routes": routes}


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
        if advisor.get("mismatch_sessions"):
            lines.append("Mismatch sessions:")
            for row in advisor.get("mismatch_sessions") or []:
                route_text = ",".join(f"{route}:{count}" for route, count in (row.get("routes") or {}).items()) or "none"
                advisor_text = ",".join(f"{family}:{count}" for family, count in (row.get("advisor_families") or {}).items()) or "none"
                route_family_text = ",".join(f"{family}:{count}" for family, count in (row.get("route_families") or {}).items()) or "none"
                lines.append(
                    f"  - {row.get('session_id', 'unknown')}: events={row.get('events', 0)}, "
                    f"advisor={advisor_text}, actual_family={route_family_text}, routes={route_text}"
                )
    families = summary.get("families") or []
    if families:
        lines.append("Route families:")
        for row in families:
            route_text = ",".join(f"{route}:{count}" for route, count in (row.get("routes") or {}).items()) or "none"
            advisor_suffix = ""
            if row.get("advisor_mismatches"):
                advisor_suffix = ", advisor_mismatches={advisor_mismatches} ({advisor_mismatch_rate})".format(**row)
            lines.append(
                "- {route_family}: calls={calls}, errors={errors}, top_routes=".format(**row)
                + route_text
                + advisor_suffix
            )
    routes = summary.get("routes") or []
    if not routes:
        lines.append("No route events found.")
        return "\n".join(lines)
    lines.append("Routes:")
    for row in routes:
        advisor_suffix = ""
        if row.get("advisor_mismatches"):
            advisor_suffix = ", advisor_mismatches={advisor_mismatches} ({advisor_mismatch_rate})".format(**row)
        lines.append(
            ("- {route}: calls={calls}, errors={errors}, avg_duration={avg_duration_s}s, "
             "avg_result_chars={avg_result_chars}, sessions={sessions}" + advisor_suffix).format(**row)
        )
    return "\n".join(lines)


def build_report(
    path: str | None = None,
    *,
    limit: int | None = None,
    since: str | float | int | None = None,
    family: str | None = None,
    mismatches_only: bool = False,
    json_output: bool = False,
) -> str:
    p = resolve_input_path(path)
    events = load_events(p, limit=limit, since=since, family=family, mismatches_only=mismatches_only)
    summary = summarize_events(events)
    if json_output:
        return json.dumps({"source": str(p), **summary}, ensure_ascii=False, sort_keys=True, indent=2)
    header = f"Source: {p}"
    filters = []
    if limit:
        filters.append(f"limit={limit}")
    if since:
        filters.append(f"since={since}")
    if family:
        filters.append(f"family={family}")
    if mismatches_only:
        filters.append("mismatches_only=true")
    if filters:
        header += "\nFilters: " + ", ".join(filters)
    return header + "\n" + format_summary(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize Hermes context-efficiency telemetry JSONL logs.")
    parser.add_argument("path", nargs="?", help="Telemetry JSONL path; defaults to HERMES_HOME/logs/context_efficiency.jsonl")
    parser.add_argument("--limit", type=int, default=None, help="Only read the last N log lines before applying filters")
    parser.add_argument("--since", help="Only include events at or after a Unix timestamp or ISO-8601 time")
    parser.add_argument("--family", help="Only include events whose route_family or advisor_family matches this value")
    parser.add_argument("--mismatches-only", action="store_true", help="Only include advisor mismatch events")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON summary")
    args = parser.parse_args(argv)
    print(
        build_report(
            args.path,
            limit=args.limit,
            since=args.since,
            family=args.family,
            mismatches_only=args.mismatches_only,
            json_output=args.json_output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
