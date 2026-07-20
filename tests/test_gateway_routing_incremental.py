"""Regression coverage for bounded gateway routing persistence."""

import json
from types import SimpleNamespace

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


def test_session_store_save_uses_point_updates_not_full_replace(tmp_path, monkeypatch):
    from threading import Lock
    from gateway.session import SessionStore

    store = SessionStore.__new__(SessionStore)
    store._lock = Lock()
    store._entries = {
        "agent:main:discord:1": SimpleNamespace(to_dict=lambda: {"session_id": "s1"})
    }
    store._routing_generation = 0
    store._persisted_routing_generation = 0
    store._save_lock = None
    store._db = SimpleNamespace()
    calls = {"save": [], "replace": []}

    def fake_save(key, value, *, scope):
        calls["save"].append((key, json.loads(value), scope))

    def fake_replace(entries, *, scope):
        calls["replace"].append((dict(entries), scope))

    store._db.save_gateway_routing_entry = fake_save
    store._db.replace_gateway_routing_entries = fake_replace
    store._write_sessions_json = False
    store._routing_scope = lambda: "gateway"

    monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)
    monkeypatch.setattr(store, "_persist_routing_data", SessionStore._persist_routing_data.__get__(store, SessionStore))
    store._save()

    assert calls["replace"] == []
    assert calls["save"] == [("agent:main:discord:1", {"session_id": "s1"}, "gateway")]


def test_save_gateway_routing_entry_updates_one_key_only(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        monkeypatch.setattr("hermes_state.time.time", lambda: 100.0)
        db.replace_gateway_routing_entries(
            {"keep": '{"v":1}', "change": '{"v":1}', "remove": '{"v":1}'},
            scope="gateway",
        )

        monkeypatch.setattr("hermes_state.time.time", lambda: 200.0)
        db.save_gateway_routing_entry("change", '{"v":2}', scope="gateway")

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
            "remove": ('{"v":1}', 100.0),
        }
    finally:
        db.close()


def test_delete_gateway_routing_entry_removes_only_target_key(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        monkeypatch.setattr("hermes_state.time.time", lambda: 100.0)
        db.replace_gateway_routing_entries(
            {"keep": '{"v":1}', "change": '{"v":1}', "remove": '{"v":1}'},
            scope="gateway",
        )
        db.replace_gateway_routing_entries({"other": '{"v":9}'}, scope="other")

        db.delete_gateway_routing_entries(["change"], scope="gateway")

        conn = db._conn
        assert conn is not None
        gateway_rows = {
            row["session_key"]
            for row in conn.execute(
                "SELECT session_key FROM gateway_routing WHERE scope = ?",
                ("gateway",),
            )
        }
        other_rows = {
            row["session_key"]
            for row in conn.execute(
                "SELECT session_key FROM gateway_routing WHERE scope = ?",
                ("other",),
            )
        }
        assert gateway_rows == {"keep", "remove"}
        assert other_rows == {"other"}
    finally:
        db.close()


def test_routing_sessions_json_mirror_stays_compatible(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.save_gateway_routing_entry("agent:main:discord:1", json.dumps({"session_id": "s1"}), scope="gateway")
        mirrored = db.load_gateway_routing_entries(scope="gateway")
        assert mirrored == {"agent:main:discord:1": json.dumps({"session_id": "s1"})}
    finally:
        db.close()
