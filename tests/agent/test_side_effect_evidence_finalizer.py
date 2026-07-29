"""Turn-finalizer integration tests for side-effect evidence regulation."""

from __future__ import annotations

import json

import pytest

from tests.agent.test_turn_finalizer_final_response_persistence import FakeAgent
from agent.turn_finalizer import finalize_turn


def _run_finalizer(agent, messages, final_response):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="text_response(2)",
    )


# ── Integration: finalizer appends footer to delivered response ─────────


def test_finalizer_appends_side_effect_warning_when_claim_lacks_current_turn_evidence(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    # Messages end with a tool result so the finalizer appends final_response.
    messages = [
        {"role": "user", "content": "create an issue"},
        {
            "role": "assistant",
            "content": "I will do it.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": "{}"},
    ]

    result = _run_finalizer(agent, messages, "I created the GitHub issue successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "github" in result["final_response"]
    # Footer must NOT leak into persisted messages (cache safety).
    assert result["messages"][-1]["role"] == "assistant"
    assert "Side-effect evidence regulator" not in result["messages"][-1]["content"]
    # Persisted messages got the clean final_response (without footer).
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == "I created the GitHub issue successfully."


def test_identity_transform_does_not_persist_delivery_only_footer(monkeypatch):
    def invoke_hook(name, **kwargs):
        if name == "transform_llm_output":
            return [kwargs["response_text"]]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", invoke_hook)
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "create an issue"},
        {
            "role": "assistant",
            "content": "I will do it.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "terminal", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "terminal",
            "content": "{}",
        },
    ]

    result = _run_finalizer(agent, messages, "I created the GitHub issue successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == (
        "I created the GitHub issue successfully."
    )
    assert "Side-effect evidence regulator" not in result["messages"][-1]["content"]


def test_transform_hook_never_receives_delivery_only_footer(monkeypatch):
    seen = {}

    def invoke_hook(name, **kwargs):
        if name == "transform_llm_output":
            seen["response_text"] = kwargs["response_text"]
            return [
                kwargs["response_text"].replace(
                    "Side-effect evidence regulator", "TRANSFORMED REGULATOR"
                )
            ]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", invoke_hook)
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "create an issue"},
        {
            "role": "assistant",
            "content": "I will do it.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "terminal", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "terminal",
            "content": "{}",
        },
    ]

    result = _run_finalizer(agent, messages, "I created the GitHub issue successfully.")

    assert "Side-effect evidence regulator" not in seen["response_text"]
    assert "Side-effect evidence regulator" in result["final_response"]
    assert "TRANSFORMED REGULATOR" not in result["final_response"]
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == (
        "I created the GitHub issue successfully."
    )


def test_finalizer_does_not_warn_when_current_turn_tool_evidence_exists(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "send it"},
        {
            "role": "assistant",
            "content": "I will send it.",
            "tool_calls": [{"id": "call-1", "function": {"name": "send_message", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "send_message", "content": '{"message_id":123,"status":"sent"}'},
    ]

    result = _run_finalizer(agent, messages, "I sent the message successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_rejects_unverified_controller_kanban_creation_and_status_claims(monkeypatch):
    """Regression for the controller-chat phantom-card incident."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Continue"}]
    claim = (
        "Continued. Added the Kanban branch:\n"
        "- t_5c8e72a4 — running\n"
        "- t_3b7e19d6 — todo\n"
        "- t_8f9a4c2b — blocked"
    )

    result = _run_finalizer(agent, messages, claim)

    assert result["final_response"].startswith("Kanban claim rejected:")
    assert "t_5c8e72a4" in result["final_response"]
    assert claim not in result["final_response"]
    assert result["completed"] is False
    assert result["turn_exit_reason"] == "kanban_claim_evidence_rejected"
    assert result["claim_verification"] == {
        "verified": False,
        "task_ids": ["t_5c8e72a4", "t_3b7e19d6", "t_8f9a4c2b"],
        "missing_evidence": [
            "t_5c8e72a4:mutation",
            "t_5c8e72a4:readback",
            "t_3b7e19d6:mutation",
            "t_3b7e19d6:readback",
            "t_8f9a4c2b:mutation",
            "t_8f9a4c2b:readback",
        ],
    }
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == result["final_response"]


def test_finalizer_accepts_controller_kanban_claim_after_create_and_readback(monkeypatch):
    """Creation claims require both a successful create result and readback."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Create and start it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "create-1", "function": {"name": "kanban_create", "arguments": '{"title":"x"}'}}],
        },
        {"role": "tool", "tool_call_id": "create-1", "name": "kanban_create", "content": '{"success":true,"task_id":"t_5c8e72a4"}'},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "show-1", "function": {"name": "kanban_show", "arguments": '{"task_id":"t_5c8e72a4"}'}}],
        },
        {"role": "tool", "tool_call_id": "show-1", "name": "kanban_show", "content": '{"task":{"id":"t_5c8e72a4","status":"running"},"parents":[],"children":[],"comments":[],"events":[],"runs":[],"worker_context":""}'},
    ]
    claim = "Created Kanban task t_5c8e72a4; readback confirms it is running."

    result = _run_finalizer(agent, messages, claim)

    assert result["final_response"] == claim
    assert result["completed"] is True


