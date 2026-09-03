"""Retry of transient ``no more rows available`` SQLite engine errors.

Under concurrent WAL writers, SQLite can surface this transient engine failure
through different ``sqlite3`` exception classes. The policy is message-scoped:
known transient failures retry within the existing bounded patience window,
while unrelated errors still propagate immediately.
"""

import sqlite3

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionDB, "_WRITE_PATIENCE_S", 2.0)
    monkeypatch.setattr(SessionDB, "_WRITE_RETRY_MIN_S", 0.001)
    monkeypatch.setattr(SessionDB, "_WRITE_RETRY_MAX_S", 0.005)
    database = SessionDB(db_path=tmp_path / "state.db")
    yield database
    database.close()


class TestNoMoreRowsRetry:
    def test_transient_interface_error_is_retried_to_success(self, db):
        calls = {"n": 0}

        def flaky(conn):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise sqlite3.InterfaceError("no more rows available")
            conn.execute(
                "INSERT INTO state_meta (key, value) VALUES ('nmr', 'ok') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            return "done"

        assert db._execute_write(flaky) == "done"
        assert calls["n"] == 4
        assert db.get_meta("nmr") == "ok"

    def test_unrelated_interface_error_propagates_immediately(self, db):
        calls = {"n": 0}

        def broken(conn):
            calls["n"] += 1
            raise sqlite3.InterfaceError("bad parameter or other API misuse")

        with pytest.raises(sqlite3.InterfaceError, match="bad parameter"):
            db._execute_write(broken)
        assert calls["n"] == 1

    def test_no_more_rows_via_database_error_is_retried(self, db):
        calls = {"n": 0}

        def flaky(conn):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise sqlite3.DatabaseError("no more rows available")
            return "ok"

        assert db._execute_write(flaky) == "ok"
        assert calls["n"] == 3

    def test_exhausted_patience_propagates_the_transient_error(self, db, monkeypatch):
        monkeypatch.setattr(SessionDB, "_WRITE_PATIENCE_S", 0.05)

        def always(conn):
            raise sqlite3.InterfaceError("no more rows available")

        with pytest.raises(sqlite3.InterfaceError, match="no more rows"):
            db._execute_write(always)
