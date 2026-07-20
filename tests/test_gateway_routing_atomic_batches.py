"""Regression coverage for atomic multi-key routing persistence batches.

CodeRabbit finding: _persist_routing_data loops through save_gateway_routing_entry
and then calls delete_gateway_routing_entries, each committing separately. A
multi-key save can partially persist if interrupted. These tests enforce that
exactly-one-mutation saves still use low-amplification point writes, while any
routine save with >1 total mutation is routed atomically.
"""

import json
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
# RED tests: current point-write loop violates atomicity for multi-key batches
# ---------------------------------------------------------------------------


def test_one_key_upsert_uses_point_write_not_replace(tmp_path, monkeypatch):
    """A routine save with exactly one changed upsert uses low-amplification point
    write, not the full atomic replace."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "k": _make_entry("k", "s1"),
        }
        store._persisted_routing_data = {}
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        calls = {"replace": 0, "save": 0}
        orig_replace = db.replace_gateway_routing_entries
        orig_save = db.save_gateway_routing_entry

        def track_replace(entries, *, scope):
            calls["replace"] += 1
            return orig_replace(entries, scope=scope)

        def track_save(key, value, *, scope):
            calls["save"] += 1
            return orig_save(key, value, scope=scope)

        db.replace_gateway_routing_entries = track_replace
        db.save_gateway_routing_entry = track_save

        store._save()
        assert calls["save"] == 1
        assert calls["replace"] == 0
        assert db.load_gateway_routing_entries(scope="gateway") == {
            "k": json.dumps(store._entries["k"].to_dict()),
        }
    finally:
        db.close()


def test_one_key_delete_uses_exact_delete_not_replace(tmp_path, monkeypatch):
    """A routine save removing exactly one previously persisted key uses the exact
    point delete, not the full atomic replace."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {"k": json.dumps({"session_id": "s1"})},
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {}
        store._persisted_routing_data = {"k": {"session_id": "s1"}}
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        calls = {"replace": 0, "save": 0, "delete": 0}
        orig_replace = db.replace_gateway_routing_entries
        orig_save = db.save_gateway_routing_entry
        orig_delete = db.delete_gateway_routing_entries

        def track_replace(entries, *, scope):
            calls["replace"] += 1
            return orig_replace(entries, scope=scope)

        def track_save(key, value, *, scope):
            calls["save"] += 1
            return orig_save(key, value, scope=scope)

        def track_delete(keys, *, scope):
            calls["delete"] += 1
            return orig_delete(keys, scope=scope)

        db.replace_gateway_routing_entries = track_replace
        db.save_gateway_routing_entry = track_save
        db.delete_gateway_routing_entries = track_delete

        store._save()
        assert calls["delete"] == 1
        assert calls["save"] == 0
        assert calls["replace"] == 0
        assert db.load_gateway_routing_entries(scope="gateway") == {}
    finally:
        db.close()


def test_multi_key_update_uses_atomic_replace(tmp_path, monkeypatch):
    """A routine save with more than one changed upsert must be atomic."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "a": _make_entry("a", "s1"),
            "b": _make_entry("b", "s2"),
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

        db.replace_gateway_routing_entries = track_replace

        store._save()
        assert calls["replace"] == 1
        assert calls["save"] == 0
        assert db.load_gateway_routing_entries(scope="gateway") == {
            "a": json.dumps(store._entries["a"].to_dict()),
            "b": json.dumps(store._entries["b"].to_dict()),
        }
    finally:
        db.close()


def test_mixed_update_and_delete_uses_atomic_replace(tmp_path, monkeypatch):
    """A routine save with one changed upsert and one removed key must be atomic."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {
                "keep": json.dumps({"session_id": "old"}),
                "remove": json.dumps({"session_id": "del"}),
            },
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "keep": _make_entry("keep", "new"),
        }
        store._persisted_routing_data = {
            "keep": {"session_id": "old"},
            "remove": {"session_id": "del"},
        }
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        calls = {"replace": 0, "save": 0, "delete": 0}
        orig_replace = db.replace_gateway_routing_entries
        orig_save = db.save_gateway_routing_entry
        orig_delete = db.delete_gateway_routing_entries

        def track_replace(entries, *, scope):
            calls["replace"] += 1
            return orig_replace(entries, scope=scope)

        def track_save(key, value, *, scope):
            calls["save"] += 1
            return orig_save(key, value, scope=scope)

        def track_delete(keys, *, scope):
            calls["delete"] += 1
            return orig_delete(keys, scope=scope)

        db.replace_gateway_routing_entries = track_replace
        db.save_gateway_routing_entry = track_save
        db.delete_gateway_routing_entries = track_delete

        store._save()
        # total mutation count = 1 upsert + 1 delete = 2 -> atomic path
        assert calls["replace"] == 1
        assert calls["save"] == 0
        assert calls["delete"] == 0
        assert db.load_gateway_routing_entries(scope="gateway") == {
            "keep": json.dumps(store._entries["keep"].to_dict()),
        }
    finally:
        db.close()