def test_status_only_claim_accepts_successful_terminal_show_readback(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "show-1", "function": {"name": "terminal", "arguments": '{"command":"hermes kanban --board ops show t_5c8e72a4 --json"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "show-1",
            "name": "terminal",
            "content": '{"output":"{\\"id\\":\\"t_5c8e72a4\\",\\"status\\":\\"blocked\\"}","exit_code":0,"error":null}',
        },
    ]
    claim = "Task t_5c8e72a4 is blocked."

    result = _run_finalizer(agent, messages, claim)

    assert result["final_response"] == claim


def test_direct_terminal_list_accepts_real_bare_json_array_contract(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Check it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "list-1",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"hermes kanban list --json"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "list-1",
            "name": "terminal",
            "content": '{"output":"[{\\"id\\":\\"t_5c8e72a4\\",\\"status\\":\\"running\\"}]","exit_code":0,"error":null}',
        },
    ]
    claim = "Task t_5c8e72a4 is running."

    result = _run_finalizer(agent, messages, claim)

    assert result["final_response"] == claim


def test_transform_hook_cannot_inject_unverified_kanban_claim(monkeypatch):
    def invoke_hook(name, **_kwargs):
        if name == "transform_llm_output":
            return ["Task t_5c8e72a4 is done."]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", invoke_hook)
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Give me a neutral answer"}]

    result = _run_finalizer(agent, messages, "No task update.")

    assert result["final_response"].startswith("Kanban claim rejected:")
    assert result["completed"] is False
    assert result["failed"] is True
    assert result["turn_exit_reason"] == "kanban_claim_evidence_rejected"
    assert result["claim_verification"] == {
        "verified": False,
        "task_ids": ["t_5c8e72a4"],
        "missing_evidence": ["t_5c8e72a4:readback"],
    }
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == result["final_response"]


def test_native_kanban_readback_success_contract_rejects_malformed_shapes():
    from agent.harness_learning import _tool_result_succeeded

    malformed = [
        ("kanban_show", '{"task":{}}'),
        ("kanban_show", '{"task":{"id":"t_1","status":""}}'),
        ("kanban_list", '{"tasks":[],"count":false}'),
        (
            "kanban_list",
            '{"tasks":[{"id":"t_1","status":"done"}],"count":true}',
        ),
        ("kanban_list", '{"tasks":[{}],"count":1}'),
    ]
    for tool_name, result in malformed:
        assert _tool_result_succeeded(tool_name, result, "{}") is False


