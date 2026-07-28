"""Tests for session handoff (CLI to gateway platform).

The handoff state machine lives on the ``sessions`` table:

    None  → "pending" → "running" → ("completed" | "failed")

CLI side calls ``request_handoff`` and poll-waits on ``get_handoff_state``.
Gateway side iterates ``list_pending_handoffs``, calls ``claim_handoff`` to
flip pending → running, and finishes with ``complete_handoff`` or
``fail_handoff``.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from hermes_state import SessionDB


class TestHandoffStateDB:
    """Test the handoff schema + helper methods on SessionDB."""

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        return SessionDB(db_path=home / "state.db")

    def _make_session(self, db, session_id, source="cli", title=None):
        """Insert a session row directly for testing."""
        def _do(conn):
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, source, title, started_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, source, title, time.time()),
            )
        db._execute_write(_do)

    def test_columns_exist(self, db):
        db._conn.execute(
            "SELECT handoff_state, handoff_platform, handoff_error "
            "FROM sessions LIMIT 0"
        )

    def test_request_handoff_marks_pending(self, db):
        sid = "sess-1"
        self._make_session(db, sid)

        assert db.request_handoff(sid, "telegram") is True

        state = db.get_handoff_state(sid)
        assert state == {
            "state": "pending",
            "platform": "telegram",
            "error": None,
        }

    def test_request_handoff_rejects_in_flight(self, db):
        sid = "sess-2"
        self._make_session(db, sid)

        assert db.request_handoff(sid, "telegram") is True
        # Still pending → reject re-request
        assert db.request_handoff(sid, "discord") is False

        # And after gateway claims it (running) → still rejected
        assert db.claim_handoff(sid) is True
        assert db.request_handoff(sid, "discord") is False

    def test_request_handoff_after_terminal_state_resets_error(self, db):
        sid = "sess-3"
        self._make_session(db, sid)
        db.request_handoff(sid, "telegram")
        db.claim_handoff(sid)
        db.fail_handoff(sid, "earlier failure")

        # User retries — should be allowed and clear the prior error.
        assert db.request_handoff(sid, "discord") is True
        state = db.get_handoff_state(sid)
        assert state["state"] == "pending"
        assert state["platform"] == "discord"
        assert state["error"] is None

    def test_list_pending_handoffs_excludes_running_and_terminal(self, db):
        a, b, c, d = "sess-a", "sess-b", "sess-c", "sess-d"
        for sid in (a, b, c, d):
            self._make_session(db, sid)

        db.request_handoff(a, "telegram")
        db.request_handoff(b, "discord")
        db.request_handoff(c, "telegram")
        db.claim_handoff(c)  # c is now running, not pending
        db.request_handoff(d, "slack")
        db.claim_handoff(d)
        db.complete_handoff(d)  # d is terminal

        pending = db.list_pending_handoffs()
        ids = [r["id"] for r in pending]
        assert set(ids) == {a, b}

    def test_list_pending_handoffs_does_not_use_shared_writer_connection(self, db):
        sid = "sess-isolated-reader"
        self._make_session(db, sid)
        db.request_handoff(sid, "telegram")

        writer_connection = db._conn

        class _SharedConnectionMustNotBeUsed:
            def execute(self, *_args, **_kwargs):
                raise AssertionError("handoff polling used the shared writer connection")

        db._conn = _SharedConnectionMustNotBeUsed()
        try:
            pending = db.list_pending_handoffs()
        finally:
            db._conn = writer_connection

        assert pending == [
            {"id": sid, "handoff_platform": "telegram", "title": None}
        ]

    def test_list_pending_handoffs_does_not_wait_for_shared_connection(self):
        probe = textwrap.dedent(
            """
            import tempfile
            import threading
            import time
            from concurrent.futures import ThreadPoolExecutor
            from pathlib import Path

            from hermes_state import SessionDB

            with tempfile.TemporaryDirectory() as tmp:
                db = SessionDB(db_path=Path(tmp) / "state.db")

                def insert(conn):
                    conn.execute(
                        "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                        ("sess-contended-writer", "cli", time.time()),
                    )

                db._execute_write(insert)
                assert db.request_handoff("sess-contended-writer", "telegram")

                entered = threading.Event()
                release = threading.Event()

                def hold_shared_connection():
                    entered.set()
                    release.wait(timeout=30)
                    return 1

                db._conn.create_function(
                    "hold_shared_connection", 0, hold_shared_connection
                )
                with ThreadPoolExecutor(max_workers=2) as pool:
                    blocker = pool.submit(
                        lambda: db._conn.execute(
                            "SELECT hold_shared_connection()"
                        ).fetchone()
                    )
                    assert entered.wait(timeout=2)
                    poll = pool.submit(db.list_pending_handoffs)
                    pending = poll.result(timeout=2)
                    release.set()
                    blocker.result(timeout=2)

                assert pending == [{
                    "id": "sess-contended-writer",
                    "handoff_platform": "telegram",
                    "title": None,
                }]
                db.close()
            """
        )

        try:
            completed = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=str(Path(__file__).resolve().parents[2]),
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("handoff polling waited for the shared connection")

        assert completed.returncode == 0, completed.stderr

    def test_list_pending_handoffs_returns_only_dispatch_fields(self, db):
        sid = "sess-narrow-projection"
        self._make_session(db, sid, title="handoff title")
        db.request_handoff(sid, "discord")

        assert db.list_pending_handoffs() == [
            {
                "id": sid,
                "handoff_platform": "discord",
                "title": "handoff title",
            }
        ]

    def test_close_releases_dedicated_handoff_reader(self, db):
        db.list_pending_handoffs()
        reader = db._handoff_read_conn

        db.close()

        assert db._handoff_read_conn is None
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            reader.execute("SELECT 1")

    def test_claim_handoff_is_atomic(self, db):
        sid = "sess-claim"
        self._make_session(db, sid)
        db.request_handoff(sid, "telegram")

        # First claim wins
        assert db.claim_handoff(sid) is True
        # Second claim is a no-op (state is now "running", not "pending")
        assert db.claim_handoff(sid) is False
        assert db.get_handoff_state(sid)["state"] == "running"

    def test_complete_handoff_clears_error(self, db):
        sid = "sess-complete"
        self._make_session(db, sid)
        db.request_handoff(sid, "telegram")
        db.claim_handoff(sid)
        db.fail_handoff(sid, "transient")
        # User retries; mock the watcher path
        db.request_handoff(sid, "telegram")
        db.claim_handoff(sid)
        db.complete_handoff(sid)

        state = db.get_handoff_state(sid)
        assert state["state"] == "completed"
        assert state["error"] is None

    def test_fail_handoff_records_reason(self, db):
        sid = "sess-fail"
        self._make_session(db, sid)
        db.request_handoff(sid, "telegram")
        db.claim_handoff(sid)
        db.fail_handoff(sid, "no home channel for telegram")

        state = db.get_handoff_state(sid)
        assert state["state"] == "failed"
        assert state["error"] == "no home channel for telegram"

    def test_fail_handoff_truncates_long_reasons(self, db):
        sid = "sess-fail-long"
        self._make_session(db, sid)
        db.request_handoff(sid, "telegram")
        db.claim_handoff(sid)

        # 1000-character error string
        big_err = "x" * 1000
        db.fail_handoff(sid, big_err)

        state = db.get_handoff_state(sid)
        assert len(state["error"]) <= 500

    def test_get_handoff_state_for_unknown_session(self, db):
        assert db.get_handoff_state("does-not-exist") is None

    def test_full_pending_to_completed_flow(self, db):
        """End-to-end sequence the CLI + gateway watcher follow."""
        sid = "sess-flow"
        self._make_session(db, sid, title="my session")
        db.append_message(sid, "user", "Hello")
        db.append_message(sid, "assistant", "Hi there!")

        # CLI: request handoff
        assert db.request_handoff(sid, "telegram") is True
        assert db.get_handoff_state(sid)["state"] == "pending"

        # Gateway watcher: discover + claim
        pending = db.list_pending_handoffs()
        assert len(pending) == 1
        assert pending[0]["id"] == sid
        assert pending[0]["title"] == "my session"
        assert db.claim_handoff(sid) is True
        assert db.get_handoff_state(sid)["state"] == "running"

        # Gateway uses get_messages to load the transcript (real flow uses
        # session_store.switch_session which reads the same table).
        messages = db.get_messages(sid)
        assert [m["role"] for m in messages] == ["user", "assistant"]

        # Gateway: mark completed
        db.complete_handoff(sid)
        assert db.get_handoff_state(sid)["state"] == "completed"
        assert db.list_pending_handoffs() == []


class TestHandoffCommandRegistration:
    """Slash-command surface checks."""

    def test_command_registered(self):
        from hermes_cli.commands import resolve_command
        cmd = resolve_command("handoff")
        assert cmd is not None
        assert cmd.name == "handoff"
        assert cmd.category == "Session"

    def test_command_is_cli_only(self):
        """`/handoff` is initiated from the CLI; gateway shouldn't expose it."""
        from hermes_cli.commands import resolve_command, GATEWAY_KNOWN_COMMANDS
        cmd = resolve_command("handoff")
        assert cmd is not None
        assert cmd.cli_only is True
        assert "handoff" not in GATEWAY_KNOWN_COMMANDS
