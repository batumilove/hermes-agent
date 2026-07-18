"""Regression tests for Honcho shutdown/init race and deterministic writer teardown."""

from __future__ import annotations

import threading
import time
from queue import Queue
from types import SimpleNamespace

from plugins.memory.honcho import HonchoMemoryProvider


class _FakeHonchoConfig(SimpleNamespace):
    def resolve_session_name(self, **kwargs):
        return "test-session"


def _configured_hybrid_config() -> _FakeHonchoConfig:
    return _FakeHonchoConfig(
        enabled=True,
        api_key=None,
        base_url="http://127.0.0.1:8000",
        recall_mode="hybrid",
        init_on_session_start=False,
        injection_frequency="every-turn",
        context_cadence=1,
        dialectic_cadence=1,
        query_rewrite=False,
        first_turn_base_wait=3.0,
        first_turn_dialectic_wait=2.0,
        dialectic_depth=1,
        dialectic_depth_levels=None,
        reasoning_heuristic=True,
        reasoning_level_cap="high",
        context_tokens=None,
        message_max_chars=25000,
        session_strategy="per-directory",
    )


def _make_ready_provider() -> HonchoMemoryProvider:
    """Return a provider with a fully initialized manager/session key."""
    provider = HonchoMemoryProvider()
    provider._config = _configured_hybrid_config()
    provider._recall_mode = "hybrid"
    provider._session_key = "test-session"
    provider._session_initialized = True
    return provider


class _FakeSession:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


class _FakeManager:
    def __init__(self, flush_delay=0.0, flush_event=None):
        self._flush_delay = flush_delay
        self._flush_event = flush_event
        self._flushed = False

    def get_or_create(self, session_key):
        return _FakeSession()

    def _flush_session(self, session):
        if self._flush_event is not None:
            self._flush_event.set()
        time.sleep(self._flush_delay)
        self._flushed = True

    def flush_all(self):
        self._flushed = True


def test_shutdown_during_init_cancels_and_joins_without_deadlock():
    """shutdown() must cancel in-flight init, join the thread, and not hang."""
    provider = HonchoMemoryProvider()
    cfg = _configured_hybrid_config()
    init_started = threading.Event()
    can_finish = threading.Event()
    init_calls = []

    class SlowManager:
        def __init__(self, *args, **kwargs):
            pass

        def get_or_create(self, session_key):
            init_started.set()
            can_finish.wait(timeout=5)
            init_calls.append("get_or_create")
            return SimpleNamespace(messages=[])

    provider._config = cfg
    provider._lazy_init_kwargs = {"platform": "cli"}
    provider._lazy_init_session_id = "session-1"
    provider._session_key = "test-session"

    def slow_session_init(_self, _cfg, session_id, **kwargs):
        # The real _do_session_init checks shutdown right before/after the
        # blocking SDK work. We simulate that exact cooperative point so the test
        # verifies the actual behavior users will hit: the init thread notices
        # shutdown and exits without finishing get_or_create.
        if _self._shutdown.is_set():
            return
        _self._manager = SlowManager()
        # get_or_create is the real blocking point in _do_session_init.
        _self._manager.get_or_create("test-session")
        if _self._shutdown.is_set():
            _self._manager = None
            return
        _self._session_initialized = True

    original = HonchoMemoryProvider._do_session_init
    HonchoMemoryProvider._do_session_init = slow_session_init
    try:
        provider._start_session_init_background(wait_timeout=0.0)
        assert init_started.wait(timeout=1)

        # Trigger shutdown while init is still blocked inside get_or_create.
        start = time.perf_counter()
        provider.shutdown()
        elapsed = time.perf_counter() - start

        # The init thread should be joined and terminated without finishing the
        # blocking network work. The exact timeout is an implementation detail;
        # what matters is that shutdown returns promptly and the thread is gone.
        assert elapsed < 5.0, f"shutdown hung for {elapsed}s"
        assert not (provider._init_thread and provider._init_thread.is_alive())
        assert init_calls == []  # cancelled before get_or_create returned
    finally:
        can_finish.set()
        HonchoMemoryProvider._do_session_init = original
        init_thread = getattr(provider, "_init_thread", None)
        if init_thread:
            init_thread.join(timeout=1)


def test_post_shutdown_publication_is_rejected():
    """Once shutdown() is called, sync_turn/on_memory_write must not publish
    messages or start new threads that touch the manager."""
    provider = _make_ready_provider()
    manager = _FakeManager()
    provider._manager = manager

    provider.shutdown()

    provider.sync_turn("hello", "world")
    provider.on_memory_write("add", "user", "prefers safe shutdown")

    assert not manager._flushed
    assert provider._sync_thread is None or not provider._sync_thread.is_alive()


def test_shutdown_is_idempotent():
    """Repeated shutdown calls must be safe and return promptly."""
    provider = _make_ready_provider()
    provider._manager = _FakeManager()
    provider.shutdown()
    provider.shutdown()
    provider.shutdown()

    assert not (provider._init_thread and provider._init_thread.is_alive())


def test_async_writer_join_timeout_fails_closed():
    """If the async writer thread does not exit within its join timeout,
    shutdown must fail closed (no lingering writer that can still process
    items)."""
    from plugins.memory.honcho.session import HonchoSessionManager, _ASYNC_SHUTDOWN

    manager = HonchoSessionManager.__new__(HonchoSessionManager)
    manager._cache_lock = threading.RLock()
    manager._cache = {}
    manager._async_queue = Queue()
    manager._async_thread = threading.Thread(
        target=lambda: time.sleep(60), daemon=True, name="honcho-async-writer"
    )
    manager._async_thread.start()

    manager.shutdown()

    # The thread should be gone (daemon or joined) and the manager should
    # have dropped its queue reference so no further items can be processed.
    assert manager._async_queue is None
    assert manager._async_thread is None or not manager._async_thread.is_alive()


def test_shutdown_during_init_does_not_leak_async_writer():
    """If shutdown races with _do_session_init, the manager (and its async
    writer) must be stopped deterministically, not orphaned."""
    from unittest.mock import patch

    provider = HonchoMemoryProvider()
    cfg = _configured_hybrid_config()
    cfg.write_frequency = "async"

    init_started = threading.Event()
    can_finish = threading.Event()
    shutdown_called = threading.Event()
    created_managers: list = []

    class FakeManager:
        def __init__(self, **kwargs):
            created_managers.append(self)
            self._async_thread = threading.Thread(
                target=lambda: can_finish.wait(timeout=60),
                daemon=True,
                name="honcho-async-writer",
            )
            self._async_thread.start()

        def get_or_create(self, session_key):
            init_started.set()
            can_finish.wait(timeout=60)

        def shutdown(self):
            shutdown_called.set()
            can_finish.set()
            self._async_thread.join(timeout=1.0)

    provider._config = cfg
    provider._lazy_init_kwargs = {"platform": "cli"}
    provider._lazy_init_session_id = "session-1"
    provider._session_key = "test-session"

    def fake_get_honcho_client(cfg_arg):
        return object()

    with patch("plugins.memory.honcho.client.get_honcho_client", fake_get_honcho_client):
        with patch("plugins.memory.honcho.session.HonchoSessionManager", FakeManager):
            provider._start_session_init_background(wait_timeout=0.0)
            assert init_started.wait(timeout=1)

            provider.shutdown()

    assert not (provider._init_thread and provider._init_thread.is_alive())
    assert provider._manager is None
    assert len(created_managers) == 1
    assert shutdown_called.is_set(), "manager.shutdown() was not called during shutdown-during-init"
    assert not created_managers[0]._async_thread.is_alive()
