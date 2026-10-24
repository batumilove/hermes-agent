"""Tests for the langfuse plugin (formerly bundled with Hermes as observability/langfuse)."""
from __future__ import annotations

import importlib
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


from tests.plugins.external.langfuse_fixture import PLUGIN_DIR, _load_plugin

from tests.plugins.external.test_langfuse_plugin import TestTurnTraceIsolation as _TurnTraceIsolation

class TestSessionFinalizeHook:
    def _fresh_plugin(self):
        sys.modules.pop("frozen_langfuse_plugin", None)
        return _load_plugin()

    def _client(self, flushes):
        class _Client:
            def flush(self):
                flushes.append(1)
        return _Client()

    def _state(self, mod):
        class _Root:
            def __init__(self):
                self.ended = False
            def update(self, **kw): pass
            def end(self, **kw): self.ended = True
            def set_trace_io(self, **kw): pass
        root = _Root()
        return mod.TraceState(trace_id="t", root_span=root), root

    def test_finalize_closes_matching_session_traces(self, monkeypatch):
        mod = self._fresh_plugin()
        flushes = []
        client = self._client(flushes)
        monkeypatch.setattr(mod, "_LANGFUSE_CLIENT", client)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: client)
        mod._TRACE_STATE.clear()

        s1, r1 = self._state(mod)
        s2, r2 = self._state(mod)
        mod._TRACE_STATE["session:sess-a:turn:1"] = s1
        mod._TRACE_STATE["session:sess-b:turn:1"] = s2

        mod.on_session_finalize(session_id="sess-a")

        assert "session:sess-a:turn:1" not in mod._TRACE_STATE
        assert "session:sess-b:turn:1" in mod._TRACE_STATE
        assert r1.ended is True
        assert r2.ended is False
        assert not flushes  # session rotation must not wait for network export

    def test_finalize_without_session_closes_all(self, monkeypatch):
        mod = self._fresh_plugin()
        flushes = []
        client = self._client(flushes)
        monkeypatch.setattr(mod, "_LANGFUSE_CLIENT", client)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: client)
        mod._TRACE_STATE.clear()

        s1, r1 = self._state(mod)
        mod._TRACE_STATE["session:sess-x:turn:1"] = s1
        mod.on_session_finalize()
        assert not mod._TRACE_STATE
        assert r1.ended is True

    def test_finalize_noop_when_client_never_initialized(self):
        mod = self._fresh_plugin()
        mod._TRACE_STATE.clear()
        # _LANGFUSE_CLIENT is None on a fresh module; must not raise or init.
        mod.on_session_finalize(session_id="whatever")

    def test_finalize_shuts_down_client_on_process_exit(self, monkeypatch):
        """reason="shutdown" must call client.shutdown() while the interpreter
        is alive, so the SDK's own atexit handler (which runs during
        interpreter finalization, after opentelemetry.trace.Span is torn
        down) becomes a no-op instead of raising the "isinstance() arg 2
        must be a type" TypeError on quit."""
        mod = self._fresh_plugin()
        events = []

        class _Client:
            def flush(self):
                events.append("flush")

            def shutdown(self):
                events.append("shutdown")

        client = _Client()
        monkeypatch.setattr(mod, "_LANGFUSE_CLIENT", client)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: client)
        mod._TRACE_STATE.clear()

        mod.on_session_finalize(session_id="sess-a", reason="shutdown")
        assert "shutdown" in events
        assert events.index("flush") < events.index("shutdown")

    def test_finalize_keeps_client_alive_on_session_rotation(self, monkeypatch):
        """/new, /reset, and gateway session expiry finalize the session but
        the process lives on — the cached client must NOT be shut down or
        later sessions silently stop exporting."""
        mod = self._fresh_plugin()
        events = []

        class _Client:
            def flush(self):
                events.append("flush")

            def shutdown(self):
                events.append("shutdown")

        client = _Client()
        monkeypatch.setattr(mod, "_LANGFUSE_CLIENT", client)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: client)
        mod._TRACE_STATE.clear()

        for reason in ("session_boundary", "new_session", "session_expired", ""):
            mod.on_session_finalize(session_id="sess-a", reason=reason)
        assert "shutdown" not in events
        assert "flush" not in events

    @pytest.mark.parametrize("boundary", ["success", "error", "session"])
    def test_live_boundary_returns_while_flush_is_blocked(self, monkeypatch, boundary):
        """#87468: client.flush() waits on the network; a hung backend must not
        hold up a finished turn (cron watchdog then discards the real output).
        Live boundaries end spans and return; the SDK exporter delivers."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        mod = self._fresh_plugin()
        release = threading.Event()

        class _Obs:
            def update(self, **kw): pass
            def end(self, **kw): pass

        class _Client:
            def flush(self):
                release.wait(10)

        client = _Client()
        monkeypatch.setattr(mod, "_LANGFUSE_CLIENT", client)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: client)
        mod._TRACE_STATE.clear()
        key = mod._trace_key("", "live", turn_id="turn")
        mod._TRACE_STATE[key] = mod.TraceState(trace_id="t", root_span=_Obs(),
                                               generations={"1": _Obs()})
        finish = {
            "success": lambda: mod.on_post_llm_call(session_id="live", turn_id="turn", api_call_count=1,
                                                    assistant_response="done"),
            "error": lambda: mod.on_api_request_error(session_id="live", turn_id="turn", api_call_count=1,
                                                      retryable=False, error={"type": "APIError"}),
            "session": lambda: mod.on_session_finalize(session_id="live", reason="session_boundary"),
        }[boundary]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(finish)
            try:
                future.result(timeout=2)
                assert key not in mod._TRACE_STATE
            finally:
                release.set()


# ---------------------------------------------------------------------------
# Subagent tracing: delegated children as spans under the parent turn
# ---------------------------------------------------------------------------

class TestSubagentTracing:
    """``tools/delegate_tool.py`` emits subagent_start/subagent_stop. The
    payloads carry ``parent_turn_id`` but no ``task_id``, so the parent trace
    must be resolved by turn id rather than by rebuilding the scope key."""

    def _fresh_plugin(self):
        sys.modules.pop("frozen_langfuse_plugin", None)
        return _load_plugin()

    def _state(self, mod, monkeypatch, key, spans):
        class _Obs:
            def __init__(self, kw):
                self.kw = kw
                self.ended = False
                self.updates = {}

            def update(self, **kw):
                self.updates.update(kw)

            def end(self, **kw):
                self.ended = True

        class _Root:
            def start_observation(self, **kw):
                obs = _Obs(kw)
                spans.append(obs)
                return obs

        monkeypatch.setattr(mod, "_get_langfuse", lambda: object())
        state = mod.TraceState(trace_id="trace-1", root_span=_Root())
        monkeypatch.setitem(mod._TRACE_STATE, key, state)
        return state

    def test_start_attaches_span_despite_task_scoped_key(self, monkeypatch):
        mod = self._fresh_plugin()
        spans = []
        # Key minted by the LLM hooks with a task id — a naive rebuild from
        # session_id alone would not match this.
        state = self._state(mod, monkeypatch, "task:task-9:turn:turn-7", spans)

        mod.on_subagent_start(
            parent_session_id="sess-1",
            parent_turn_id="turn-7",
            child_session_id="child-sess-1",
            child_subagent_id="sub-1",
            child_role="researcher",
            child_goal="find the thing",
        )

        assert len(spans) == 1
        assert spans[0].kw["name"] == "Subagent: researcher"
        assert spans[0].kw["metadata"]["child_subagent_id"] == "sub-1"
        assert "child-sess-1" in state.subagents

    def test_stop_ends_span_and_records_outcome(self, monkeypatch):
        mod = self._fresh_plugin()
        spans = []
        state = self._state(mod, monkeypatch, "task:task-9:turn:turn-7", spans)

        mod.on_subagent_start(
            parent_session_id="sess-1", parent_turn_id="turn-7",
            child_session_id="child-sess-1", child_role="researcher",
            child_goal="find the thing",
        )
        mod.on_subagent_stop(
            parent_session_id="sess-1", parent_turn_id="turn-7",
            child_session_id="child-sess-1", child_role="researcher",
            child_summary="found it", child_status="ok",
            tool_call_history=[{"name": "read_file"}, {"name": "grep"}],
            duration_ms=1234,
        )

        assert spans[0].ended is True
        assert spans[0].updates["metadata"]["status"] == "ok"
        assert spans[0].updates["metadata"]["tool_call_count"] == 2
        assert spans[0].updates["metadata"]["duration_ms"] == 1234
        # Popped so a repeated stop cannot double-end the span.
        assert not state.subagents

    def test_unknown_turn_is_a_noop(self, monkeypatch):
        mod = self._fresh_plugin()
        spans = []
        self._state(mod, monkeypatch, "task:task-9:turn:turn-7", spans)

        mod.on_subagent_start(
            parent_turn_id="turn-does-not-exist",
            child_session_id="child-sess-1", child_role="researcher",
        )
        mod.on_subagent_stop(
            parent_turn_id="turn-does-not-exist",
            child_session_id="child-sess-1",
        )

        assert spans == []

    def test_start_without_child_session_is_a_noop(self, monkeypatch):
        mod = self._fresh_plugin()
        spans = []
        self._state(mod, monkeypatch, "task:task-9:turn:turn-7", spans)

        # subagent_stop keys on child_session_id, so a start without one could
        # never be matched and must not open an unclosable span.
        mod.on_subagent_start(
            parent_turn_id="turn-7", child_session_id=None, child_role="researcher",
        )

        assert spans == []


# ---------------------------------------------------------------------------
# MoA fan-out: one generation per advisor, priced at the advisor's own model
# ---------------------------------------------------------------------------

class TestMoAReferenceGenerations:
    """MoA returns only the aggregator's response, so without per-advisor
    generations the whole fan-out collapses into one line priced at the
    aggregator's model. Advisors routinely run on a different provider."""

    def _fresh_plugin(self):
        sys.modules.pop("frozen_langfuse_plugin", None)
        return _load_plugin()

    def _state(self, mod, monkeypatch, gens):
        class _Obs:
            def __init__(self, kw):
                self.kw = kw
                self.updates = {}
                self.ended = False

            def update(self, **kw):
                self.updates.update(kw)

            def end(self, **kw):
                self.ended = True

        class _Root:
            def start_observation(self, **kw):
                obs = _Obs(kw)
                gens.append(obs)
                return obs

        monkeypatch.setattr(mod, "_get_langfuse", lambda: object())
        return mod.TraceState(trace_id="t", root_span=_Root())

    def _refs(self):
        return [
            {
                "label": "anthropic:claude-sonnet-4-6",
                "model": "claude-sonnet-4-6",
                "provider": "anthropic",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost_usd": 0.001,
                "cost_status": "ok",
                "cost_source": "pricing_table",
            },
            {
                "label": "openai:gpt-5",
                "model": "gpt-5",
                "provider": "openai",
                "usage": {"input_tokens": 80, "output_tokens": 40, "reasoning_tokens": 10},
                "cost_usd": 0.002,
            },
        ]

    def test_one_generation_per_advisor_with_own_model_and_cost(self, monkeypatch):
        mod = self._fresh_plugin()
        gens = []
        state = self._state(mod, monkeypatch, gens)

        mod._emit_moa_reference_generations(state, client=object(), references=self._refs())

        assert len(gens) == 2
        assert gens[0].kw["model"] == "claude-sonnet-4-6"
        assert gens[1].kw["model"] == "gpt-5"
        # Each advisor's dollars, not the aggregator's rate applied to all.
        assert gens[0].updates["cost_details"]["total"] == pytest.approx(0.001)
        assert gens[1].updates["cost_details"]["total"] == pytest.approx(0.002)
        assert gens[0].updates["usage_details"] == {"input": 100, "output": 50}
        assert gens[1].updates["usage_details"]["reasoning_tokens"] == 10
        assert all(g.ended for g in gens)

    def test_repeat_emit_is_deduped_within_a_turn(self, monkeypatch):
        mod = self._fresh_plugin()
        gens = []
        state = self._state(mod, monkeypatch, gens)

        # The MoA client holds its last fan-out until the next one, so a
        # tool-loop turn delivers the same references on every API call.
        refs = self._refs()
        mod._emit_moa_reference_generations(state, client=object(), references=refs)
        mod._emit_moa_reference_generations(state, client=object(), references=refs)
        mod._emit_moa_reference_generations(state, client=object(), references=list(refs))

        assert len(gens) == 2

    def test_a_new_fanout_emits_again(self, monkeypatch):
        mod = self._fresh_plugin()
        gens = []
        state = self._state(mod, monkeypatch, gens)

        mod._emit_moa_reference_generations(state, client=object(), references=self._refs())
        second = self._refs()
        second[0]["usage"]["output_tokens"] = 999
        mod._emit_moa_reference_generations(state, client=object(), references=second)

        assert len(gens) == 4

    def test_non_moa_turn_emits_nothing(self, monkeypatch):
        mod = self._fresh_plugin()
        gens = []
        state = self._state(mod, monkeypatch, gens)

        for value in (None, [], "not-a-list", [None, "junk"]):
            mod._emit_moa_reference_generations(state, client=object(), references=value)

        assert gens == []

