"""Strict-TDD tests for gateway process-local lifecycle counters.

These tests pin the ``GatewayLifecycleCounters`` API: a small thread-safe,
process-local counter with no SQLite / files / outbound telemetry / network /
message or session identifiers. It reuses the existing local shared-metrics
opt-in and feeds ``/agents --diagnostics`` (sorted
JSON snapshot) and is wired into ``GatewayRunner`` via ``_lifecycle_counters``.

Privacy contract: the snapshot MUST NOT contain session keys, chat ids, user
ids, file paths, message contents, or any other user-identifying data — only
aggregate counts and class/type names.
"""

import copy
import json
from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# GatewayLifecycleCounters — core API (RED first, then GREEN)
# ---------------------------------------------------------------------------


def test_disabled_by_default_is_noop():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters()
    ctr.record_cache_event("hit")
    ctr.record_eviction("idle")
    snap = ctr.snapshot()
    assert snap["enabled"] is False
    assert snap["monotonic_seq"] == 0
    assert snap["agent_cache"]["events"] == {}


def test_from_config_reuses_existing_shared_metrics_opt_in(monkeypatch):
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    monkeypatch.setattr(
        "hermes_cli.config.read_raw_config_readonly",
        lambda: {"telemetry": {"shared_metrics": {"enabled": True}}},
    )
    enabled = GatewayLifecycleCounters.from_config()
    enabled.record_cache_event("hit")
    assert enabled.snapshot()["enabled"] is True
    assert enabled.snapshot()["agent_cache"]["events"]["hit"] == 1

    monkeypatch.setattr(
        "hermes_cli.config.read_raw_config_readonly",
        lambda: {"telemetry": {"shared_metrics": {"enabled": False}}},
    )
    disabled = GatewayLifecycleCounters.from_config()
    disabled.record_cache_event("hit")
    assert disabled.snapshot()["enabled"] is False
    assert disabled.snapshot()["agent_cache"]["events"] == {}


def test_snapshot_is_a_deep_copy_and_thread_safe():
    """``snapshot()`` returns an isolated copy; mutating it cannot corrupt the
    live counter, and concurrent increments + snapshots never raise."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    ctr.incr("agent_cache.events", "hit")
    ctr.incr("agent_cache.events", "hit")
    ctr.incr("agent_cache.events", "miss_absent")

    snap = ctr.snapshot()
    # Mutating the returned snapshot must not affect the live counter.
    snap["agent_cache"]["events"]["hit"] = 999
    again = ctr.snapshot()
    assert again["agent_cache"]["events"]["hit"] == 2

    # Concurrent increments + snapshots must be safe (no exception / no lost
    # updates beyond the inherent race on the *read* — totals must be >= the
    # number of successful increments).
    errors = []

    def _hammer():
        try:
            for _ in range(500):
                ctr.incr("agent_cache.events", "hit")
                ctr.snapshot()
        except Exception as exc:  # pragma: no cover - asserted via errors list
            errors.append(exc)

    threads = [threading.Thread(target=_hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    final = ctr.snapshot()
    # 2 (initial) + 8 threads * 500 = 4002
    assert final["agent_cache"]["events"]["hit"] == 4002


def test_snapshot_top_level_schema_and_monotonic_seq():
    """Snapshot exposes ``schema_version`` and a monotonically increasing
    ``monotonic_seq``."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    s0 = ctr.snapshot()
    ctr.record_cache_event("hit")
    s1 = ctr.snapshot()
    assert isinstance(s0["schema_version"], (int, str))
    assert s1["monotonic_seq"] > s0["monotonic_seq"]


