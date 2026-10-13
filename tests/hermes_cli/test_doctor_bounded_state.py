"""Routine doctor probes are bounded; explicit repair retains full integrity checks."""

import pytest

import hermes_state_repair as repair
from hermes_cli.doctor_state import _write_health_reason
from hermes_state import SessionDB


@pytest.mark.parametrize("should_fix", [False, True])
def test_doctor_retains_probe_but_scans_full_integrity_only_for_repair(
    tmp_path, monkeypatch, should_fix
):
    path = tmp_path / "state.db"
    with SessionDB(path):
        pass
    connect = repair._connect_repair_durable
    sql = []

    def traced_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(sql.append)
        return conn

    monkeypatch.setattr(repair, "_connect_repair_durable", traced_connect)
    monkeypatch.setattr(repair, "_live_writer_holds_db", lambda _path: False)
    assert _write_health_reason(path, should_fix=should_fix) is None
    integrity = [
        statement
        for statement in sql
        if statement.upper().startswith("PRAGMA INTEGRITY_CHECK")
    ]
    assert bool(integrity) is should_fix
    assert any(
        "SELECT COUNT(*) FROM SESSIONS" in statement.upper() for statement in sql
    )
    assert any("BEGIN IMMEDIATE" in statement.upper() for statement in sql)
    assert any("ROLLBACK" in statement.upper() for statement in sql)