class TestAtexitFinalization(_TurnTraceIsolation):
    """Short-lived processes (kanban workers, `hermes chat -q`, cron) can exit
    with tool calls still queued — the root span never ends and the backend
    shows an anonymous trace (no name/session/metadata). _finalize_all_traces
    (registered atexit after client construction) must end every open root."""

    def test_finalize_all_ends_open_roots_and_clears_state(self, monkeypatch):
        mod = self._fresh_plugin()
        started: list = []
        ended: list = []
        client = self._fake_client(started)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: client)
        monkeypatch.setattr(
            mod, "_end_observation", lambda obs, **k: ended.append(obs)
        )
        mod._TRACE_STATE.clear()

        # Three worker-style turns that never finalize (tool calls pending).
        for n in range(3):
            self._run_turn(mod, session=f"worker-{n}", turn_n=0, finalize=False)
        assert len(mod._TRACE_STATE) == 3

        root_ends: list = []
        for state in mod._TRACE_STATE.values():
            real_end = state.root_span.end
            state.root_span.end = lambda *a, _r=real_end, **k: root_ends.append(1)

        mod._finalize_all_traces()

        assert len(root_ends) == 3, "every open root span must be ended"
        assert mod._TRACE_STATE == {}, "state must be drained"
        # Idempotent: a second call (SDK/atexit re-entry) is a no-op.
        mod._finalize_all_traces()
        assert len(root_ends) == 3

    def test_atexit_hook_is_registered_on_client_init(self, monkeypatch):
        mod = self._fresh_plugin()
        registered: list = []
        import atexit as _atexit

        monkeypatch.setattr(mod, "Langfuse", lambda **kw: object())
        monkeypatch.setattr(
            _atexit, "register", lambda fn, *a, **k: registered.append(fn)
        )
        monkeypatch.setenv("HERMES_LANGFUSE_PUBLIC_KEY", "pk-lf-0123456789abcdef")
        monkeypatch.setenv("HERMES_LANGFUSE_SECRET_KEY", "sk-lf-0123456789abcdef")
        mod._LANGFUSE_CLIENT = None

        assert mod._get_langfuse() is not None
        assert mod._finalize_all_traces in registered

    def test_finalize_flushes_every_profile_without_an_ambient_scope(self, monkeypatch, tmp_path):
        """Multiplex gateway: every turn ran inside a profile scope (home override + secret scope),
        so only the per-home slots hold clients and the launch slot stays empty. atexit has no
        scope, and a credential read there raises UnscopedSecretError. The finalizer must not read
        credentials at all — it ends the open roots and flushes each settled client, so neither
        profile loses its pending traces."""
        from agent import secret_scope
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        mod = self._fresh_plugin()
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        monkeypatch.setattr(mod, "_end_observation", lambda obs, **k: None)
        mod._LANGFUSE_CLIENT = None
        mod._TRACE_STATE.clear()
        mod._LANGFUSE_CLIENT_BY_HOME.clear()

        flushed: list = []
        fake_client = self._fake_client

        def _sdk(**kw):
            client = fake_client([])
            client.flush = lambda pk=kw["public_key"]: flushed.append(pk)
            return client

        monkeypatch.setattr(mod, "Langfuse", _sdk)

        for profile in ("alpha", "beta"):
            home = tmp_path / profile
            home.mkdir()
            home_token = set_hermes_home_override(home)
            scope_token = secret_scope.set_secret_scope({
                "HERMES_LANGFUSE_PUBLIC_KEY": f"pk-lf-{profile}-0123456789",
                "HERMES_LANGFUSE_SECRET_KEY": f"sk-lf-{profile}-0123456789",
            })
            try:
                self._run_turn(mod, session=f"{profile}-turn", turn_n=0, finalize=False)
            finally:
                secret_scope.reset_secret_scope(scope_token)
                reset_hermes_home_override(home_token)

        assert len(mod._TRACE_STATE) == 2 and len(mod._LANGFUSE_CLIENT_BY_HOME) == 2
        assert mod._LANGFUSE_CLIENT is None and secret_scope.current_secret_scope() is None

        mod._finalize_all_traces()  # no scope: must not raise, must not build a client

        assert sorted(flushed) == ["pk-lf-alpha-0123456789", "pk-lf-beta-0123456789"]
        assert mod._TRACE_STATE == {} and mod._LANGFUSE_CLIENT is None