def test_snapshot_privacy_no_session_chat_user_path_or_message_contents():
    """The JSON snapshot string MUST NOT leak session keys, chat ids, user
    ids, file paths, or message/session identifiers — only aggregate counts
    and class/type names."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    # Exercise a representative set of recording paths with deliberately
    # sensitive-looking labels to prove they never reach the snapshot.
    ctr.record_cache_event("hit", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_cache_event("miss_absent", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_cache_event(
        "miss_signature", session_key="telegram:999:dm:user@evil.com"
    )
    ctr.record_cache_event("created", session_key="telegram:999:dm:user@evil.com")
    ctr.record_eviction("explicit", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_eviction("cap", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_eviction("idle", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_eviction("stale_self_heal", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_eviction("cross_process", session_key="SECRET_SESSION_KEY_abc123")
    ctr.record_soft_release(success=True)
    ctr.record_soft_release(success=False)
    ctr.record_hard_cleanup(attempted=True)
    ctr.record_agent_close(attempted=True, success=True)
    ctr.record_agent_close(attempted=True, success=False)
    ctr.record_shutdown_cache_clear(cleared=3)

    blob = json.dumps(ctr.snapshot(), sort_keys=True)
    forbidden = [
        "SECRET_SESSION_KEY_abc123",
        "999",
        "evil.com",
        "user@",
        "telegram:",
        "/",
        "abc123",
    ]
    for needle in forbidden:
        assert needle not in blob, (
            f"PRIVACY LEAK: snapshot JSON contains {needle!r}: {blob}"
        )


def test_record_cache_event_hit_miss_absent_miss_signature_created():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    ctr.record_cache_event("hit")
    ctr.record_cache_event("hit")
    ctr.record_cache_event("miss_absent")
    ctr.record_cache_event("miss_signature")
    ctr.record_cache_event("created")
    snap = ctr.snapshot()
    ev = snap["agent_cache"]["events"]
    assert ev["hit"] == 2
    assert ev["miss_absent"] == 1
    assert ev["miss_signature"] == 1
    assert ev["created"] == 1


def test_record_eviction_explicit_cap_idle_stale_self_heal_cross_process():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    for kind in ("explicit", "cap", "idle", "stale_self_heal", "cross_process"):
        ctr.record_eviction(kind)
        ctr.record_eviction(kind)
    snap = ctr.snapshot()
    ev = snap["agent_cache"]["events"]
    for kind in ("explicit", "cap", "idle", "stale_self_heal", "cross_process"):
        assert ev[f"evict_{kind}"] == 2, f"{kind} not counted"


def test_record_soft_release_success_and_failure():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    ctr.record_soft_release(success=True)
    ctr.record_soft_release(success=True)
    ctr.record_soft_release(success=False)
    snap = ctr.snapshot()
    assert snap["agent_cache"]["events"]["soft_release_calls"] == 3
    assert snap["agent_cache"]["events"]["soft_release_success"] == 2
    assert snap["agent_cache"]["events"]["soft_release_failure"] == 1


def test_record_hard_cleanup_and_agent_close():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    ctr.record_hard_cleanup(attempted=True)
    ctr.record_agent_close(attempted=True, success=True)
    ctr.record_agent_close(attempted=True, success=False)
    snap = ctr.snapshot()
    ev = snap["agent_cache"]["events"]
    assert ev["hard_cleanup_calls"] == 1
    assert ev["agent_close_attempt"] == 2
    assert ev["agent_close_success"] == 1
    assert ev["agent_close_failure"] == 1


def test_record_shutdown_cache_clear():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    ctr.record_shutdown_cache_clear(cleared=5)
    ctr.record_shutdown_cache_clear(cleared=2)
    snap = ctr.snapshot()
    assert snap["agent_cache"]["events"]["shutdown_cache_clear_count"] == 2
    # total cleared is also surfaced
    assert snap["agent_cache"]["events"]["shutdown_cache_clear_total"] == 7


def test_record_context_engine_type_uses_class_name_only():
    """Context-engine cache counts use ONLY the class/type name — no ids."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)

    class _MyContextEngine:
        pass

    ctr.record_context_engine(_MyContextEngine())
    ctr.record_context_engine(_MyContextEngine())
    ctr.record_context_engine(MagicMock())  # MagicMock -> "MagicMock"
    snap = ctr.snapshot()
    by_type = snap["context_engines"]["by_type"]
    assert by_type.get("_MyContextEngine") == 2
    assert by_type.get("MagicMock") == 1
    assert snap["context_engines"]["cached_total"] == 3


def test_set_agent_cache_size_and_idle_ttl():
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    ctr.set_agent_cache_size(entries=7, max_size=128, idle_ttl_seconds=3600.0)
    snap = ctr.snapshot()
    assert snap["agent_cache"]["entries"] == 7
    assert snap["agent_cache"]["max_size"] == 128
    assert snap["agent_cache"]["idle_ttl_seconds"] == 3600.0


def test_object_dunder_new_compatibility():
    """``getattr``-safe no-op helpers: a GatewayRunner built via
    ``object.__new__`` (test fixtures) must not crash when the instrumented
    paths call ``self._lifecycle_counters.record_*`` even though
    ``__init__`` never ran.

    The counter instance itself must also be constructable without args.
    """
    from gateway.lifecycle_counters import GatewayLifecycleCounters
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    # Provide the counter so the getattr-safe path is exercised.
    runner._lifecycle_counters = GatewayLifecycleCounters(enabled=True)
    # These must not raise even though the runner has no real state.
    runner._lifecycle_counters.record_cache_event("hit")
    runner._lifecycle_counters.record_eviction("explicit")
    runner._lifecycle_counters.record_soft_release(success=True)
    runner._lifecycle_counters.record_hard_cleanup(attempted=True)
    runner._lifecycle_counters.record_agent_close(attempted=True, success=True)
    runner._lifecycle_counters.record_shutdown_cache_clear(cleared=1)
    snap = runner._lifecycle_counters.snapshot()
    assert snap["agent_cache"]["events"]["hit"] == 1


# ---------------------------------------------------------------------------
# /agents --diagnostics JSON output
# ---------------------------------------------------------------------------


class _DiagEvent:
    """Minimal MessageEvent stand-in carrying command args."""

    def __init__(self, text: str = "/agents --diagnostics"):
        self.text = text
        self.source = MagicMock()

    def get_command_args(self) -> str:
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    def is_command(self) -> bool:
        return True


