"""Focused tests for low-risk state.db read-path tuning."""

import sqlite3
from typing import cast

from hermes_state import STATE_DB_MMAP_SIZE, SessionDB, apply_state_db_read_tuning


class RecordingConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement: str):
        self.statements.append(statement)


def test_read_tuning_normalizes_supplied_wal_mode_case_insensitively():
    conn = RecordingConnection()

    apply_state_db_read_tuning(
        cast(sqlite3.Connection, conn),
        journal_mode="WaL",
    )

    assert conn.statements == [f"PRAGMA mmap_size={STATE_DB_MMAP_SIZE}"]


def test_read_tuning_does_not_change_cache_size(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        cache_size_before = conn.execute("PRAGMA cache_size").fetchone()[0]

        apply_state_db_read_tuning(conn, journal_mode="wal")

        assert conn.execute("PRAGMA cache_size").fetchone()[0] == cache_size_before
    finally:
        conn.close()


def test_read_tuning_does_not_enable_mmap_in_delete_mode():
    conn = RecordingConnection()

    apply_state_db_read_tuning(
        cast(sqlite3.Connection, conn),
        journal_mode="DELETE",
    )

    assert conn.statements == []


def test_session_db_initializes_tuned_read_write_connection(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        assert db._conn is not None
    finally:
        db.close()


def test_session_db_initializes_tuned_read_only_connection(tmp_path):
    db_path = tmp_path / "state.db"
    writer = SessionDB(db_path=db_path)
    writer.close()

    reader = SessionDB(db_path=db_path, read_only=True)
    try:
        assert reader._conn is not None
    finally:
        reader.close()


def test_session_schema_has_partial_prune_candidate_index(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        conn = db._conn
        assert conn is not None
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='idx_sessions_prunable_started'"
        ).fetchone()
        assert row is not None
        normalized = " ".join(row[0].lower().split())
        assert "on sessions(started_at)" in normalized
        assert "where ended_at is not null and archived = 0" in normalized
    finally:
        db.close()


def test_prune_candidate_query_uses_partial_started_at_index(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        conn = db._conn
        assert conn is not None
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM sessions "
            "WHERE ended_at IS NOT NULL "
            "AND archived = 0 "
            "AND started_at < ?",
            (1_000_000.0,),
        ).fetchall()

        assert any(
            "USING INDEX idx_sessions_prunable_started" in row[3] for row in plan
        ), plan
    finally:
        db.close()
