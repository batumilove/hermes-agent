"""Skill usage / context-cost investigation engine.

Analyzes historical Hermes sessions to report how often skills are loaded via
``skill_view``, how much context they add to the conversation, and how large
the fixed ``<available_skills>`` system-prompt index is.

Privacy-safe: the report contains only aggregate sizes and skill names.  It
never emits raw system prompts, skill bodies, or message content.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from hermes_cli.prompt_size import _SKILLS_BLOCK_RE


def _token_estimate(s: str | int | None) -> int:
    """Rough token estimate used for prompt context sizing: chars / 4."""
    if not s:
        return 0
    if isinstance(s, int):
        return round(s / 4)
    return round(len(s) / 4)


def _percentile(values: Iterable[int], p: int) -> int:
    """Nearest-rank percentile (p in 0..100). Returns 0 for an empty list."""
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0
    rank = math.ceil(p / 100 * n)
    rank = max(1, min(rank, n))
    return vals[rank - 1]


def _mean(values: Iterable[int]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


class SkillUsageEngine:
    """Analyze skill_view usage and fixed/loaded skill context cost."""

    def __init__(self, db: Any):
        """
        Args:
            db: A SessionDB-like object with a ``_conn`` sqlite3 attribute, or
                a raw sqlite3 connection.
        """
        self._conn = getattr(db, "_conn", db)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate(
        self, days: int = 30, source: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a skill-usage and context-cost report.

        Args:
            days: Number of days to look back.
            source: Optional platform source filter (e.g. ``cli``,
                ``telegram``).

        Returns:
            Dict report with summary stats, top skills, co-occurrence pairs,
            and per-session data suitable for profile-split simulation.
        """
        cutoff = time.time() - days * 86400
        sessions = self._get_sessions(cutoff, source)

        if not sessions:
            return self._empty_report(days, source)

        # session_id -> list of skill_view call records
        skill_calls = self._get_skill_view_calls(cutoff, source)
        # (session_id, tool_call_id) -> content length
        response_lengths = self._get_tool_response_lengths(cutoff, source)

        per_session = self._build_per_session(sessions, skill_calls, response_lengths)
        summary = self._compute_summary(per_session)
        top_skills = self._compute_top_skills(skill_calls, response_lengths)
        cooccurrence = self._compute_cooccurrence(per_session)

        return {
            "days": days,
            "source_filter": source,
            "generated_at": time.time(),
            "empty": False,
            "sessions": len(per_session),
            "sessions_with_skill_loads": summary["sessions_with_skill_loads"],
            "percent_sessions_with_skill_loads": summary[
                "percent_sessions_with_skill_loads"
            ],
            "total_skill_view_calls": summary["total_skill_view_calls"],
            "skill_view_calls_per_session": summary["skill_view_calls_per_session"],
            "unique_loaded_skills_per_session": summary[
                "unique_loaded_skills_per_session"
            ],
            "fixed_skill_index_chars": summary["fixed_skill_index_chars"],
            "fixed_skill_index_tokens": summary["fixed_skill_index_tokens"],
            "loaded_skill_chars": summary["loaded_skill_chars"],
            "loaded_skill_tokens": summary["loaded_skill_tokens"],
            "loaded_skill_chars_per_load_session": summary[
                "loaded_skill_chars_per_load_session"
            ],
            "loaded_skill_tokens_per_load_session": summary[
                "loaded_skill_tokens_per_load_session"
            ],
            "avg_fixed_skill_index_chars": summary["fixed_skill_index_chars"]["avg"],
            "avg_fixed_skill_index_tokens": summary["fixed_skill_index_tokens"]["avg"],
            "avg_loaded_skill_chars": summary["loaded_skill_chars"]["avg"],
            "avg_loaded_skill_tokens": summary["loaded_skill_tokens"]["avg"],
            "avg_loaded_skill_chars_per_load_session": summary[
                "loaded_skill_chars_per_load_session"
            ]["avg"],
            "avg_loaded_skill_tokens_per_load_session": summary[
                "loaded_skill_tokens_per_load_session"
            ]["avg"],
            "top_skills": top_skills,
            "cooccurrence": cooccurrence,
            "per_session": per_session,
        }

    # -----------------------------------------------------------------------
    # Data gathering
    # -----------------------------------------------------------------------

    def _get_sessions(
        self, cutoff: float, source: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch sessions in the window, selecting only columns we need."""
        cols = "id, source, system_prompt, started_at"
        if source:
            cursor = self._conn.execute(
                f"SELECT {cols} FROM sessions WHERE started_at >= ? AND source = ?",
                (cutoff, source),
            )
        else:
            cursor = self._conn.execute(
                f"SELECT {cols} FROM sessions WHERE started_at >= ?", (cutoff,)
            )
        return [dict(row) for row in cursor.fetchall()]

    def _get_skill_view_calls(
        self, cutoff: float, source: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return one record per skill_view tool call from assistant messages."""
        if source:
            cursor = self._conn.execute(
                """SELECT m.session_id, m.tool_calls, m.timestamp
                   FROM messages m
                   JOIN sessions s ON s.id = m.session_id
                   WHERE s.started_at >= ? AND s.source = ?
                     AND m.role = 'assistant' AND m.tool_calls IS NOT NULL""",
                (cutoff, source),
            )
        else:
            cursor = self._conn.execute(
                """SELECT m.session_id, m.tool_calls, m.timestamp
                   FROM messages m
                   JOIN sessions s ON s.id = m.session_id
                   WHERE s.started_at >= ?
                     AND m.role = 'assistant' AND m.tool_calls IS NOT NULL""",
                (cutoff,),
            )

        calls: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            tool_calls = row["tool_calls"]
            if isinstance(tool_calls, str):
                try:
                    tool_calls = json.loads(tool_calls)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(tool_calls, list):
                continue

            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                func = call.get("function", {}) or {}
                if func.get("name") != "skill_view":
                    continue

                args = func.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not isinstance(args, dict):
                    continue

                skill_name = args.get("name")
                if not isinstance(skill_name, str) or not skill_name.strip():
                    continue

                calls.append(
                    {
                        "session_id": row["session_id"],
                        "skill": skill_name.strip(),
                        "tool_call_id": call.get("id") or call.get("call_id"),
                        "timestamp": row["timestamp"],
                    }
                )
        return calls

    def _get_tool_response_lengths(
        self, cutoff: float, source: Optional[str] = None
    ) -> Dict[Tuple[str, str], int]:
        """Return map of (session_id, tool_call_id) -> content char length."""
        if source:
            cursor = self._conn.execute(
                """SELECT m.session_id, m.tool_call_id, m.content
                   FROM messages m
                   JOIN sessions s ON s.id = m.session_id
                   WHERE s.started_at >= ? AND s.source = ?
                     AND m.role = 'tool' AND m.tool_call_id IS NOT NULL""",
                (cutoff, source),
            )
        else:
            cursor = self._conn.execute(
                """SELECT m.session_id, m.tool_call_id, m.content
                   FROM messages m
                   JOIN sessions s ON s.id = m.session_id
                   WHERE s.started_at >= ?
                     AND m.role = 'tool' AND m.tool_call_id IS NOT NULL""",
                (cutoff,),
            )

        lengths: Dict[Tuple[str, str], int] = {}
        for row in cursor.fetchall():
            key = (row["session_id"], row["tool_call_id"])
            content = row["content"] or ""
            lengths[key] = len(content)
        return lengths

    # -----------------------------------------------------------------------
    # Computation
    # -----------------------------------------------------------------------

    def _build_per_session(
        self,
        sessions: List[Dict[str, Any]],
        skill_calls: List[Dict[str, Any]],
        response_lengths: Dict[Tuple[str, str], int],
    ) -> List[Dict[str, Any]]:
        """Aggregate data per session."""
        # session_id -> list of calls
        calls_by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for call in skill_calls:
            calls_by_session[call["session_id"]].append(call)

        results: List[Dict[str, Any]] = []
        for session in sessions:
            session_id = session["id"]
            system_prompt = session.get("system_prompt") or ""
            skills_match = _SKILLS_BLOCK_RE.search(system_prompt)
            skills_block = skills_match.group(0) if skills_match else ""

            calls = calls_by_session.get(session_id, [])
            unique_skills = {c["skill"] for c in calls}

            loaded_chars = 0
            for call in calls:
                key = (call["session_id"], call["tool_call_id"])
                loaded_chars += response_lengths.get(key, 0)

            results.append(
                {
                    "session_id": session_id,
                    "source": session.get("source") or "",
                    "started_at": session.get("started_at"),
                    "skill_view_calls": len(calls),
                    "unique_loaded_skills": len(unique_skills),
                    "fixed_skill_index_chars": len(skills_block),
                    "fixed_skill_index_tokens": _token_estimate(skills_block),
                    "loaded_skill_chars": loaded_chars,
                    "loaded_skill_tokens": _token_estimate(loaded_chars),
                    "loaded_skills": sorted(unique_skills),
                }
            )
        return results

    def _compute_summary(
        self, per_session: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Compute summary stats across all sessions."""
        sessions = per_session
        sessions_with_loads = [s for s in sessions if s["skill_view_calls"] > 0]

        total_calls = sum(s["skill_view_calls"] for s in sessions)

        def _stats(values: List[int]) -> Dict[str, int]:
            return {
                "avg": round(_mean(values)),
                "median": _percentile(values, 50),
                "p90": _percentile(values, 90),
            }

        return {
            "sessions_with_skill_loads": len(sessions_with_loads),
            "percent_sessions_with_skill_loads": (
                len(sessions_with_loads) / len(sessions) * 100 if sessions else 0.0
            ),
            "total_skill_view_calls": total_calls,
            "skill_view_calls_per_session": _stats(
                [s["skill_view_calls"] for s in sessions]
            ),
            "unique_loaded_skills_per_session": _stats(
                [s["unique_loaded_skills"] for s in sessions]
            ),
            "fixed_skill_index_chars": _stats(
                [s["fixed_skill_index_chars"] for s in sessions]
            ),
            "fixed_skill_index_tokens": _stats(
                [s["fixed_skill_index_tokens"] for s in sessions]
            ),
            "loaded_skill_chars": _stats(
                [s["loaded_skill_chars"] for s in sessions]
            ),
            "loaded_skill_tokens": _stats(
                [s["loaded_skill_tokens"] for s in sessions]
            ),
            "loaded_skill_chars_per_load_session": _stats(
                [s["loaded_skill_chars"] for s in sessions_with_loads]
            )
            if sessions_with_loads
            else {"avg": 0, "median": 0, "p90": 0},
            "loaded_skill_tokens_per_load_session": _stats(
                [s["loaded_skill_tokens"] for s in sessions_with_loads]
            )
            if sessions_with_loads
            else {"avg": 0, "median": 0, "p90": 0},
        }

    def _compute_top_skills(
        self,
        skill_calls: List[Dict[str, Any]],
        response_lengths: Dict[Tuple[str, str], int],
    ) -> List[Dict[str, Any]]:
        """Rank skills by number of skill_view loads."""
        view_counts = Counter()
        char_totals: Dict[str, int] = defaultdict(int)

        for call in skill_calls:
            skill = call["skill"]
            view_counts[skill] += 1
            key = (call["session_id"], call["tool_call_id"])
            char_totals[skill] += response_lengths.get(key, 0)

        return [
            {
                "skill": skill,
                "views": count,
                "chars": char_totals[skill],
                "tokens": _token_estimate(char_totals[skill]),
            }
            for skill, count in view_counts.most_common()
        ]

    def _compute_cooccurrence(
        self, per_session: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Count how often pairs of skills appear together in the same session."""
        pair_counts = Counter()
        for session in per_session:
            skills = sorted(session.get("loaded_skills", []))
            if len(skills) >= 2:
                for a, b in itertools.combinations(skills, 2):
                    pair_counts[(a, b)] += 1

        return [
            {"pair": list(pair), "sessions": count}
            for pair, count in pair_counts.most_common()
        ]

    def _empty_report(
        self, days: int, source: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return a consistent report shape when there is no data."""
        zero_stats = {"avg": 0, "median": 0, "p90": 0}
        return {
            "days": days,
            "source_filter": source,
            "generated_at": time.time(),
            "empty": True,
            "sessions": 0,
            "sessions_with_skill_loads": 0,
            "percent_sessions_with_skill_loads": 0.0,
            "total_skill_view_calls": 0,
            "skill_view_calls_per_session": zero_stats.copy(),
            "unique_loaded_skills_per_session": zero_stats.copy(),
            "fixed_skill_index_chars": zero_stats.copy(),
            "fixed_skill_index_tokens": zero_stats.copy(),
            "loaded_skill_chars": zero_stats.copy(),
            "loaded_skill_tokens": zero_stats.copy(),
            "loaded_skill_chars_per_load_session": zero_stats.copy(),
            "loaded_skill_tokens_per_load_session": zero_stats.copy(),
            "avg_fixed_skill_index_chars": 0,
            "avg_fixed_skill_index_tokens": 0,
            "avg_loaded_skill_chars": 0,
            "avg_loaded_skill_tokens": 0,
            "avg_loaded_skill_chars_per_load_session": 0,
            "avg_loaded_skill_tokens_per_load_session": 0,
            "top_skills": [],
            "cooccurrence": [],
            "per_session": [],
        }
