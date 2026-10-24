"""Contracts for context passed to the final-output plugin hook."""

from __future__ import annotations

from agent.turn_finalizer import finalize_turn
from tests.agent.test_turn_finalizer_final_response_persistence import FakeAgent


def test_transform_hook_receives_detached_messages_and_workdir(monkeypatch):
    seen = {}

    def invoke_hook(name, **kwargs):
        if name == "transform_llm_output":
            seen.update(kwargs)
            kwargs["messages"][0]["content"] = "plugin mutation"
        return []

    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", invoke_hook)
    agent = FakeAgent()
    agent.cwd = "/tmp/plugin-workdir"
    messages = [{"role": "user", "content": "original"}]

    finalize_turn(
        agent,
        final_response="Done.",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="text_response(1)",
    )

    assert seen["workdir"] == "/tmp/plugin-workdir"
    assert seen["messages"][0]["content"] == "plugin mutation"
    assert messages[0]["content"] == "original"
