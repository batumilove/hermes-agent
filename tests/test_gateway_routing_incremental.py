"""Regression coverage for bounded gateway routing persistence."""

from hermes_state import SessionDB


def test_routing_sync_only_rewrites_changed_rows(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        monkeypatch.setattr("hermes_state.time.time", lambda: 100.0)
        db.replace_gateway_routing_entries(
            {"keep": '{"v":1}', "change": '{"v":1}', "remove": '{"v":1}'},
            scope="gateway",
        )

        monkeypatch.setattr("hermes_state.time.time", lambda: 200.0)
        db.replace_gateway_routing_entries(
            {"keep": '{"v":1}', "change": '{"v":2}', "add": '{"v":1}'},
            scope="gateway",
        )

        conn = db._conn
        assert conn is not None
        rows = {
            row["session_key"]: (row["entry_json"], row["updated_at"])
            for row in conn.execute(
                "SELECT session_key, entry_json, updated_at "
                "FROM gateway_routing WHERE scope = ?",
                ("gateway",),
            )
        }
        assert rows == {
            "keep": ('{"v":1}', 100.0),
            "change": ('{"v":2}', 200.0),
            "add": ('{"v":1}', 200.0),
        }
    finally:
        db.close()