class TestSystemPromptInGenerationInput:
    """The generation input must carry the system prompt even for providers
    that move it out of ``messages``: Anthropic Messages (``system`` kwarg)
    and the Responses/Codex API (``instructions``).  Hermes forwards it to
    hooks as ``system_prompt``; the plugin prepends a ``role: system`` entry.

    Regression for the trace gap discussed in PR #32175 (Anthropic) and its
    Codex sibling: without this, hosted traces show conversations without the
    agent's instructions, skills, and memory."""

    def _make_mod(self):
        sys.modules.pop("frozen_langfuse_plugin", None)
        return _load_plugin()

    def _capture_generation(self, mod, monkeypatch):
        """Route on_pre_llm_request into a seeded TraceState and record the
        generation observation kwargs."""
        captured = {}
        monkeypatch.setattr(mod, "_get_langfuse", lambda: object())
        state = mod.TraceState(trace_id="t", root_span=None)
        task_key = mod._trace_key("task-1", "sess-1")
        monkeypatch.setitem(mod._TRACE_STATE, task_key, state)

        def fake_child(state_, **kw):
            captured["input"] = kw.get("input_value")
            captured["metadata"] = kw.get("metadata")
            return object()

        monkeypatch.setattr(mod, "_start_child_observation", fake_child)
        return captured

    def _fire(self, mod, *, request_messages, system_prompt=None):
        kwargs = dict(
            task_id="task-1",
            session_id="sess-1",
            model="m",
            provider="p",
            api_mode="codex_responses",
            api_call_count=1,
            request_messages=request_messages,
        )
        if system_prompt is not None:
            kwargs["system_prompt"] = system_prompt
        mod.on_pre_llm_request(**kwargs)


    def test_anthropic_block_list_flattened(self, monkeypatch):
        """Anthropic OAuth mode sends ``system`` as content blocks (with
        cache_control); the trace should carry the readable text."""
        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        blocks = [
            {"type": "text", "text": "part one", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "part two"},
        ]
        self._fire(
            mod,
            request_messages=[{"role": "user", "content": "hi"}],
            system_prompt=blocks,
        )
        first = captured["input"][0]
        assert first["role"] == "system"
        assert "part one" in first["content"]
        assert "part two" in first["content"]


    def test_base_url_credentials_stripped_from_generation_metadata(self, monkeypatch):
        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        mod.on_pre_llm_request(task_id="task-1", session_id="sess-1", api_call_count=1, request_messages=[],
                               base_url="https://user:s3cr3tpass@proxy.example/v1?api_key=abcdef123456&region=us")
        assert captured["metadata"]["base_url"] == "https://***:***@proxy.example/v1?api_key=***&region=us"

    def test_absent_system_prompt_keeps_previous_shape(self, monkeypatch):
        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        self._fire(mod, request_messages=[{"role": "user", "content": "hi"}])
        assert captured["input"][0]["role"] == "user"
        assert "system_prompt_chars" not in (captured["metadata"] or {})

    def test_system_survives_serialization_window(self, monkeypatch):
        """_serialize_messages keeps only the last 12 messages; the system
        prompt must be prepended after windowing so long conversations
        never drop it."""
        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        many = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(30)
        ]
        self._fire(mod, request_messages=many, system_prompt="SYS")
        assert captured["input"][0]["role"] == "system"
        assert captured["input"][0]["content"] == "SYS"


