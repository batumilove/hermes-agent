"""Regression tests for #35314 — empty model on the post-interrupt recovery turn.

After a ``stream_interrupt_abort`` during an active gateway session, the recovery
turn was sometimes built with ``model=""`` (a transient config-cache miss returned
an empty ``user_config``). Every API call then failed HTTP 400 "No models
provided", "trying fallback..." was logged but never executed (the user had no
fallback configured), and the session went silent until the user re-sent.

These tests pin two fixes:
  1. ``_resolve_session_agent_runtime`` caches the last successfully-resolved
     model per session and recovers it when a fresh resolution comes back empty.
  2. ``_has_pending_fallback`` gates the "trying fallback..." status so it is only
     announced when a fallback chain actually exists.
"""

import asyncio
import threading
from types import SimpleNamespace

import gateway.run as gateway_run
import pytest


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._session_model_overrides = {}
    runner._last_resolved_model = {}
    runner._service_tier = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    return runner


def _patch_resolution(monkeypatch, *, model_from_config: str, provider: str = "openrouter"):
    """Stub gateway model + runtime resolution to a known state."""
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda cfg=None: model_from_config)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": provider,
            "api_key": "x",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
        },
    )


def test_normal_turn_caches_last_resolved_model(monkeypatch):
    _patch_resolution(monkeypatch, model_from_config="deepseek/deepseek-v4-flash")
    runner = _make_runner()
    sk = "agent:main:discord:dm:123"

    model, _ = runner._resolve_session_agent_runtime(session_key=sk, user_config={"model": {"default": "x"}})

    assert model == "deepseek/deepseek-v4-flash"
    # Cached per-session AND process-wide for first-seen-session recovery.
    assert runner._last_resolved_model[sk] == "deepseek/deepseek-v4-flash"
    assert runner._last_resolved_model["*"] == "deepseek/deepseek-v4-flash"


def test_empty_model_recovers_session_last_good(monkeypatch):
    runner = _make_runner()
    sk = "agent:main:discord:dm:123"

    # Turn 1: config has the model — cache it.
    _patch_resolution(monkeypatch, model_from_config="deepseek/deepseek-v4-flash")
    runner._resolve_session_agent_runtime(session_key=sk, user_config={"model": {"default": "x"}})

    # Turn 2: simulate the transient empty config read (the #35314 race).
    _patch_resolution(monkeypatch, model_from_config="", provider="")
    model, _ = runner._resolve_session_agent_runtime(session_key=sk, user_config={})

    assert model == "deepseek/deepseek-v4-flash", "recovery turn must reuse last-known-good, not build model=''"


def test_empty_model_new_session_recovers_global_last_good(monkeypatch):
    runner = _make_runner()

    # Prime a different session so the process-wide "*" slot is populated.
    _patch_resolution(monkeypatch, model_from_config="deepseek/deepseek-v4-flash")
    runner._resolve_session_agent_runtime(session_key="agent:main:discord:dm:111", user_config={"model": {}})

    # A brand-new session that hits an empty config read still recovers via "*".
    _patch_resolution(monkeypatch, model_from_config="", provider="")
    model, _ = runner._resolve_session_agent_runtime(session_key="agent:main:discord:dm:999", user_config={})

    assert model == "deepseek/deepseek-v4-flash"


def test_cold_start_empty_model_does_not_crash(monkeypatch):
    """No last-good anywhere + empty config → returns '' gracefully (no exception)."""
    _patch_resolution(monkeypatch, model_from_config="", provider="")
    runner = _make_runner()

    model, _ = runner._resolve_session_agent_runtime(session_key="agent:main:discord:dm:1", user_config={})

    assert model == ""


def test_bare_runner_without_cache_attr_does_not_crash(monkeypatch):
    """object.__new__ runners (test helpers / pitfall #17) lack _last_resolved_model.

    The getattr guard must tolerate the missing attribute.
    """
    _patch_resolution(monkeypatch, model_from_config="deepseek/deepseek-v4-flash")
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._session_model_overrides = {}
    runner._service_tier = None
    # Deliberately omit _last_resolved_model.

    model, _ = runner._resolve_session_agent_runtime(session_key="x", user_config={"model": {}})

    assert model == "deepseek/deepseek-v4-flash"


@pytest.mark.asyncio
async def test_async_runtime_resolution_does_not_block_on_model_override_lookup(
    monkeypatch,
):
    _patch_resolution(
        monkeypatch, model_from_config="deepseek/deepseek-v4-flash"
    )
    runner = _make_runner()
    heartbeat_ran = threading.Event()
    heartbeat_seen_while_lookup_blocked = []

    def get_model_override(_session_key: str):
        heartbeat_seen_while_lookup_blocked.append(
            heartbeat_ran.wait(timeout=0.5)
        )
        return None

    runner.session_store = SimpleNamespace(get_model_override=get_model_override)

    async def heartbeat() -> None:
        await asyncio.sleep(0)
        heartbeat_ran.set()

    resolution_task = asyncio.create_task(
        runner._resolve_session_agent_runtime_async(
            session_key="agent:main:telegram:dm:123:456",
            user_config={"model": {"default": "x"}},
        )
    )
    heartbeat_task = asyncio.create_task(heartbeat())
    (model, _runtime), _ = await asyncio.gather(
        resolution_task, heartbeat_task
    )

    assert model == "deepseek/deepseek-v4-flash"
    assert heartbeat_seen_while_lookup_blocked == [True]


