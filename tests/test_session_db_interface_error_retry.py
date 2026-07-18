"""Regression: SessionDB must retry the sqlite3 statement-cache race
(``sqlite3.InterfaceError: no more rows available``) instead of dropping
canonical messages.

CPython 3.12+ can raise ``sqlite3.InterfaceError`` when a cached prepared
statement is reused across threads on a ``check_same_thread=False`` connection.
SessionDB uses such connections for the shared state.db. The symptom surfaces
in production as ``Session DB append_message failed: no more rows available``
because ``run_agent.py`` swallows the exception.

These tests pin the retry/failure behaviour, not the warning log.
"""

import sqlite3

import pytest

from hermes_state import SessionDB


class TestSessionDBInterfaceErrorRetry:
    def test_append_message_retries_on_no_more_rows_available(self, tmp_path):
        """A transient InterfaceError on the first INSERT must not lose the message."""
        db = SessionDB(db_path=tmp_path / "interface_error.db")
        try:
            db.create_session(session_id="s1", source="cli")

            calls = []
            orig_conn = db._conn
            raised = False

            class FlakyConnection:
                def __init__(self, real):
                    self._real = real

                def __getattr__(self, name):
                    return getattr(self._real, name)

                def execute(self, sql, *args, **kwargs):
                    calls.append(str(sql))
                    nonlocal raised
                    if not raised and "INSERT INTO messages" in str(sql):
                        raised = True
                        raise sqlite3.InterfaceError("no more rows available")
                    return self._real.execute(sql, *args, **kwargs)

            db._conn = FlakyConnection(orig_conn)

            msg_id = db.append_message("s1", role="user", content="hello")

            assert msg_id is not None
            assert len(db.get_messages("s1")) == 1
            assert db.get_messages("s1")[0]["content"] == "hello"
            assert len(calls) >= 4, "expected BEGIN + failed INSERT + retry cycle"
        finally:
            db.close()

    def test_append_message_propagates_after_max_interface_retries(self, tmp_path):
        """A persistent InterfaceError must raise after exhausting retries."""
        db = SessionDB(db_path=tmp_path / "interface_error_persist.db")
        try:
            db.create_session(session_id="s1", source="cli")

            orig_conn = db._conn

            class FlakyConnection:
                def __init__(self, real):
                    self._real = real

                def __getattr__(self, name):
                    return getattr(self._real, name)

                def execute(self, *_args, **_kwargs):
                    raise sqlite3.InterfaceError("no more rows available")

            db._conn = FlakyConnection(orig_conn)

            with pytest.raises(sqlite3.InterfaceError):
                db.append_message("s1", role="user", content="hello")
        finally:
            db.close()

    def test_main_connection_uses_cached_statements_zero(self, tmp_path, monkeypatch):
        """The primary connection disables statement caching to prevent the race."""
        captured = {}
        real_connect = sqlite3.connect

        def spy_connect(*args, **kwargs):
            # Capture only the first main read/write connection attempt.
            if not captured and len(args) > 0 and str(args[0]) == str(tmp_path / "cs.db"):
                captured["kwargs"] = dict(kwargs)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", spy_connect)
        db = SessionDB(db_path=tmp_path / "cs.db")
        try:
            assert "cached_statements" in captured.get("kwargs", {}), (
                "sqlite3.connect was not captured for the main connection"
            )
            assert captured["kwargs"]["cached_statements"] == 0
        finally:
            db.close()
