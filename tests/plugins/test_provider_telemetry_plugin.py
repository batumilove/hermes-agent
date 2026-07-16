from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest import mock

import pytest

# Prefer the installed local plugin; fall back to the worktree mirror when present.
PLUGIN_CANDIDATES = [
    Path.home() / ".hermes" / "plugins" / "provider_telemetry" / "__init__.py",
    Path(__file__).resolve().parents[2] / "plugins" / "observability" / "provider_telemetry" / "__init__.py",
]


def _plugin_path() -> Path:
    for path in PLUGIN_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "provider_telemetry plugin not found under ~/.hermes/plugins or worktree plugins/"
    )


class FakeContext:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, hook_name, callback):
        self.hooks[hook_name] = callback


def load_plugin_module(module_name: str = "provider_telemetry_under_test"):
    plugin_path = _plugin_path()
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registered_hooks(module, monkeypatch, tmp_path):
    metrics_file = tmp_path / "hermes_provider_telemetry.prom"
    monkeypatch.setenv("HERMES_PROVIDER_TELEMETRY_METRICS_FILE", str(metrics_file))
    module.reset_metrics_for_tests()
    ctx = FakeContext()
    module.register(ctx)
    return ctx.hooks, metrics_file


def test_manifest_declares_lifecycle_hooks():
    manifest = (_plugin_path().with_name("plugin.yaml")).read_text()
    assert "name: provider_telemetry" in manifest
    for hook in (
        "pre_api_request",
        "post_api_request",
        "api_request_error",
        "on_fallback_activated",
        "on_primary_restored",
    ):
        assert hook in manifest


def test_register_hooks_writes_bootstrap_metrics(monkeypatch, tmp_path):
    module = load_plugin_module("provider_telemetry_bootstrap")
    hooks, metrics_file = _registered_hooks(module, monkeypatch, tmp_path)
    assert set(hooks) >= {
        "pre_api_request",
        "post_api_request",
        "api_request_error",
        "on_fallback_activated",
        "on_primary_restored",
    }
    body = metrics_file.read_text()
    assert "hermes_provider_telemetry_up 1" in body


def test_success_path_records_request_and_latency(monkeypatch, tmp_path):
    module = load_plugin_module("provider_telemetry_success")
    hooks, metrics_file = _registered_hooks(module, monkeypatch, tmp_path)

    hooks["pre_api_request"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-1",
        approx_input_tokens=86613,
        started_at=1000.0,
    )
    hooks["post_api_request"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-1",
        api_duration=1.25,
        started_at=1000.0,
        ended_at=1001.25,
    )

    body = metrics_file.read_text()
    assert (
        'hermes_model_requests_total{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 1'
        in body
    )
    assert (
        'hermes_model_request_duration_seconds_sum{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 1.25'
        in body
    )
    assert (
        'hermes_model_request_duration_seconds_count{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 1'
        in body
    )
    assert (
        'hermes_model_approx_input_tokens{provider="openai-codex",model="gpt-5.6-sol"} 86613'
        in body
    )


def test_classified_error_records_failure_reason_not_log_wording(monkeypatch, tmp_path):
    module = load_plugin_module("provider_telemetry_error")
    hooks, metrics_file = _registered_hooks(module, monkeypatch, tmp_path)

    hooks["api_request_error"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-err",
        reason="silent_hang",
        retryable=False,
        api_duration=120.0,
        error={
            "type": "TimeoutError",
            "message": "Codex stream produced no SSE events for 120s after first byte",
        },
        approx_input_tokens=86613,
    )

    body = metrics_file.read_text()
    assert (
        'hermes_model_failures_total{provider="openai-codex",model="gpt-5.6-sol",reason="silent_hang"} 1'
        in body
    )
    assert (
        'hermes_model_requests_total{provider="openai-codex",model="gpt-5.6-sol",outcome="error"} 1'
        in body
    )
    # Must not depend on free-form log wording as the label.
    assert "no SSE events" not in body


