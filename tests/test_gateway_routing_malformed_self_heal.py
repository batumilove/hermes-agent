"""Regression coverage for malformed DB routing rows self-healing.

CodeRabbit finding: during _ensure_loaded_locked, rows with JSON or
SessionEntry.from_dict parsing failures are silently skipped. If such invalid
rows remain in the DB, the next successful save must remove them — but valid
unseen keys must not be deleted and pending cleanup must not be cleared before a
successful DB persistence.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import SessionEntry, SessionStore
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_dict(key: str, session_id: str) -> dict:
    return {
        "session_key": key,
        "session_id": session_id,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "platform": "telegram",
        "chat_type": "dm",
    }


# ---------------------------------------------------------------------------
# RED tests: malformed rows are skipped and must be cleaned up on next save
# ---------------------------------------------------------------------------


def _seed_db_with_invalid(
    db: SessionDB, *, valid: dict, invalid: dict, scope: str
) -> None:
    """Pre-populate the DB for malformed-row tests. The scope must be the resolved
    sessions_dir path because SessionStore._routing_scope uses the resolved path.
    """
    db.replace_gateway_routing_entries(
        {**valid, **invalid},
        scope=scope,
    )


def _load_store(tmp_path, db):
    """Create a SessionStore wired to the provided DB and load its routing index."""
    config = GatewayConfig(
        write_sessions_json=False,
        default_reset_policy=SessionResetPolicy(mode="none"),
    )
    with patch("hermes_state.SessionDB", return_value=db):
        store = SessionStore(sessions_dir=tmp_path, config=config)
    store._ensure_loaded()
    return store


def test_malformed_json_row_is_reconciled_on_next_save(tmp_path, monkeypatch):
    """A row with non-JSON entry_json must be removed after a successful save."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        # Pre-seed the DB with one valid and one malformed row.
        _seed_db_with_invalid(
            db,
            valid={"valid": json.dumps(_entry_dict("valid", "s-valid"))},
            invalid={"bad": "not-json"},
            scope=str(tmp_path.resolve()),
        )
        store = _load_store(tmp_path, db)

        # The malformed row was skipped; only the valid one loaded.
        assert set(store._entries) == {"valid"}
        assert "bad" in store._invalid_routing_keys

        # Trigger a save with the valid row; it must reconcile the invalid key away.
        store._entries["new"] = SessionEntry.from_dict(_entry_dict("new", "s-new"))
        store._save()

        rows = db.load_gateway_routing_entries(scope=store._routing_scope())
        assert set(rows) == {"valid", "new"}
        assert "bad" not in rows
        assert store._invalid_routing_keys == set()
    finally:
        db.close()


