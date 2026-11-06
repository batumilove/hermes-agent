"""Queued core stream hooks belong to the turn until actual exit or invalidated drop."""
import contextvars
import queue
import threading

from agent import plugin_stream_hooks as stream
from hermes_cli import plugins_turn_boundary as boundary
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def test_queued_stream_work_prevents_early_collection(tmp_path, monkeypatch):
    manager = PluginManager(scope_key=str(tmp_path))
    context = PluginContext(PluginManifest(name="stream-test"), manager)
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    state = {"valid": True}
    def callback(**kwargs):
        entered.set()
        try:
            assert release.wait(5)
            state["valid"] = False
        finally:
            ended.set()
    context.register_hook("on_stream_end", callback)
    try:
        with boundary.turn_boundary(manager, "turn"):
            handle = context.retain_turn_evidence(lambda: dict(state))
            copied = contextvars.copy_context()
            assert stream.enqueue_plugin_stream_hook("on_stream_end", turn_id="turn")
            assert entered.wait(5)
        assert context.collect_turn_evidence(handle).status == "pending"
        assert not copied.run(stream.enqueue_plugin_stream_hook, "on_stream_end", turn_id="turn")
        assert context.collect_turn_evidence(handle).status == "unverified"
    finally:
        release.set()
        assert ended.wait(5)
        stream.shutdown_plugin_stream_hook_dispatcher(timeout=2)


def test_drop_invalidates_queued_activity(tmp_path):
    manager = PluginManager(scope_key=str(tmp_path))
    context = PluginContext(PluginManifest(name="drop-test"), manager)
    events = queue.Queue(maxsize=1)
    with boundary.turn_boundary(manager, "turn") as scope:
        handle = context.retain_turn_evidence(lambda: True)
        activity = boundary.admit_callback(manager, {"turn_id": "turn"})
        item = stream._QueuedHook(contextvars.copy_context(), {}, activity)
        assert stream._put_drop_oldest(events, item)
        assert stream._put_drop_oldest(events, stream._STOP)
        assert scope.pending == 0
    assert context.collect_turn_evidence(handle).status == "unverified"
