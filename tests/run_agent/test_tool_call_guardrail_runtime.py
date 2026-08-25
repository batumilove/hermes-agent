"""Runtime tests for tool-call loop guardrails."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _make_tool_defs(*names: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _mock_tool_call(name="web_search", arguments="{}", call_id=None):
    return SimpleNamespace(
        id=call_id or f"call_{uuid.uuid4().hex[:8]}",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _mock_response(content="Hello", finish_reason="stop", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


def _make_agent(*tool_names: str, max_iterations: int = 10, config: dict | None = None) -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs(*tool_names)),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value=config or {}),
        patch("hermes_cli.config.load_config_readonly", return_value=config or {}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            max_iterations=max_iterations,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def _seed_exact_failures(agent: AIAgent, tool_name: str, args: dict, count: int = 2) -> None:
    for _ in range(count):
        agent._tool_guardrails.after_call(
            tool_name,
            args,
            json.dumps({"error": "boom"}),
            failed=True,
        )


def _hard_stop_config(**overrides) -> dict:
    cfg = {
        "tool_loop_guardrails": {
            "warnings_enabled": True,
            "hard_stop_enabled": True,
            "hard_stop_after": {
                "exact_failure": 2,
                "same_tool_failure": 8,
                "idempotent_no_progress": 5,
            },
        }
    }
    cfg["tool_loop_guardrails"].update(overrides)
    return cfg


def test_default_sequential_path_warns_repeated_exact_failure_without_blocking_execution():
    agent = _make_agent("web_search")
    args = {"query": "same"}
    _seed_exact_failures(agent, "web_search", args)
    starts = []
    progress = []
    agent.tool_start_callback = lambda *a, **k: starts.append((a, k))
    agent.tool_progress_callback = lambda *a, **k: progress.append((a, k))
    tc = _mock_tool_call("web_search", json.dumps(args), "c-soft")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []

    with patch("run_agent.handle_function_call", return_value=json.dumps({"error": "boom"})) as mock_hfc:
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    mock_hfc.assert_called_once()
    assert len(starts) == 1
    assert any(event[0][0] == "tool.completed" for event in progress)
    assert len(messages) == 1
    assert messages[0]["role"] == "tool"
    assert messages[0]["tool_call_id"] == "c-soft"
    assert "repeated_exact_failure_warning" in messages[0]["content"]
    assert "repeated_exact_failure_block" not in messages[0]["content"]
    assert agent._tool_guardrail_halt_decision is None


def test_config_enabled_hard_stop_blocks_repeated_exact_failure_before_execution():
    agent = _make_agent("web_search", config=_hard_stop_config())
    args = {"query": "same"}
    _seed_exact_failures(agent, "web_search", args)
    starts = []
    progress = []
    agent.tool_start_callback = lambda *a, **k: starts.append((a, k))
    agent.tool_progress_callback = lambda *a, **k: progress.append((a, k))
    tc = _mock_tool_call("web_search", json.dumps(args), "c-block")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []

    with patch("run_agent.handle_function_call", return_value="SHOULD_NOT_RUN") as mock_hfc:
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    mock_hfc.assert_not_called()
    assert starts == []
    assert progress == []
    assert len(messages) == 1
    assert messages[0]["role"] == "tool"
    assert messages[0]["tool_call_id"] == "c-block"
    assert "repeated_exact_failure_block" in messages[0]["content"]


def test_sequential_after_call_appends_guidance_to_tool_result_without_extra_messages():
    agent = _make_agent("web_search")
    args = {"query": "same"}
    _seed_exact_failures(agent, "web_search", args, count=1)
    tc = _mock_tool_call("web_search", json.dumps(args), "c-warn")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []

    with patch("run_agent.handle_function_call", return_value=json.dumps({"error": "boom"})):
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    assert [m["role"] for m in messages] == ["tool"]
    assert messages[0]["tool_call_id"] == "c-warn"
    assert "Tool loop warning" in messages[0]["content"]
    assert "repeated_exact_failure_warning" in messages[0]["content"]


def test_same_tool_failure_warning_tells_model_to_recover_with_tools():
    agent = _make_agent("terminal")
    guardrails = getattr(agent, "_tool_guardrails")
    guardrails.after_call(
        "terminal",
        {"command": "bad-1"},
        json.dumps({"exit_code": 1}),
        failed=True,
    )
    guardrails.after_call(
        "terminal",
        {"command": "bad-2"},
        json.dumps({"exit_code": 1}),
        failed=True,
    )
    tc = _mock_tool_call("terminal", json.dumps({"command": "bad-3"}), "c-recover")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []

    with patch("run_agent.handle_function_call", return_value=json.dumps({"exit_code": 1})):
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    content = messages[0]["content"]
    assert "same_tool_failure_warning" in content
    assert "Do not switch to text-only replies" in content
    assert "keep using tools" in content
    assert "pwd && ls -la" in content
    assert "absolute path" in content
    assert "different tool" in content


def test_config_enabled_hard_stop_concurrent_path_does_not_submit_blocked_calls_and_preserves_result_order():
    agent = _make_agent("web_search", config=_hard_stop_config())
    blocked_args = {"query": "blocked"}
    allowed_args = {"query": "allowed"}
    _seed_exact_failures(agent, "web_search", blocked_args)
    starts = []
    progress_events = []
    agent.tool_start_callback = lambda tool_call_id, name, args: starts.append((tool_call_id, name, args))
    agent.tool_progress_callback = lambda event, name, preview, args, **kw: progress_events.append((event, name, args, kw))
    calls = [
        _mock_tool_call("web_search", json.dumps(blocked_args), "c-block"),
        _mock_tool_call("web_search", json.dumps(allowed_args), "c-allow"),
    ]
    msg = SimpleNamespace(content="", tool_calls=calls)
    messages = []
    executed = []

    def fake_handle(name, args, task_id, **kwargs):
        executed.append((name, args, kwargs["tool_call_id"]))
        return json.dumps({"ok": args["query"]})

    with patch("run_agent.handle_function_call", side_effect=fake_handle):
        agent._execute_tool_calls_concurrent(msg, messages, "task-1")

    assert executed == [("web_search", allowed_args, "c-allow")]
    assert [m["tool_call_id"] for m in messages] == ["c-block", "c-allow"]
    assert "repeated_exact_failure_block" in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {"ok": "allowed"}
    assert starts == [("c-allow", "web_search", allowed_args)]
    started_events = [event for event in progress_events if event[0] == "tool.started"]
    completed_events = [event for event in progress_events if event[0] == "tool.completed"]
    assert started_events == [("tool.started", "web_search", allowed_args, {})]
    assert len(completed_events) == 1
    assert completed_events[0][1] == "web_search"


def test_relay_rewrite_precedes_sequential_policy_approval_checkpoint_and_dispatch():
    agent = _make_agent("write_file")
    original_args = {"path": "/original/path", "content": "old"}
    final_args = {"path": "/approved/path", "content": "new"}
    tc = _mock_tool_call("write_file", json.dumps(original_args), "c-rewrite")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []
    observed = {
        "plugin": [],
        "guardrail": [],
        "approval": [],
        "checkpoint": [],
        "start": [],
        "dispatch": [],
    }

    original_before_call = agent._tool_guardrails.before_call

    def observe_guardrail(name, args):
        observed["guardrail"].append((name, dict(args)))
        return original_before_call(name, args)

    def relay_execute(name, args, callback, **kwargs):
        del name, args, kwargs
        return callback(dict(final_args)), dict(final_args)

    def observe_plugin(name, args, **kwargs):
        del kwargs
        observed["plugin"].append((name, dict(args)))
        return (None, None)

    def observe_approval(name, args):
        observed["approval"].append((name, dict(args)))
        return None

    def dispatch(name, args, task_id, **kwargs):
        del task_id, kwargs
        observed["dispatch"].append((name, dict(args)))
        return json.dumps({"ok": True})

    agent._checkpoint_mgr = SimpleNamespace(
        enabled=True,
        get_working_dir_for_path=lambda path: path,
        ensure_checkpoint=lambda path, reason: observed["checkpoint"].append(
            (path, reason)
        ),
    )
    agent.tool_start_callback = lambda _call_id, name, args: observed["start"].append(
        (name, dict(args))
    )

    with (
        patch("agent.relay_tools.execute", side_effect=relay_execute),
        patch(
            "hermes_cli.plugins._dispatch_pre_tool_call_hooks",
            side_effect=observe_plugin,
        ),
        patch.object(agent._tool_guardrails, "before_call", side_effect=observe_guardrail),
        patch(
            "acp_adapter.edit_approval.maybe_require_edit_approval",
            side_effect=observe_approval,
        ),
        patch("model_tools.registry.dispatch", side_effect=dispatch),
    ):
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    expected = [("write_file", final_args)]
    assert observed["plugin"] == expected
    assert observed["guardrail"] == expected
    assert observed["approval"] == expected
    assert observed["start"] == expected
    assert observed["dispatch"] == expected
    assert observed["checkpoint"] == [
        ("/approved/path", "before write_file")
    ]


def test_relay_rewrite_is_guarded_before_dispatch_in_concurrent_path():
    agent = _make_agent("web_search", config=_hard_stop_config())
    original_args = {"query": "original"}
    blocked_args = {"query": "blocked"}
    _seed_exact_failures(agent, "web_search", blocked_args)
    tc = _mock_tool_call("web_search", json.dumps(original_args), "c-rewrite-block")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []
    starts = []

    def relay_execute(name, args, callback, **kwargs):
        del name, args, kwargs
        return callback(dict(blocked_args)), dict(blocked_args)

    agent.tool_start_callback = lambda *args: starts.append(args)
    with (
        patch("agent.relay_tools.execute", side_effect=relay_execute),
        patch("run_agent.handle_function_call", return_value="SHOULD_NOT_RUN") as dispatch,
    ):
        agent._execute_tool_calls_concurrent(msg, messages, "task-1")

    dispatch.assert_not_called()
    assert starts == []
    assert "repeated_exact_failure_block" in messages[0]["content"]


def test_plugin_pre_tool_block_wins_without_counting_as_toolguard_block():
    agent = _make_agent("web_search")
    args = {"query": "same"}
    tc = _mock_tool_call("web_search", json.dumps(args), "c-plugin")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []

    with (
        patch(
            "hermes_cli.plugins._dispatch_pre_tool_call_hooks",
            return_value=("plugin policy", None),
        ),
        patch("run_agent.handle_function_call", return_value="SHOULD_NOT_RUN") as mock_hfc,
    ):
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    mock_hfc.assert_not_called()
    assert "plugin policy" in messages[0]["content"]
    assert agent._tool_guardrails.before_call("web_search", args).action == "allow"


def test_default_run_conversation_warns_without_guardrail_halt():
    agent = _make_agent("web_search", max_iterations=10)
    same_args = {"query": "same"}
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[_mock_tool_call("web_search", json.dumps(same_args), f"c{i}")],
        )
        for i in range(1, 4)
    ]
    responses.append(_mock_response(content="done", finish_reason="stop", tool_calls=None))
    agent.client.chat.completions.create.side_effect = responses

    with (
        patch("run_agent.handle_function_call", return_value=json.dumps({"error": "boom"})) as mock_hfc,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("search repeatedly")

    assert mock_hfc.call_count == 3
    assert result["turn_exit_reason"].startswith("text_response")
    assert "guardrail" not in result
    assert result["final_response"] == "done"
    tool_contents = [m["content"] for m in result["messages"] if m.get("role") == "tool"]
    assert any("repeated_exact_failure_warning" in content for content in tool_contents)



def test_guardrail_halt_emits_final_response_through_stream_delta_callback():
    """Regression for #30770: when the guardrail halts the loop, the
    synthesized halt message must be pushed through ``stream_delta_callback``
    so SSE/TUI clients see why the agent stopped instead of a silent stream
    close.  Without this the chat-completions SSE writer drains an empty
    queue and emits a finish chunk with zero content (indistinguishable
    from a crash for Open WebUI and similar clients).
    """
    agent = _make_agent("web_search", max_iterations=10, config=_hard_stop_config())
    same_args = {"query": "same"}
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[_mock_tool_call("web_search", json.dumps(same_args), f"c{i}")],
        )
        for i in range(1, 10)
    ]
    agent.client.chat.completions.create.side_effect = responses

    deltas: list = []
    agent.stream_delta_callback = lambda d: deltas.append(d)
    # The mocked client returns SimpleNamespace responses which aren't
    # iterable as streaming chunks; force the non-streaming code path so
    # the guardrail-halt branch is reached without engaging the real
    # streaming machinery.
    agent._disable_streaming = True

    with (
        patch("run_agent.handle_function_call", return_value=json.dumps({"error": "boom"})),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("search repeatedly")

    assert result["turn_exit_reason"] == "guardrail_halt"
    halt_text = result["final_response"]
    assert "stopped retrying" in halt_text

    # The halt message must have been pushed through the callback at least
    # once.  Empty-queue SSE writers were the bug — clients saw no content
    # delta before the finish chunk.
    text_deltas = [d for d in deltas if isinstance(d, str)]
    assert halt_text in text_deltas, (
        f"halt message was never streamed; callback only saw {deltas!r}"
    )


def _web_search_budget_config(*, warn: int, maximum: int) -> dict:
    return {
        "tool_loop_guardrails": {
            "warnings_enabled": True,
            "hard_stop_enabled": False,
            "loop_caps": {
                "warn_web_searches": warn,
                "max_web_searches": maximum,
            },
        }
    }


def test_web_search_soft_budget_warning_reaches_model_after_execution():
    agent = _make_agent(
        "web_search",
        max_iterations=5,
        config=_web_search_budget_config(warn=1, maximum=2),
    )
    call = _mock_tool_call(
        "web_search", json.dumps({"query": "q1"}), "c-soft-budget"
    )
    message = SimpleNamespace(content="", tool_calls=[call])
    messages = []

    with patch(
        "run_agent.handle_function_call",
        return_value=json.dumps({"success": True, "data": {"web": []}}),
    ) as dispatch:
        agent._execute_tool_calls_sequential(message, messages, "task-1")

    dispatch.assert_called_once()
    assert [item["role"] for item in messages] == ["tool"]
    content = messages[0]["content"]
    assert "loop_web_search_soft_warning" in content
    assert "synthesize rather than continuing broad searching" in content


def test_web_search_soft_budget_warning_reaches_concurrent_result_after_observation():
    agent = _make_agent(
        "web_search",
        max_iterations=5,
        config=_web_search_budget_config(warn=2, maximum=3),
    )
    calls = [
        _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c-soft-1"),
        _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c-soft-2"),
    ]
    messages = []

    with patch(
        "run_agent.handle_function_call",
        return_value=json.dumps({"success": True, "data": {"web": []}}),
    ) as dispatch:
        agent._execute_tool_calls_concurrent(
            SimpleNamespace(content="", tool_calls=calls),
            messages,
            "task-1",
        )

    assert dispatch.call_count == 2
    assert [message["tool_call_id"] for message in messages] == ["c-soft-1", "c-soft-2"]
    warned = [
        message for message in messages
        if "loop_web_search_soft_warning" in message["content"]
    ]
    assert len(warned) == 1
    assert warned[0]["tool_call_id"] in {"c-soft-1", "c-soft-2"}


def test_web_search_soft_warning_does_not_mask_runtime_no_progress_halt():
    config = {
        "tool_loop_guardrails": {
            "warnings_enabled": True,
            "hard_stop_enabled": True,
            "hard_stop_after": {
                "exact_failure": 8,
                "same_tool_failure": 8,
                "idempotent_no_progress": 2,
            },
            "loop_caps": {
                "warn_web_searches": 2,
                "max_web_searches": 10,
            },
        }
    }
    agent = _make_agent("web_search", max_iterations=5, config=config)
    messages = []
    raw_result = json.dumps({"success": True, "data": {"web": []}})

    with patch("run_agent.handle_function_call", return_value=raw_result) as dispatch:
        for index in range(1, 4):
            call = _mock_tool_call(
                "web_search",
                json.dumps({"query": "same"}),
                f"c-no-progress-{index}",
            )
            agent._execute_tool_calls_sequential(
                SimpleNamespace(content="", tool_calls=[call]),
                messages,
                "task-1",
            )

    assert dispatch.call_count == 2
    decision = agent._tool_guardrail_halt_decision
    assert decision is not None
    assert decision.code == "idempotent_no_progress_block"
    assert "loop_web_search_soft_warning" in messages[-2]["content"]
    assert "idempotent_no_progress_block" in messages[-1]["content"]


def test_web_search_soft_budget_warning_preserves_multimodal_result():
    agent = _make_agent(
        "web_search",
        max_iterations=5,
        config=_web_search_budget_config(warn=1, maximum=2),
    )
    call = _mock_tool_call(
        "web_search", json.dumps({"query": "q1"}), "c-soft-multimodal"
    )
    message = SimpleNamespace(content="", tool_calls=[call])
    messages = []
    agent._model_supports_vision = lambda: True
    agent._provider_supports_vision_tool_messages = lambda: True
    image_part = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }
    result = {
        "_multimodal": True,
        "content": [
            {"type": "text", "text": "image result"},
            image_part,
        ],
        "text_summary": "image result",
    }

    with patch("run_agent.handle_function_call", return_value=result):
        agent._execute_tool_calls_sequential(message, messages, "task-1")

    assert [item["role"] for item in messages] == ["tool"]
    assert isinstance(messages[0]["content"], list)
    assert messages[0]["content"][1] == image_part
    assert "loop_web_search_soft_warning" in messages[0]["content"][0]["text"]


def test_web_search_cap_runs_one_tool_disabled_synthesis_pass():
    agent = _make_agent(
        "web_search",
        max_iterations=10,
        config=_web_search_budget_config(warn=1, maximum=2),
    )
    # Use sequential responses (one tool per turn) so guardrail guidance
    # is reliably appended via _append_guardrail_observation
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1")
            ],
        ),
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2")
            ],
        ),
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q3"}), "c3")
            ],
        ),
        _mock_response(
            content="Evidence-grounded synthesis.",
            finish_reason="stop",
            tool_calls=None,
        ),
    ]
    agent.client.chat.completions.create.side_effect = responses

    call_count = [0]

    def mock_handle_function_call(*args, **kwargs):
        call_count[0] += 1
        # Return base result; guardrail guidance is appended by the real flow
        return json.dumps({"success": True, "data": {"web": []}})

    with (
        patch("run_agent.handle_function_call", side_effect=mock_handle_function_call) as dispatch,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    assert call_count[0] == 2
    assert result["turn_exit_reason"].startswith("text_response")
    assert result["final_response"] == "Evidence-grounded synthesis."
    # q1, q2, q3 blocked, synthesis = 4 API calls
    assert agent.client.chat.completions.create.call_count == 4
    synthesis_kwargs = agent.client.chat.completions.create.call_args_list[-1].kwargs
    assert not synthesis_kwargs.get("tools")

    tool_contents = [
        message["content"]
        for message in result["messages"]
        if message.get("role") == "tool"
    ]
    # The soft warning is appended to the executed result by the middleware.
    # This assertion separately proves the cap-block result remains truthful.
    assert any("loop_web_search_cap" in content for content in tool_contents)
    # Verify the blocked result message contains the truthful wording
    cap_message = next(c for c in tool_contents if "loop_web_search_cap" in c)
    assert "per-turn search budget" in cap_message
    assert "repeated non-progressing" not in cap_message


def test_cap_synthesis_state_resets_at_start_of_new_user_turn():
    agent = _make_agent("web_search", max_iterations=5)
    agent._cap_synthesis_mode = True
    agent._cap_synthesis_consumed = True
    agent._budget_grace_call = True
    agent.client.chat.completions.create.return_value = _mock_response(
        content="fresh turn", finish_reason="stop", tool_calls=None
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("new user request")

    request_kwargs = agent.client.chat.completions.create.call_args.kwargs
    assert request_kwargs.get("tools")
    assert agent.iteration_budget.used == 1
    assert result["final_response"] == "fresh turn"


def test_web_search_cap_gets_one_synthesis_call_past_final_iteration():
    agent = _make_agent(
        "web_search",
        max_iterations=1,
        config=_web_search_budget_config(warn=1, maximum=1),
    )
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1"),
                _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2"),
            ],
        ),
        _mock_response(
            content="Final synthesis from collected evidence.",
            finish_reason="stop",
            tool_calls=None,
        ),
    ]
    agent.client.chat.completions.create.side_effect = responses

    with (
        patch(
            "run_agent.handle_function_call",
            return_value=json.dumps({"success": True, "data": {"web": []}}),
        ) as dispatch,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    assert dispatch.call_count == 1
    assert agent.client.chat.completions.create.call_count == 2
    assert result["api_calls"] == 2
    assert result["turn_exit_reason"].startswith("text_response")
    synthesis_kwargs = agent.client.chat.completions.create.call_args_list[-1].kwargs
    assert not synthesis_kwargs.get("tools")
    assert result["final_response"] == "Final synthesis from collected evidence."


def test_web_search_cap_synthesis_keeps_tools_disabled_across_provider_retry():
    agent = _make_agent(
        "web_search",
        max_iterations=2,
        config=_web_search_budget_config(warn=1, maximum=1),
    )
    capped = _mock_response(
        content="",
        finish_reason="tool_calls",
        tool_calls=[
            _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1"),
            _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2"),
        ],
    )
    agent.client.chat.completions.create.side_effect = [
        capped,
        RuntimeError("transient provider failure"),
        _mock_response(
            content="Synthesis after retry.",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call(
                    "web_search",
                    json.dumps({"query": "must-not-run"}),
                    "retry-tool",
                )
            ],
        ),
    ]

    with (
        patch(
            "run_agent.handle_function_call",
            return_value=json.dumps({"success": True, "data": {"web": []}}),
        ) as dispatch,
        patch("agent.conversation_loop.time.sleep"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    assert dispatch.call_count == 1
    assert agent.client.chat.completions.create.call_count == 3
    retry_calls = agent.client.chat.completions.create.call_args_list[-2:]
    assert all(not call.kwargs.get("tools") for call in retry_calls)
    assert result["final_response"] == "Synthesis after retry."


def test_web_search_cap_synthesis_keeps_tools_disabled_across_provider_fallback():
    agent = _make_agent(
        "web_search",
        max_iterations=2,
        config=_web_search_budget_config(warn=1, maximum=1),
    )
    capped = _mock_response(
        content="",
        finish_reason="tool_calls",
        tool_calls=[
            _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1"),
            _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2"),
        ],
    )
    invalid_synthesis = SimpleNamespace(choices=[])
    fallback_synthesis = _mock_response(
        content="Synthesis from fallback.",
        finish_reason="tool_calls",
        tool_calls=[
            _mock_tool_call(
                "web_search",
                json.dumps({"query": "fallback-must-not-run"}),
                "fallback-tool",
            )
        ],
    )
    agent.client.chat.completions.create.side_effect = [
        capped,
        invalid_synthesis,
        fallback_synthesis,
    ]

    def activate_fallback():
        agent.provider = "fallback"
        agent.model = "fallback/model"
        return True

    with (
        patch(
            "run_agent.handle_function_call",
            return_value=json.dumps({"success": True, "data": {"web": []}}),
        ) as dispatch,
        patch.object(agent, "_try_activate_fallback", side_effect=activate_fallback) as fallback,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    assert fallback.call_count == 1
    assert dispatch.call_count == 1
    assert agent.client.chat.completions.create.call_count == 3
    synthesis_calls = agent.client.chat.completions.create.call_args_list[-2:]
    assert all(not call.kwargs.get("tools") for call in synthesis_calls)
    assert result["final_response"] == "Synthesis from fallback."


def test_cap_synthesis_empty_response_falls_back_without_retrying():
    agent = _make_agent(
        "web_search",
        max_iterations=10,
        config=_web_search_budget_config(warn=1, maximum=1),
    )
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1"),
                _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2"),
            ],
        ),
        _mock_response(content="", finish_reason="stop", tool_calls=None),
    ]
    agent.client.chat.completions.create.side_effect = responses

    with (
        patch(
            "run_agent.handle_function_call",
            return_value=json.dumps({"success": True, "data": {"web": []}}),
        ) as dispatch,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    assert dispatch.call_count == 1
    assert agent.client.chat.completions.create.call_count == 2
    assert result["turn_exit_reason"].startswith("text_response")
    assert "per-turn web_search budget" in result["final_response"]


def test_cap_synthesis_tool_only_response_falls_back_to_nonempty_text():
    agent = _make_agent(
        "web_search",
        max_iterations=10,
        config=_web_search_budget_config(warn=1, maximum=1),
    )
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1"),
                _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2"),
            ],
        ),
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q3"}), "c3")
            ],
        ),
    ]
    agent.client.chat.completions.create.side_effect = responses

    with (
        patch(
            "run_agent.handle_function_call",
            return_value=json.dumps({"success": True, "data": {"web": []}}),
        ) as dispatch,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    assert dispatch.call_count == 1
    assert agent.client.chat.completions.create.call_count == 2
    assert result["turn_exit_reason"].startswith("text_response")
    assert result["final_response"]
    assert "per-turn web_search budget" in result["final_response"]


def test_web_search_cap_synthesis_pass_cannot_reenter_tool_execution():
    """If the model still returns tool_calls in the synthesis pass,
    those calls must NOT be dispatched - tools were disabled, so any
    tool_call in the response is discarded and the content is emitted as text.
    """
    agent = _make_agent(
        "web_search",
        max_iterations=10,
        config=_web_search_budget_config(warn=1, maximum=1),
    )
    responses = [
        _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q1"}), "c1"),
                _mock_tool_call("web_search", json.dumps({"query": "q2"}), "c2"),
            ],
        ),
        # Model still tries to call tools even though tools=[] in the API call.
        # This must be discarded - synthesis cannot re-enter tool execution.
        _mock_response(
            content="Cannot search - synthesis mode.",
            finish_reason="tool_calls",
            tool_calls=[
                _mock_tool_call("web_search", json.dumps({"query": "q3"}), "c3")
            ],
        ),
        # Fallback text response if the synthesis loop iterates again.
        _mock_response(
            content="Final synthesis.",
            finish_reason="stop",
            tool_calls=None,
        ),
    ]
    agent.client.chat.completions.create.side_effect = responses

    with (
        patch(
            "run_agent.handle_function_call",
            return_value=json.dumps({"success": True, "data": {"web": []}}),
        ) as dispatch,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("research this")

    # Only q1 should execute; q2 is blocked at the cap.
    assert dispatch.call_count == 1
    # Synthesis pass sends tools=[], and any tool_calls in the response are
    # discarded - so the loop terminates with a text response, not guardrail_halt.
    assert agent.client.chat.completions.create.call_count == 2
    synthesis_kwargs = agent.client.chat.completions.create.call_args_list[-1].kwargs
    assert not synthesis_kwargs.get("tools")
    assert result["turn_exit_reason"].startswith("text_response")