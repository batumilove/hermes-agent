"""Regression coverage for point-write failure and reconciliation semantics."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import SessionEntry, SessionSource, SessionStore
from hermes_state import SessionDB


def _make_entry(key: str, session_id: str) -> SessionEntry:
    from datetime import datetime, timedelta

    return SessionEntry(
        session_key=key,
        session_id=session_id,
        created_at=datetime.now() - timedelta(hours=2),
        updated_at=datetime.now() - timedelta(hours=1),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )


def _store_with_db(store, db, **overrides):
    store._db = db
    store._write_sessions_json = False
    for k, v in overrides.items():
        setattr(store, k, v)
    return store


# ---------------------------------------------------------------------------
# Blocker 1: failed point write must not advance durable baseline
# ---------------------------------------------------------------------------


def test_point_save_failure_does_not_advance_persisted_generation(tmp_path, monkeypatch):
    """A failed DB point upsert must leave the durable baseline unchanged so the
    next save retries the same delta."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "agent:main:discord:1": SimpleNamespace(to_dict=lambda: {"session_id": "s1"})
        }
        store._persisted_routing_generation = 0
        store._persisted_routing_data = {}
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        failing = []

        def fail_upsert(session_key, entry_json, *, scope):
            failing.append(("upsert", session_key))
            raise RuntimeError("disk full")

        db.save_gateway_routing_entry = fail_upsert

        store._save()
        assert store._persisted_routing_generation == 0
        assert store._persisted_routing_data == {}

        # Restore the real DB method and retry: the same key should be written.
        db.save_gateway_routing_entry = lambda session_key, entry_json, *, scope: SessionDB.save_gateway_routing_entry(db, session_key, entry_json, scope=scope)
        store._routing_generation = 0
        store._persisted_routing_generation = 0
        store._save()
        assert store._persisted_routing_generation == 1
        assert store._persisted_routing_data["agent:main:discord:1"] == {"session_id": "s1"}
        assert db.load_gateway_routing_entries(scope="gateway") == {
            "agent:main:discord:1": json.dumps({"session_id": "s1"})
        }
    finally:
        db.close()


def test_point_delete_failure_does_not_advance_persisted_generation(tmp_path, monkeypatch):
    """A failed DB batch delete must leave the durable baseline unchanged so the
    delete is retried later."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {"agent:main:discord:1": json.dumps({"session_id": "s1"})},
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {}
        store._persisted_routing_generation = 0
        store._persisted_routing_data = {
            "agent:main:discord:1": {"session_id": "s1"}
        }
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        failing = []

        def fail_delete(session_keys, *, scope):
            failing.append(("delete", session_keys))
            raise RuntimeError("disk full")

        db.delete_gateway_routing_entries = fail_delete

        store._save()
        assert store._persisted_routing_generation == 0
        assert "agent:main:discord:1" in store._persisted_routing_data

        db.delete_gateway_routing_entries = lambda session_keys, *, scope: SessionDB.delete_gateway_routing_entries(db, session_keys, scope=scope)
        store._routing_generation = 0
        store._persisted_routing_generation = 0
        store._save()
        assert store._persisted_routing_generation == 1
        assert db.load_gateway_routing_entries(scope="gateway") == {}
    finally:
        db.close()


def test_legacy_sessions_json_fallback_does_not_count_as_db_success(tmp_path, monkeypatch):
    """When the DB write fails, writing sessions.json must not advance the durable
    generation or durable snapshot."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "agent:main:discord:1": SimpleNamespace(to_dict=lambda: {"session_id": "s1"})
        }
        store._persisted_routing_generation = 0
        store._persisted_routing_data = {}
        _store_with_db(store, db, _write_sessions_json=True, _routing_scope=lambda: "gateway")

        written_json = {}

        def capture_json(data):
            written_json.update(dict(data))

        monkeypatch.setattr(store, "_save_sessions_json", capture_json)

        def fail_upsert(session_key, entry_json, *, scope):
            raise RuntimeError("disk full")

        db.save_gateway_routing_entry = fail_upsert

        store._save()
        assert store._persisted_routing_generation == 0
        assert store._persisted_routing_data == {}
        assert "_README" in written_json or "agent:main:discord:1" in written_json
    finally:
        db.close()