class TestSystemPromptCrossesHookBoundary:
    """End-to-end across the hook seam with real transport-built kwargs —
    the regression coverage PR #32175's review asked for: verify the
    provider-specific request shape (Anthropic ``system`` kwarg, Codex
    ``instructions``) actually reaches the Langfuse generation input, with
    no Hermes internals mocked (only the Langfuse client is faked)."""

    def _make_mod(self):
        sys.modules.pop("frozen_langfuse_plugin", None)
        return _load_plugin()

    def _capture_generation(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod, "_get_langfuse", lambda: object())
        state = mod.TraceState(trace_id="t", root_span=None)
        task_key = mod._trace_key("task-1", "sess-1")
        monkeypatch.setitem(mod._TRACE_STATE, task_key, state)

        def fake_child(state_, **kw):
            captured["input"] = kw.get("input_value")
            return object()

        monkeypatch.setattr(mod, "_start_child_observation", fake_child)
        return captured

    def _derive_and_fire(self, mod, api_kwargs, api_messages):
        """Mirror agent/conversation_loop.py's pre_api_request emission:
        derive request_messages exactly the way the loop does, derive
        system_prompt via the loop's helper, and invoke the plugin hook."""
        from agent.conversation_loop import _system_prompt_for_hooks

        request_messages = api_kwargs.get("messages")
        if not isinstance(request_messages, list):
            request_messages = api_kwargs.get("input")
        if not isinstance(request_messages, list):
            request_messages = api_messages
        mod.on_pre_llm_request(
            task_id="task-1",
            session_id="sess-1",
            model="m",
            provider="p",
            api_mode="x",
            api_call_count=1,
            request_messages=list(request_messages),
            system_prompt=_system_prompt_for_hooks(api_kwargs, request_messages),
        )

    def test_codex_instructions_reach_generation_input(self, monkeypatch):
        from agent.transports.codex import ResponsesApiTransport

        api_messages = [
            {"role": "system", "content": "SYS-CODEX"},
            {"role": "user", "content": "hi"},
        ]
        api_kwargs = ResponsesApiTransport().build_kwargs("gpt-x", api_messages, None)
        # Premise: the Responses API moves the system prompt out of the input.
        assert api_kwargs["instructions"] == "SYS-CODEX"
        assert all(i.get("role") != "system" for i in api_kwargs["input"] if isinstance(i, dict))

        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        self._derive_and_fire(mod, api_kwargs, api_messages)
        assert captured["input"][0]["role"] == "system"
        assert captured["input"][0]["content"] == "SYS-CODEX"

    def test_anthropic_system_kwarg_reaches_generation_input(self, monkeypatch):
        from agent.transports.anthropic import AnthropicTransport

        api_messages = [
            {"role": "system", "content": "SYS-ANTHROPIC"},
            {"role": "user", "content": "hi"},
        ]
        api_kwargs = AnthropicTransport().build_kwargs(
            "claude-x", api_messages, None, max_tokens=64
        )
        # Premise: the Messages API moves the system prompt to a kwarg.
        assert "system" in api_kwargs
        assert all(m.get("role") != "system" for m in api_kwargs["messages"])

        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        self._derive_and_fire(mod, api_kwargs, api_messages)
        assert captured["input"][0]["role"] == "system"
        assert "SYS-ANTHROPIC" in captured["input"][0]["content"]

    def test_bedrock_system_kwarg_reaches_generation_input(self, monkeypatch):
        from agent.transports.bedrock import BedrockTransport

        api_messages = [
            {"role": "system", "content": "SYS-BEDROCK"},
            {"role": "user", "content": "hi"},
        ]
        api_kwargs = BedrockTransport().build_kwargs(
            "anthropic.claude-x", api_messages, None, max_tokens=64
        )
        # Premise: Bedrock Converse moves system into a separate 'system' kwarg,
        # shaped as [{"text": ...}] blocks — no "type" key, unlike Anthropic.
        # (The transport may append extra blocks, e.g. cachePoint markers.)
        assert {"text": "SYS-BEDROCK"} in api_kwargs["system"]
        assert all(m.get("role") != "system" for m in api_kwargs["messages"])

        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        self._derive_and_fire(mod, api_kwargs, api_messages)
        assert captured["input"][0]["role"] == "system"
        assert "SYS-BEDROCK" in captured["input"][0]["content"]

    def test_chat_completions_shape_needs_no_fallback(self, monkeypatch):
        """When system stays in messages[0] (chat_completions), the helper
        returns it but the plugin must not duplicate the entry."""
        from agent.conversation_loop import _system_prompt_for_hooks

        api_kwargs = {
            "messages": [
                {"role": "system", "content": "SYS-CHAT"},
                {"role": "user", "content": "hi"},
            ]
        }
        sp = _system_prompt_for_hooks(api_kwargs, api_kwargs["messages"])
        assert sp == "SYS-CHAT"

        mod = self._make_mod()
        captured = self._capture_generation(mod, monkeypatch)
        self._derive_and_fire(mod, api_kwargs, api_kwargs["messages"])
        roles = [m["role"] for m in captured["input"]]
        assert roles.count("system") == 1
