"""Integration tests for gateway AIAgent caching.

Verifies that the agent cache correctly:
- Reuses agents across messages (same config → same instance)
- Rebuilds agents when config changes (model, provider, toolsets)
- Updates reasoning_config in-place without rebuilding
- Evicts on session reset
- Evicts on fallback activation
- Preserves frozen system prompt across turns
"""

import threading
from unittest.mock import MagicMock, patch

import pytest
from hermes_cli.config import DEFAULT_CONFIG, cfg_get
from tools import browser_tool_lifecycle as bt_lifecycle

def _make_runner():
    """Create a minimal GatewayRunner with just the cache infrastructure."""
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    return runner


def _context_engine_agent(last_activity: float | None = None):
    """Minimal real AIAgent carrying an owned context engine mock."""
    import time
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent.session_id = "cache-context-engine-test"
    agent._active_children = []
    agent._active_children_lock = threading.Lock()
    agent._context_engine_shutdown_lock = threading.Lock()
    agent.client = None
    agent._last_activity_ts = last_activity if last_activity is not None else time.time()
    engine = MagicMock()
    agent.context_compressor = engine
    return agent, engine


def _wait_for_call(mock, timeout: float = 2.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline and mock.call_count == 0:
        time.sleep(0.02)


class TestAgentConfigSignature:
    """Config signature produces stable, distinct keys."""

    def test_model_change_different_signature(self):
        from gateway.run import GatewayRunner

        runtime = {"api_key": "sk-test12345678", "base_url": "https://openrouter.ai/api/v1",
                    "provider": "openrouter"}
        sig1 = GatewayRunner._agent_config_signature("claude-sonnet-4", runtime, ["hermes-telegram"], "")
        sig2 = GatewayRunner._agent_config_signature("claude-opus-4.6", runtime, ["hermes-telegram"], "")
        assert sig1 != sig2

    def test_same_token_prefix_different_full_token_changes_signature(self):
        """Tokens sharing a JWT-style prefix must not collide."""
        from gateway.run import GatewayRunner

        rt1 = {
            "api_key": "eyJhbGci.token-for-account-a",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
        }
        rt2 = {
            "api_key": "eyJhbGci.token-for-account-b",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
        }

        assert rt1["api_key"][:8] == rt2["api_key"][:8]
        sig1 = GatewayRunner._agent_config_signature("gpt-5.3-codex", rt1, ["hermes-telegram"], "")
        sig2 = GatewayRunner._agent_config_signature("gpt-5.3-codex", rt2, ["hermes-telegram"], "")
        assert sig1 != sig2

    def test_provider_change_different_signature(self):
        from gateway.run import GatewayRunner

        rt1 = {"api_key": "sk-test12345678", "base_url": "https://openrouter.ai/api/v1", "provider": "openrouter"}
        rt2 = {"api_key": "sk-test12345678", "base_url": "https://api.anthropic.com", "provider": "anthropic"}
        sig1 = GatewayRunner._agent_config_signature("claude-sonnet-4", rt1, ["hermes-telegram"], "")
        sig2 = GatewayRunner._agent_config_signature("claude-sonnet-4", rt2, ["hermes-telegram"], "")
        assert sig1 != sig2

    def test_capability_change_different_signature(self):
        from gateway.run import GatewayRunner

        runtime = {"api_key": "sk-test12345678", "base_url": "https://proxy.example/v1", "provider": "custom"}
        native = {**runtime, "capabilities": {"openai_native_compaction": True}}
        plain = {**runtime, "capabilities": {"openai_native_compaction": False}}
        assert GatewayRunner._agent_config_signature("gpt-5.6", native, [], "") != (
            GatewayRunner._agent_config_signature("gpt-5.6", plain, [], "")
        )

    def test_default_gateway_runtime_forwards_filtered_capabilities(self, monkeypatch):
        """Configured provider capabilities must reach a newly created gateway agent."""
        from gateway.run import _resolve_runtime_agent_kwargs
        from hermes_cli import runtime_provider

        monkeypatch.setattr(
            runtime_provider,
            "resolve_runtime_provider",
            lambda **_kw: {
                "api_key": "test-key",
                "base_url": "https://trusted-proxy.example/v1",
                "provider": "custom",
                "requested_provider": "custom:trusted-proxy",
                "api_mode": "responses",
                "capabilities": {
                    "openai_native_compaction": True,
                    "ignore-me": "not-a-bool",
                },
            },
        )
        monkeypatch.setattr(runtime_provider, "_get_model_config", dict)

        runtime = _resolve_runtime_agent_kwargs()

        assert runtime["capabilities"] == {"openai_native_compaction": True}

    # ---------------------------------------------------------------
    # cache_keys (compression/context config cache-busting)
    # ---------------------------------------------------------------

    def test_compression_threshold_change_busts_cache(self):
        from gateway.run import GatewayRunner

        runtime = {"api_key": "k", "base_url": "u", "provider": "p"}
        sig1 = GatewayRunner._agent_config_signature(
            "m", runtime, [], "",
            cache_keys={"compression.threshold": 0.50},
        )
        sig2 = GatewayRunner._agent_config_signature(
            "m", runtime, [], "",
            cache_keys={"compression.threshold": 0.75},
        )
        assert sig1 != sig2

    def test_cache_keys_key_order_does_not_matter(self):
        """Signature must be stable regardless of dict key insertion order."""
        from gateway.run import GatewayRunner

        runtime = {"api_key": "k", "base_url": "u", "provider": "p"}
        sig_a = GatewayRunner._agent_config_signature(
            "m", runtime, [], "",
            cache_keys={"model.context_length": 200_000, "compression.threshold": 0.5},
        )
        sig_b = GatewayRunner._agent_config_signature(
            "m", runtime, [], "",
            cache_keys={"compression.threshold": 0.5, "model.context_length": 200_000},
        )
        assert sig_a == sig_b

class TestExtractCacheBustingConfig:
    """Verify _extract_cache_busting_config pulls the documented subset of
    config values that must invalidate the cached agent on change."""

    def test_missing_keys_yield_the_shipped_default(self):
        """An absent key carries the value in force — DEFAULT_CONFIG's — for every documented key."""
        from gateway.run import GatewayRunner

        out = GatewayRunner._extract_cache_busting_config({})
        for section, key in GatewayRunner._CACHE_BUSTING_CONFIG_KEYS:
            assert out[f"{section}.{key}"] == cfg_get(DEFAULT_CONFIG, section, key)

    def test_explicit_cap_differs_from_absent(self):
        """Setting or clearing `threshold_tokens` rebuilds the cached agent (the signature changes), while
        absent and an explicit default build the same agent (the signature matches)."""
        from gateway.run import GatewayRunner

        default_cap = DEFAULT_CONFIG["compression"]["threshold_tokens"]
        other_cap = (default_cap or 0) + 100_000
        sig = lambda cfg: GatewayRunner._extract_cache_busting_config(cfg)["compression.threshold_tokens"]
        assert sig({}) == sig({"compression": {"threshold_tokens": default_cap}}) == default_cap
        assert sig({"compression": {"threshold_tokens": other_cap}}) == other_cap != sig({})

    def test_legacy_checkpoints_bool_carries_defaults_for_the_other_keys(self):
        """`checkpoints: true` builds the agent with DEFAULT_CONFIG's limits (`_checkpoint_agent_kwargs`), so
        migrating to `checkpoints: {enabled: true}` must not change the signature."""
        from gateway.run import GatewayRunner

        legacy = GatewayRunner._extract_cache_busting_config({"checkpoints": True})
        explicit = GatewayRunner._extract_cache_busting_config({"checkpoints": {"enabled": True}})
        assert legacy["checkpoints.enabled"] is True
        assert {k: v for k, v in legacy.items() if k.startswith("checkpoints.")} == {
            k: v for k, v in explicit.items() if k.startswith("checkpoints.")}

    def test_non_dict_section_treated_as_missing(self):
        from gateway.run import GatewayRunner

        # compression is a string — should not crash; compression.* keys fall back to the shipped defaults
        out = GatewayRunner._extract_cache_busting_config(
            {"compression": "broken", "model": {"context_length": 100_000}}
        )
        assert out["compression.enabled"] == DEFAULT_CONFIG["compression"]["enabled"]
        assert out["compression.threshold"] == DEFAULT_CONFIG["compression"]["threshold"]
        assert out["model.context_length"] == 100_000

    def test_none_config_is_safe(self):
        from gateway.run import GatewayRunner

        out = GatewayRunner._extract_cache_busting_config(None)
        for section, key in GatewayRunner._CACHE_BUSTING_CONFIG_KEYS:
            assert out[f"{section}.{key}"] == cfg_get(DEFAULT_CONFIG, section, key)
        assert "tools.registry_generation" in out

    def test_extract_includes_live_tool_registry_generation(self, monkeypatch):
        from gateway.run import GatewayRunner
        from tools.registry import registry

        monkeypatch.setattr(registry, "_generation", 12345)

        out = GatewayRunner._extract_cache_busting_config({})

        assert out["tools.registry_generation"] == 12345

    # -- Provider-declared identity (MemoryProvider.identity_signature) ------

    @staticmethod
    def _provider_declared_keys(out):
        """``memory.*`` keys a provider added, excluding the config.yaml keys already documented."""
        from gateway.run import GatewayRunner

        documented = {f"{s}.{k}" for s, k in GatewayRunner._CACHE_BUSTING_CONFIG_KEYS if s == "memory"}
        return sorted(k for k in out if k.startswith("memory.") and k not in documented)

    @staticmethod
    def _install_fake_provider(monkeypatch, provider):
        """Route ``load_memory_provider`` to ``provider`` and start from an empty memo."""
        import plugins.memory as plugins_memory
        from gateway.run_agent_cache import GatewayAgentCacheMixin

        calls = []

        def _load(name, *, register_skills=None):
            calls.append((name, register_skills))
            return provider

        monkeypatch.setattr(plugins_memory, "load_memory_provider", _load)
        monkeypatch.setattr(GatewayAgentCacheMixin, "_MEMORY_IDENTITY_PROVIDER_MEMO", {})
        return calls

    def test_provider_identity_signature_enters_under_memory_prefix_and_is_re_read_from_one_instance(self, monkeypatch):
        from gateway.run import GatewayRunner
        from tests.agent.test_memory_provider import FakeMemoryProvider

        class IdentityProvider(FakeMemoryProvider):
            writer = "alice"

            def identity_signature(self):
                return {"fakeprov.writer": self.writer, "fakeprov.aliases": [("a", "b")]}

        provider = IdentityProvider("fakeprov")
        calls = self._install_fake_provider(monkeypatch, provider)
        cfg = {"memory": {"provider": "fakeprov"}}

        first = GatewayRunner._extract_cache_busting_config(cfg)
        provider.writer = "bob"
        second = GatewayRunner._extract_cache_busting_config(cfg)

        assert self._provider_declared_keys(first) == ["memory.fakeprov.aliases", "memory.fakeprov.writer"]
        assert first["memory.fakeprov.aliases"] == [("a", "b")]
        assert (first["memory.fakeprov.writer"], second["memory.fakeprov.writer"]) == ("alice", "bob")
        assert calls == [("fakeprov", False)]

    @pytest.mark.parametrize("kind", ["no hook", "no provider", "raising hook"])
    def test_provider_contributes_nothing_without_a_working_identity_hook(self, monkeypatch, kind):
        from gateway.run import GatewayRunner
        from tests.agent.test_memory_provider import FakeMemoryProvider

        class BrokenProvider(FakeMemoryProvider):
            def identity_signature(self):
                raise RuntimeError("boom")

        provider = {"no hook": FakeMemoryProvider("p"), "no provider": None, "raising hook": BrokenProvider("p")}[kind]
        calls = self._install_fake_provider(monkeypatch, provider)

        out = GatewayRunner._extract_cache_busting_config({"memory": {"provider": "p"}} if provider else {})

        assert self._provider_declared_keys(out) == []
        assert "tools.registry_generation" in out
        assert calls == ([] if provider is None else [("p", False)])

class TestAgentCacheLifecycle:
    """End-to-end cache behavior with real AIAgent construction."""

    def test_evict_on_session_reset(self):
        """_evict_cached_agent removes the entry."""
        from run_agent import AIAgent

        runner = _make_runner()
        session_key = "telegram:12345"

        agent = AIAgent(
            model="anthropic/claude-sonnet-4", api_key="test",
            base_url="https://openrouter.ai/api/v1", provider="openrouter",
            max_iterations=5, quiet_mode=True, skip_context_files=True,
            skip_memory=True,
        )
        with runner._agent_cache_lock:
            runner._agent_cache[session_key] = (agent, "sig123")

        runner._evict_cached_agent(session_key)

        with runner._agent_cache_lock:
            assert session_key not in runner._agent_cache


class TestOwnedContextEngineEviction:
    """Every cache-removal path releases its Python agent's engine clone."""

    def _bounded_runner(self):
        from collections import OrderedDict
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = OrderedDict()
        runner._agent_cache_lock = threading.Lock()
        runner._running_agents = {}
        return runner

    def test_explicit_eviction_shuts_down_engine_once(self):
        runner = _make_runner()
        agent, engine = _context_engine_agent()
        runner._agent_cache["session"] = (agent, "sig")

        runner._evict_cached_agent("session")
        _wait_for_call(engine.shutdown)
        runner._evict_cached_agent("session")

        engine.shutdown.assert_called_once_with()

    def test_cache_cap_eviction_shuts_down_each_evicted_engine_once(self, monkeypatch):
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_MAX_SIZE", 1)
        runner = self._bounded_runner()
        old_agent, old_engine = _context_engine_agent()
        new_agent, new_engine = _context_engine_agent()
        runner._agent_cache["old"] = (old_agent, "old-sig")
        runner._agent_cache["new"] = (new_agent, "new-sig")

        with runner._agent_cache_lock:
            runner._enforce_agent_cache_cap()
        _wait_for_call(old_engine.shutdown)

        old_engine.shutdown.assert_called_once_with()
        new_engine.shutdown.assert_not_called()

    def test_idle_eviction_shuts_down_permanent_session_engine_once(self, monkeypatch):
        import time
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_IDLE_TTL_SECS", 0.01)
        runner = self._bounded_runner()
        agent, engine = _context_engine_agent(last_activity=time.time() - 10)
        agent._memory_manager = None
        runner.session_store = MagicMock()
        runner.session_store._entries = {"session": MagicMock()}
        runner._agent_cache["session"] = (agent, "sig")

        assert runner._sweep_idle_cached_agents() == 1
        _wait_for_call(engine.shutdown)

        engine.shutdown.assert_called_once_with()

    def test_explicit_eviction_defers_engine_shutdown_until_turn_boundary_without_poller(
        self, monkeypatch
    ):
        import time
        from gateway import run as gw_run

        runner = self._bounded_runner()
        agent, engine = _context_engine_agent()
        runner._agent_cache["session"] = (agent, "sig")
        runner._running_agents["session"] = agent

        original_thread = gw_run.threading.Thread
        started_threads: list[object] = []

        class _UnexpectedThread:
            def __init__(self, *args, **kwargs):
                started_threads.append((args, kwargs))

            def start(self):
                return None

        monkeypatch.setattr(gw_run.threading, "Thread", _UnexpectedThread)
        runner._evict_cached_agent("session")

        engine.shutdown.assert_not_called()
        assert started_threads == [], "active eviction must not create an unbounded poller"

        monkeypatch.setattr(gw_run.threading, "Thread", original_thread)
        assert runner._release_running_agent_state("session") is True
        _wait_for_call(engine.shutdown)
        engine.shutdown.assert_called_once_with()


class TestAgentCacheBoundedGrowth:
    """LRU cap and idle-TTL eviction prevent unbounded cache growth."""

    def _bounded_runner(self):
        """Runner with an OrderedDict cache (matches real gateway init)."""
        from collections import OrderedDict
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = OrderedDict()
        runner._agent_cache_lock = threading.Lock()
        return runner

    def _fake_agent(self, last_activity: float | None = None):
        """Lightweight stand-in; real AIAgent is heavy to construct."""
        m = MagicMock()
        if last_activity is not None:
            m._last_activity_ts = last_activity
        else:
            import time as _t
            m._last_activity_ts = _t.time()
        return m

    def test_cap_commits_memory_before_soft_release(self, monkeypatch):
        """LRU eviction commits the transcript before releasing clients."""
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_MAX_SIZE", 1)
        runner = self._bounded_runner()

        commit_calls: list = []
        release_calls: list = []
        runner._release_evicted_agent_soft = lambda agent: release_calls.append(agent)

        runner.session_store = MagicMock()
        runner.session_store._entries = {"old": MagicMock(), "new": MagicMock()}

        old_agent = self._fake_agent()
        old_agent._memory_manager = MagicMock()  # has an external provider
        old_agent._session_messages = [{"role": "user", "content": "hi"}]
        old_agent.commit_memory_session = lambda msgs=None: commit_calls.append(msgs)
        new_agent = self._fake_agent()

        with runner._agent_cache_lock:
            runner._agent_cache["old"] = (old_agent, "sig_old")
            runner._agent_cache["new"] = (new_agent, "sig_new")
            runner._enforce_agent_cache_cap()

        import time as _t
        deadline = _t.time() + 2.0
        while _t.time() < deadline and not release_calls:
            _t.sleep(0.02)
        # Memory committed with the live transcript, THEN client released.
        assert commit_calls == [[{"role": "user", "content": "hi"}]]
        assert old_agent in release_calls

    def test_cap_skips_memory_commit_without_provider(self, monkeypatch):
        """An agent without a memory provider is released without extraction."""
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_MAX_SIZE", 1)
        runner = self._bounded_runner()

        commit_calls: list = []
        release_calls: list = []
        runner._release_evicted_agent_soft = lambda agent: release_calls.append(agent)

        runner.session_store = MagicMock()
        runner.session_store._entries = {"old": MagicMock(), "new": MagicMock()}

        old_agent = self._fake_agent()
        old_agent._memory_manager = None
        old_agent._session_messages = [{"role": "user", "content": "hi"}]
        old_agent.commit_memory_session = lambda msgs=None: commit_calls.append(msgs)
        new_agent = self._fake_agent()

        with runner._agent_cache_lock:
            runner._agent_cache["old"] = (old_agent, "sig_old")
            runner._agent_cache["new"] = (new_agent, "sig_new")
            runner._enforce_agent_cache_cap()

        import time as _t
        deadline = _t.time() + 2.0
        while _t.time() < deadline and not release_calls:
            _t.sleep(0.02)
        assert commit_calls == []       # no premature extraction
        assert old_agent in release_calls  # still released


    def test_idle_sweep_commits_then_evicts_permanent_session(self, monkeypatch):
        """Idle eviction commits the permanent session before releasing its agent."""
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_IDLE_TTL_SECS", 0.01)
        runner = self._bounded_runner()
        commit_calls: list = []
        release_calls: list = []
        runner._release_evicted_agent_soft = lambda agent: release_calls.append(agent)

        import time as _t
        stale = self._fake_agent(last_activity=_t.time() - 10.0)
        stale._memory_manager = MagicMock()
        stale._session_messages = [{"role": "user", "content": "hi"}]
        stale.commit_memory_session = lambda msgs=None: commit_calls.append(msgs)

        session_entry = MagicMock()
        runner.session_store = MagicMock()
        runner.session_store._entries = {"stale-session": session_entry}
        runner._agent_cache["stale-session"] = (stale, "sig")

        evicted = runner._sweep_idle_cached_agents()
        deadline = _t.time() + 2.0
        while _t.time() < deadline and not release_calls:
            _t.sleep(0.02)

        assert evicted == 1
        assert "stale-session" not in runner._agent_cache
        assert commit_calls == [[{"role": "user", "content": "hi"}]]
        assert release_calls == [stale]

    def test_idle_sweep_evicts_without_provider(self, monkeypatch):
        """Idle agents without a memory provider are released without extraction."""
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_IDLE_TTL_SECS", 0.01)
        runner = self._bounded_runner()
        commit_calls: list = []
        release_calls: list = []
        runner._release_evicted_agent_soft = lambda agent: release_calls.append(agent)

        import time as _t
        stale = self._fake_agent(last_activity=_t.time() - 10.0)
        stale._memory_manager = None
        stale._session_messages = [{"role": "user", "content": "hi"}]
        stale.commit_memory_session = lambda msgs=None: commit_calls.append(msgs)

        session_entry = MagicMock()
        runner.session_store = MagicMock()
        runner.session_store._entries = {"stale-session": session_entry}
        runner._agent_cache["stale-session"] = (stale, "sig")

        evicted = runner._sweep_idle_cached_agents()
        deadline = _t.time() + 2.0
        while _t.time() < deadline and not release_calls:
            _t.sleep(0.02)

        assert evicted == 1
        assert "stale-session" not in runner._agent_cache
        assert commit_calls == []
        assert release_calls == [stale]


    def test_permanent_session_memory_extraction_precedes_provider_release(self, tmp_path):
        """Resource eviction commits a real durable session without an expiry policy."""
        from gateway.config import GatewayConfig, Platform
        from gateway.session import SessionSource, SessionStore
        from run_agent import AIAgent

        store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
        runner = self._bounded_runner()
        runner.session_store = store
        source = SessionSource(platform=Platform.TELEGRAM, user_id="u1", chat_id="c1", chat_type="dm")
        try:
            entry = store.get_or_create_session(source)
            messages = [{"role": "user", "content": "retain this durable conversation"}]
            events = []
            manager = MagicMock()
            manager.on_session_end.side_effect = lambda transcript: events.append(("extract", transcript))
            manager.shutdown_all.side_effect = lambda: events.append(("shutdown", None))
            agent = AIAgent.__new__(AIAgent)
            agent._memory_manager = manager
            agent._session_messages = messages
            agent.release_clients = lambda: events.append(("clients", None))

            assert runner._commit_then_release_soft(agent, entry.session_key)
            assert [name for name, _ in events] == ["extract", "shutdown", "clients"]
            assert events[0][1] == messages
            assert agent._memory_manager is None
            assert store.get_or_create_session(source).session_id == entry.session_id
            assert runner._commit_then_release_soft(agent, entry.session_key)
            manager.on_session_end.assert_called_once_with(messages)
            manager.shutdown_all.assert_called_once_with()
        finally:
            store._db_handle_cache.close_all(lambda handle: handle.close())

    def test_plain_dict_cache_is_tolerated(self):
        """Test fixtures using plain {} don't crash _enforce_agent_cache_cap."""
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = {}  # plain dict, not OrderedDict
        runner._agent_cache_lock = threading.Lock()
        runner._cleanup_agent_resources = MagicMock()

        # Should be a no-op rather than raising.
        with runner._agent_cache_lock:
            for i in range(200):
                runner._agent_cache[f"s{i}"] = (MagicMock(), f"sig{i}")
            runner._enforce_agent_cache_cap()  # no crash, no eviction

        assert len(runner._agent_cache) == 200


class TestAgentCacheActiveSafety:
    """Safety: eviction must not tear down agents currently mid-turn.

    AIAgent.close() kills process_registry entries for the task, cleans
    the terminal sandbox, closes the OpenAI client, and cascades
    .close() into active child subagents.  Calling it while the agent
    is still processing would crash the in-flight request.  These tests
    pin that eviction skips any agent present in _running_agents.
    """

    def _runner(self):
        from collections import OrderedDict
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = OrderedDict()
        runner._agent_cache_lock = threading.Lock()
        runner._running_agents = {}
        return runner

    def _fake_agent(self, idle_seconds: float = 0.0):
        import time as _t
        m = MagicMock()
        m._last_activity_ts = _t.time() - idle_seconds
        return m

    def test_cap_skips_active_lru_entry(self, monkeypatch):
        """Active LRU entry is skipped; cache stays over cap rather than
        compensating by evicting a newer entry.

        Rationale: evicting a more-recent entry just because the oldest
        slot is temporarily locked would punish the most recently-
        inserted session (which has no cache to preserve) to protect
        one that happens to be mid-turn.  Better to let the cache stay
        transiently over cap and re-check on the next insert.
        """
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_MAX_SIZE", 2)
        runner = self._runner()
        runner._cleanup_agent_resources = MagicMock()

        active = self._fake_agent()
        idle_a = self._fake_agent()
        idle_b = self._fake_agent()

        # Insertion order: active (oldest), idle_a, idle_b.
        runner._agent_cache["session-active"] = (active, "sig")
        runner._agent_cache["session-idle-a"] = (idle_a, "sig")
        runner._agent_cache["session-idle-b"] = (idle_b, "sig")

        # Mark `active` as mid-turn — it's LRU, but protected.
        runner._running_agents["session-active"] = active

        with runner._agent_cache_lock:
            runner._enforce_agent_cache_cap()

        # All three remain; no eviction ran, no cleanup dispatched.
        assert "session-active" in runner._agent_cache
        assert "session-idle-a" in runner._agent_cache
        assert "session-idle-b" in runner._agent_cache
        assert runner._cleanup_agent_resources.call_count == 0

    def test_idle_sweep_skips_active_agent(self, monkeypatch):
        """Idle-TTL sweep must not tear down an active agent even if 'stale'."""
        from gateway import run as gw_run

        monkeypatch.setattr(gw_run, "_AGENT_CACHE_IDLE_TTL_SECS", 0.01)
        runner = self._runner()
        runner._cleanup_agent_resources = MagicMock()

        old_but_active = self._fake_agent(idle_seconds=10.0)
        runner._agent_cache["s1"] = (old_but_active, "sig")
        runner._running_agents["s1"] = old_but_active

        evicted = runner._sweep_idle_cached_agents()

        assert evicted == 0
        assert "s1" in runner._agent_cache
        assert runner._cleanup_agent_resources.call_count == 0

class TestAgentCacheSpilloverLive:
    """Live E2E: fill cache with real AIAgent instances and stress it."""

    def _runner(self):
        from collections import OrderedDict
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = OrderedDict()
        runner._agent_cache_lock = threading.Lock()
        runner._running_agents = {}
        return runner

    def _real_agent(self):
        """A genuine AIAgent; no API calls are made during these tests."""
        from run_agent import AIAgent
        return AIAgent(
            model="anthropic/claude-sonnet-4", api_key="test",
            base_url="https://openrouter.ai/api/v1", provider="openrouter",
            max_iterations=5, quiet_mode=True,
            skip_context_files=True, skip_memory=True,
            platform="telegram",
        )

    def test_fill_to_cap_then_spillover(self, monkeypatch):
        """Fill to cap with real agents, insert one more, oldest evicted."""
        from gateway import run as gw_run

        CAP = 8
        monkeypatch.setattr(gw_run, "_AGENT_CACHE_MAX_SIZE", CAP)
        runner = self._runner()

        agents = [self._real_agent() for _ in range(CAP)]
        for i, a in enumerate(agents):
            with runner._agent_cache_lock:
                runner._agent_cache[f"s{i}"] = (a, "sig")
                runner._enforce_agent_cache_cap()
        assert len(runner._agent_cache) == CAP

        # Spillover insertion.
        newcomer = self._real_agent()
        with runner._agent_cache_lock:
            runner._agent_cache["new"] = (newcomer, "sig")
            runner._enforce_agent_cache_cap()

        # Oldest (s0) evicted, cap still CAP.
        assert "s0" not in runner._agent_cache
        assert "new" in runner._agent_cache
        assert len(runner._agent_cache) == CAP

        # Clean up so pytest doesn't leak resources.
        for a in agents + [newcomer]:
            try:
                a.close()
            except Exception:
                pass

class TestAgentCacheIdleResume:
    """End-to-end: idle-TTL-evicted session resumes cleanly with task state.

    Real-world scenario: user leaves a Telegram session open for 2+ hours.
    Idle-TTL evicts their cached agent.  They come back and send a message.
    The new agent built for the same session_id must inherit:
      - Conversation history (from SessionStore — outside cache concern)
      - Terminal sandbox (same task_id → same _active_environments entry)
      - Browser daemon (same task_id → same browser session)
      - Background processes (same task_id → same process_registry entries)
    The ONLY thing that should reset is the LLM client pool (rebuilt fresh).
    """

    def _runner(self):
        from collections import OrderedDict
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = OrderedDict()
        runner._agent_cache_lock = threading.Lock()
        runner._running_agents = {}
        return runner

    def test_release_clients_does_not_touch_terminal_or_browser(self, monkeypatch):
        """release_clients must not call cleanup_vm or cleanup_browser."""
        from run_agent import AIAgent
        from tools import terminal_tool_lifecycle as _tt

        agent = AIAgent(
            model="anthropic/claude-sonnet-4", api_key="test",
            base_url="https://openrouter.ai/api/v1", provider="openrouter",
            max_iterations=5, quiet_mode=True,
            skip_context_files=True, skip_memory=True,
            session_id="idle-resume-test-2",
        )

        vm_calls: list = []
        browser_calls: list = []
        original_vm = _tt.cleanup_vm
        original_browser = bt_lifecycle.cleanup_browser
        _tt.cleanup_vm = lambda tid: vm_calls.append(tid)
        bt_lifecycle.cleanup_browser = lambda tid: browser_calls.append(tid)
        try:
            agent.release_clients()
        finally:
            _tt.cleanup_vm = original_vm
            bt_lifecycle.cleanup_browser = original_browser
            try:
                agent.close()
            except Exception:
                pass

        assert vm_calls == [], (
            f"release_clients() tore down terminal sandbox — user's cwd, "
            f"env, and bg shells would be gone on resume. Calls: {vm_calls}"
        )
        assert browser_calls == [], (
            f"release_clients() tore down browser session — user's open "
            f"tabs and cookies gone on resume. Calls: {browser_calls}"
        )

    def test_close_vs_release_full_teardown_difference(self, monkeypatch):
        """close() tears down task state; release_clients() does not.

        This pins the semantic contract: session-expiry path uses close()
        (full teardown — session is done), cache-eviction path uses
        release_clients() (soft — session may resume).
        """
        from run_agent import AIAgent
        import run_agent as _ra

        # Agent A: evicted from cache (soft) — terminal survives.
        # Agent B: session expired (hard) — terminal torn down.
        agent_a = AIAgent(
            model="anthropic/claude-sonnet-4", api_key="test",
            base_url="https://openrouter.ai/api/v1", provider="openrouter",
            max_iterations=5, quiet_mode=True,
            skip_context_files=True, skip_memory=True,
            session_id="soft-session",
        )
        agent_b = AIAgent(
            model="anthropic/claude-sonnet-4", api_key="test",
            base_url="https://openrouter.ai/api/v1", provider="openrouter",
            max_iterations=5, quiet_mode=True,
            skip_context_files=True, skip_memory=True,
            session_id="hard-session",
        )

        vm_calls: list = []
        # AIAgent.close() calls the ``cleanup_vm`` name bound into
        # ``run_agent`` at import time, not ``tools.terminal_tool_lifecycle.cleanup_vm``
        # directly — so patch the ``run_agent`` reference.
        original_vm = _ra.cleanup_vm
        _ra.cleanup_vm = lambda tid: vm_calls.append(tid)
        try:
            agent_a.release_clients()   # cache eviction
            agent_b.close()              # session expiry
        finally:
            _ra.cleanup_vm = original_vm
            try:
                agent_a.close()
            except Exception:
                pass

        # Only agent_b's task_id should appear in cleanup calls.
        assert "hard-session" in vm_calls
        assert "soft-session" not in vm_calls


class TestCacheEvictionMemoryProviderRelease:
    """Retire per-agent memory workers without destroying resumable tools."""

    def test_provider_only_release_is_idempotent_and_detaches_manager(self, monkeypatch):
        from run_agent import AIAgent
        import run_agent as _ra

        events = []
        manager = MagicMock()
        manager.shutdown_all.side_effect = lambda: events.append("shutdown_all")
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager

        vm_calls = []
        browser_calls = []
        monkeypatch.setattr(_ra, "cleanup_vm", lambda task_id: vm_calls.append(task_id))
        monkeypatch.setattr(
            "tools.browser_tool_lifecycle.cleanup_browser",
            lambda task_id: browser_calls.append(task_id),
        )

        agent.release_memory_provider_for_cache_evict()
        agent.release_memory_provider_for_cache_evict()

        assert events == ["shutdown_all"]
        assert agent._memory_manager is None
        assert vm_calls == []
        assert browser_calls == []

    def test_soft_evict_commits_then_stops_memory_then_releases_clients(self):
        from gateway.run import GatewayRunner

        events = []

        class _Agent:
            _session_messages = [{"role": "user", "content": "hello"}]
            _db_flush_scan_prefix = _session_messages[:]

            def release_memory_provider_for_cache_evict(self):
                events.append("memory_shutdown")

            def release_clients(self):
                events.append("client_release")

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._commit_memory_before_soft_evict = (
            lambda agent, key: events.append("memory_commit")
        )
        runner._commit_then_release_soft(_Agent(), "session-1")

        assert events == ["memory_commit", "memory_shutdown", "client_release"]


class TestCacheEvictionMemoryProviderClaims:
    def test_concurrent_provider_release_claims_manager_once(self):
        import threading
        from run_agent import AIAgent

        barrier = threading.Barrier(2)
        shutdown_calls = []
        manager = MagicMock()
        manager.shutdown_all.side_effect = lambda: shutdown_calls.append("shutdown")

        class _RacingAgent(AIAgent):
            @property
            def _memory_manager(self):
                value = self._memory_manager_value
                if value is not None:
                    try:
                        barrier.wait(timeout=0.2)
                    except threading.BrokenBarrierError:
                        pass
                return value

            @_memory_manager.setter
            def _memory_manager(self, value):
                self._memory_manager_value = value

        agent = _RacingAgent.__new__(_RacingAgent)
        agent._memory_manager_value = manager
        agent._memory_manager_release_lock = threading.Lock()
        threads = [
            threading.Thread(target=agent.release_memory_provider_for_cache_evict)
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert shutdown_calls == ["shutdown"]
        assert agent._memory_manager is None

    def test_hard_cleanup_then_soft_evict_shuts_manager_down_once(self):
        import threading
        from run_agent import AIAgent

        manager = MagicMock()
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()

        agent.shutdown_memory_provider([{"role": "user", "content": "hello"}])
        agent.release_memory_provider_for_cache_evict()

        manager.on_session_end.assert_called_once()
        manager.shutdown_all.assert_called_once()
        assert agent._memory_manager is None

    def test_failed_provider_shutdown_can_be_retried(self):
        import threading
        from run_agent import AIAgent

        manager = MagicMock()
        manager.shutdown_all.side_effect = [RuntimeError("boom"), None]
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()

        with pytest.raises(RuntimeError, match="shutdown incomplete"):
            agent.release_memory_provider_for_cache_evict()
        assert agent._memory_manager is manager

        agent.release_memory_provider_for_cache_evict()
        assert manager.shutdown_all.call_count == 2
        assert agent._memory_manager is None


class TestCacheEvictionMemoryProviderFailureOverlap:
    def test_failed_soft_shutdown_is_retried_by_overlapping_hard_cleanup(self):
        import threading
        from run_agent import AIAgent

        first_started = threading.Event()
        allow_first_failure = threading.Event()
        shutdown_calls = []

        manager = MagicMock()

        def shutdown_all():
            shutdown_calls.append("shutdown")
            if len(shutdown_calls) == 1:
                first_started.set()
                assert allow_first_failure.wait(timeout=2)
                raise RuntimeError("first shutdown failed")

        manager.shutdown_all.side_effect = shutdown_all
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()

        soft_errors = []

        def soft_release():
            try:
                agent.release_memory_provider_for_cache_evict()
            except Exception as exc:
                soft_errors.append(exc)

        soft_thread = threading.Thread(target=soft_release)
        soft_thread.start()
        assert first_started.wait(timeout=1)

        hard_thread = threading.Thread(
            target=lambda: agent.shutdown_memory_provider(
                [{"role": "user", "content": "hello"}]
            )
        )
        hard_thread.start()
        allow_first_failure.set()

        soft_thread.join(timeout=2)
        hard_thread.join(timeout=2)

        assert not soft_thread.is_alive()
        assert not hard_thread.is_alive()
        assert [type(exc) for exc in soft_errors] == [RuntimeError]
        assert shutdown_calls == ["shutdown", "shutdown"]
        manager.on_session_end.assert_called_once()
        assert agent._memory_manager is None


class TestAllSoftEvictionPathsCommitBeforeProviderShutdown:
    def test_explicit_soft_release_scheduler_preserves_session_key_for_commit(self, monkeypatch):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        events = []
        runner._commit_then_release_soft = (
            lambda agent, key: events.append((agent, key))
        )

        class _InlineThread:
            def __init__(self, *, target, args, **kwargs):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        monkeypatch.setattr("gateway.run.threading.Thread", _InlineThread)
        agent = object()

        runner._schedule_evicted_agent_soft_release(agent, "session-explicit")

        assert events == [(agent, "session-explicit")]

    def test_deferred_active_eviction_preserves_session_key_until_release(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._running_agents = {}
        runner._running_agents_lock = None
        scheduled = []
        runner._schedule_evicted_agent_soft_release = (
            lambda agent, key="": scheduled.append((agent, key))
        )
        agent = object()

        runner._defer_evicted_agent_release(agent, "session-deferred")
        runner._drain_pending_evicted_agent_releases()

        assert scheduled == [(agent, "session-deferred")]


class TestFailedEvictedAgentCleanupRetention:
    def test_failed_soft_release_is_retained_and_periodic_retry_removes_it(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._lifecycle_counters = None
        attempts = []

        class _Agent:
            _session_messages = []
            _db_flush_scan_prefix = None

            def release_memory_provider_for_cache_evict(self):
                attempts.append("shutdown")
                if len(attempts) == 1:
                    raise RuntimeError("temporary shutdown failure")

            def release_clients(self):
                pass

        agent = _Agent()
        runner._retain_failed_evicted_agent_cleanup(agent, "session-retry")
        runner._release_evicted_agent_soft(agent)
        registry, _ = runner._failed_evicted_agent_cleanup_state()
        assert registry == {id(agent): (agent, "session-retry")}

        assert runner._retry_failed_evicted_agent_cleanups() == 1
        registry, _ = runner._failed_evicted_agent_cleanup_state()
        assert registry == {}
        assert attempts == ["shutdown", "shutdown"]

    def test_full_soft_then_overlapping_hard_does_not_duplicate_session_end(self):
        import threading
        from gateway.run import GatewayRunner
        from run_agent import AIAgent

        first_started = threading.Event()
        allow_first_failure = threading.Event()
        shutdown_calls = []
        manager = MagicMock()

        def shutdown_all():
            shutdown_calls.append("shutdown")
            if len(shutdown_calls) == 1:
                first_started.set()
                assert allow_first_failure.wait(timeout=2)
                raise RuntimeError("first shutdown failed")
            return True

        manager.shutdown_all.side_effect = shutdown_all
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()
        agent._session_messages = [{"role": "user", "content": "hello"}]
        agent._db_flush_scan_prefix = agent._session_messages[:]
        agent.commit_memory_session = lambda messages=None: manager.on_session_end(
            messages or []
        )
        agent.release_clients = lambda: None

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._lifecycle_counters = None
        runner._commit_memory_before_soft_evict = lambda a, key: (
            a.commit_memory_session(a._session_messages) or True
        )

        soft_thread = threading.Thread(
            target=lambda: runner._commit_then_release_soft(agent, "session-1")
        )
        soft_thread.start()
        assert first_started.wait(timeout=1)
        hard_thread = threading.Thread(
            target=lambda: agent.shutdown_memory_provider(agent._session_messages)
        )
        hard_thread.start()
        allow_first_failure.set()
        soft_thread.join(timeout=2)
        hard_thread.join(timeout=2)

        assert manager.on_session_end.call_count == 1
        assert shutdown_calls == ["shutdown", "shutdown"]
        assert agent._memory_manager is None


class TestBoundedMemoryProviderShutdownOwnership:
    def test_blocking_provider_shutdown_returns_incomplete_without_duplicate_worker(self, monkeypatch):
        import threading
        import time
        import run_agent as run_agent_module
        from run_agent import AIAgent

        monkeypatch.setattr(run_agent_module, "_MEMORY_PROVIDER_SHUTDOWN_TIMEOUT_S", 0.05)
        started = threading.Event()
        release = threading.Event()
        calls = []
        manager = MagicMock()

        def shutdown_all():
            calls.append("shutdown")
            started.set()
            release.wait(timeout=2)
            return True

        manager.shutdown_all.side_effect = shutdown_all
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()

        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="shutdown incomplete"):
            agent.release_memory_provider_for_cache_evict()
        assert time.monotonic() - t0 < 0.3
        assert started.is_set()
        with pytest.raises(RuntimeError, match="shutdown incomplete"):
            agent.release_memory_provider_for_cache_evict()
        assert calls == ["shutdown"]

        release.set()
        for _ in range(50):
            if getattr(agent, "_memory_manager_shutdown_state", None) is None:
                break
            time.sleep(0.01)
        assert agent.release_memory_provider_for_cache_evict() is True
        assert agent._memory_manager is None

    def test_soft_cleanup_retains_transcript_when_provider_reports_incomplete(self):
        from types import SimpleNamespace
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._lifecycle_counters = None
        messages = [{"role": "user", "content": "still owed to the provider"}]
        agent = SimpleNamespace(
            _memory_manager=None,
            _session_messages=messages,
            _db_flush_scan_prefix=messages,
            release_memory_provider_for_cache_evict=lambda: False,
            release_clients=MagicMock(),
        )
        runner._retain_failed_evicted_agent_cleanup(agent, "unfinished-provider")

        assert runner._commit_then_release_soft(agent, "unfinished-provider") is False
        assert agent._session_messages == messages
        assert agent._db_flush_scan_prefix == messages
        registry, _ = runner._failed_evicted_agent_cleanup_state()
        assert registry[id(agent)] == (agent, "unfinished-provider")

    def test_hard_cleanup_propagates_incomplete_memory_shutdown(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._lifecycle_counters = None
        agent = MagicMock()
        agent.shutdown_memory_provider.return_value = False
        agent._memory_manager = None
        agent._session_messages = []

        assert runner._cleanup_agent_resources(agent) is False

    def test_shutdown_daemon_returns_false_for_false_worker_result(self):
        import asyncio
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)

        async def exercise():
            return await runner._run_shutdown_sync_daemon(
                lambda: False,
                timeout=1,
                context="false-result",
            )

        assert asyncio.run(exercise()) is False


class TestDurableEvictionOperationOwnership:
    def test_cap_eviction_registers_full_owner_before_cleanup_thread_start(self, monkeypatch):
        import threading
        from collections import OrderedDict
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._agent_cache = OrderedDict()
        runner._agent_cache_lock = threading.RLock()
        runner._agent_cache_cap = lambda: 0
        runner._running_agent_items = list
        runner._lifecycle_counters = None
        agent = MagicMock()
        runner._agent_cache["session-1"] = (agent, "sig")

        class _NeverStartedThread:
            def __init__(self, *, target, args, **kwargs):
                self.target = target
                self.args = args

            def start(self):
                registry, _ = runner._failed_evicted_agent_cleanup_state()
                assert registry[id(agent)] == (agent, "session-1")
                raise RuntimeError("thread start failed")

        monkeypatch.setattr("gateway.run.threading.Thread", _NeverStartedThread)
        runner._commit_then_release_soft = lambda a, key: False

        with runner._agent_cache_lock:
            assert runner._enforce_agent_cache_cap() == 1

        registry, _ = runner._failed_evicted_agent_cleanup_state()
        assert runner._agent_cache == {}
        assert registry[id(agent)] == (agent, "session-1")

    def test_commit_and_shutdown_share_one_owner_against_hard_cleanup(self):
        import threading
        from gateway.run import GatewayRunner
        from run_agent import AIAgent

        commit_started = threading.Event()
        allow_commit = threading.Event()
        manager = MagicMock()

        def on_session_end(messages):
            commit_started.set()
            assert allow_commit.wait(timeout=2)

        manager.on_session_end.side_effect = on_session_end
        manager.shutdown_all.return_value = True
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()
        agent._session_messages = [{"role": "user", "content": "hello"}]
        agent.release_clients = lambda: None

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._lifecycle_counters = None
        runner._soft_evict_memory_commit_plan = lambda a, key: (
            True,
            a._session_messages,
        )
        runner._retain_failed_evicted_agent_cleanup(agent, "session-1")

        soft_thread = threading.Thread(
            target=lambda: runner._commit_then_release_soft(agent, "session-1")
        )
        soft_thread.start()
        assert commit_started.wait(timeout=1)
        hard_results = []
        hard_thread = threading.Thread(
            target=lambda: hard_results.append(
                agent.shutdown_memory_provider(agent._session_messages)
            )
        )
        hard_thread.start()
        allow_commit.set()
        soft_thread.join(timeout=2)
        hard_thread.join(timeout=2)

        assert not soft_thread.is_alive()
        assert not hard_thread.is_alive()
        assert hard_results == [True]
        assert manager.on_session_end.call_count == 1
        assert manager.shutdown_all.call_count == 1
        registry, _ = runner._failed_evicted_agent_cleanup_state()
        assert registry == {}

    def test_failed_commit_retry_preserves_original_transcript_until_success(self):
        import threading
        from gateway.run import GatewayRunner
        from run_agent import AIAgent

        payloads = []
        manager = MagicMock()

        def on_session_end(messages):
            payloads.append(list(messages))
            if len(payloads) == 1:
                raise RuntimeError("temporary commit failure")

        manager.on_session_end.side_effect = on_session_end
        manager.shutdown_all.return_value = True
        original = [{"role": "user", "content": "must survive"}]
        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = manager
        agent._memory_manager_release_lock = threading.Lock()
        agent._session_messages = list(original)
        agent._db_flush_scan_prefix = list(original)
        agent.release_clients = lambda: None

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._lifecycle_counters = None
        runner._soft_evict_memory_commit_plan = lambda a, key: (
            True,
            list(a._session_messages),
        )
        runner._retain_failed_evicted_agent_cleanup(agent, "session-retry")

        assert runner._commit_then_release_soft(agent, "session-retry") is False
        assert agent._session_messages == original
        assert agent._db_flush_scan_prefix == original

        assert runner._retry_failed_evicted_agent_cleanups() == 1
        assert payloads == [original, original]
        assert agent._session_messages == []
        assert agent._db_flush_scan_prefix is None
        registry, _ = runner._failed_evicted_agent_cleanup_state()
        assert registry == {}


_FAKE_NOW = 10_000.0  # Fixed epoch for deterministic time assertions

class TestCachedAgentInactivityReset:
    """Inactivity-clock reset must be gated on _interrupt_depth == 0.

    On interrupt-recursive turns (_interrupt_depth > 0) the clock must
    keep accumulating so the inactivity watchdog can fire when a turn is
    stuck in an interrupt loop.  Resetting unconditionally prevented the
    30-min timeout from triggering (#15654).  The depth-0 reset is still
    needed: a session idle for 29 min must not trip the watchdog before
    the new turn makes its first API call (#9051).
    """

    def _fake_agent(self, stale_seconds: float = 1800.0):
        from agent.session_activity import ActivityProvenance

        m = MagicMock()
        m._last_activity_ts = _FAKE_NOW - stale_seconds
        m._api_call_count = 10
        m._last_activity_desc = "previous turn activity"
        m._last_activity_provenance = ActivityProvenance.AGENT_COMPRESSION
        return m

    def test_fresh_turn_resets_idle_clock(self):
        """interrupt_depth=0: clock resets so a post-idle turn gets a
        fresh 30-min inactivity window (guard for #9051)."""
        from gateway.run import GatewayRunner

        agent = self._fake_agent(stale_seconds=1800.0)
        old_ts = agent._last_activity_ts

        with patch("gateway.run.time") as mock_time:
            mock_time.time.return_value = _FAKE_NOW
            GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)

        assert agent._last_activity_ts == _FAKE_NOW, (
            "_last_activity_ts was not reset on a fresh turn (interrupt_depth=0)"
        )
        assert agent._last_activity_ts > old_ts, (
            "Stale idle time should be cleared so the new turn gets a fresh window"
        )

    def test_fresh_turn_resets_provenance(self):
        """interrupt_depth=0: provenance resets with ts/desc (#72039)."""
        from agent.session_activity import ActivityProvenance
        from gateway.run import GatewayRunner

        agent = self._fake_agent()
        assert agent._last_activity_provenance is ActivityProvenance.AGENT_COMPRESSION

        with patch("gateway.run.time") as mock_time:
            mock_time.time.return_value = _FAKE_NOW
            GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)

        assert agent._last_activity_provenance is ActivityProvenance.UNKNOWN

    def test_interrupt_turn_preserves_idle_clock(self):
        """interrupt_depth=1: clock preserved so accumulated stuck-turn
        idle time is not discarded by an interrupt-recursive re-entry (#15654)."""
        from gateway.run import GatewayRunner

        agent = self._fake_agent(stale_seconds=1200.0)
        old_ts = agent._last_activity_ts

        GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=1)

        assert agent._last_activity_ts == old_ts, (
            "_last_activity_ts must not be reset on interrupt-recursive turns "
            "(interrupt_depth>0) — the watchdog needs the accumulated idle time"
        )

    def test_interrupt_turn_preserves_desc(self):
        """interrupt_depth=1: desc preserved — it is semantically paired with ts."""
        from gateway.run import GatewayRunner

        agent = self._fake_agent(stale_seconds=1200.0)

        GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=1)

        assert agent._last_activity_desc == "previous turn activity", (
            "_last_activity_desc must not change on interrupt-recursive turns; "
            "it describes the activity *at* _last_activity_ts"
        )

    def test_interrupt_turn_preserves_provenance(self):
        """interrupt_depth=1: provenance preserved with ts/desc."""
        from agent.session_activity import ActivityProvenance
        from gateway.run import GatewayRunner

        agent = self._fake_agent(stale_seconds=1200.0)

        GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=1)

        assert agent._last_activity_provenance is ActivityProvenance.AGENT_COMPRESSION

    def test_fresh_turn_resets_flush_cursor(self):
        """interrupt_depth=0: _last_flushed_db_idx resets so new-turn
        messages are fully persisted to the session DB (#44327)."""
        from gateway.run import GatewayRunner

        agent = self._fake_agent()
        agent._last_flushed_db_idx = 42  # stale from previous turn

        with patch("gateway.run.time") as mock_time:
            mock_time.time.return_value = _FAKE_NOW
            GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)

        assert agent._last_flushed_db_idx == 0, (
            "_last_flushed_db_idx must be reset on a fresh turn so that "
            "_flush_messages_to_session_db starts from index 0"
        )

