"""Post-turn consumers must receive the engine belonging to this agent."""
import logging
from types import SimpleNamespace

from agent import turn_finalizer


def test_post_turn_uses_agent_engine_when_another_engine_is_registered(monkeypatch):
    owner_engine = object()
    later_engine = object()
    agent = SimpleNamespace(session_id="shared-session", model="test", context_compressor=owner_engine)
    observed = []

    def consumer(name, logger, **payload):
        observed.append(payload.get("context_compressor") or later_engine)
        return []

    monkeypatch.setattr(turn_finalizer, "_invoke_hook_safely", consumer)
    monkeypatch.setattr(turn_finalizer, "apply_llm_output_transform", lambda *a, **k: ("reply", False, None))
    monkeypatch.setattr("agent.side_effect_evidence.build_side_effect_evidence_footer", lambda *a: "")
    message = {"role": "user", "content": "same words may occur twice"}
    messages = [message]
    result = turn_finalizer._apply_output_hooks(
        agent, "reply", logging.getLogger(__name__), platform="test",
        effective_task_id="task", turn_id="turn", original_user_message=message["content"],
        messages=messages,
    )
    assert observed == [owner_engine]
    assert result == ("reply", False, None)
    assert messages == [message]


def test_detached_agent_does_not_publish_post_turn_engine(monkeypatch):
    agent = SimpleNamespace(session_id="shared-session", model="test", context_compressor=object(), _persist_disabled=True)
    observed = []
    monkeypatch.setattr(turn_finalizer, "_invoke_hook_safely", lambda *a, **k: observed.append(k))
    monkeypatch.setattr(turn_finalizer, "apply_llm_output_transform", lambda *a, **k: ("reply", False, None))
    monkeypatch.setattr("agent.side_effect_evidence.build_side_effect_evidence_footer", lambda *a: "")
    turn_finalizer._apply_output_hooks(
        agent, "reply", logging.getLogger(__name__), platform="test",
        effective_task_id="task", turn_id="turn", original_user_message="fixture", messages=[],
    )
    assert observed == []
