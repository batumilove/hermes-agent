"""Tests for the async-memory Honcho improvements.

Covers:
  - write_frequency parsing (async / turn / session / int)
  - resolve_session_name with session_title
  - HonchoSessionManager.save() routing per write_frequency
  - async writer thread lifecycle and retry
  - flush_all() drains pending messages
  - shutdown() joins the thread
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from plugins.memory.honcho.client import HonchoClientConfig
from plugins.memory.honcho.session import (
    HonchoSession,
    HonchoSessionManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(**kwargs) -> HonchoSession:
    return HonchoSession(
        key=kwargs.get("key", "cli:test"),
        user_peer_id=kwargs.get("user_peer_id", "eri"),
        assistant_peer_id=kwargs.get("assistant_peer_id", "hermes"),
        honcho_session_id=kwargs.get("honcho_session_id", "cli-test"),
        messages=kwargs.get("messages", []),
    )


def _make_manager(write_frequency="turn") -> HonchoSessionManager:
    cfg = HonchoClientConfig(
        write_frequency=write_frequency,
        api_key="test-key",
        enabled=True,
    )
    mgr = HonchoSessionManager(config=cfg)
    mgr._honcho = MagicMock()
    return mgr


# ---------------------------------------------------------------------------
# write_frequency parsing from config file
# ---------------------------------------------------------------------------

class TestWriteFrequencyParsing:
    def test_string_async(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"apiKey": "k", "writeFrequency": "async"}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == "async"

    def test_string_turn(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"apiKey": "k", "writeFrequency": "turn"}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == "turn"

    def test_string_session(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"apiKey": "k", "writeFrequency": "session"}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == "session"

    def test_integer_frequency(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"apiKey": "k", "writeFrequency": 5}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == 5

    def test_integer_string_coerced(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"apiKey": "k", "writeFrequency": "3"}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == 3

    def test_host_block_overrides_root(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "apiKey": "k",
            "writeFrequency": "turn",
            "hosts": {"hermes": {"writeFrequency": "session"}},
        }))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == "session"

    def test_defaults_to_async(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"apiKey": "k"}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.write_frequency == "async"


# ---------------------------------------------------------------------------
# resolve_session_name with session_title
# ---------------------------------------------------------------------------

class TestResolveSessionNameTitle:
    def test_manual_override_beats_title(self):
        cfg = HonchoClientConfig(sessions={"/my/project": "manual-name"})
        result = cfg.resolve_session_name("/my/project", session_title="the-title")
        assert result == "manual-name"

    def test_title_beats_dirname(self):
        cfg = HonchoClientConfig()
        result = cfg.resolve_session_name("/some/dir", session_title="my-project")
        assert result == "my-project"

    def test_title_with_peer_prefix(self):
        cfg = HonchoClientConfig(peer_name="eri", session_peer_prefix=True)
        result = cfg.resolve_session_name("/some/dir", session_title="aeris")
        assert result == "eri-aeris"

    def test_title_sanitized(self):
        cfg = HonchoClientConfig()
        result = cfg.resolve_session_name("/some/dir", session_title="my project/name!")
        # trailing dashes stripped by .strip('-')
        assert result == "my-project-name"

    def test_title_all_invalid_chars_falls_back_to_dirname(self):
        cfg = HonchoClientConfig()
        result = cfg.resolve_session_name("/some/dir", session_title="!!! ###")
        # sanitized to empty → falls back to dirname
        assert result == "dir"

    def test_none_title_falls_back_to_dirname(self):
        cfg = HonchoClientConfig()
        result = cfg.resolve_session_name("/some/dir", session_title=None)
        assert result == "dir"

    def test_empty_title_falls_back_to_dirname(self):
        cfg = HonchoClientConfig()
        result = cfg.resolve_session_name("/some/dir", session_title="")
        assert result == "dir"

    def test_per_session_uses_session_id(self):
        cfg = HonchoClientConfig(session_strategy="per-session")
        result = cfg.resolve_session_name("/some/dir", session_id="20260309_175514_9797dd")
        assert result == "20260309_175514_9797dd"

    def test_per_session_with_peer_prefix(self):
        cfg = HonchoClientConfig(session_strategy="per-session", peer_name="eri", session_peer_prefix=True)
        result = cfg.resolve_session_name("/some/dir", session_id="20260309_175514_9797dd")
        assert result == "eri-20260309_175514_9797dd"

    def test_per_session_no_id_falls_back_to_dirname(self):
        cfg = HonchoClientConfig(session_strategy="per-session")
        result = cfg.resolve_session_name("/some/dir", session_id=None)
        assert result == "dir"

    def test_per_session_id_beats_title(self):
        # per-session: the run's session_id is authoritative; an (auto-)generated
        # title must NOT remap a live conversation onto a second Honcho session.
        cfg = HonchoClientConfig(session_strategy="per-session")
        result = cfg.resolve_session_name("/some/dir", session_title="my-title", session_id="20260309_175514_9797dd")
        assert result == "20260309_175514_9797dd"

    def test_per_session_id_beats_manual_map(self):
        # per-session: session_id also wins over a stale cwd map entry (e.g. the
        # desktop launching from a mapped home dir).
        cfg = HonchoClientConfig(session_strategy="per-session", sessions={"/some/dir": "pinned"})
        result = cfg.resolve_session_name("/some/dir", session_id="20260309_175514_9797dd")
        assert result == "20260309_175514_9797dd"

    def test_title_still_applies_for_non_per_session(self):
        # Outside per-session, /title still names the Honcho session.
        cfg = HonchoClientConfig(session_strategy="per-directory")
        result = cfg.resolve_session_name("/some/dir", session_title="my-title", session_id="20260309_175514_9797dd")
        assert result == "my-title"

    def test_gateway_key_beats_per_session_id(self):
        # Gateways keep per-chat isolation even in per-session.
        cfg = HonchoClientConfig(session_strategy="per-session")
        result = cfg.resolve_session_name("/some/dir", gateway_session_key="agent:main:telegram:dm:42", session_id="20260309_175514_9797dd")
        assert result == "agent-main-telegram-dm-42"

    def test_global_strategy_returns_workspace(self):
        cfg = HonchoClientConfig(session_strategy="global", workspace_id="my-workspace")
        result = cfg.resolve_session_name("/some/dir")
        assert result == "my-workspace"


# ---------------------------------------------------------------------------
# save() routing per write_frequency
# ---------------------------------------------------------------------------

class TestSaveRouting:
    def _make_session_with_message(self, mgr=None):
        sess = _make_session()
        sess.add_message("user", "hello")
        sess.add_message("assistant", "hi")
        if mgr:
            mgr._cache[sess.key] = sess
        return sess

    def test_turn_flushes_immediately(self):
        mgr = _make_manager(write_frequency="turn")
        sess = self._make_session_with_message(mgr)
        with patch.object(mgr, "_flush_session") as mock_flush:
            mgr.save(sess)
            mock_flush.assert_called_once_with(sess)

    def test_session_mode_does_not_flush(self):
        mgr = _make_manager(write_frequency="session")
        sess = self._make_session_with_message(mgr)
        with patch.object(mgr, "_flush_session") as mock_flush:
            mgr.save(sess)
            mock_flush.assert_not_called()

    def test_async_mode_enqueues(self):
        mgr = _make_manager(write_frequency="async")
        sess = self._make_session_with_message(mgr)
        with patch.object(mgr, "_flush_session") as mock_flush:
            mgr.save(sess)
            # flush_session should NOT be called synchronously
            mock_flush.assert_not_called()
        assert not mgr._async_queue.empty()

    def test_int_frequency_flushes_on_nth_turn(self):
        mgr = _make_manager(write_frequency=3)
        sess = self._make_session_with_message(mgr)
        with patch.object(mgr, "_flush_session") as mock_flush:
            mgr.save(sess)  # turn 1
            mgr.save(sess)  # turn 2
            assert mock_flush.call_count == 0
            mgr.save(sess)  # turn 3
            assert mock_flush.call_count == 1

    def test_int_frequency_skips_other_turns(self):
        mgr = _make_manager(write_frequency=5)
        sess = self._make_session_with_message(mgr)
        with patch.object(mgr, "_flush_session") as mock_flush:
            for _ in range(4):
                mgr.save(sess)
            assert mock_flush.call_count == 0
            mgr.save(sess)  # turn 5
            assert mock_flush.call_count == 1


# ---------------------------------------------------------------------------
# flush_all()
# ---------------------------------------------------------------------------

class TestFlushAll:
    def test_flushes_all_cached_sessions(self):
        mgr = _make_manager(write_frequency="session")
        s1 = _make_session(key="s1", honcho_session_id="s1")
        s2 = _make_session(key="s2", honcho_session_id="s2")
        s1.add_message("user", "a")
        s2.add_message("user", "b")
        mgr._cache = {"s1": s1, "s2": s2}

        with patch.object(mgr, "_flush_session") as mock_flush:
            mgr.flush_all()
            assert mock_flush.call_count == 2

    def test_flush_all_drains_async_queue(self):
        mgr = _make_manager(write_frequency="async")
        sess = _make_session()
        sess.add_message("user", "pending")

        with patch.object(mgr, "_flush_session") as mock_flush:
            # Put the item AFTER the mock is installed so the background
            # writer thread (if it dequeues before flush_all) still hits
            # the mock rather than the real _flush_session.
            mgr._async_queue.put(sess)
            mgr.flush_all()
            # Called at least once for the queued item
            assert mock_flush.call_count >= 1

    def test_flush_all_tolerates_errors(self):
        mgr = _make_manager(write_frequency="session")
        sess = _make_session()
        mgr._cache = {"key": sess}
        with patch.object(mgr, "_flush_session", side_effect=RuntimeError("oops")):
            # Should not raise
            mgr.flush_all()


# ---------------------------------------------------------------------------
# async writer thread lifecycle
# ---------------------------------------------------------------------------

class TestAsyncWriterThread:
    def test_thread_started_on_async_mode(self):
        mgr = _make_manager(write_frequency="async")
        assert mgr._async_thread is not None
        assert mgr._async_thread.is_alive()
        mgr.shutdown()

    def test_no_thread_for_turn_mode(self):
        mgr = _make_manager(write_frequency="turn")
        assert mgr._async_thread is None
        assert mgr._async_queue is None

    def test_shutdown_joins_thread(self):
        mgr = _make_manager(write_frequency="async")
        assert mgr._async_thread.is_alive()
        mgr.shutdown()
        assert not mgr._async_thread.is_alive()

    def test_async_writer_calls_flush(self):
        mgr = _make_manager(write_frequency="async")
        sess = _make_session()
        sess.add_message("user", "async msg")

        flushed = []
        flushed_event = threading.Event()

        def capture(session):
            flushed.append(session)
            flushed_event.set()
            return True

        mgr._flush_session = capture
        mgr._async_queue.put(sess)
        assert flushed_event.wait(timeout=10), "async writer never flushed"

        mgr.shutdown()
        assert len(flushed) == 1
        assert flushed[0] is sess

    def test_shutdown_sentinel_stops_loop(self):
        mgr = _make_manager(write_frequency="async")
        thread = mgr._async_thread
        mgr.shutdown()
        thread.join(timeout=10)
        assert not thread.is_alive()

    def test_shutdown_can_retry_after_flush_failure(self):
        """A failed flush must not strand a live writer behind an idempotence flag."""
        mgr = _make_manager(write_frequency="async")
        thread = mgr._async_thread

        with patch.object(
            mgr,
            "flush_all",
            side_effect=[RuntimeError("flush failed"), None],
        ) as flush_all:
            with pytest.raises(RuntimeError, match="flush failed"):
                mgr.shutdown()
            mgr.shutdown()

        assert flush_all.call_count == 2
        assert mgr._shutdown_called is True
        assert not thread.is_alive()


# ---------------------------------------------------------------------------
# async retry on failure
# ---------------------------------------------------------------------------

class TestAsyncWriterRetry:
    def test_retries_once_on_failure(self):
        mgr = _make_manager(write_frequency="async")
        sess = _make_session()
        sess.add_message("user", "msg")

        call_count = [0]
        retry_done = threading.Event()

        def flaky_flush(session):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("network blip")
            retry_done.set()
            return True

        mgr._flush_session = flaky_flush

        with patch("time.sleep"):  # skip the 2s sleep in retry
            mgr._async_queue.put(sess)
            assert retry_done.wait(timeout=10), "async writer never retried"

        mgr.shutdown()
        assert call_count[0] == 2

    def test_drops_after_two_failures(self):
        mgr = _make_manager(write_frequency="async")
        sess = _make_session()
        sess.add_message("user", "msg")

        call_count = [0]
        retry_done = threading.Event()

        def always_fail(session):
            call_count[0] += 1
            if call_count[0] >= 2:
                retry_done.set()
            raise RuntimeError("always broken")

        mgr._flush_session = always_fail

        with patch("time.sleep"):
            mgr._async_queue.put(sess)
            assert retry_done.wait(timeout=10), "async writer never retried"

        mgr.shutdown()
        # Should have tried exactly twice (initial + one retry) and not crashed
        assert call_count[0] == 2
        assert not mgr._async_thread.is_alive()

    def test_retries_when_flush_reports_failure(self):
        mgr = _make_manager(write_frequency="async")
        sess = _make_session()
        sess.add_message("user", "msg")

        call_count = [0]
        retry_done = threading.Event()

        def fail_then_succeed(session):
            call_count[0] += 1
            if call_count[0] >= 2:
                retry_done.set()
            return call_count[0] > 1

        mgr._flush_session = fail_then_succeed

        with patch("time.sleep"):
            mgr._async_queue.put(sess)
            assert retry_done.wait(timeout=10), "async writer never retried"

        mgr.shutdown()
        assert call_count[0] == 2


class TestMemoryFileMigrationTargets:
    def test_soul_upload_targets_ai_peer(self, tmp_path):
        mgr = _make_manager(write_frequency="turn")
        session = _make_session(
            key="cli:test",
            user_peer_id="custom-user",
            assistant_peer_id="custom-ai",
            honcho_session_id="cli-test",
        )
        mgr._cache[session.key] = session

        user_peer = MagicMock(name="user-peer")
        ai_peer = MagicMock(name="ai-peer")
        mgr._peers_cache[session.user_peer_id] = user_peer
        mgr._peers_cache[session.assistant_peer_id] = ai_peer

        honcho_session = MagicMock()
        mgr._sessions_cache[session.honcho_session_id] = honcho_session

        (tmp_path / "MEMORY.md").write_text("memory facts", encoding="utf-8")
        (tmp_path / "USER.md").write_text("user profile", encoding="utf-8")
        (tmp_path / "SOUL.md").write_text("ai identity", encoding="utf-8")

        uploaded = mgr.migrate_memory_files(session.key, str(tmp_path))

        assert uploaded is True
        assert honcho_session.upload_file.call_count == 3

        peer_by_upload_name = {}
        for call_args in honcho_session.upload_file.call_args_list:
            payload = call_args.kwargs["file"]
            peer_by_upload_name[payload[0]] = call_args.kwargs["peer"]

        assert peer_by_upload_name["consolidated_memory.md"] is user_peer
        assert peer_by_upload_name["user_profile.md"] is user_peer
        assert peer_by_upload_name["agent_soul.md"] is ai_peer


# ---------------------------------------------------------------------------
# HonchoClientConfig dataclass defaults for new fields
# ---------------------------------------------------------------------------

class TestNewConfigFieldDefaults:
    def test_write_frequency_default(self):
        cfg = HonchoClientConfig()
        assert cfg.write_frequency == "async"

    def test_write_frequency_set(self):
        cfg = HonchoClientConfig(write_frequency="turn")
        assert cfg.write_frequency == "turn"


class TestPrefetchCacheAccessors:
    def test_set_and_pop_context_result(self):
        mgr = _make_manager(write_frequency="turn")
        payload = {"representation": "Known user", "card": "prefers concise replies"}

        mgr.set_context_result("cli:test", payload)

        assert mgr.pop_context_result("cli:test") == payload
        assert mgr.pop_context_result("cli:test") == {}


# ---------------------------------------------------------------------------
# HonchoMemoryProvider lifecycle (P0-1)
# ---------------------------------------------------------------------------

class TestAsyncWriterLifecycle:
    def _make_provider(self, manager=None):
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider
        provider = HonchoMemoryProvider()
        provider._manager = manager or _make_manager(write_frequency="async")
        provider._session_initialized = True
        return provider

    def test_provider_shutdown_joins_async_writer_thread(self):
        provider = self._make_provider()
        mgr = provider._manager
        assert mgr._async_thread is not None
        assert mgr._async_thread.is_alive()
        provider.shutdown()
        assert not mgr._async_thread.is_alive(), "async writer thread should be joined after provider.shutdown()"

    def test_provider_shutdown_is_idempotent(self):
        provider = self._make_provider()
        mgr = provider._manager
        provider.shutdown()
        provider.shutdown()  # second call must not raise
        assert not mgr._async_thread.is_alive()

    def test_provider_shutdown_flushes_before_join(self):
        provider = self._make_provider()
        mgr = provider._manager
        sess = _make_session()
        sess.add_message("user", "must-flush")
        mgr._cache[sess.key] = sess

        flushed = []
        original = mgr._flush_session

        def capture(s):
            flushed.append(s)
            return original(s)

        mgr._flush_session = capture
        provider.shutdown()
        assert any(flushed), "provider.shutdown() should flush remaining messages before joining"
        assert not mgr._async_thread.is_alive()

    def test_provider_shutdown_no_manager_is_safe(self):
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider
        provider = HonchoMemoryProvider()
        provider._manager = None
        provider.shutdown()  # must not raise

    def test_provider_shutdown_uninitialized_session_is_safe(self):
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider
        provider = HonchoMemoryProvider()
        provider._manager = _make_manager(write_frequency="async")
        provider._session_initialized = False
        provider._init_thread = None
        provider.shutdown()  # must not raise

    def test_provider_does_not_lazy_initialize_after_shutdown(self):
        """Tools-mode lazy initialization remains disabled after teardown."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider.shutdown()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "after-shutdown"

        def publish_manager(*_args, **_kwargs):
            provider._manager = MagicMock()
            provider._session_initialized = True

        provider._do_session_init = MagicMock(side_effect=publish_manager)

        assert provider._ensure_session() is False
        provider._do_session_init.assert_not_called()
        assert provider._manager is None

    def test_provider_retains_manager_when_shutdown_needs_retry(self):
        """A failed manager flush keeps the retry handle until shutdown succeeds."""
        manager = _make_manager(write_frequency="async")
        provider = self._make_provider(manager)
        thread = manager._async_thread

        with patch.object(
            manager,
            "flush_all",
            side_effect=[RuntimeError("flush failed"), None],
        ) as flush_all:
            provider.shutdown()
            assert provider._manager is manager
            assert not thread.is_alive()
            provider.shutdown()

        assert flush_all.call_count == 2
        assert provider._manager is None
        assert manager._shutdown_called is True

    def test_provider_shutdown_waits_for_background_init_and_closes_manager(self):
        """An init finishing during shutdown cannot leave a new writer behind."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "race"
        manager = MagicMock()
        init_started = threading.Event()
        allow_init_finish = threading.Event()
        shutdown_done = threading.Event()

        def blocked_init(*_args, **_kwargs):
            init_started.set()
            allow_init_finish.wait(timeout=2.0)
            provider._manager = manager
            provider._session_initialized = True

        provider._do_session_init = blocked_init
        provider._start_session_init_background()
        assert init_started.wait(timeout=1.0)

        def run_shutdown():
            provider.shutdown()
            shutdown_done.set()

        shutdown_thread = threading.Thread(target=run_shutdown)
        shutdown_thread.start()
        try:
            assert not shutdown_done.wait(timeout=0.05)
        finally:
            allow_init_finish.set()
            shutdown_thread.join(timeout=2.0)
            if provider._init_thread:
                provider._init_thread.join(timeout=2.0)

        assert shutdown_done.is_set()
        manager.shutdown.assert_called_once()
        assert provider._manager is None
        # _session_initialized may be set to True by the racing init before it
        # observes shutdown; the provider teardown path is still complete and
        # safe because the manager reference has been dropped.
        assert provider._shutdown.is_set()

    def test_background_init_shutdown_retries_manager_failure(self):
        """Initializer cleanup cannot discard a manager whose first shutdown fails."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "race-retry"
        manager = MagicMock()
        manager.shutdown.side_effect = [RuntimeError("flush failed"), None]
        init_started = threading.Event()
        allow_init_finish = threading.Event()

        def blocked_init(*_args, **_kwargs):
            init_started.set()
            allow_init_finish.wait(timeout=2.0)
            provider._manager = manager
            provider._session_initialized = True

        provider._do_session_init = blocked_init
        provider._start_session_init_background()
        assert init_started.wait(timeout=1.0)

        shutdown_thread = threading.Thread(target=provider.shutdown)
        shutdown_thread.start()
        allow_init_finish.set()
        shutdown_thread.join(timeout=2.0)
        if provider._init_thread:
            provider._init_thread.join(timeout=2.0)

        assert not shutdown_thread.is_alive()
        assert manager.shutdown.call_count == 2
        assert provider._manager is None

    def test_lazy_init_racing_shutdown_cannot_publish_manager(self):
        """Shutdown wins against an already-running tools-mode lazy initializer."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "lazy-race"
        manager = MagicMock()
        init_started = threading.Event()
        allow_init_finish = threading.Event()
        shutdown_done = threading.Event()
        ensure_result = []

        def blocked_init(*_args, **_kwargs):
            init_started.set()
            allow_init_finish.wait(timeout=2.0)
            provider._manager = manager
            provider._session_initialized = True

        provider._do_session_init = blocked_init
        ensure_thread = threading.Thread(
            target=lambda: ensure_result.append(provider._ensure_session())
        )
        ensure_thread.start()
        assert init_started.wait(timeout=1.0)

        shutdown_thread = threading.Thread(
            target=lambda: (provider.shutdown(), shutdown_done.set())
        )
        shutdown_thread.start()
        try:
            assert not shutdown_done.wait(timeout=0.05)
        finally:
            allow_init_finish.set()
            ensure_thread.join(timeout=2.0)
            shutdown_thread.join(timeout=2.0)

        assert ensure_result == [False]
        assert shutdown_done.is_set()
        manager.shutdown.assert_called_once()
        assert provider._manager is None
        assert provider._session_initialized is False

    def test_failed_background_init_stops_published_writer(self):
        """A partial manager is shut down when background initialization fails."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "failed-init"
        manager = _make_manager(write_frequency="async")
        thread = manager._async_thread

        def failing_init(*_args, **_kwargs):
            provider._manager = manager
            raise RuntimeError("get_or_create failed")

        provider._do_session_init = failing_init
        provider._start_session_init_background()
        provider._init_thread.join(timeout=2.0)
        try:
            assert not thread.is_alive()
            assert provider._manager is None
        finally:
            if thread.is_alive():
                manager.shutdown()

    def test_failed_lazy_init_stops_published_writer(self):
        """Tools-mode initialization failure also closes its partial manager."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "failed-lazy-init"
        manager = _make_manager(write_frequency="async")
        thread = manager._async_thread

        def failing_init(*_args, **_kwargs):
            provider._manager = manager
            raise RuntimeError("get_or_create failed")

        provider._do_session_init = failing_init
        try:
            assert provider._ensure_session() is False
            assert not thread.is_alive()
            assert provider._manager is None
        finally:
            if thread.is_alive():
                manager.shutdown()

    def test_lazy_retry_cleans_retained_manager_before_reinitializing(self):
        """A failed cleanup handle cannot be overwritten by the next lazy init."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "lazy-cleanup-retry"
        manager_a = MagicMock()
        manager_a.shutdown.side_effect = [RuntimeError("still flushing"), None]
        manager_b = MagicMock()
        attempts = 0

        def init_sequence(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                provider._manager = manager_a
                raise RuntimeError("init failed")
            provider._manager = manager_b
            provider._session_initialized = True

        provider._do_session_init = init_sequence

        assert provider._ensure_session() is False
        assert provider._manager is manager_a
        assert provider._ensure_session() is True
        assert manager_a.shutdown.call_count == 2
        assert provider._manager is manager_b

    def test_background_retry_cleans_retained_manager_before_reinitializing(self):
        """Background retries also close a retained manager before replacement."""
        from plugins.memory.honcho.__init__ import HonchoMemoryProvider

        provider = HonchoMemoryProvider()
        provider._config = MagicMock()
        provider._lazy_init_kwargs = {}
        provider._lazy_init_session_id = "background-cleanup-retry"
        manager_a = MagicMock()
        manager_a.shutdown.side_effect = [RuntimeError("still flushing"), None]
        manager_b = MagicMock()
        attempts = 0

        def init_sequence(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                provider._manager = manager_a
                raise RuntimeError("init failed")
            provider._manager = manager_b
            provider._session_initialized = True

        provider._do_session_init = init_sequence
        provider._start_session_init_background()
        provider._init_thread.join(timeout=2.0)
        assert provider._manager is manager_a

        provider._start_session_init_background()
        provider._init_thread.join(timeout=2.0)

        assert manager_a.shutdown.call_count == 2
        assert provider._manager is manager_b