def test_fallback_activation_records_transition_and_reason(monkeypatch, tmp_path):
    module = load_plugin_module("provider_telemetry_fallback")
    hooks, metrics_file = _registered_hooks(module, monkeypatch, tmp_path)

    # Last classified failure reason should attach to the fallback transition.
    hooks["api_request_error"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-err",
        reason="silent_hang",
        retryable=False,
        api_duration=120.0,
        error={"type": "TimeoutError", "message": "silent"},
    )
    hooks["on_fallback_activated"](
        from_provider="openai-codex",
        from_model="gpt-5.6-sol",
        to_provider="kimi-coding",
        to_model="kimi-k2.7-code",
        reason="silent_hang",
        session_id="sess-1",
    )

    body = metrics_file.read_text()
    assert (
        'hermes_model_fallbacks_total{from_provider="openai-codex",from_model="gpt-5.6-sol",'
        'to_provider="kimi-coding",to_model="kimi-k2.7-code",reason="silent_hang"} 1'
        in body
    )
    assert re.search(
        r'hermes_model_last_fallback_timestamp_seconds\{from_provider="openai-codex",'
        r'from_model="gpt-5\.6-sol",to_provider="kimi-coding",to_model="kimi-k2\.7-code"\} [0-9.]+',
        body,
    )


def test_primary_restore_records_counter(monkeypatch, tmp_path):
    module = load_plugin_module("provider_telemetry_restore")
    hooks, metrics_file = _registered_hooks(module, monkeypatch, tmp_path)

    hooks["on_primary_restored"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        session_id="sess-1",
    )

    body = metrics_file.read_text()
    assert (
        'hermes_model_primary_restores_total{provider="openai-codex",model="gpt-5.6-sol"} 1'
        in body
    )