def test_multi_key_partial_failure_does_not_advance_baseline(tmp_path, monkeypatch):
    """If the atomic multi-key replace fails, the durable baseline/generation must not
    advance and the next save must retry."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "a": _make_entry("a", "s1"),
            "b": _make_entry("b", "s2"),
        }
        store._persisted_routing_data = {}
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        attempts = []

        def fail_once(entries, *, scope):
            attempts.append(("replace", dict(entries)))
            raise RuntimeError("disk full")

        db.replace_gateway_routing_entries = fail_once

        store._save()
        assert store._persisted_routing_generation == 0
        assert store._persisted_routing_data == {}

        # Restore real replace and retry: generation should advance and both rows
        # must be present.
        db.replace_gateway_routing_entries = lambda entries, *, scope: SessionDB.replace_gateway_routing_entries(
            db, entries, scope=scope
        )
        store._routing_generation = 0
        store._persisted_routing_generation = 0
        store._save()
        assert store._persisted_routing_generation == 1
        rows = db.load_gateway_routing_entries(scope="gateway")
        assert set(rows) == {"a", "b"}
    finally:
        db.close()


def test_generation_and_baseline_advance_only_after_successful_primary_write(tmp_path, monkeypatch):
    """After a successful atomic multi-key write the durable baseline and generation
    must reflect the full snapshot."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._entries = {
            "a": _make_entry("a", "s1"),
            "b": _make_entry("b", "s2"),
        }
        store._persisted_routing_data = {}
        store._persisted_routing_generation = 0
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        store._save()
        assert store._persisted_routing_generation == 1
        assert set(store._persisted_routing_data) == {"a", "b"}
        assert store._persisted_routing_data["a"] == store._entries["a"].to_dict()
        assert store._persisted_routing_data["b"] == store._entries["b"].to_dict()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Edge cases: point-write path still behaves correctly when mutation count is one
# ---------------------------------------------------------------------------


def test_single_no_op_save_does_nothing(tmp_path, monkeypatch):
    """A save with no changes at all must not touch the DB."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.replace_gateway_routing_entries(
            {"k": json.dumps({"session_id": "s1"})},
            scope="gateway",
        )
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        entry = _make_entry("k", "s1")
        store._entries = {"k": entry}
        store._persisted_routing_data = {"k": entry.to_dict()}
        store._persisted_routing_generation = 1
        _store_with_db(store, db, _routing_scope=lambda: "gateway")
        monkeypatch.setattr(store, "_save_sessions_json", lambda data: None)

        calls = {"replace": 0, "save": 0, "delete": 0}
        orig_replace = db.replace_gateway_routing_entries
        orig_save = db.save_gateway_routing_entry
        orig_delete = db.delete_gateway_routing_entries

        def track_replace(entries, *, scope):
            calls["replace"] += 1
            return orig_replace(entries, scope=scope)

        def track_save(key, value, *, scope):
            calls["save"] += 1
            return orig_save(key, value, scope=scope)

        def track_delete(keys, *, scope):
            calls["delete"] += 1
            return orig_delete(keys, scope=scope)

        db.replace_gateway_routing_entries = track_replace
        db.save_gateway_routing_entry = track_save
        db.delete_gateway_routing_entries = track_delete

        store._save()
        assert calls == {"replace": 0, "save": 0, "delete": 0}
    finally:
        db.close()