class TestAgentCacheMessageCountRebaseline:
    """The cross-process coherence guard (#45966) must NOT invalidate the
    cache on this process's OWN writes.

    The guard snapshots ``message_count`` at agent-build time (before the
    turn writes its own rows) and never refreshes it on reuse.  Without a
    post-turn re-baseline, the gateway's own turn grows the count and the
    next turn sees a mismatch and rebuilds the agent — every turn, for every
    conversation — silently destroying per-conversation prompt caching.

    ``_refresh_agent_cache_message_count`` re-baselines the stored count to
    the now-current value after each turn, so the guard fires ONLY when a
    different process changed the transcript.  These tests pin both halves of
    the invariant against the REAL SessionDB + the REAL guard condition.
    """

    def _runner_with_db(self, db):
        from hermes_state import AsyncSessionDB

        runner = _make_runner()
        # The gateway holds the async facade; the production refresh awaits it.
        runner._session_db = AsyncSessionDB(db)
        return runner

    @staticmethod
    def _guard_would_reuse(runner, session_key, session_id):
        """Mirror the production cache-hit guard's reuse decision exactly.

        Reuse iff the live on-disk count equals the snapshot stored next to
        the cached agent (or either side is None / it's a legacy 2-tuple).
        """
        try:
            row = runner._session_db._db.get_session(session_id)
            live = row.get("message_count", 0) if row else None
        except Exception:
            live = None
        with runner._agent_cache_lock:
            cached = runner._agent_cache.get(session_key)
        cached_mc = cached[2] if cached and len(cached) > 2 else None
        invalidate = (
            cached_mc is not None
            and live is not None
            and live != cached_mc
        )
        return not invalidate

    @pytest.mark.asyncio
    async def test_cross_process_write_still_invalidates(self, tmp_path):
        """After the re-baseline, a DIFFERENT process appending to the same
        session must still flip the guard to rebuild (the #45966 fix holds).
        """
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "sessions.db")
        db.create_session("s1", source="telegram")
        runner = self._runner_with_db(db)
        agent = object()

        with runner._agent_cache_lock:
            _row = db.get_session("s1")
            runner._agent_cache["telegram:s1"] = (
                agent, "sig", (_row.get("message_count", 0) if _row else 0),
            )

        # Our own turn + re-baseline -> reuse next turn.
        db.append_message("s1", role="user", content="u")
        db.append_message("s1", role="assistant", content="a")
        await runner._refresh_agent_cache_message_count("telegram:s1", "s1")
        assert self._guard_would_reuse(runner, "telegram:s1", "s1") is True

        # ANOTHER process (e.g. the desktop dashboard backend) appends a turn
        # to the SAME session in the shared DB — we have NOT re-baselined for it.
        db.append_message("s1", role="user", content="external from dashboard")

        # Guard must now reject reuse so the agent rebuilds from fresh disk.
        assert self._guard_would_reuse(runner, "telegram:s1", "s1") is False

    @pytest.mark.asyncio
    async def test_in_band_followup_reuses_cached_agent(self, tmp_path):
        """Behavioral regression for the in-band queued (/queue) follow-up.

        #46237 re-baselines the snapshot only on the EXTERNAL-turn boundary
        (in ``_handle_message_with_agent``, after the whole ``_run_agent``
        chain unwinds).  The recursive in-band follow-up re-enters the cache
        guard MID-CHAIN — while the cache still holds the build-time snapshot
        and the first turn has already flushed its own rows — so without a
        re-baseline at the follow-up boundary the guard sees the grown count
        and rebuilds the agent on THIS process's own writes, re-introducing
        the every-turn rebuild #46237 set out to fix, on the follow-up path.

        Pins both halves at that boundary: WITHOUT the re-baseline the in-band
        follow-up would rebuild; WITH it the follow-up REUSES the warm agent.
        The guard's reuse decision (``_guard_would_reuse``) mirrors the real
        cache-hit guard, which reads ``get_session(session_id)`` with the same
        ``session_id`` the recursive ``_run_agent`` call is given.
        """
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "sessions.db")
        db.create_session("s1", source="telegram")
        runner = self._runner_with_db(db)
        agent = object()

        # First turn: cache miss -> build. Snapshot is the pre-turn count.
        _row = db.get_session("s1")
        build_count = _row.get("message_count", 0) if _row else 0
        with runner._agent_cache_lock:
            runner._agent_cache["telegram:s1"] = (agent, "sig", build_count)

        # First turn flushes its own user + assistant rows.
        db.append_message("s1", role="user", content="u")
        db.append_message("s1", role="assistant", content="a")

        # Bug reproduction: re-entering the guard at the in-band follow-up
        # boundary WITHOUT the re-baseline sees the grown count and rebuilds.
        assert self._guard_would_reuse(runner, "telegram:s1", "s1") is False

        # The fix: re-baseline at the follow-up boundary.
        await runner._refresh_agent_cache_message_count("telegram:s1", "s1")

        # The in-band follow-up now REUSES the cached, warm-prefix agent.
        assert self._guard_would_reuse(runner, "telegram:s1", "s1") is True
        with runner._agent_cache_lock:
            assert runner._agent_cache["telegram:s1"][0] is agent


@pytest.mark.parametrize("result", [False, True, ["session-end message"]])
def test_profile_release_preserves_cleanup_receipt(monkeypatch, result):
    """Cleanup owners need the exact callback result to retain failed work."""
    from gateway.run import GatewayRunner
    monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: False)
    runner = GatewayRunner.__new__(GatewayRunner)
    assert runner._run_release_in_profile_scope(lambda: result, (), None) is result