def test_native_show_scopes_status_to_top_level_task_not_history(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    content = (
        '{"task":{"id":"t_12345678","status":"running"},'
        '"events":[{"kind":"status_change","payload":'
        '{"task_id":"t_12345678","status":"done"}}],'
        '"parents":[],"children":[],"comments":[],"runs":[],'
        '"worker_context":""}'
    )
    messages = [
        {"role": "user", "content": "Check it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "show-1",
                    "function": {
                        "name": "kanban_show",
                        "arguments": '{"task_id":"t_12345678"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "show-1",
            "name": "kanban_show",
            "content": content,
        },
    ]

    import copy

    accepted = _run_finalizer(
        agent, copy.deepcopy(messages), "Task t_12345678 is running."
    )
    rejected = _run_finalizer(
        FakeAgent(), copy.deepcopy(messages), "Task t_12345678 is done."
    )

    assert accepted["final_response"] == "Task t_12345678 is running."
    assert rejected["final_response"].startswith("Kanban claim rejected:")


@pytest.mark.parametrize(
    "tool_name,content",
    [
        (
            "kanban_show",
            '{"task":{"id":"t_12345678","status":"running"},'
            '"output":"{\\"task\\":{\\"id\\":\\"t_12345678\\",'
            '\\"status\\":\\"done\\"}}"}',
        ),
        (
            "kanban_list",
            '{"tasks":[{"id":"t_12345678","status":"running"}],'
            '"count":1,"output":"{\\"tasks\\":[{\\"id\\":'
            '\\"t_12345678\\",\\"status\\":\\"done\\"}]}"}',
        ),
    ],
)
def test_native_readback_rejects_status_spoofed_through_output_field(
    monkeypatch, tool_name, content
):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    operation = tool_name.removeprefix("kanban_")
    messages = [
        {"role": "user", "content": "Check it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "readback-1",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(
                            {"task_id": "t_12345678"}
                            if operation == "show"
                            else {"board": "ops"}
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "readback-1",
            "name": tool_name,
            "content": content,
        },
    ]

    result = _run_finalizer(
        FakeAgent(), messages, "Task t_12345678 is done."
    )

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_status_claim_rejects_readback_with_different_status(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "show-1", "function": {"name": "kanban_show", "arguments": '{"task_id":"t_5c8e72a4"}'}}]},
        {"role": "tool", "tool_call_id": "show-1", "name": "kanban_show", "content": '{"ok":true,"task":{"id":"t_5c8e72a4","status":"blocked"}}'},
    ]

    result = _run_finalizer(agent, messages, "Task t_5c8e72a4 is running.")

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_status_claim_rejects_prefix_task_id_readback(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "show-1", "function": {"name": "kanban_show", "arguments": '{"task_id":"t_12345678ff"}'}}]},
        {"role": "tool", "tool_call_id": "show-1", "name": "kanban_show", "content": '{"ok":true,"task":{"id":"t_12345678ff","status":"blocked"}}'},
    ]

    result = _run_finalizer(agent, messages, "Task t_12345678 is blocked.")

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_status_claim_rejects_status_from_different_list_task(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "list-1", "function": {"name": "kanban_list", "arguments": '{"board":"ops"}'}}]},
        {"role": "tool", "tool_call_id": "list-1", "name": "kanban_list", "content": '{"tasks":[{"id":"t_12345678","status":"running"},{"id":"t_deadbeef","status":"blocked"}],"count":2,"limit":50,"truncated":false,"next_limit":null,"promoted":[]}'},
    ]

    result = _run_finalizer(agent, messages, "Task t_12345678 is blocked.")

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_status_claim_rejects_status_from_different_terminal_json_row(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "list-1", "function": {"name": "terminal", "arguments": '{"command":"hermes kanban list --board ops --json"}'}}]},
        {"role": "tool", "tool_call_id": "list-1", "name": "terminal", "content": '{"output":"{\\"tasks\\":[{\\"id\\":\\"t_12345678\\",\\"status\\":\\"running\\"},{\\"id\\":\\"t_deadbeef\\",\\"status\\":\\"blocked\\"}]}","exit_code":0,"error":null}'},
    ]

    result = _run_finalizer(agent, messages, "Task t_12345678 is blocked.")

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_status_claim_rejects_orphan_tool_result_without_matching_call(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {"role": "tool", "tool_call_id": "missing-call", "name": "kanban_show", "content": '{"ok":true,"task":{"id":"t_5c8e72a4","status":"blocked"}}'},
    ]

    result = _run_finalizer(agent, messages, "Task t_5c8e72a4 is blocked.")

    assert result["final_response"].startswith("Kanban claim rejected:")


