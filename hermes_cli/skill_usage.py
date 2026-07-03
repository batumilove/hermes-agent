"""``hermes skill-usage`` CLI command implementation.

Thin wrapper around ``agent.skill_usage.SkillUsageEngine``.  Handles profile
selection, output formatting, and CSV/JSON rendering.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, TextIO


def _profile_state_db(profile: str) -> Path:
    """Return the state.db path for a named Hermes profile."""
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "profiles" / profile / "state.db"


def _open_db(profile: Optional[str] = None):
    """Open a SessionDB for the requested profile (or the default one)."""
    from hermes_state import SessionDB

    if profile:
        db_path = _profile_state_db(profile)
        if not db_path.exists():
            print(
                f"Profile state database not found: {db_path}", file=sys.stderr
            )
            sys.exit(1)
        return SessionDB(db_path=db_path, read_only=True)
    return SessionDB()


def _render_markdown(report: Dict[str, Any], limit: int = 10) -> str:
    """Human-readable Markdown/plain-text report."""
    lines: list[str] = []
    lines.append(
        f"# Skill usage report ({report['days']} day window, "
        f"source={report['source_filter'] or 'all'})"
    )
    lines.append("")

    if report["empty"]:
        lines.append("No sessions found in the requested window.")
        return "\n".join(lines)

    lines.append(f"- Sessions analyzed: {report['sessions']}")
    lines.append(
        f"- Sessions with skill loads: {report['sessions_with_skill_loads']} "
        f"({report['percent_sessions_with_skill_loads']:.1f}%)"
    )
    lines.append(f"- Total skill_view calls: {report['total_skill_view_calls']}")
    lines.append("")

    lines.append("## Fixed skill-index context")
    c = report["fixed_skill_index_chars"]
    t = report["fixed_skill_index_tokens"]
    lines.append(
        f"- Average chars: {report['avg_fixed_skill_index_chars']:,} "
        f"(median {c['median']:,}, P90 {c['p90']:,})"
    )
    lines.append(
        f"- Average tokens: {report['avg_fixed_skill_index_tokens']:,} "
        f"(median {t['median']:,}, P90 {t['p90']:,})"
    )
    lines.append("")

    lines.append("## Loaded skill payload context")
    c = report["loaded_skill_chars"]
    t = report["loaded_skill_tokens"]
    lines.append(
        f"- Average chars per session: {report['avg_loaded_skill_chars']:,} "
        f"(median {c['median']:,}, P90 {c['p90']:,})"
    )
    lines.append(
        f"- Average tokens per session: {report['avg_loaded_skill_tokens']:,} "
        f"(median {t['median']:,}, P90 {t['p90']:,})"
    )
    c_load = report["loaded_skill_chars_per_load_session"]
    t_load = report["loaded_skill_tokens_per_load_session"]
    lines.append(
        f"- Average chars per load-session: "
        f"{report['avg_loaded_skill_chars_per_load_session']:,} "
        f"(median {c_load['median']:,}, P90 {c_load['p90']:,})"
    )
    lines.append(
        f"- Average tokens per load-session: "
        f"{report['avg_loaded_skill_tokens_per_load_session']:,} "
        f"(median {t_load['median']:,}, P90 {t_load['p90']:,})"
    )
    lines.append("")

    lines.append("## Top loaded skills")
    if report["top_skills"]:
        for skill in report["top_skills"][:limit]:
            lines.append(
                f"- {skill['skill']}: {skill['views']} views, "
                f"{skill['chars']:,} chars, {skill['tokens']:,} tokens"
            )
    else:
        lines.append("- No skill loads recorded.")
    lines.append("")

    lines.append("## Skill co-occurrence (same session)")
    if report["cooccurrence"]:
        for pair in report["cooccurrence"][:limit]:
            a, b = pair["pair"]
            lines.append(f"- {a} + {b}: {pair['sessions']} sessions")
    else:
        lines.append("- No co-occurrence data.")
    lines.append("")

    return "\n".join(lines)


def _flatten(report: Dict[str, Any]) -> Dict[str, Any]:
    """Return a flat dict of summary fields for CSV export."""
    return {
        "days": report["days"],
        "source_filter": report["source_filter"] or "",
        "generated_at": report["generated_at"],
        "empty": report["empty"],
        "sessions": report["sessions"],
        "sessions_with_skill_loads": report["sessions_with_skill_loads"],
        "percent_sessions_with_skill_loads": round(
            report["percent_sessions_with_skill_loads"], 2
        ),
        "total_skill_view_calls": report["total_skill_view_calls"],
        "avg_skill_view_calls_per_session": report["skill_view_calls_per_session"]["avg"],
        "median_skill_view_calls_per_session": report["skill_view_calls_per_session"]["median"],
        "p90_skill_view_calls_per_session": report["skill_view_calls_per_session"]["p90"],
        "avg_unique_loaded_skills_per_session": report[
            "unique_loaded_skills_per_session"
        ]["avg"],
        "avg_fixed_skill_index_chars": report["avg_fixed_skill_index_chars"],
        "avg_fixed_skill_index_tokens": report["avg_fixed_skill_index_tokens"],
        "avg_loaded_skill_chars": report["avg_loaded_skill_chars"],
        "avg_loaded_skill_tokens": report["avg_loaded_skill_tokens"],
        "avg_loaded_skill_chars_per_load_session": report[
            "avg_loaded_skill_chars_per_load_session"
        ],
        "avg_loaded_skill_tokens_per_load_session": report[
            "avg_loaded_skill_tokens_per_load_session"
        ],
    }


def _write_csv(report: Dict[str, Any], f: TextIO) -> None:
    """Write a two-row CSV: summary row + per-session rows."""
    summary = _flatten(report)

    # Summary row
    writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
    writer.writeheader()
    writer.writerow(summary)

    f.write("\n")

    # Per-session rows
    if report["per_session"]:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "session_id",
                "source",
                "started_at",
                "skill_view_calls",
                "unique_loaded_skills",
                "fixed_skill_index_chars",
                "fixed_skill_index_tokens",
                "loaded_skill_chars",
                "loaded_skill_tokens",
            ],
        )
        writer.writeheader()
        for s in report["per_session"]:
            writer.writerow(
                {
                    "session_id": s["session_id"],
                    "source": s["source"],
                    "started_at": s["started_at"],
                    "skill_view_calls": s["skill_view_calls"],
                    "unique_loaded_skills": s["unique_loaded_skills"],
                    "fixed_skill_index_chars": s["fixed_skill_index_chars"],
                    "fixed_skill_index_tokens": s["fixed_skill_index_tokens"],
                    "loaded_skill_chars": s["loaded_skill_chars"],
                    "loaded_skill_tokens": s["loaded_skill_tokens"],
                }
            )


def skill_usage_command(args) -> None:
    """Entry point for ``hermes skill-usage``."""
    from agent.skill_usage import SkillUsageEngine

    db = _open_db(profile=getattr(args, "profile", None))
    try:
        engine = SkillUsageEngine(db)
        report = engine.generate(
            days=args.days,
            source=args.source or None,
        )

        limit = getattr(args, "limit", 10)
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, default=str))
        elif getattr(args, "csv", False):
            _write_csv(report, sys.stdout)
        else:
            print(_render_markdown(report, limit=limit))
    finally:
        db.close()
