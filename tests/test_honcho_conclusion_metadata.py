"""Tests for Honcho conclusion metadata support."""

from __future__ import annotations

import json
from types import SimpleNamespace

from plugins.memory.honcho import CONCLUDE_SCHEMA, HonchoMemoryProvider
from plugins.memory.honcho.session import HonchoSession, HonchoSessionManager


def test_honcho_conclude_schema_accepts_metadata_object():
    metadata_schema = CONCLUDE_SCHEMA["parameters"]["properties"]["metadata"]

    assert metadata_schema["type"] == "object"
    assert metadata_schema["additionalProperties"] is True


def test_honcho_conclude_forwards_metadata_to_manager():
    provider = HonchoMemoryProvider()
    provider._session_initialized = True
    provider._session_key = "session-key"
    provider._cron_skipped = False

    class RecordingManager:
        calls = []

        def create_conclusion(self, session_key, conclusion, peer="user", metadata=None):
            self.calls.append({
                "session_key": session_key,
                "conclusion": conclusion,
                "peer": peer,
                "metadata": metadata,
            })
            return True

    manager = RecordingManager()
    provider._manager = manager

    result = json.loads(provider.handle_tool_call(
        "honcho_conclude",
        {
            "conclusion": "User prefers provenance on promoted memories",
            "peer": "user",
            "metadata": {"source": "scratchpad", "task_id": "t_meta"},
        },
    ))

    assert result["result"].startswith("Conclusion saved")
    assert manager.calls == [{
        "session_key": "session-key",
        "conclusion": "User prefers provenance on promoted memories",
        "peer": "user",
        "metadata": {"source": "scratchpad", "task_id": "t_meta"},
    }]


def test_honcho_conclude_rejects_metadata_on_delete():
    provider = HonchoMemoryProvider()
    provider._session_initialized = True
    provider._session_key = "session-key"
    provider._cron_skipped = False
    provider._manager = SimpleNamespace()

    result = json.loads(provider.handle_tool_call(
        "honcho_conclude",
        {"delete_id": "conc_1", "metadata": {"source": "not-allowed"}},
    ))

    assert "metadata can only be provided" in result["error"]


class _RecordingConclusionsScope:
    def __init__(self, *, fail_http: bool = False):
        self.observer = "hermes"
        self.observed = "user-peer"
        self.workspace_id = "workspace"
        self.created = []
        self._honcho = SimpleNamespace(
            _ensure_workspace=lambda: None,
            _http=SimpleNamespace(post=self._post),
        )
        self.http_posts = []
        self.fail_http = fail_http

    def _post(self, route, body):
        self.http_posts.append({"route": route, "body": body})
        if self.fail_http:
            raise RuntimeError("metadata unsupported")
        return []

    def create(self, conclusions):
        self.created.append(conclusions)
        return []


class _Peer:
    def __init__(self, scope):
        self.scope = scope

    def conclusions_of(self, target):
        return self.scope


def _manager_for_scope(scope):
    cfg = SimpleNamespace(
        write_frequency="turn",
        dialectic_reasoning_level="low",
        dialectic_dynamic=True,
        dialectic_max_chars=600,
        observation_mode="directional",
        user_observe_me=True,
        user_observe_others=True,
        ai_observe_me=True,
        ai_observe_others=True,
        message_max_chars=25000,
        dialectic_max_input_chars=10000,
    )
    manager = HonchoSessionManager(honcho=SimpleNamespace(), config=cfg)
    manager._cache["session-key"] = HonchoSession(
        key="session-key",
        user_peer_id="user-peer",
        assistant_peer_id="hermes",
        honcho_session_id="honcho-session",
    )
    manager._peers_cache["hermes"] = _Peer(scope)
    return manager


def test_create_conclusion_posts_metadata_wire_payload():
    scope = _RecordingConclusionsScope()
    manager = _manager_for_scope(scope)

    assert manager.create_conclusion(
        "session-key",
        "User prefers sourced conclusions",
        metadata={"source": "direct-api-test", "confidence": 0.9},
    ) is True

    assert scope.created == []
    assert scope.http_posts == [{
        "route": "/v3/workspaces/workspace/conclusions",
        "body": {
            "conclusions": [{
                "observer_id": "hermes",
                "observed_id": "user-peer",
                "content": "User prefers sourced conclusions",
                "session_id": "honcho-session",
                "metadata": {"source": "direct-api-test", "confidence": 0.9},
            }]
        },
    }]


def test_create_conclusion_falls_back_to_content_marker_when_metadata_unsupported():
    scope = _RecordingConclusionsScope(fail_http=True)
    manager = _manager_for_scope(scope)

    assert manager.create_conclusion(
        "session-key",
        "User prefers sourced conclusions",
        metadata={"source": "direct-api-test"},
    ) is True

    assert len(scope.created) == 1
    fallback_payload = scope.created[0][0]
    assert fallback_payload["session_id"] == "honcho-session"
    assert fallback_payload["content"].startswith("User prefers sourced conclusions")
    assert "[honcho_conclusion_metadata:" in fallback_payload["content"]
    assert '"source": "direct-api-test"' in fallback_payload["content"]
