from collections import deque
import logging

import pytest

from agent.chat_completion_helpers import handle_max_iterations


class _FakeTransport:
    def normalize_response(self, response, **kwargs):
        return response


class _FakeAgent:
    max_iterations = 80
    api_mode = "codex_responses"
    model = "gpt-5.5"
    provider = "openai-codex"
    base_url = "https://chatgpt.com/backend-api/codex"
    _base_url_lower = base_url
    max_tokens = 1024
    reasoning_config = None
    providers_allowed = None
    providers_ignored = None
    providers_order = None
    provider_sort = None
    openrouter_min_coding_score = None
    prefill_messages = []
    ephemeral_system_prompt = ""
    _cached_system_prompt = ""
    _is_anthropic_oauth = False

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _should_sanitize_tool_calls(self):
        return False

    def _copy_reasoning_content_for_api(self, _msg, _api_msg):
        pass

    def _sanitize_api_messages(self, messages):
        return messages

    def _drop_thinking_only_and_merge_users(self, messages):
        return messages

    def _build_api_kwargs(self, messages):
        return {"model": self.model, "messages": messages, "tools": []}

    def _get_transport(self):
        return _FakeTransport()

    def _supports_reasoning_extra_body(self):
        return False

    def _resolve_lmstudio_summary_reasoning_effort(self):
        return None

    def _is_openrouter_url(self):
        return False

    def _run_codex_stream(self, _kwargs):
        raise RuntimeError("Error code: 429 - {'error': {'type': 'usage_limit_reached', 'message': 'The usage limit has been reached'}}")


def test_max_iterations_summary_uses_local_fallback_on_usage_limit():
    agent = _FakeAgent()
    messages = [
        {"role": "user", "content": "Log in to eBay and make the approved offer"},
        {"role": "tool", "tool_name": "browser_snapshot", "content": "Review offer dialog shows total $36.00 and Send offer button"},
    ]

    out = handle_max_iterations(agent, messages, 80)

    assert "maximum tool-calling iteration limit" in out
    assert "browser_snapshot" in out
    assert "Review offer dialog" in out
    assert "usage_limit_reached" not in out
    assert "Error code: 429" not in out


def test_max_iterations_summary_never_raises_on_provider_failure():
    agent = _FakeAgent()
    messages = [{"role": "user", "content": "Do a long browser task"}]

    out = handle_max_iterations(agent, messages, 80)

    assert isinstance(out, str)
    assert out.strip()
    assert "maximum tool-calling iteration limit" in out
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == out


def test_max_iterations_generic_failure_does_not_leak_raw_provider_body(caplog):
    agent = _FakeAgent()

    def provider_body_error(_kwargs):
        raise RuntimeError(
            "Error code: 422 - {'error': {'message': 'bad request', "
            "'Authorization': 'Bearer SECRET_TOKEN', 'body': 'private prompt'}}"
        )

    agent._run_codex_stream = provider_body_error
    messages = [{"role": "user", "content": "Do a long browser task"}]

    with caplog.at_level(logging.WARNING):
        out = handle_max_iterations(agent, messages, 80)

    assert "Bearer" not in out
    assert "SECRET_TOKEN" not in out
    assert "private prompt" not in out
    assert "Error code" not in out
    assert "summary model/provider failed" in out
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == out
    logs = caplog.text
    assert "Bearer" not in logs
    assert "SECRET_TOKEN" not in logs
    assert "private prompt" not in logs


def test_max_iterations_auxiliary_failure_logs_are_sanitized(monkeypatch, caplog):
    agent = _FakeAgent()

    def primary_down(_kwargs):
        raise RuntimeError("primary down")

    class _BadAuxCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("Authorization: Bearer AUX_SECRET; body=private prompt")

    class _BadAuxChat:
        completions = _BadAuxCompletions()

    class _BadAuxClient:
        chat = _BadAuxChat()

    agent._run_codex_stream = primary_down
    monkeypatch.setattr(
        "agent.auxiliary_client.get_text_auxiliary_client",
        lambda _task: (_BadAuxClient(), "compact-model"),
    )
    messages = [{"role": "user", "content": "Do a long browser task"}]

    with caplog.at_level(logging.WARNING):
        out = handle_max_iterations(agent, messages, 80)

    assert out
    logs = caplog.text
    assert "Bearer" not in logs
    assert "AUX_SECRET" not in logs
    assert "private prompt" not in logs
    assert "RuntimeError" in logs


def test_browser_loop_detector_flags_low_diversity_browser_loop():
    from agent.chat_completion_helpers import browser_tool_loop_detected

    recent = deque(
        ["browser_press", "browser_press", "browser_snapshot", "browser_press", "browser_press", "browser_snapshot", "browser_press", "browser_press", "browser_press", "browser_snapshot"],
        maxlen=12,
    )

    assert browser_tool_loop_detected(recent)


def test_browser_loop_detector_does_not_flag_diverse_browser_progress():
    from agent.chat_completion_helpers import browser_tool_loop_detected

    recent = deque(
        ["browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_snapshot", "browser_vision", "browser_click", "browser_console", "browser_scroll", "browser_snapshot"],
        maxlen=12,
    )

    assert not browser_tool_loop_detected(recent)
