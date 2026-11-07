"""Per-turn evidence never converts abandoned work or caller JSON into acceptance."""
import asyncio
import contextvars
import threading

import pytest

from hermes_cli import plugins_turn_boundary as boundary
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


@pytest.fixture
def world(tmp_path, monkeypatch):
    manager = PluginManager(scope_key=str(tmp_path))
    context = PluginContext(PluginManifest(name="receipt-test"), manager)
    monkeypatch.setattr("hermes_cli.plugins._resolve_hook_callback_timeout", lambda: 0.02)
    yield manager, context
    with boundary._CUSTODY_LOCK:
        boundary._RETAINED.clear()


def test_pending_fresh_read_and_single_use(world):
    manager, context = world
    reads = []
    with boundary.turn_boundary(manager, "turn"):
        handle = context.retain_turn_evidence(lambda: reads.append("fresh") or {"valid": True})
        assert context.collect_turn_evidence(handle).status == "pending"
        assert reads == []
    result = context.collect_turn_evidence(handle)
    assert result.status == "collected" and result.value == {"valid": True}
    assert reads == ["fresh"]
    assert context.collect_turn_evidence(handle).status == "unverified"


def test_timed_out_callback_remains_unverified_after_it_finishes(world):
    manager, context = world
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    def callback(**kwargs):
        entered.set()
        try:
            assert release.wait(5)
        finally:
            ended.set()
    context.register_hook("post_llm_call", callback)
    try:
        with boundary.turn_boundary(manager, "turn") as scope:
            handle = context.retain_turn_evidence(lambda: {"valid": True})
            assert manager.invoke_hook("post_llm_call", turn_id="turn") == []
            assert entered.is_set() and scope.pending > 0
        assert context.collect_turn_evidence(handle).status == "unverified"
    finally:
        release.set()
        assert ended.wait(5)
    assert context.collect_turn_evidence(handle).status == "unverified"


def test_closed_copied_context_rejects_policy_callback(world):
    manager, context = world
    calls = []
    context.register_hook("pre_tool_call", lambda **kw: calls.append(kw))
    with boundary.turn_boundary(manager, "turn"):
        copied = contextvars.copy_context()
        handle = context.retain_turn_evidence(lambda: True)
    result = copied.run(manager.invoke_hook, "pre_tool_call", turn_id="turn")
    assert result[0]["action"] == "block" and calls == []
    assert context.collect_turn_evidence(handle).status == "unverified"


def test_async_pending_callback_then_terminal_read(world):
    manager, context = world
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def callback(**kw):
            entered.set()
            await release.wait()
        context.register_hook("unbounded_test_hook", callback)
        with boundary.turn_boundary(manager, "turn") as scope:
            handle = context.retain_turn_evidence(lambda: True)
            task = asyncio.create_task(manager.ainvoke_hook("unbounded_test_hook", turn_id="turn"))
            await entered.wait()
        assert scope.pending > 0
        assert context.collect_turn_evidence(handle).status == "pending"
        release.set()
        await task
        assert context.collect_turn_evidence(handle).status == "collected"
    asyncio.run(scenario())


def test_async_cancellation_never_validates(world):
    manager, context = world
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def callback(**kw):
            entered.set()
            await release.wait()
        context.register_hook("unbounded_test_hook", callback)
        with boundary.turn_boundary(manager, "turn"):
            handle = context.retain_turn_evidence(lambda: True)
            task = asyncio.create_task(manager.ainvoke_hook("unbounded_test_hook", turn_id="turn"))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert context.collect_turn_evidence(handle).status == "unverified"
    asyncio.run(scenario())


def test_exception_reload_and_foreign_owner_fail_closed(world):
    manager, context = world
    foreign = PluginContext(PluginManifest(name="other"), manager)
    with boundary.turn_boundary(manager, "turn"):
        handle = context.retain_turn_evidence(lambda: True)
    assert foreign.collect_turn_evidence(handle).status == "unverified"
    registration = context.register_hook("post_llm_call", lambda: None)
    registration.dispose()
    assert context.collect_turn_evidence(handle).status == "unverified"
    with pytest.raises(ValueError), boundary.turn_boundary(manager, "other"):
        handle = context.retain_turn_evidence(lambda: True)
        raise ValueError()
    assert context.collect_turn_evidence(handle).status == "unverified"


