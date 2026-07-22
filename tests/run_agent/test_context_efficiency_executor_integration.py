import json


def test_sequential_executor_records_context_efficiency_for_runtime_tool(monkeypatch, tmp_path):
    from agent import tool_executor

    calls = []

    def fake_record(agent, name, args, result, duration, *, is_error=False):
        calls.append((agent, name, args, result, duration, is_error))

    monkeypatch.setattr(tool_executor, "record_tool_route", fake_record)

    class Agent:
        quiet_mode = True
        tool_progress_mode = "off"
        valid_tool_names = {"session_search"}
        enabled_toolsets = None
        disabled_toolsets = None
        session_id = "sess"
        tool_delay = 0
        verbose_logging = False
        log_prefix_chars = 80
        tool_progress_callback = None
        tool_start_callback = None
        tool_complete_callback = None
        _interrupt_requested = False
        _current_turn_id = "turn"
        _current_api_request_id = "api"
        _current_tool = None

        def _is_tool_allowed_by_session_scope(self, name):
            return True

        def _get_session_db_for_recall(self):
            return object()

        def _run_agent_tool_execution_middleware(self, **kwargs):
            return kwargs["execute"](kwargs["function_args"]), kwargs["function_args"]

        def _append_guardrail_observation(self, name, args, result, failed=False):
            return result

        def _record_file_mutation_result(self, *args, **kwargs):
            pass

        def _record_detached_tool_result(self, *args, **kwargs):
            pass

        def _should_emit_quiet_tool_messages(self):
            return False

        def _touch_activity(self, *args, **kwargs):
            pass

        def _tool_result_content_for_active_model(self, name, result):
            return result

        def _apply_pending_steer_to_tool_results(self, messages, n):
            pass

        def _guardrail_block_result(self, decision):
            return "blocked"

        def _flush_session_db(self):
            pass

    class Guardrails:
        def before_call(self, name, args):
            class Decision:
                allows_execution = True
            return Decision()

    class Hints:
        def check_tool_call(self, name, args):
            return ""

    class Checkpoint:
        enabled = False

    class Fn:
        name = "session_search"
        arguments = json.dumps({"query": "routing"})

    class TC:
        id = "tc1"
        function = Fn()

    class Msg:
        tool_calls = [TC()]

    agent = Agent()
    agent._tool_guardrails = Guardrails()
    agent._subdirectory_hints = Hints()
    agent._checkpoint_mgr = Checkpoint()

    monkeypatch.setattr(tool_executor, "_apply_tool_request_middleware_for_agent", lambda agent, **kw: (kw["function_args"], []))
    monkeypatch.setattr(tool_executor, "_emit_terminal_post_tool_call", lambda *a, **kw: None)
    monkeypatch.setattr(tool_executor, "_flush_session_db_after_tool_progress", lambda *a, **kw: None)
    monkeypatch.setattr(tool_executor, "maybe_persist_tool_result", lambda content, **kw: content)
    monkeypatch.setattr(tool_executor, "enforce_turn_budget", lambda *a, **kw: None)
    monkeypatch.setattr(tool_executor, "get_active_env", lambda task_id=None: {})
    monkeypatch.setattr(tool_executor, "agent_runtime_owns_post_tool_hook", lambda agent, name: False, raising=False)
    import tools.session_search_tool
    monkeypatch.setattr(tools.session_search_tool, "session_search", lambda **kw: "{\"success\": true}")

    messages = []
    tool_executor.execute_tool_calls_sequential(agent, Msg(), messages, effective_task_id=None)

    assert calls, "sequential executor did not record context-efficiency telemetry"
    _, name, args, result, duration, is_error = calls[0]
    assert name == "session_search"
    assert args == {"query": "routing"}
    assert result == "{\"success\": true}"
    assert duration >= 0
    assert is_error is False