@pytest.mark.parametrize(
    "tool_name,arguments,content",
    [
        (
            "terminal",
            '{"command":"hermes kanban show t_5c8e72a4 --json"}',
            '{"output":"{\\"id\\":\\"t_5c8e72a4\\",\\"status\\":\\"blocked\\"}","error":null}',
        ),
        (
            "kanban_show",
            '{"task_id":"t_5c8e72a4"}',
            'task t_5c8e72a4 status: blocked',
        ),
        (
            "terminal",
            '{"command":"hermes kanban show t_5c8e72a4 --json"}',
            '{"output":"t_5c8e72a4 status: blocked","exit_code":1,"error":null}',
        ),
    ],
)
def test_status_claim_rejects_missing_malformed_or_nonzero_tool_outcome(
    monkeypatch, tool_name, arguments, content
):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Update"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "show-1", "function": {"name": tool_name, "arguments": arguments}}]},
        {"role": "tool", "tool_call_id": "show-1", "name": tool_name, "content": content},
    ]

    result = _run_finalizer(agent, messages, "Task t_5c8e72a4 is blocked.")

    assert result["final_response"].startswith("Kanban claim rejected:")


@pytest.mark.parametrize(
    "response",
    [
        "```text\nCreated Kanban task t_5c8e72a4; it is running.\n```",
        "> Created Kanban task t_5c8e72a4; it is running.",
        "Task t_5c8e72a4 is not running.",
        "If task t_5c8e72a4 were running, I would inspect it.",
        "For example: status=done on t_5c8e72a4 does not mean PASS.",
    ],
)
def test_nonassertive_or_quoted_kanban_examples_are_not_rejected(monkeypatch, response):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Explain the policy"}]

    result = _run_finalizer(agent, messages, response)

    assert result["final_response"] == response


def test_finalizer_rejects_shell_wrapper_that_swallows_child_failure(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Create it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "create-1",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"sh -c \'hermes kanban create --title x || true\'"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "create-1",
            "name": "terminal",
            "content": '{"output":"{\\"id\\":\\"t_5c8e72a4\\",\\"status\\":\\"running\\"}","exit_code":0,"error":null}',
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "show-1",
                    "function": {
                        "name": "kanban_show",
                        "arguments": '{"task_id":"t_5c8e72a4"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "show-1",
            "name": "kanban_show",
            "content": '{"task":{"id":"t_5c8e72a4","status":"running"}}',
        },
    ]

    result = _run_finalizer(
        agent,
        messages,
        "Created Kanban task t_5c8e72a4; it is running.",
    )

    assert result["final_response"].startswith("Kanban claim rejected:")
    assert "t_5c8e72a4:mutation" in result["final_response"]


def test_terminal_evidence_rejects_untrusted_hermes_executable_path():
    from agent.harness_learning import _terminal_is_direct_kanban_command

    assert not _terminal_is_direct_kanban_command(
        '{"command":"/tmp/hermes kanban show t_5c8e72a4 --json"}'
    )


def test_plural_pronoun_status_claim_requires_matching_status_for_each_task(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages: list[dict] = [{"role": "user", "content": "Create both"}]
    for index, task_id in enumerate(("t_12345678", "t_deadbeef"), start=1):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"create-{index}",
                            "function": {
                                "name": "kanban_create",
                                "arguments": '{"title":"x"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"create-{index}",
                    "name": "kanban_create",
                    "content": (
                        '{"ok":true,"task":{"id":"'
                        + task_id
                        + '","status":"blocked"}}'
                    ),
                },
            ]
        )
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "list-1",
                        "function": {
                            "name": "kanban_list",
                            "arguments": '{"board":"ops"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "list-1",
                "name": "kanban_list",
                "content": '{"tasks":[{"id":"t_12345678","status":"blocked"},{"id":"t_deadbeef","status":"blocked"}],"count":2}',
            },
        ]
    )

    result = _run_finalizer(
        agent,
        messages,
        "Created tasks t_12345678 and t_deadbeef are blocked. They are running.",
    )

    assert result["final_response"].startswith("Kanban claim rejected:")
    assert "t_12345678:readback" in result["final_response"]
    assert "t_deadbeef:readback" in result["final_response"]