def test_wrong_turn_or_profile_preserves_dispatch_but_invalidates_proof(world, tmp_path):
    manager, context = world
    calls = []
    context.register_hook("post_llm_call", lambda **kw: calls.append(kw))
    with boundary.turn_boundary(manager, "turn"):
        handle = context.retain_turn_evidence(lambda: True)
        manager.invoke_hook("post_llm_call", turn_id="other")
    assert len(calls) == 1
    assert context.collect_turn_evidence(handle).status == "unverified"
    other = PluginManager(scope_key=str(tmp_path / "other"))
    with boundary.turn_boundary(manager, "turn"):
        assert boundary.admit_callback(other, {"turn_id": "turn"}) is None


def test_capacity_expiry_and_abandoned_reader_remain_bounded(world, monkeypatch):
    manager, context = world
    monkeypatch.setattr(boundary, "_MAX_RETAINED", 1)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    def reader():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            ended.set()
    try:
        with boundary.turn_boundary(manager, "turn"):
            handle = context.retain_turn_evidence(reader)
        assert context.collect_turn_evidence(handle, timeout=0.02).status == "unverified"
        assert entered.is_set()
        monkeypatch.setattr(boundary, "_RETENTION_SECONDS", -1)
        with boundary.turn_boundary(manager, "next"):
            assert context.retain_turn_evidence(lambda: True) is None
        assert len(boundary._RETAINED) == 1
    finally:
        release.set()
        assert ended.wait(5)


def test_expired_or_foreign_process_handle_never_reads(world, monkeypatch):
    manager, context = world
    calls = []
    with boundary.turn_boundary(manager, "turn") as scope:
        handle = context.retain_turn_evidence(lambda: calls.append(True))
    scope.created -= 301
    assert context.collect_turn_evidence(handle).status == "unverified"
    assert calls == []
    with boundary.turn_boundary(manager, "next") as scope:
        handle = context.retain_turn_evidence(lambda: calls.append(True))
    scope.pid = -1
    assert context.collect_turn_evidence(handle).status == "unverified"
    assert calls == []


def test_reader_exception_or_new_late_activity_during_read_invalidates(world):
    manager, context = world
    with boundary.turn_boundary(manager, "turn"):
        copied = contextvars.copy_context()
        def reader():
            copied.run(boundary.admit_callback, manager, {"turn_id": "turn"})
            return {"valid": True}
        handle = context.retain_turn_evidence(reader)
    assert context.collect_turn_evidence(handle).status == "unverified"
    with boundary.turn_boundary(manager, "next"):
        handle = context.retain_turn_evidence(lambda: 1 / 0)
    assert context.collect_turn_evidence(handle).status == "unverified"


def test_no_scope_and_nested_scope_do_not_alias(world):
    manager, context = world
    assert context.retain_turn_evidence(lambda: True) is None
    with boundary.turn_boundary(manager, "parent"):
        parent = context.retain_turn_evidence(lambda: "parent")
        with boundary.turn_boundary(manager, "child"):
            child = context.retain_turn_evidence(lambda: "child")
        assert context.collect_turn_evidence(child).value == "child"
        assert context.collect_turn_evidence(parent).status == "pending"
    assert context.collect_turn_evidence(parent).value == "parent"


def test_no_consumer_keeps_late_dispatch_behavior(world):
    manager, context = world
    calls = []
    context.register_hook("post_llm_call", lambda **kw: calls.append(True))
    with boundary.turn_boundary(manager, "turn"):
        copied = contextvars.copy_context()
    copied.run(manager.invoke_hook, "post_llm_call", turn_id="turn")
    assert calls == [True]


def test_real_discovery_retains_plugin_reader(tmp_path, monkeypatch):
    from tests.hermes_cli.test_plugins import _make_plugin_dir
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _make_plugin_dir(home / "plugins", "receipt-example", register_body="""state = {'calls': 0}
    def reader():
        state['calls'] += 1
        return state['calls']
    def observe(**kwargs):
        state['handle'] = ctx.retain_turn_evidence(reader)
    ctx.register_hook('post_llm_call', observe)
    ctx.register_hook('receipt_collect', lambda: ctx.collect_turn_evidence(state['handle']))""", home=home)
    manager = PluginManager(scope_key=str(home))
    manager.discover_and_load()
    assert manager.has_hook("receipt_collect")
    with boundary.turn_boundary(manager, "turn"):
        manager.invoke_hook("post_llm_call", turn_id="turn")
    results = manager.invoke_hook("receipt_collect")
    assert results[0].status == "collected" and results[0].value == 1
    assert manager.invoke_hook("receipt_collect")[0].status == "unverified"
