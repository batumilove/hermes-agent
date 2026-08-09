"""Behavioral contract for delta reconciliation of gateway routing snapshots."""

from __future__ import annotations

import json
import logging

from hermes_state import SessionDB


def _row(db: SessionDB, scope: str, key: str):
    with db._lock:
        return db._conn.execute(
            "SELECT entry_json, updated_at FROM gateway_routing "
            "WHERE scope = ? AND session_key = ?",
            (scope, key),
        ).fetchone()


def test_identical_snapshot_performs_no_routing_dml(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        entries = {
            "route-a": json.dumps({"session_id": "a"}),
            "route-b": json.dumps({"session_id": "b"}),
        }
        db.replace_gateway_routing_entries(entries, scope="gateway", reason="seed")

        statements = []
        db._conn.set_trace_callback(statements.append)
        try:
            db.replace_gateway_routing_entries(
                entries, scope="gateway", reason="identical"
            )
        finally:
            db._conn.set_trace_callback(None)

        routing_dml = [
            statement
            for statement in statements
            if "GATEWAY_ROUTING" in statement.upper()
            and statement.lstrip().upper().startswith(("DELETE", "INSERT", "UPDATE"))
        ]
        assert routing_dml == []
    finally:
        db.close()


def test_snapshot_reconcile_writes_only_changed_keys(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        before = {
            "unchanged": json.dumps({"session_id": "keep"}),
            "changed": json.dumps({"session_id": "old"}),
            "stale": json.dumps({"session_id": "remove"}),
        }
        db.replace_gateway_routing_entries(before, scope="gateway", reason="seed")
        unchanged_timestamp = _row(db, "gateway", "unchanged")["updated_at"]

        statements = []
        db._conn.set_trace_callback(statements.append)
        try:
            after = {
                "unchanged": before["unchanged"],
                "changed": json.dumps({"session_id": "new"}),
                "added": json.dumps({"session_id": "add"}),
            }
            db.replace_gateway_routing_entries(
                after, scope="gateway", reason="structural_change"
            )
        finally:
            db._conn.set_trace_callback(None)

        assert db.load_gateway_routing_entries(scope="gateway") == after
        assert _row(db, "gateway", "unchanged")["updated_at"] == unchanged_timestamp

        routing_dml = [
            statement
            for statement in statements
            if "GATEWAY_ROUTING" in statement.upper()
            and statement.lstrip().upper().startswith(("DELETE", "INSERT", "UPDATE"))
        ]
        assert len([s for s in routing_dml if s.lstrip().upper().startswith("DELETE")]) == 1
        assert len([s for s in routing_dml if s.lstrip().upper().startswith("INSERT")]) == 2
        assert all("unchanged" not in statement for statement in routing_dml)
    finally:
        db.close()


def test_empty_snapshot_removes_only_requested_scope(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {"route-a": json.dumps({"session_id": "a"})}, scope="gateway-a"
        )
        db.replace_gateway_routing_entries(
            {"route-b": json.dumps({"session_id": "b"})}, scope="gateway-b"
        )

        db.replace_gateway_routing_entries({}, scope="gateway-a", reason="reset")

        assert db.load_gateway_routing_entries(scope="gateway-a") == {}
        assert db.load_gateway_routing_entries(scope="gateway-b") == {
            "route-b": json.dumps({"session_id": "b"})
        }
    finally:
        db.close()


def test_reconcile_logs_counts_and_internal_phase_timings(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        before = {
            "unchanged": json.dumps({"session_id": "keep"}),
            "changed": json.dumps({"session_id": "old"}),
            "stale": json.dumps({"session_id": "remove"}),
        }
        db.replace_gateway_routing_entries(before, scope="gateway", reason="seed")

        after = {
            "unchanged": before["unchanged"],
            "changed": json.dumps({"session_id": "new"}),
            "added": json.dumps({"session_id": "add"}),
        }
        with caplog.at_level(logging.INFO, logger="hermes_state"):
            db.replace_gateway_routing_entries(
                after, scope="gateway", reason="explicit_repair"
            )

        message = next(
            record.getMessage()
            for record in caplog.records
            if "Gateway routing reconciliation:" in record.getMessage()
        )
        assert "reason=explicit_repair" in message
        assert "existing=3" in message
        assert "changed=2" in message
        assert "stale=1" in message
        assert "select=" in message
        assert "comparison=" in message
        assert "dml=" in message
    finally:
        db.close()