def test_out_of_order_generation_does_not_regress_state(tmp_path, monkeypatch):
    """A delayed older writer must not overwrite a newer durable baseline."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "k": SimpleNamespace(to_dict=lambda v="s1": {"session_id": v})
        }
        store._persisted_routing_generation = 0
        store._persisted_routing_data = {}
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        store._save()
        assert store._persisted_routing_generation == 1
        baseline = dict(store._persisted_routing_data)

        # Simulate a delayed older writer (generation 1, not 2) arriving after a
        # newer one has already advanced the baseline.  The in-memory entry still
        # reads s1, so we pre-advance the snapshot generation to 2 while keeping the
        # value s1; the generation gate must not allow the older snapshot to overwrite
        # the DB state s2.
        store._routing_generation = 1
        store._persisted_routing_generation = 2
        store._persisted_routing_data = {"k": {"session_id": "s2"}}
        db.replace_gateway_routing_entries(
            {"k": json.dumps({"session_id": "s2"})}, scope="gateway"
        )
        store._save()
        # The older writer sees gen <= persisted and short-circuits; state stays s2.
        assert store._persisted_routing_data == {"k": {"session_id": "s2"}}
        assert db.load_gateway_routing_entries(scope="gateway") == {"k": json.dumps({"session_id": "s2"})}
    finally:
        db.close()


def test_routine_single_key_update_uses_point_not_replace(tmp_path, monkeypatch):
    """A routine _save with one changed key must call the point upsert, not the
    full atomic replace."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "k": SimpleNamespace(to_dict=lambda: {"session_id": "s1"})
        }
        store._persisted_routing_data = {}
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        calls = {"replace": 0, "save": 0}
        orig_replace = db.replace_gateway_routing_entries

        def track_replace(entries, *, scope):
            calls["replace"] += 1
            return orig_replace(entries, scope=scope)

        orig_save = db.save_gateway_routing_entry

        def track_save(key, value, *, scope):
            calls["save"] += 1
            return orig_save(key, value, scope=scope)

        db.replace_gateway_routing_entries = track_replace
        db.save_gateway_routing_entry = track_save

        store._save()
        assert calls["save"] == 1
        assert calls["replace"] == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Blocker 2: startup / reconciliation must remain atomic
# ---------------------------------------------------------------------------