def test_finalizer_rejects_kanban_claim_when_nested_subprocess_failed(monkeypatch):
    """Outer execute_code success must not bless a failed inner CLI command."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Create it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "exec-1", "function": {"name": "execute_code", "arguments": '{"code":"subprocess.run(...)"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "exec-1",
            "name": "execute_code",
            "content": '{"status":"success","output":"stderr: hermes: error: unrecognized arguments: --title","exit_code":0}',
        },
    ]

    result = _run_finalizer(agent, messages, "Created Kanban task t_5c8e72a4 and it is running.")

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_finalizer_rejects_execute_code_completed_process_failure(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "Create it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "exec-create", "function": {"name": "execute_code", "arguments": '{"code":"terminal(\u0027hermes kanban create\u0027)"}'}}]},
        {"role": "tool", "tool_call_id": "exec-create", "name": "execute_code", "content": '{"status":"success","output":"t_5c8e72a4 created"}'},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "exec-show", "function": {"name": "execute_code", "arguments": '{"code":"terminal(\u0027hermes kanban show t_5c8e72a4\u0027)"}'}}]},
        {"role": "tool", "tool_call_id": "exec-show", "name": "execute_code", "content": '{"status":"success","output":"CompletedProcess(args=[\u0027hermes\u0027], returncode=1, stdout=\u0027t_5c8e72a4 status: running\u0027)"}'},
    ]

    result = _run_finalizer(agent, messages, "Created Kanban task t_5c8e72a4 and it is running.")

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_modal_suffix_does_not_suppress_affirmative_creation_claim(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Create it"}]

    result = _run_finalizer(
        agent,
        messages,
        "I created t_5c8e72a4 and it is running, though it could fail later.",
    )

    assert result["final_response"].startswith("Kanban claim rejected:")


@pytest.mark.parametrize("claim", ["I unblocked t_5c8e72a4.", "I commented on t_5c8e72a4."])
def test_unsupported_mutation_verbs_cannot_pass_as_prose(monkeypatch, claim):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Continue"}]

    result = _run_finalizer(agent, messages, claim)

    assert result["final_response"].startswith("Kanban claim rejected:")


def test_finalizer_allows_forensic_description_of_historical_phantom_claim(monkeypatch):
    """Quoting an old false report is analysis, not a fresh state claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Investigate the old report"}]
    response = (
        "The previous assistant claimed that t_5c8e72a4 was running, "
        "but the board database proved that task never existed."
    )

    result = _run_finalizer(agent, messages, response)

    assert result["final_response"] == response


def test_forensic_context_does_not_mask_a_fresh_unverified_claim(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "Repair the old report"}]
    response = (
        "The previous assistant claimed t_5c8e72a4 existed. "
        "I created Kanban task t_3b7e19d6 and it is running."
    )

    result = _run_finalizer(agent, messages, response)

    assert result["final_response"].startswith("Kanban claim rejected:")
    assert "t_3b7e19d6" in result["final_response"]


