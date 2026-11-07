"""Manual compression publishes its child only while it owns the original route."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import hermes_state
from gateway.config import GatewayConfig, Platform
from gateway.session import AsyncSessionStore, SessionSource, SessionStore
from gateway.slash_commands_session import GatewaySessionCommandsMixin


@pytest.mark.parametrize("newer_owner", [False, True])
def test_manual_compression_uses_owned_point_rebind(tmp_path, monkeypatch, newer_owner):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    source = SessionSource(platform=Platform.LOCAL, chat_id="local", chat_type="dm", user_id="user")
    entry = store.get_or_create_session(source)
    parent, child, newer = entry.session_id, "compressed-child", "newer-route"
    store._db.create_session(parent, "local")
    store._db.create_session(child, "local")
    store._db.create_session(newer, "local")
    messages = [{"role": "assistant", "content": "compressed summary"}]
    persist = store.persist_rotated_compression_child

    def persist_and_interleave(parent_id, child_id, rows):
        persisted = persist(parent_id, child_id, rows)
        if newer_owner:
            assert store.rebind_session_id(entry.session_key, parent, newer)
        return persisted

    monkeypatch.setattr(store, "persist_rotated_compression_child", persist_and_interleave)
    reconciliations = []
    replace = store._db.replace_gateway_routing_entries

    def record_reconciliation(*args, **kwargs):
        reconciliations.append(True)
        return replace(*args, **kwargs)

    monkeypatch.setattr(store._db, "replace_gateway_routing_entries", record_reconciliation)
    runner = SimpleNamespace(async_session_store=AsyncSessionStore(store), _sync_telegram_topic_binding=MagicMock())
    agent = SimpleNamespace(session_id=child)
    try:
        operation = GatewaySessionCommandsMixin._persist_manual_compression(runner, agent, entry, source, messages)
        if newer_owner:
            with pytest.raises(RuntimeError, match="binding changed"):
                asyncio.run(operation)
        else:
            asyncio.run(operation)
        expected = newer if newer_owner else child
        assert entry.session_id == expected
        rows = store._db.load_gateway_routing_entries(scope=store._routing_scope())
        assert json.loads(rows[entry.session_key])["session_id"] == expected
        mirror = json.loads((tmp_path / "sessions" / "sessions.json").read_text(encoding="utf-8"))
        assert mirror[entry.session_key]["session_id"] == expected
        assert reconciliations == []
        assert [row["content"] for row in store._db.get_messages(child)] == ["compressed summary"]
        assert runner._sync_telegram_topic_binding.call_count == (0 if newer_owner else 1)
    finally:
        store.close_all_db_handles()