@pytest.mark.asyncio
async def test_async_runtime_rehydrate_does_not_overwrite_live_model_override(
    monkeypatch,
):
    _patch_resolution(
        monkeypatch, model_from_config="deepseek/deepseek-v4-flash"
    )
    runner = _make_runner()
    lookup_started = threading.Event()
    allow_lookup_to_finish = threading.Event()
    session_key = "agent:main:telegram:dm:123:456"

    def get_model_override(_session_key: str):
        lookup_started.set()
        assert allow_lookup_to_finish.wait(timeout=2)
        return {"model": "persisted-model", "provider": None, "base_url": None}

    runner.session_store = SimpleNamespace(get_model_override=get_model_override)
    resolution_task = asyncio.create_task(
        runner._resolve_session_agent_runtime_async(
            session_key=session_key,
            user_config={"model": {"default": "x"}},
        )
    )
    assert await asyncio.to_thread(lookup_started.wait, 1)

    # A live /model update that lands while the persisted lookup is in flight
    # must remain authoritative when the worker thread returns.
    runner._session_model_overrides[session_key] = {
        "model": "live-model",
        "provider": None,
        "base_url": None,
        "api_key": "live-key",
    }
    allow_lookup_to_finish.set()

    model, _runtime = await resolution_task

    assert model == "live-model"
    assert runner._session_model_overrides[session_key]["model"] == "live-model"


@pytest.mark.asyncio
async def test_async_runtime_rehydrate_drops_stale_override_after_session_boundary(
    monkeypatch,
):
    _patch_resolution(
        monkeypatch, model_from_config="deepseek/deepseek-v4-flash"
    )
    runner = _make_runner()
    lookup_started = threading.Event()
    allow_lookup_to_finish = threading.Event()
    session_key = "agent:main:telegram:dm:123:456"

    def get_model_override(_session_key: str):
        lookup_started.set()
        assert allow_lookup_to_finish.wait(timeout=2)
        return {"model": "stale-model", "provider": None, "base_url": None}

    runner.session_store = SimpleNamespace(get_model_override=get_model_override)
    resolution_task = asyncio.create_task(
        runner._resolve_session_agent_runtime_async(
            session_key=session_key,
            user_config={"model": {"default": "x"}},
        )
    )
    assert await asyncio.to_thread(lookup_started.wait, 1)

    # /new and every other conversation boundary clear this state while the
    # persisted read can still be in flight.
    runner._clear_conversation_scope(session_key, reason="test_reset")
    allow_lookup_to_finish.set()

    model, _runtime = await resolution_task

    assert model == "deepseek/deepseek-v4-flash"
    assert session_key not in runner._session_model_overrides


@pytest.mark.asyncio
async def test_cancelled_async_rehydrate_cannot_resurrect_boundary_state(monkeypatch):
    _patch_resolution(
        monkeypatch, model_from_config="deepseek/deepseek-v4-flash"
    )
    runner = _make_runner()
    lookup_started = threading.Event()
    allow_lookup_to_finish = threading.Event()
    lookup_finished = threading.Event()
    session_key = "agent:main:telegram:dm:123:456"

    def get_model_override(_session_key: str):
        lookup_started.set()
        assert allow_lookup_to_finish.wait(timeout=2)
        lookup_finished.set()
        return {"model": "stale-model", "provider": None, "base_url": None}

    runner.session_store = SimpleNamespace(get_model_override=get_model_override)
    resolution_task = asyncio.create_task(
        runner._resolve_session_agent_runtime_async(
            session_key=session_key,
            user_config={"model": {"default": "x"}},
        )
    )
    assert await asyncio.to_thread(lookup_started.wait, 1)

    resolution_task.cancel()
    runner._clear_conversation_scope(session_key, reason="test_reset")
    allow_lookup_to_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await resolution_task
    assert await asyncio.to_thread(lookup_finished.wait, 1)
    # Let the completed worker publish any shared-state mutation before checking.
    await asyncio.sleep(0)

    assert session_key not in runner._session_model_overrides


# ── _has_pending_fallback gate ──────────────────────────────────────────────


def _bare_agent():
    import run_agent

    return object.__new__(run_agent.AIAgent)


def test_has_pending_fallback_empty_chain():
    agent = _bare_agent()
    agent._fallback_chain = []
    agent._fallback_index = 0
    assert agent._has_pending_fallback() is False


def test_has_pending_fallback_with_chain():
    agent = _bare_agent()
    agent._fallback_chain = [{"provider": "openai", "model": "gpt-5"}]
    agent._fallback_index = 0
    assert agent._has_pending_fallback() is True


def test_has_pending_fallback_exhausted_chain():
    agent = _bare_agent()
    agent._fallback_chain = [{"provider": "openai", "model": "gpt-5"}]
    agent._fallback_index = 1
    assert agent._has_pending_fallback() is False


def test_has_pending_fallback_missing_attrs():
    """Bare agent with no fallback attributes set must default to False, not crash."""
    agent = _bare_agent()
    assert agent._has_pending_fallback() is False
