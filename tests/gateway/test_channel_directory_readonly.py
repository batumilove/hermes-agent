"""P0-C: the channel-directory housekeeping reader must be read-only.

Inventory (t_00e4c7d2 §2a #4 / §5 rec 2): ``_build_from_sessions_db`` opens a
WRITABLE SessionDB every 5 minutes per connected platform to do SELECT-only
work (``list_gateway_routing_origins``). Each close then runs
``PRAGMA wal_checkpoint(PASSIVE)`` against the hot state.db 12–24×/hour —
avoidable write-lock traffic under backup-I/O contention, plus a wasteful
writable construction (schema probe, writability preflight) for a pure read.

Contract pinned here: the discovery reader attaches ``read_only=True`` so it
takes no write lock and never checkpoints at close, while preserving the
routing projection and the existing close-on-exit behavior.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gateway.channel_directory import _build_from_sessions_db


class TestBuildFromSessionsDbReadOnly:
    def test_opens_session_db_read_only(self):
        """SELECT-only discovery must attach with read_only=True."""
        db = MagicMock()
        db.list_gateway_routing_origins.return_value = []

        with patch(
            "hermes_state.SessionDB", return_value=db
        ) as session_db_cls:
            _build_from_sessions_db("telegram")

        assert session_db_cls.call_args.kwargs.get("read_only") is True, (
            "channel-directory housekeeping reads only — it must open the "
            "SessionDB read_only so close() never runs a PASSIVE checkpoint "
            "against the hot state.db 12–24x/hour"
        )

    def test_read_only_open_failure_falls_back_to_empty(self):
        """A read-only attach can fail when state.db does not exist yet (fresh
        install). The housekeeping reader must degrade to an empty directory,
        not raise into the 5-minute tick."""
        with patch(
            "hermes_state.SessionDB", side_effect=sqlite_error()
        ):
            entries = _build_from_sessions_db("telegram")

        assert entries == []


class TestBuildFromSessionsDbEndToEnd:
    """Real state.db under tmp HERMES_HOME: proves the read-only attach can
    actually serve the routing projection against a live WAL database — the
    exact shape the 5-minute housekeeping tick runs.
    """

    def test_read_only_attach_serves_routing_projection(self, tmp_path):
        import hermes_state
        from hermes_state import SessionDB

        # The autouse hermetic fixture re-pins hermes_state.DEFAULT_DB_PATH to
        # its own per-test home; the argless reader open resolves through it,
        # so seed exactly the file the housekeeping tick will open.
        db_file = hermes_state.DEFAULT_DB_PATH

        # Seed a real database with one gateway routing row.
        writer = SessionDB(db_path=db_file)
        try:
            writer.create_session(
                "sess-1",
                "telegram",
                chat_id="123",
                display_name="Alice",
                chat_type="dm",
            )
            writer.record_gateway_session_peer(
                "sess-1",
                source="telegram",
                session_key="telegram:123",
                chat_id="123",
                chat_type="dm",
                display_name="Alice",
                origin_json=json.dumps(
                    {"chat_id": "123", "chat_name": "Alice", "platform": "telegram"}
                ),
            )
        finally:
            writer.close()

        # The housekeeping reader must see the row through a read-only attach.
        entries = _build_from_sessions_db("telegram")

        assert entries == [
            {"id": "123", "name": "Alice", "type": "dm", "thread_id": None}
        ]


def sqlite_error():
    import sqlite3

    return sqlite3.OperationalError("unable to open database file")