def test_structurally_invalid_row_is_reconciled_on_next_save(tmp_path, monkeypatch):
    """A row with JSON that fails SessionEntry.from_dict must be removed after a
    successful save."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_db_with_invalid(
            db,
            valid={"valid": json.dumps(_entry_dict("valid", "s-valid"))},
            invalid={"bad": json.dumps({"not": "a-session-entry"})},
            scope=str(tmp_path.resolve()),
        )
        store = _load_store(tmp_path, db)

        assert set(store._entries) == {"valid"}
        assert "bad" in store._invalid_routing_keys

        store._save()
        rows = db.load_gateway_routing_entries(scope=store._routing_scope())
        assert set(rows) == {"valid"}
        assert "bad" not in rows
        assert store._invalid_routing_keys == set()
    finally:
        db.close()


def test_failed_cleanup_retains_pending_invalid_keys(tmp_path, monkeypatch):
    """If the save that would clean up invalid rows fails, the pending set must be
    retained so the next successful save retries."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_db_with_invalid(
            db,
            valid={"valid": json.dumps(_entry_dict("valid", "s-valid"))},
            invalid={"bad": json.dumps({"not": "a-session-entry"})},
            scope=str(tmp_path.resolve()),
        )
        store = _load_store(tmp_path, db)
        assert "bad" in store._invalid_routing_keys

        # Make the next atomic save fail; pending invalid keys must survive.
        monkeypatch.setattr(db, "replace_gateway_routing_entries", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
        store._save()
        assert "bad" in store._invalid_routing_keys

        # Restore and retry; cleanup must complete.
        monkeypatch.undo()
        store._save()
        rows = db.load_gateway_routing_entries(scope=store._routing_scope())
        assert "bad" not in rows
        assert store._invalid_routing_keys == set()
    finally:
        db.close()


def test_no_over_delete_of_valid_unseen_keys(tmp_path, monkeypatch):
    """Reconciling invalid keys must not delete valid keys in other scopes."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_db_with_invalid(
            db,
            valid={},
            invalid={"bad": "not-json"},
            scope=str(tmp_path.resolve()),
        )
        db.replace_gateway_routing_entries(
            {"other": json.dumps(_entry_dict("other", "s-other"))},
            scope="other",
        )
        store = _load_store(tmp_path, db)
        assert "bad" in store._invalid_routing_keys

        store._save()
        assert db.load_gateway_routing_entries(scope=store._routing_scope()) == {}
        assert db.load_gateway_routing_entries(scope="other") == {
            "other": json.dumps(_entry_dict("other", "s-other")),
        }
    finally:
        db.close()


def test_no_over_delete_when_db_already_empty(tmp_path, monkeypatch):
    """If all DB rows are invalid and the in-memory routing index is empty, cleanup
    must leave the table for this scope empty without touching other scopes."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_db_with_invalid(
            db,
            valid={},
            invalid={"bad": "not-json"},
            scope=str(tmp_path.resolve()),
        )
        db.replace_gateway_routing_entries(
            {"good": json.dumps(_entry_dict("good", "s-good"))},
            scope="other",
        )
        store = _load_store(tmp_path, db)
        assert "bad" in store._invalid_routing_keys
        assert store._entries == {}

        store._save()
        assert db.load_gateway_routing_entries(scope=store._routing_scope()) == {}
        assert db.load_gateway_routing_entries(scope="other") == {
            "good": json.dumps(_entry_dict("good", "s-good")),
        }
    finally:
        db.close()


def test_invalid_keys_cleaned_by_atomic_replace_only_on_success(tmp_path, monkeypatch):
    """Invalid-key cleanup must be included in the atomic replacement snapshot; if the
    replacement fails, invalid keys remain in the DB and the pending set remains."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_db_with_invalid(
            db,
            valid={"valid": json.dumps(_entry_dict("valid", "s-valid"))},
            invalid={"bad": "not-json"},
            scope=str(tmp_path.resolve()),
        )
        store = _load_store(tmp_path, db)
        assert store._invalid_routing_keys == {"bad"}

        fail_calls = []

        def fail_replace(entries, *, scope):
            fail_calls.append(("replace", dict(entries)))
            raise RuntimeError("locked")

        monkeypatch.setattr(db, "replace_gateway_routing_entries", fail_replace)
        # Force atomic path by adding a second entry so multi-key replace is used.
        store._entries["new"] = SessionEntry.from_dict(_entry_dict("new", "s-new"))
        store._save()

        assert fail_calls
        # The replacement snapshot must have included the valid key but excluded
        # the invalid one.
        attempted = fail_calls[0][1]
        assert "bad" not in attempted
        assert "valid" in attempted
        # Invalid keys remain pending; DB is unchanged because the save failed.
        assert store._invalid_routing_keys == {"bad"}
        assert db.load_gateway_routing_entries(scope=store._routing_scope()) == {
            "valid": json.dumps(_entry_dict("valid", "s-valid")),
            "bad": "not-json",
        }
    finally:
        db.close()


def test_multiple_invalid_keys_all_cleaned(tmp_path, monkeypatch):
    """More than one invalid key in the same scope must be removed in one go."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_db_with_invalid(
            db,
            valid={"good": json.dumps(_entry_dict("good", "s-good"))},
            invalid={
                "bad1": "not-json",
                "bad2": json.dumps({"not": "valid"}),
            },
            scope=str(tmp_path.resolve()),
        )
        store = _load_store(tmp_path, db)
        assert store._invalid_routing_keys == {"bad1", "bad2"}

        store._save()
        rows = db.load_gateway_routing_entries(scope=store._routing_scope())
        assert set(rows) == {"good"}
        assert store._invalid_routing_keys == set()
    finally:
        db.close()