def test_worker_completion_response_uses_worker_manifest_gate_not_controller_guard(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_deadbeef")
    agent = FakeAgent()
    messages = [{"role": "user", "content": "worker task"}]
    response = "Created child t_5c8e72a4; it is ready."

    result = _run_finalizer(agent, messages, response)

    assert result["final_response"] == response


def test_finalizer_ignores_prior_turn_side_effect_evidence(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "old"},
        {"role": "tool", "name": "send_message", "content": '{"message_id":1}'},
        {"role": "assistant", "content": "Sent."},
        {"role": "user", "content": "new"},
    ]

    result = _run_finalizer(agent, messages, "I sent the message successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "send_message" in result["final_response"]


# ── False-positive: words that look like claims but aren't ──────────────


def test_finalizer_no_warning_for_cron_config_discussion(monkeypatch):
    """Mentioning 'cron' in 'cron config' or 'cron schedule' context without
    a success claim should not trigger the regulator."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "what is cron?"},
    ]

    result = _run_finalizer(agent, messages, "The cron config file is at /etc/crontab.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_generic_discussion_without_action_claims(monkeypatch):
    """Words like 'upload', 'deploy', 'delete' in non-claim context should
    not trigger the regulator."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "explain deployment"},
    ]

    result = _run_finalizer(
        agent, messages, "Deployment is the process of moving code to production."
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_negated_side_effect_claims(monkeypatch):
    """Explicit denials are not success claims and must not demand evidence."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify the prior report"}]

    result = _run_finalizer(
        agent,
        messages,
        (
            "I have not sent the message. "
            "Nothing was deployed, and nothing was deleted."
        ),
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_inspected_historical_state(monkeypatch):
    """Describing inspected code/history is not a fresh side-effect claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "what is the current state?"}]

    result = _run_finalizer(
        agent,
        messages,
        (
            "The structural fix was deployed earlier. "
            "The automatic optimizer is removed from the current code path."
        ),
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_deployed_runtime_parity_status_heading(monkeypatch):
    """A parity heading describes inspected state, not a deployment this turn."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify source/runtime parity"}]

    result = _run_finalizer(
        agent,
        messages,
        "## 7. Merged source and deployed runtime parity — PASS",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_warns_for_fresh_deploy_claim_shaped_like_status_line(monkeypatch):
    """A status suffix must not hide an affirmative side-effect claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy to production"}]

    result = _run_finalizer(
        agent,
        messages,
        "Deployed to production, status: PASS",
    )

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "deploy" in result["final_response"]


def test_finalizer_no_warning_when_response_has_no_side_effect_claims(monkeypatch):
    """A normal informational response should never get a footer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "what is 2+2?"},
    ]

    result = _run_finalizer(agent, messages, "The answer is 4.")

    assert "Side-effect evidence regulator" not in result["final_response"]


# ── Write-file / terminal evidence integration ──────────────────────────


def test_terminal_positive_result_satisfies_generic_side_effect_claim(monkeypatch):
    """A terminal tool result with exit_code/status should satisfy deploy/delete
    claims via the generic_side_effect evidence bucket."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": '{"exit_code":0,"output":"Deployed."}'},
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_terminal_real_success_envelope_satisfies_generic_side_effect_claim(monkeypatch):
    """The live terminal envelope includes error:null and remains success evidence."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "terminal",
            "content": '{"output":"Deployed.","exit_code":0,"error":null}',
        },
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_execute_code_real_success_envelope_satisfies_generic_side_effect_claim(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "execute_code", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "execute_code",
            "content": '{"status":"success","output":"Deployed.","exit_code":0,"error":null}',
        },
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_execute_code_nonzero_inner_exit_does_not_satisfy_side_effect_claim(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "execute_code", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "execute_code",
            "content": '{"status":"success","output":"child failed","exit_code":1,"error":null}',
        },
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]


def test_terminal_error_result_does_not_satisfy_side_effect_claim(monkeypatch):
    """A terminal result that looks like an error should NOT satisfy the claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": '{"error":"connection refused","exit_code":1}'},
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]


