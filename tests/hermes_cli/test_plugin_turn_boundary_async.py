"""Regressions from independent R1: cancellation, helper abandonment and fork custody."""
import asyncio
import os
import signal
import threading

import pytest

from hermes_cli import plugins_turn_boundary as boundary
from hermes_cli import plugins_dispatch as dispatch
from tests.hermes_cli.test_plugin_turn_boundary import world as world  # noqa: PLC0414 -- re-export the shared pytest fixture


@pytest.mark.parametrize("mode", ["timeout", "external_cancel", "policy_timeout"])
def test_suppressed_cancellation_never_collects(world, mode):
    manager, context = world
    async def scenario():
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def callback(**kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return "suppressed"
        hook = {"timeout": "post_llm_call", "external_cancel": "on_session_finalize",
                "policy_timeout": "pre_tool_call"}[mode]
        context.register_hook(hook, callback)
        try:
            with boundary.turn_boundary(manager, "turn") as scope:
                handle = context.retain_turn_evidence(lambda: {"valid": True})
                task = asyncio.create_task(manager.ainvoke_hook(hook, turn_id="turn"))
                await entered.wait()
                if mode == "external_cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    result = await task
                    if mode == "policy_timeout":
                        assert result[0]["action"] == "block"
                await cancelled.wait()
                assert scope.pending > 0
            assert context.collect_turn_evidence(handle).status == "unverified"
        finally:
            release.set()
            # Let the real child complete instead of leaking it into asyncio.run teardown.
            await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not asyncio.current_task()))
    asyncio.run(scenario())


def test_helper_thread_retains_activity_until_exit(world, monkeypatch):
    manager, context = world
    monkeypatch.setattr(dispatch, "_PLUGIN_COMMAND_AWAIT_TIMEOUT_SECS", 0.02)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    async def callback(**kwargs):
        entered.set()
        try:
            assert release.wait(5)
        finally:
            ended.set()
    context.register_hook("on_session_finalize", callback)
    async def scenario():
        try:
            with boundary.turn_boundary(manager, "turn") as scope:
                handle = context.retain_turn_evidence(lambda: True)
                manager.invoke_hook("on_session_finalize", turn_id="turn")
                assert entered.is_set() and not ended.is_set()
                assert scope.pending > 0
            assert context.collect_turn_evidence(handle).status == "unverified"
        finally:
            release.set()
            assert ended.wait(5)
    asyncio.run(scenario())


@pytest.mark.platforms("posix")
def test_fork_rejects_before_inherited_locks(world):
    manager, context = world
    with boundary.turn_boundary(manager, "turn") as scope:
        handle = context.retain_turn_evidence(lambda: True)
    boundary._CUSTODY_LOCK.acquire()
    scope.lock.acquire()
    try:
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGALRM, lambda *_: os._exit(99))
            signal.alarm(3)
            result = context.collect_turn_evidence(handle, timeout=0.01)
            # Direct references and copied parent scopes cannot touch inherited locks either.
            scope.fail()
            scope.leave()
            scope.close(False)
            admitted = scope.enter(manager, {"turn_id": "turn"})
            os._exit(0 if result.status == "unverified" and admitted is False else 98)
    finally:
        scope.lock.release()
        boundary._CUSTODY_LOCK.release()
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
