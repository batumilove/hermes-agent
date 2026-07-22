"""Per-turn detached-work tracking for operation-phase delivery."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import run_agent


class _FakeAgent:
    _record_detached_tool_result = run_agent.AIAgent._record_detached_tool_result


def _invoke(agent, args, result):
    with patch("agent.agent_runtime_helpers.invoke_tool", return_value=result):
        return run_agent.AIAgent._invoke_tool(
            agent,
            "terminal",
            args,
            "task-1",
            tool_call_id="call-1",
            messages=[],
        )


def test_successful_background_terminal_start_marks_detached_work():
    agent = _FakeAgent()

    result = _invoke(
        agent,
        {"command": "long-job", "background": True, "notify_on_complete": True},
        '{"session_id":"proc_test","status":"running"}',
    )

    assert '"session_id":"proc_test"' in result
    assert agent._turn_detached_work == [
        {"kind": "process", "id": "proc_test"}
    ]


def test_foreground_terminal_result_does_not_mark_detached_work():
    agent = _FakeAgent()

    _invoke(
        agent,
        {"command": "true", "background": False},
        '{"output":"","exit_code":0}',
    )

    assert getattr(agent, "_turn_detached_work", []) == []


def test_failed_background_terminal_start_does_not_mark_detached_work():
    agent = _FakeAgent()

    _invoke(
        agent,
        {"command": "bad", "background": True},
        '{"output":"","exit_code":-1,"error":"failed to start"}',
    )

    assert getattr(agent, "_turn_detached_work", []) == []


def _tool_call(args):
    return SimpleNamespace(
        id="call-terminal",
        type="function",
        function=SimpleNamespace(name="terminal", arguments=run_agent.json.dumps(args)),
    )


@pytest.fixture()
def production_agent():
    definitions = [{
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "terminal",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    with (
        patch("run_agent.get_tool_definitions", return_value=definitions),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = run_agent.AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent._turn_detached_work = []
        return agent


@pytest.mark.parametrize(
    ("arguments", "tool_result", "expected"),
    [
        (
            {"command": "long-job", "background": True},
            '{"session_id":"proc_single","status":"running"}',
            [{"kind": "process", "id": "proc_single"}],
        ),
        (
            {"command": "true", "background": False},
            '{"output":"","exit_code":0}',
            [],
        ),
        (
            {"command": "bad", "background": True},
            '{"error":"failed to start","exit_code":-1}',
            [],
        ),
    ],
)
def test_single_terminal_production_path_records_only_successful_background_work(
    production_agent,
    arguments,
    tool_result,
    expected,
):
    """A one-call model response takes the sequential dispatcher, not
    ``AIAgent._invoke_tool``. This is the normal terminal production path."""
    message = SimpleNamespace(content="", tool_calls=[_tool_call(arguments)])
    messages = []

    with patch("run_agent.handle_function_call", return_value=tool_result):
        production_agent._execute_tool_calls(message, messages, "task-1")

    assert production_agent._turn_detached_work == expected