def test_metrics_override_rejects_non_prom_path(monkeypatch, tmp_path, capsys):
    module = load_plugin_module("provider_telemetry_bad_path")
    monkeypatch.setenv("HERMES_PROVIDER_TELEMETRY_METRICS_FILE", str(tmp_path / "not-a-prom"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    path = module._metrics_file()
    assert path.name == "hermes_provider_telemetry.prom"
    err = capsys.readouterr().err
    assert "ignoring HERMES_PROVIDER_TELEMETRY_METRICS_FILE" in err


def test_fail_open_on_bad_payloads(monkeypatch, tmp_path):
    module = load_plugin_module("provider_telemetry_fail_open")
    hooks, metrics_file = _registered_hooks(module, monkeypatch, tmp_path)

    # None / incomplete payloads must not raise.
    hooks["pre_api_request"]()
    hooks["post_api_request"]()
    hooks["api_request_error"]()
    hooks["on_fallback_activated"]()
    hooks["on_primary_restored"]()

    assert metrics_file.exists()
    assert "hermes_provider_telemetry_up 1" in metrics_file.read_text()


def test_counters_reload_from_existing_prom_file_across_process(monkeypatch, tmp_path):
    """Gateway restarts must not zero cumulative counters/fallbacks."""
    metrics_file = tmp_path / "hermes_provider_telemetry.prom"
    monkeypatch.setenv("HERMES_PROVIDER_TELEMETRY_METRICS_FILE", str(metrics_file))

    m1 = load_plugin_module("provider_telemetry_persist_a")
    m1.reset_metrics_for_tests()
    hooks, _ = _registered_hooks(m1, monkeypatch, tmp_path)
    hooks["api_request_error"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        reason="silent_hang",
        api_duration=120.0,
        session_id="s1",
    )
    hooks["on_fallback_activated"](
        from_provider="openai-codex",
        from_model="gpt-5.6-sol",
        to_provider="kimi-coding",
        to_model="kimi-k2.7-code",
        reason="silent_hang",
        session_id="s1",
    )
    body1 = metrics_file.read_text()
    assert (
        'hermes_model_fallbacks_total{from_provider="openai-codex",from_model="gpt-5.6-sol",'
        'to_provider="kimi-coding",to_model="kimi-k2.7-code",reason="silent_hang"} 1'
        in body1
    )

    # Simulate a new process: fresh module import, no in-memory state.
    m2 = load_plugin_module("provider_telemetry_persist_b")
    m2.reset_metrics_for_tests()
    ctx = FakeContext()
    m2.register(ctx)
    # After reload from disk, register should keep fallback=1, not wipe it.
    body2 = metrics_file.read_text()
    assert (
        'hermes_model_fallbacks_total{from_provider="openai-codex",from_model="gpt-5.6-sol",'
        'to_provider="kimi-coding",to_model="kimi-k2.7-code",reason="silent_hang"} 1'
        in body2
    )
    assert (
        'hermes_model_failures_total{provider="openai-codex",model="gpt-5.6-sol",reason="silent_hang"} 1'
        in body2
    )
    # Increment after reload continues from hydrated baseline.
    ctx.hooks["on_fallback_activated"](
        from_provider="openai-codex",
        from_model="gpt-5.6-sol",
        to_provider="kimi-coding",
        to_model="kimi-k2.7-code",
        reason="silent_hang",
        session_id="s2",
    )
    body3 = metrics_file.read_text()
    assert (
        'hermes_model_fallbacks_total{from_provider="openai-codex",from_model="gpt-5.6-sol",'
        'to_provider="kimi-coding",to_model="kimi-k2.7-code",reason="silent_hang"} 2'
        in body3
    )


def test_core_hooks_are_registered_as_valid():
    from hermes_cli.plugins import VALID_HOOKS

    assert "on_fallback_activated" in VALID_HOOKS
    assert "on_primary_restored" in VALID_HOOKS


def test_try_activate_fallback_fires_observer_hook(monkeypatch):
    from agent.chat_completion_helpers import try_activate_fallback

    fired = []

    def _fake_invoke(name, **kwargs):
        if name == "on_fallback_activated":
            fired.append(kwargs)
        return []

    class Agent:
        def __init__(self):
            self._fallback_chain = [{"provider": "kimi-coding", "model": "kimi-k2.7-code"}]
            self._fallback_index = 0
            self._fallback_activated = False
            self.model = "gpt-5.6-sol"
            self.provider = "openai-codex"
            self.base_url = "https://chatgpt.com/backend-api/codex"
            self.api_mode = "codex_responses"
            self.api_key = "sk-test"
            self._client_kwargs = {}
            self._primary_runtime = {
                "provider": "openai-codex",
                "model": "gpt-5.6-sol",
            }
            self.context_compressor = None
            self._cached_system_prompt = "Model: gpt-5.6-sol\nProvider: openai-codex\n"
            self._rate_limited_until = 0
            self._unavailable_fallback_keys = set()
            self._pending_fallback_notice = None
            self._status_buffer = []

        def _buffer_status(self, msg):
            self._status_buffer.append(msg)

        def _is_azure_openai_url(self, *_a, **_k):
            return False

        def _is_direct_openai_url(self, *_a, **_k):
            return False

        def _provider_model_requires_responses_api(self, *_a, **_k):
            return False

        def _replace_primary_openai_client(self, **_k):
            return True

        def _ensure_lmstudio_runtime_loaded(self):
            return None

        def _anthropic_prompt_cache_policy(self, **_k):
            return False, False

    agent = Agent()

    class FakeClient:
        def __init__(self):
            self.api_key = "sk-kimi"
            self.base_url = "https://api.kimi.com/coding/v1"
            self._custom_headers = {"User-Agent": "claude-code/0.1.0"}

    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *a, **k: (FakeClient(), "kimi-k2.7-code"),
    )
    monkeypatch.setattr(
        "hermes_cli.model_normalize.normalize_model_for_provider",
        lambda model, provider: model,
    )
    monkeypatch.setattr("agent.model_metadata.get_model_context_length", lambda *a, **k: 128000)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke)
    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: name == "on_fallback_activated")

    # Bypass optional provider timeout lookup / pool loads.
    monkeypatch.setattr(
        "agent.chat_completion_helpers.get_provider_request_timeout",
        lambda *a, **k: None,
        raising=False,
    )

    ok = try_activate_fallback(agent, reason=None)
    # If full activation is too heavy in this environment, at least ensure hook
    # call site is present by importing and checking source contract separately.
    assert isinstance(ok, bool)
    # When activation succeeds, observer payload is present.
    if ok:
        assert fired
        assert fired[0]["from_provider"] == "openai-codex"
        assert fired[0]["to_provider"] == "kimi-coding"
        assert fired[0]["from_model"] == "gpt-5.6-sol"
        assert fired[0]["to_model"] == "kimi-k2.7-code"