def _make_diag_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._background_tasks = set()
    runner._session_key_for_source = lambda source: "agent:main:test:dm:1"
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._executor = None
    runner._executor_closing = False
    runner._lifecycle_counters = None  # force getattr-safe path in handler
    return runner


@pytest.mark.asyncio
async def test_agents_diagnostics_returns_sorted_json_with_snapshot_fields():
    """``/agents --diagnostics`` returns a sorted JSON string containing the
    snapshot fields (schema_version, monotonic_seq, agent_cache, agents,
    context_engines, executor, threads, tasks)."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    runner = _make_diag_runner()
    runner._lifecycle_counters = GatewayLifecycleCounters(enabled=True)
    runner._agent_cache["one"] = (object(), "sig")
    runner._agent_cache["two"] = (object(), "sig")
    runner._lifecycle_counters.record_cache_event("hit")

    out = await runner._handle_agents_command(_DiagEvent("/agents --diagnostics"))

    # Must be valid JSON.
    data = json.loads(out)
    # Must contain the documented top-level snapshot fields.
    for field in (
        "schema_version",
        "monotonic_seq",
        "agent_cache",
        "agents",
        "context_engines",
        "executor",
        "threads",
        "tasks",
    ):
        assert field in data, f"missing snapshot field: {field}"
    # agent_cache.events.hit must reflect the recorded hit.
    assert data["agent_cache"]["events"]["hit"] == 1
    # agent_cache.entries must reflect the recorded size.
    assert data["agent_cache"]["entries"] == 2
    # The raw string must be sorted (stable, deterministic).
    assert out == json.dumps(data, sort_keys=True)


@pytest.mark.asyncio
async def test_agents_diagnostics_privacy_no_session_chat_key_path():
    """The diagnostics JSON MUST NOT contain session keys, chat ids, user ids,
    or file paths — only aggregate counts and type names."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    runner = _make_diag_runner()
    runner._lifecycle_counters = GatewayLifecycleCounters(enabled=True)
    runner._lifecycle_counters.record_cache_event(
        "hit", session_key="telegram:12345:dm:secretuser@evil.com"
    )
    runner._lifecycle_counters.record_eviction(
        "explicit", session_key="/home/ubuntu/secret/path"
    )

    out = await runner._handle_agents_command(_DiagEvent("/agents --diagnostics"))
    blob = out
    forbidden = ["12345", "secretuser", "evil.com", "/home/ubuntu", "secret/path"]
    for needle in forbidden:
        assert needle not in blob, f"PRIVACY LEAK in /agents --diagnostics: {needle!r}"


@pytest.mark.asyncio
async def test_ordinary_agents_command_unchanged_without_diagnostics():
    """Ordinary ``/agents`` (no ``--diagnostics``) returns the same
    human-readable rendering as before — the diagnostics JSON only fires for
    the exact ``--diagnostics`` arg."""
    runner = _make_diag_runner()
    # No lifecycle counters wired — ordinary path must not depend on it.
    out = await runner._handle_agents_command(_DiagEvent("/agents"))
    # Must NOT be JSON; must be the human-readable agents listing.
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    # Header is present.
    assert "agents" in out.lower() or out.strip() != ""


# ---------------------------------------------------------------------------
# Executor snapshot (best-effort)
# ---------------------------------------------------------------------------


def test_executor_snapshot_present_shutdown_threads_queued():
    """The executor snapshot reports present/shutdown/threads/queued
    best-effort without crashing on a missing executor."""
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    ctr = GatewayLifecycleCounters(enabled=True)
    # No executor set → best-effort defaults.
    snap = ctr.snapshot()
    ex = snap["executor"]
    assert "present" in ex
    assert "shutdown" in ex
    assert "threads" in ex
    assert "queued" in ex
    assert ex["present"] is False

    # With a real-ish executor.
    import concurrent.futures as cf

    pool = cf.ThreadPoolExecutor(max_workers=2)
    try:
        ctr.set_executor(pool)
        snap2 = ctr.snapshot()
        ex2 = snap2["executor"]
        assert ex2["present"] is True
        assert ex2["shutdown"] is False
        assert isinstance(ex2["threads"], int)
        assert isinstance(ex2["queued"], int)
    finally:
        pool.shutdown()


# ---------------------------------------------------------------------------
# Wire-up: GatewayRunner.__init__ sets _lifecycle_counters
# ---------------------------------------------------------------------------


def test_gateway_runner_init_sets_lifecycle_counters():
    """A fully-constructed GatewayRunner must have a non-None
    ``_lifecycle_counters`` that is a GatewayLifecycleCounters."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from gateway.lifecycle_counters import GatewayLifecycleCounters

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg = GatewayConfig(
            sessions_dir=Path(tmp) / "sessions",
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=False)},
        )
        runner = GatewayRunner(config=cfg)
        assert isinstance(runner._lifecycle_counters, GatewayLifecycleCounters)
        # Snapshot is callable and well-formed.
        snap = runner._lifecycle_counters.snapshot()
        assert "schema_version" in snap