def test_prune_stale_sessions_uses_atomic_replace(tmp_path, monkeypatch):
    """_prune_stale_sessions_locked must reconcile the whole scope atomically via
    replace_gateway_routing_entries, not point delete."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {
                "stale": json.dumps({"session_id": "s1"}),
                "live": json.dumps({"session_id": "s2"}),
            },
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._loaded = True
        store._db = db
        store._write_sessions_json = False
        store._routing_scope = lambda: "gateway"
        store._entries = {
            "stale": _make_entry("stale", "s1"),
            "live": _make_entry("live", "s2"),
        }
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)
        monkeypatch.setattr(
            db, "get_session", lambda sid: {"end_reason": "agent_close"} if sid == "s1" else {"end_reason": None}
        )
        monkeypatch.setattr(db, "find_latest_gateway_session_for_peer", lambda *a, **k: None)

        calls = {"replace": 0, "delete": 0}
        orig_replace = db.replace_gateway_routing_entries

        def track_replace(entries, *, scope):
            calls["replace"] += 1
            return orig_replace(entries, scope=scope)

        orig_delete = db.delete_gateway_routing_entries

        def track_delete(keys, *, scope):
            calls["delete"] += 1
            return orig_delete(keys, scope=scope)

        db.replace_gateway_routing_entries = track_replace
        db.delete_gateway_routing_entries = track_delete

        store._prune_stale_sessions_locked()

        assert calls["replace"] == 1
        assert calls["delete"] == 0
        rows = db.load_gateway_routing_entries(scope="gateway")
        assert "stale" not in rows
        assert "live" in rows
    finally:
        db.close()


def test_prune_recovery_multiple_entries_is_atomic(tmp_path, monkeypatch):
    """Recovering one entry while pruning another must be one atomic replace."""
    from datetime import datetime, timedelta

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {
                "prune": json.dumps({"session_id": "s1"}),
                "recover": json.dumps({"session_id": "s2"}),
            },
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._loaded = True
        store._db = db
        store._write_sessions_json = False
        store._routing_scope = lambda: "gateway"
        prune_entry = _make_entry("prune", "s1")
        recover_entry = _make_entry("recover", "s2")
        recover_entry.origin = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="1",
            chat_type="dm",
            user_id="1",
        )
        store._entries = {"prune": prune_entry, "recover": recover_entry}
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)
        monkeypatch.setattr(
            db,
            "get_session",
            lambda sid: {"end_reason": "agent_close"},
        )
        monkeypatch.setattr(
            db,
            "find_latest_gateway_session_for_peer",
            lambda *a, **k: {"id": "s3", "started_at": datetime.now().timestamp()},
        )
        monkeypatch.setattr(db, "reopen_session", lambda sid: None)

        replace_calls = []
        orig_replace = db.replace_gateway_routing_entries

        def track_replace(entries, *, scope):
            replace_calls.append(dict(entries))
            return orig_replace(entries, scope=scope)

        db.replace_gateway_routing_entries = track_replace

        store._prune_stale_sessions_locked()

        assert len(replace_calls) == 1
        final = replace_calls[0]
        assert "prune" not in final
        assert "recover" in final
        assert json.loads(final["recover"])["session_id"] == "s3"
    finally:
        db.close()


def test_prune_never_calls_point_save_or_delete(tmp_path, monkeypatch):
    """The reconciliation path must not use point upserts or exact deletes."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {"stale": json.dumps({"session_id": "s1"})},
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._loaded = True
        store._db = db
        store._write_sessions_json = False
        store._routing_scope = lambda: "gateway"
        store._entries = {"stale": _make_entry("stale", "s1")}
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)
        monkeypatch.setattr(
            db, "get_session", lambda sid: {"end_reason": "agent_close"}
        )
        monkeypatch.setattr(db, "find_latest_gateway_session_for_peer", lambda *a, **k: None)

        point_calls = []
        orig_save = db.save_gateway_routing_entry
        orig_delete = db.delete_gateway_routing_entries

        def track_save(key, value, *, scope):
            point_calls.append("save")
            return orig_save(key, value, scope=scope)

        def track_delete(keys, *, scope):
            point_calls.append("delete")
            return orig_delete(keys, scope=scope)

        db.save_gateway_routing_entry = track_save
        db.delete_gateway_routing_entries = track_delete

        store._prune_stale_sessions_locked()
        assert point_calls == []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scope/profile isolation and exact-key delete
# ---------------------------------------------------------------------------


def test_routine_delete_is_exact_key_and_scope_isolated(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {"k": json.dumps({"session_id": "s1"})}, scope="gateway"
        )
        db.replace_gateway_routing_entries(
            {"k": json.dumps({"session_id": "s2"})}, scope="other"
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {}
        store._persisted_routing_data = {"k": {"session_id": "s1"}}
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        store._save()
        assert db.load_gateway_routing_entries(scope="gateway") == {}
        assert db.load_gateway_routing_entries(scope="other") == {"k": json.dumps({"session_id": "s2"})}
    finally:
        db.close()


def test_sessions_json_compatibility_after_db_failure(tmp_path, monkeypatch):
    """sessions.json is written as a mirror even when DB is unavailable, but it
    does not advance the DB durable baseline."""
    config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=tmp_path, config=config)
    store._entries = {"k": SimpleNamespace(to_dict=lambda: {"session_id": "s1"})}
    store._persisted_routing_data = {}
    store._persisted_routing_generation = 0
    store._db = None
    store._write_sessions_json = True
    store._routing_scope = lambda: "gateway"
    store._save()
    assert store._persisted_routing_generation == 0
    assert store._persisted_routing_data == {}
    sessions_file = tmp_path / "sessions.json"
    assert sessions_file.exists()
    data = json.loads(sessions_file.read_text(encoding="utf-8"))
    assert data["k"] == {"session_id": "s1"}