class TestFinishTraceUsesSetTraceIo:
    """Regression: the pinned SDK v4 has set_trace_io, not v3's update_trace.

    Calling the missing method once skipped root_span.end() (blank Input/Output
    columns, no CHAIN root); after that was guarded it still failed open on every
    turn, so traces shipped without trace-level input/output (hermes-agent#94710).
    """

    def test_finish_ends_root_and_calls_set_trace_io(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()

        roots: list = []

        class _Span:
            def __init__(self):
                self.ended = False
                self.updates = []
                self.trace_updates = []

            def update(self, **kw):
                self.updates.append(kw)

            def end(self, **kw):
                self.ended = True

            def set_trace_io(self, **kw):
                self.trace_updates.append(kw)

            def start_observation(self, **kw):
                return _Span()

            # Deliberately NO update_trace — mirrors the pinned SDK v4 LangfuseChain.

        class _RootCM:
            def __init__(self):
                self.span = _Span()
                roots.append(self.span)

            def __enter__(self):
                return self.span

            def __exit__(self, *exc):
                return False

        class _Client:
            def create_trace_id(self, seed=None):
                return f"trace::{seed}"

            def start_as_current_observation(self, **kw):
                return _RootCM()

            def start_observation(self, **kw):
                return _RootCM().__enter__()

            def flush(self):
                pass

        monkeypatch.setattr(mod, "_get_langfuse", lambda: _Client())
        monkeypatch.setattr(mod, "_end_observation", lambda *a, **k: None)
        mod._TRACE_STATE.clear()

        mod.on_pre_llm_request(
            task_id="t1",
            session_id="s1",
            model="m",
            provider="p",
            api_mode="chat",
            api_call_count=1,
            request_messages=[{"role": "user", "content": "hi"}],
            turn_id="turn-1",
        )
        mod.on_post_llm_call(
            task_id="t1",
            session_id="s1",
            model="m",
            provider="p",
            api_mode="chat",
            api_call_count=1,
            assistant_content_chars=12,
            assistant_tool_call_count=0,
            assistant_response="hello world!",
            turn_id="turn-1",
        )

        assert len(roots) == 1
        root = roots[0]
        assert root.ended is True
        assert any("input" in u for u in root.trace_updates)
        assert any("output" in u for u in root.trace_updates)
        assert any("output" in u for u in root.updates)
        assert mod._TRACE_STATE == {}

    def test_finish_still_ends_when_set_trace_io_raises(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()

        roots: list = []

        class _Span:
            def __init__(self):
                self.ended = False

            def update(self, **kw):
                pass

            def end(self, **kw):
                self.ended = True

            def set_trace_io(self, **kw):
                raise RuntimeError("simulated set_trace_io failure")

            def start_observation(self, **kw):
                return _Span()

        class _RootCM:
            def __init__(self):
                self.span = _Span()
                roots.append(self.span)

            def __enter__(self):
                return self.span

            def __exit__(self, *exc):
                return False

        class _Client:
            def create_trace_id(self, seed=None):
                return f"trace::{seed}"

            def start_as_current_observation(self, **kw):
                return _RootCM()

            def start_observation(self, **kw):
                return _RootCM().__enter__()

            def flush(self):
                pass

        monkeypatch.setattr(mod, "_get_langfuse", lambda: _Client())
        monkeypatch.setattr(mod, "_end_observation", lambda *a, **k: None)
        mod._TRACE_STATE.clear()

        mod.on_pre_llm_request(
            task_id="t1",
            session_id="s1",
            model="m",
            provider="p",
            api_mode="chat",
            api_call_count=1,
            request_messages=[{"role": "user", "content": "hi"}],
            turn_id="turn-1",
        )
        mod.on_post_llm_call(
            task_id="t1",
            session_id="s1",
            model="m",
            provider="p",
            api_mode="chat",
            api_call_count=1,
            assistant_content_chars=5,
            assistant_tool_call_count=0,
            assistant_response="done",
            turn_id="turn-1",
        )

        assert roots[0].ended is True
        assert mod._TRACE_STATE == {}

class TestCanonicalCostExport:
    """Both supported response paths must export the same complete cost."""

    @staticmethod
    def _response(input_tokens, output_tokens, cache_read=0, cache_write=0):
        cache_details = SimpleNamespace(
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        usage = SimpleNamespace(
            # Anthropic response shape.
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
            # OpenAI chat response shape used by the included-route case.
            prompt_tokens=input_tokens + cache_read + cache_write,
            completion_tokens=output_tokens,
            prompt_tokens_details=cache_details,
        )
        return SimpleNamespace(usage=usage)

    @staticmethod
    def _summary(input_tokens, output_tokens, cache_read=0, cache_write=0, request_count=1):
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "reasoning_tokens": 0,
            "request_count": request_count,
        }

    @staticmethod
    def _capture_summary_path(mod, monkeypatch, usage, *, provider, model, api_mode):
        monkeypatch.setattr(mod, "_get_langfuse", lambda: object())
        observation = object()
        state = mod.TraceState(trace_id="trace-cost", root_span=None)
        state.generations[mod._request_key(1)] = observation
        task_key = mod._trace_key("task-cost", "session-cost")
        monkeypatch.setitem(mod._TRACE_STATE, task_key, state)
        captured = {}

        def fake_end_observation(
            obs,
            *,
            output=None,
            metadata=None,
            usage_details=None,
            cost_details=None,
        ):
            captured["usage_details"] = usage_details
            captured["cost_details"] = cost_details

        monkeypatch.setattr(mod, "_end_observation", fake_end_observation)
        mod.on_post_llm_call(
            task_id="task-cost",
            session_id="session-cost",
            api_call_count=1,
            model=model,
            provider=provider,
            api_mode=api_mode,
            response={"model": model},
            usage=usage,
        )
        return captured["usage_details"], captured["cost_details"]

    def _run_both_paths(
        self,
        mod,
        monkeypatch,
        usage,
        *,
        provider="anthropic",
        model="priced-model",
        api_mode="anthropic_messages",
    ):
        response_result = mod._usage_and_cost(
            self._response(
                usage["input_tokens"],
                usage["output_tokens"],
                usage.get("cache_read_tokens", 0),
                usage.get("cache_write_tokens", 0),
            ),
            provider=provider,
            api_mode=api_mode,
            model=model,
            base_url="",
        )
        summary_result = self._capture_summary_path(
            mod,
            monkeypatch,
            usage,
            provider=provider,
            model=model,
            api_mode=api_mode,
        )
        assert response_result[0] == summary_result[0]
        return response_result[1], summary_result[1]

    @pytest.mark.parametrize(
        ("cache_read", "cache_write", "expected_total"),
        [
            (0, 0, 0.00002),
            (2, 3, 0.0000255),
        ],
        ids=("no-cache", "cached"),
    )
    def test_known_costs_include_canonical_total_on_both_paths(
        self,
        monkeypatch,
        cache_read,
        cache_write,
        expected_total,
    ):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        import agent.usage_pricing as pricing

        entry = pricing.PricingEntry(
            input_cost_per_million=Decimal(1),
            output_cost_per_million=Decimal(2),
            cache_read_cost_per_million=Decimal("0.5"),
            cache_write_cost_per_million=Decimal("1.5"),
            source="custom_contract",
        )
        monkeypatch.setattr(pricing, "get_pricing_entry", lambda *_, **__: entry)
        usage = self._summary(10, 5, cache_read, cache_write)

        response_cost, summary_cost = self._run_both_paths(mod, monkeypatch, usage)
        expected = {
            "total": expected_total,
            "input": 0.00001,
            "output": 0.00001,
        }
        if cache_read:
            expected["cache_read_input_tokens"] = 0.000001
        if cache_write:
            expected["cache_creation_input_tokens"] = 0.0000045
        assert response_cost == pytest.approx(expected)
        assert summary_cost == pytest.approx(expected)

    def test_total_uses_request_cost_instead_of_component_sum(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        import agent.usage_pricing as pricing

        entry = pricing.PricingEntry(
            input_cost_per_million=Decimal(1),
            output_cost_per_million=Decimal(2),
            cache_read_cost_per_million=Decimal("0.5"),
            cache_write_cost_per_million=Decimal("1.5"),
            request_cost=Decimal("0.01"),
            source="provider_models_api",
        )
        monkeypatch.setattr(pricing, "get_pricing_entry", lambda *_, **__: entry)
        usage = self._summary(10, 5, cache_read=2, cache_write=3)

        response_cost, summary_cost = self._run_both_paths(mod, monkeypatch, usage)
        for cost_details in (response_cost, summary_cost):
            component_sum = sum(
                value for key, value in cost_details.items() if key != "total"
            )
            assert cost_details["total"] == pytest.approx(0.0100255)
            assert component_sum == pytest.approx(0.0000255)

    def test_request_only_price_still_exports_total(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        import agent.usage_pricing as pricing

        entry = pricing.PricingEntry(
            request_cost=Decimal("0.01"),
            source="provider_models_api",
        )
        monkeypatch.setattr(pricing, "get_pricing_entry", lambda *_, **__: entry)
        usage = self._summary(0, 0)

        response_cost, summary_cost = self._run_both_paths(mod, monkeypatch, usage)
        assert response_cost == {"total": 0.01}
        assert summary_cost == {"total": 0.01}

    def test_partial_cache_pricing_exports_no_costs(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        import agent.usage_pricing as pricing

        entry = pricing.PricingEntry(
            input_cost_per_million=Decimal(1),
            output_cost_per_million=Decimal(2),
            cache_read_cost_per_million=None,
            source="provider_models_api",
        )
        monkeypatch.setattr(pricing, "get_pricing_entry", lambda *_, **__: entry)
        usage = self._summary(10, 5, cache_read=2)

        response_cost, summary_cost = self._run_both_paths(mod, monkeypatch, usage)
        assert response_cost == {}
        assert summary_cost == {}

    def test_unknown_pricing_exports_no_costs(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        import agent.usage_pricing as pricing

        monkeypatch.setattr(pricing, "get_pricing_entry", lambda *_, **__: None)
        usage = self._summary(10, 5)

        response_cost, summary_cost = self._run_both_paths(mod, monkeypatch, usage)
        assert response_cost == {}
        assert summary_cost == {}

    def test_included_route_does_not_pin_total(self, monkeypatch):
        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        usage = self._summary(10, 5, cache_read=2)

        response_cost, summary_cost = self._run_both_paths(
            mod,
            monkeypatch,
            usage,
            provider="openai-codex",
            model="gpt-5.3-codex",
            api_mode="chat_completions",
        )
        assert response_cost == summary_cost
        assert "total" not in response_cost
        # Subscription-included routes must send NO cost keys at all —
        # explicit zeros are treated as authoritative by Langfuse and block
        # its own model-based estimation (#43129).
        assert response_cost == {}


class TestChildObservationsInheritTraceAttributes:
    def test_children_are_created_inside_the_roots_propagate_attributes(self, monkeypatch):
        """hermes-agent#81730: propagate_attributes() only stamps observations created inside its
        scope. Children come from later hooks, after the root's ``with`` block closed, so they used
        to carry no session_id/tags. Every child must be opened under the root's attributes."""
        import contextlib

        sys.modules.pop("frozen_langfuse_plugin", None)
        mod = _load_plugin()
        active: list = []
        children: list = []
        opened: list = []

        @contextlib.contextmanager
        def _propagate(**kw):
            opened.append(dict(kw))
            active.append(kw)
            try:
                yield
            finally:
                active.pop()

        class _Span:
            def update(self, **kw): pass
            def update_trace(self, **kw): pass
            def end(self, **kw): pass
            def start_observation(self, **kw):
                children.append((kw["name"], dict(active[-1]) if active else None))
                return _Span()

        class _Client:
            def create_trace_id(self, seed=None): return f"trace::{seed}"
            def start_observation(self, **kw): return _Span()
            def flush(self): pass

        monkeypatch.setattr(mod, "propagate_attributes", _propagate)
        monkeypatch.setattr(mod, "_get_langfuse", lambda: _Client())
        common = dict(task_id="sess-p", session_id="sess-p", model="m", provider="p", api_mode="chat",
                      api_call_count=1, turn_id="sess-p:turn1", api_request_id="sess-p:turn1:api:1")
        mod.on_pre_llm_request(request_messages=[{"role": "user", "content": "hi"}], **common)
        mod.on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, tool_call_id="c1",
                             task_id="sess-p", session_id="sess-p", turn_id="sess-p:turn1")

        assert [name for name, _ in children] == ["LLM call 1", "Tool: terminal"]
        assert opened[0]["session_id"] == "sess-p" and opened[0]["tags"][:2] == ["hermes", "langfuse"]
        for _, attrs in children:
            assert attrs == opened[0]  # exactly the root's session / trace name / tags