def test_finalizer_still_warns_for_affirmative_claim_with_no_errors_phrase(monkeypatch):
    """The word 'no' after the action verb must not negate a success claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy it"}]

    result = _run_finalizer(agent, messages, "I deployed the service with no errors.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "deploy" in result["final_response"]


def test_finalizer_warns_for_first_person_claim_about_earlier_version(monkeypatch):
    """An object described as earlier is still a first-person completion claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy it"}]

    result = _run_finalizer(agent, messages, "I deployed the earlier version.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "deploy" in result["final_response"]


def test_finalizer_no_warning_for_negated_or_historical_other_categories(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify prior claims"}]
    cases = (
        "The GitHub issue was not created.",
        "The cron job was paused earlier.",
        "The file was uploaded previously.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_warns_for_positive_action_after_negative_contrast(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy the new service"}]
    cases = (
        "I have not deployed the old service, but I deployed the new service successfully.",
        "Previously the deploy failed, but I deployed the service successfully.",
        "I did not deploy the old service and then I deployed the new service successfully.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" in result["final_response"], response
        assert "deploy" in result["final_response"]


def test_finalizer_no_warning_for_genuinely_historical_first_person_action(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect history"}]
    cases = (
        "I deployed it earlier.",
        "Earlier, I sent the message.",
        "I previously uploaded the file.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_no_warning_for_contracted_negation(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify prior claims"}]
    cases = (
        "I haven't sent the message.",
        "I couldn't deploy the service.",
        "I don't believe I sent the message.",
        "I wouldn't delete the record.",
        "I can't upload the file.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_does_not_treat_generic_job_as_cron_context(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "run build"}]

    result = _run_finalizer(agent, messages, "I ran the build job successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_preserves_category_context_across_sentences(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "do it"}]
    cases = (
        ("The message is ready. I sent it successfully.", "send_message"),
        ("The GitHub issue is #123. I created it successfully.", "github"),
    )

    for response, category in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" in result["final_response"], response
        assert category in result["final_response"]


def test_finalizer_warns_for_positive_action_after_plain_and(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "do it"}]

    result = _run_finalizer(
        agent,
        messages,
        "I did not create the issue and submitted the pull request successfully.",
    )

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "github" in result["final_response"]


def test_finalizer_no_warning_for_first_person_inspection_report(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect it"}]

    result = _run_finalizer(
        agent,
        messages,
        "I inspected the logs and confirmed the message was sent.",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_warns_for_current_action_after_historical_action(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "upload new file"}]

    result = _run_finalizer(
        agent,
        messages,
        "I previously uploaded the old file and uploaded the new file successfully.",
    )

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "upload" in result["final_response"]


def test_finalizer_warns_for_unqualified_passive_completion_claims(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "do it"}]
    cases = (
        ("The message was sent.", "send_message"),
        ("The service was deployed.", "deploy"),
        ("The record was deleted.", "delete"),
    )

    for response, category in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" in result["final_response"], response
        assert category in result["final_response"]


def test_finalizer_no_warning_for_neither_nor(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "check issue"}]

    result = _run_finalizer(
        agent,
        messages,
        "I neither created nor submitted the GitHub issue.",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_calendar_history(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect history"}]
    cases = (
        "The service was deployed yesterday.",
        "The service was deployed last week.",
        "The service was deployed earlier today.",
        "Last week, I deployed it.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_no_warning_for_present_state_description(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect state"}]
    cases = (
        "The cron job is paused.",
        "The issue is currently opened.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_no_warning_for_coordinated_present_state_description(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect the production cron"}]
    response = (
        "Production cron: olah-health-monitor is enabled and scheduled every 30 minutes. "
        "No manual production run was triggered."
    )

    result = _run_finalizer(agent, messages, response)

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_warns_when_coordinated_sentence_contains_fresh_action(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "schedule the production cron"}]
    response = "The production cron is enabled and I scheduled it every 30 minutes."

    result = _run_finalizer(agent, messages, response)

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "cronjob" in result["final_response"]


def test_finalizer_does_not_borrow_category_context_from_later_sentence(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "work locally"}]

    result = _run_finalizer(
        agent,
        messages,
        "I created a local file successfully. GitHub access is unavailable.",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


# ── Edge cases ──────────────────────────────────────────────────────────


def test_finalizer_no_warning_when_verifier_disabled(monkeypatch):
    """When the config toggle is off, no footer should be appended."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    # Flip the toggle off by monkeypatching the method.
    monkeypatch.setattr(
        agent, "_side_effect_evidence_verifier_enabled", lambda: False
    )
    messages = [
        {"role": "user", "content": "create an issue"},
    ]

    result = _run_finalizer(agent, messages, "I created the GitHub issue successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_empty_final_response(monkeypatch):
    """Empty final_response should not trigger the footer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "hi"}]

    result = _run_finalizer(agent, messages, "")

    assert "Side-effect evidence regulator" not in (result.get("final_response") or "")


def test_finalizer_no_warning_when_interrupted(monkeypatch):
    """Interrupted turns should not get the footer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "create an issue"}]

    result = finalize_turn(
        agent,
        final_response="I created the GitHub issue successfully.",
        api_call_count=2,
        interrupted=True,  # interrupted
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="interrupted",
    )

    assert "Side-effect evidence regulator" not in (result.get("final_response") or "")


def test_footer_empty_when_no_messages():
    """build_side_effect_evidence_footer should handle empty messages."""
    from agent.harness_learning import build_side_effect_evidence_footer

    assert build_side_effect_evidence_footer([], "I sent the message.") != ""
    # None messages should be treated as empty, not crash.
    assert build_side_effect_evidence_footer(messages=None or [], final_response="I sent the message.") != ""
