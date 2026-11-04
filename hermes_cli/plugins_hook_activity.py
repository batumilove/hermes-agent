"""Host-owned accounting for awaitable hook execution, including abandoned work."""
from __future__ import annotations

import asyncio
import inspect

from hermes_cli.plugins_turn_boundary import fail_current, reserve_current_activity


def _close_unstarted(result):
    if inspect.iscoroutine(result):
        result.close()


async def await_hook_result(result, timeout=None):
    """Deadline and cancellation belong to the host, even if the plugin suppresses them.

    The child owns the activity until actual completion. Cancellation of the host
    does not wait indefinitely for plugin cancellation cleanup.
    """
    activity = reserve_current_activity()
    if activity is False:
        _close_unstarted(result)
        raise TimeoutError("hook activity admission closed")
    started = False

    async def run():
        nonlocal started
        started = True
        return await result

    def finished(task):
        if not started:
            _close_unstarted(result)
        if activity is not None:
            if task.cancelled() or task.exception() is not None:
                activity.fail()
            activity.leave()
        elif not task.cancelled():
            task.exception()  # consume errors from a timed-out detached child

    coroutine = run()
    try:
        task = asyncio.create_task(coroutine)
    except BaseException:
        coroutine.close()
        _close_unstarted(result)
        if activity is not None:
            activity.fail()
            activity.leave()
        raise
    task.add_done_callback(finished)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            fail_current()
            task.cancel()
            raise TimeoutError("hook callback deadline exceeded")
        return task.result()
    except asyncio.CancelledError:
        fail_current()
        task.cancel()
        raise
