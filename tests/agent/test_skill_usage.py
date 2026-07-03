"""Tests for agent/skill_usage.py — SkillUsageEngine.

Test-first design: these tests assume the public API described in the spec
and will fail until the implementation is written.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from agent.skill_usage import SkillUsageEngine, _percentile, _token_estimate


class _FakeDB:
    """Minimal wrapper that exposes ``_conn`` so SkillUsageEngine can attach."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            system_prompt TEXT,
            started_at REAL NOT NULL,
            ended_at REAL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL
        );
        """
    )


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    source: str,
    started_at: float,
    system_prompt: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sessions (id, source, system_prompt, started_at) VALUES (?, ?, ?, ?)",
        (session_id, source, system_prompt, started_at),
    )


def _insert_assistant_call(
    conn: sqlite3.Connection,
    session_id: str,
    tool_call_id: str,
    function_name: str,
    arguments: dict,
    timestamp: float,
) -> None:
    tool_calls = [
        {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": function_name,
                "arguments": json.dumps(arguments),
            },
        }
    ]
    conn.execute(
        "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, "assistant", json.dumps(tool_calls), timestamp),
    )


def _insert_tool_response(
    conn: sqlite3.Connection,
    session_id: str,
    tool_call_id: str,
    content: str,
    timestamp: float,
) -> None:
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, timestamp)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, "tool", content, tool_call_id, "skill_view", timestamp),
    )


@pytest.fixture
def fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    yield conn
    conn.close()


def test_empty_db_returns_empty_report(fresh_db):
    engine = SkillUsageEngine(_FakeDB(fresh_db))
    report = engine.generate(days=30)
    assert report["empty"] is True
    assert report["sessions"] == 0
    assert report["sessions_with_skill_loads"] == 0
    assert report["top_skills"] == []
    assert report["cooccurrence"] == []


def test_skill_view_counts_measured(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now, system_prompt=None)
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "github-operations"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", "github ops skill body", now)
    _insert_assistant_call(fresh_db, "s1", "tc2", "skill_view", {"name": "github-operations"}, now + 1)
    _insert_tool_response(fresh_db, "s1", "tc2", "more github body", now + 1)
    _insert_assistant_call(fresh_db, "s1", "tc3", "skill_view", {"name": "hermes-agent"}, now + 2)
    _insert_tool_response(fresh_db, "s1", "tc3", "hermes agent skill", now + 2)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    assert report["sessions"] == 1
    assert report["sessions_with_skill_loads"] == 1
    assert report["total_skill_view_calls"] == 3
    assert report["per_session"][0]["skill_view_calls"] == 3
    assert report["per_session"][0]["unique_loaded_skills"] == 2


def test_unique_loaded_skills_per_session(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now)
    _insert_session(fresh_db, "s2", "cli", now)
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "a"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", "body", now)
    _insert_assistant_call(fresh_db, "s2", "tc2", "skill_view", {"name": "a"}, now)
    _insert_tool_response(fresh_db, "s2", "tc2", "body", now)
    _insert_assistant_call(fresh_db, "s2", "tc3", "skill_view", {"name": "b"}, now + 1)
    _insert_tool_response(fresh_db, "s2", "tc3", "body", now + 1)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    per = {p["session_id"]: p for p in report["per_session"]}
    assert per["s1"]["unique_loaded_skills"] == 1
    assert per["s2"]["unique_loaded_skills"] == 2


def test_fixed_skill_index_chars_tokens_from_system_prompt(fresh_db):
    now = time.time()
    skills_block = "<available_skills>\n- github-operations\n- hermes-agent\n</available_skills>"
    system_prompt = f"identity\n{skills_block}\nmore"
    _insert_session(fresh_db, "s1", "cli", now, system_prompt=system_prompt)
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "github-operations"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", "skill body", now)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    per = report["per_session"][0]
    assert per["fixed_skill_index_chars"] == len(skills_block)
    assert per["fixed_skill_index_tokens"] == _token_estimate(skills_block)
    assert report["avg_fixed_skill_index_chars"] == len(skills_block)


def test_loaded_skill_payload_chars_tokens(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now)
    payload = "loaded skill payload"
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "github-operations"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", payload, now)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    per = report["per_session"][0]
    assert per["loaded_skill_chars"] == len(payload)
    assert per["loaded_skill_tokens"] == _token_estimate(payload)
    assert report["avg_loaded_skill_chars"] == len(payload)


def test_top_skills_ranked(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now)
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "github-operations"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", "a", now)
    _insert_assistant_call(fresh_db, "s1", "tc2", "skill_view", {"name": "github-operations"}, now + 1)
    _insert_tool_response(fresh_db, "s1", "tc2", "b", now + 1)
    _insert_assistant_call(fresh_db, "s1", "tc3", "skill_view", {"name": "hermes-agent"}, now + 2)
    _insert_tool_response(fresh_db, "s1", "tc3", "c", now + 2)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    top = report["top_skills"]
    assert [s["skill"] for s in top] == ["github-operations", "hermes-agent"]
    assert top[0]["views"] == 2
    assert top[1]["views"] == 1


def test_skill_cooccurrence_pairs(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now)
    for i, name in enumerate(["a", "b", "c"]):
        _insert_assistant_call(fresh_db, "s1", f"tc{i}", "skill_view", {"name": name}, now + i)
        _insert_tool_response(fresh_db, "s1", f"tc{i}", "body", now + i)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    pairs = {tuple(p["pair"]): p["sessions"] for p in report["cooccurrence"]}
    assert ("a", "b") in pairs
    assert ("a", "c") in pairs
    assert ("b", "c") in pairs
    assert all(v == 1 for v in pairs.values())


def test_percentile_nearest_rank(fresh_db):
    # values [1,2,3,4,5,6,7,8,9,10] -> P90 nearest-rank = 9
    values = list(range(1, 11))
    assert _percentile(values, 90) == 9
    assert _percentile(values, 50) == 5
    assert _percentile([], 50) == 0


def test_source_filter(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now)
    _insert_session(fresh_db, "s2", "telegram", now)
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "a"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", "body", now)
    _insert_assistant_call(fresh_db, "s2", "tc2", "skill_view", {"name": "b"}, now)
    _insert_tool_response(fresh_db, "s2", "tc2", "body", now)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30, source="cli")
    assert report["sessions"] == 1
    assert report["top_skills"][0]["skill"] == "a"


def test_per_session_profile_split_data(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now)
    _insert_assistant_call(fresh_db, "s1", "tc1", "skill_view", {"name": "x"}, now)
    _insert_tool_response(fresh_db, "s1", "tc1", "p" * 40, now)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    per = report["per_session"][0]
    assert per["session_id"] == "s1"
    assert per["source"] == "cli"
    assert per["skill_view_calls"] == 1
    assert per["unique_loaded_skills"] == 1
    assert per["fixed_skill_index_chars"] == 0
    assert per["loaded_skill_chars"] == 40
    assert "loaded_skill_tokens" in per


def test_days_window_excludes_old_sessions(fresh_db):
    now = time.time()
    _insert_session(fresh_db, "s1", "cli", now - 40 * 86400)
    _insert_session(fresh_db, "s2", "cli", now - 5 * 86400)
    _insert_assistant_call(fresh_db, "s2", "tc1", "skill_view", {"name": "a"}, now - 5 * 86400)
    _insert_tool_response(fresh_db, "s2", "tc1", "body", now - 5 * 86400)

    report = SkillUsageEngine(_FakeDB(fresh_db)).generate(days=30)
    assert report["sessions"] == 1
    assert report["per_session"][0]["session_id"] == "s2"
