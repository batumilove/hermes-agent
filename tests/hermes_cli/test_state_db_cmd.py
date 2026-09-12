"""Tests for the canonical read-only state.db inspection command."""

import sqlite3

import pytest

from hermes_cli.state_db_cmd import query_state_db


def _make_db(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    connection = sqlite3.connect(home / "state.db")
    connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    connection.executemany(
        "INSERT INTO messages(body) VALUES (?)",
        [("alpha",), ("beta",)],
    )
    connection.commit()
    connection.close()
    return home


def test_query_state_db_returns_named_rows(tmp_path):
    home = _make_db(tmp_path)

    result = query_state_db(
        "SELECT id, body FROM messages ORDER BY id",
        hermes_home=home,
    )

    assert result["columns"] == ["id", "body"]
    assert result["rows"] == [[1, "alpha"], [2, "beta"]]
    assert result["truncated"] is False
    assert result["sqlite_version"] == sqlite3.sqlite_version


def test_query_state_db_uses_shared_tracked_read_only_connection(tmp_path, monkeypatch):
    from hermes_cli import sqlite_safe_read

    home = _make_db(tmp_path)
    seen = {}
    original = sqlite_safe_read.connect_tracked

    def _tracked(path, **kwargs):
        seen["path"] = path
        seen["kwargs"] = kwargs
        return original(path, **kwargs)

    monkeypatch.setattr(sqlite_safe_read, "connect_tracked", _tracked)

    query_state_db("SELECT 1", hermes_home=home)

    assert str(seen["path"]).startswith("file:")
    assert seen["path"].endswith("?mode=ro")
    assert seen["kwargs"]["tracking_path"] == (home / "state.db").resolve()
    assert seen["kwargs"]["uri"] is True


def test_query_state_db_enforces_row_limit(tmp_path):
    home = _make_db(tmp_path)

    result = query_state_db(
        "SELECT id FROM messages ORDER BY id",
        hermes_home=home,
        max_rows=1,
    )

    assert result["rows"] == [[1]]
    assert result["truncated"] is True


def test_query_state_db_rejects_writes(tmp_path):
    home = _make_db(tmp_path)

    with pytest.raises(sqlite3.DatabaseError):
        query_state_db(
            "DELETE FROM messages",
            hermes_home=home,
        )

    connection = sqlite3.connect(home / "state.db")
    try:
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    finally:
        connection.close()


def test_query_state_db_rejects_attach(tmp_path):
    home = _make_db(tmp_path)

    with pytest.raises(sqlite3.DatabaseError):
        query_state_db(
            "ATTACH DATABASE ':memory:' AS extra",
            hermes_home=home,
        )


def test_query_state_db_rejects_multiple_statements(tmp_path):
    home = _make_db(tmp_path)

    with pytest.raises(sqlite3.ProgrammingError):
        query_state_db(
            "SELECT 1; SELECT 2",
            hermes_home=home,
        )
