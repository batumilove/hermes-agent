"""Tests for agent.async_utils.safe_schedule_threadsafe."""

from __future__ import annotations

import asyncio
import gc
import warnings
from concurrent.futures import Future
from typing import Any, cast
from unittest.mock import patch


from agent.async_utils import (
    run_sync_in_detached_daemon_thread,
    run_sync_in_detached_serial_daemon_thread,
    safe_schedule_threadsafe,
    submit_sync_to_detached_serial_daemon,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_unawaited_warnings(caught, *, coro_name: str = "") -> bool:
    """Return True if no "X was never awaited" warning slipped through.

    When *coro_name* is provided, only warnings naming that coroutine are
    counted
    """
    bad = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "was never awaited" in str(w.message)
        and (not coro_name or coro_name in str(w.message))
    ]
    return not bad


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSafeScheduleThreadsafe:
    def test_returns_future_on_success(self):
        loop = asyncio.new_event_loop()
        try:
            import threading
            ready = threading.Event()
            stop = threading.Event()

            def _runner():
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_until_complete(_wait_for_stop(stop))

            async def _wait_for_stop(ev):
                while not ev.is_set():
                    await asyncio.sleep(0.005)

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            ready.wait(timeout=2)

            async def _sample():
                return 42

            fut = safe_schedule_threadsafe(_sample(), loop)
            assert isinstance(fut, Future)
            assert fut.result(timeout=2) == 42

            stop.set()
            t.join(timeout=2)
        finally:
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
            loop.close()



    def test_scheduling_exception_closes_coroutine(self):
        """If run_coroutine_threadsafe raises, close the coroutine and return None."""
        # A loop that *looks* open but raises on submission
        loop = asyncio.new_event_loop()
        try:
            async def _sample():
                return "ok"

            coro = _sample()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with patch(
                    "agent.async_utils.asyncio.run_coroutine_threadsafe",
                    side_effect=RuntimeError("scheduler down"),
                ):
                    result = safe_schedule_threadsafe(coro, loop)
                del coro
                gc.collect()

            assert result is None
            assert _no_unawaited_warnings(caught, coro_name='_sample')
        finally:
            loop.close()


class TestRunSyncInDetachedDaemonThread:
    def test_runs_off_loop_without_default_executor_ownership(self):
        async def _exercise():
            import threading

            main_ident = threading.get_ident()

            def _sync_probe():
                thread = threading.current_thread()
                return thread.ident, thread.daemon

            with patch(
                "agent.async_utils.asyncio.to_thread",
                side_effect=AssertionError("default executor must not be used"),
            ):
                worker_ident, daemon = await run_sync_in_detached_daemon_thread(
                    _sync_probe
                )
            return main_ident, worker_ident, daemon

        main_ident, worker_ident, daemon = asyncio.run(_exercise())
        assert worker_ident != main_ident
        assert daemon is True

    def test_serial_daemon_preserves_submission_order(self):
        import threading

        first_started = threading.Event()
        release_first = threading.Event()
        finished = threading.Event()
        observed = []

        def _first():
            first_started.set()
            release_first.wait(timeout=2)
            observed.append(("first", threading.current_thread().daemon))

        def _second():
            observed.append(("second", threading.current_thread().daemon))
            finished.set()

        submit_sync_to_detached_serial_daemon(_first)
        assert first_started.wait(timeout=1)
        submit_sync_to_detached_serial_daemon(_second)
        assert not finished.wait(timeout=0.05)
        release_first.set()
        assert finished.wait(timeout=1)
        assert observed == [("first", True), ("second", True)]

    def test_awaitable_serial_daemon_preserves_submission_order(self):
        async def _exercise():
            import threading

            first_started = threading.Event()
            release_first = threading.Event()
            observed = []

            def _first():
                first_started.set()
                release_first.wait(timeout=2)
                observed.append("first")
                return 1

            def _second():
                observed.append("second")
                return 2

            first = asyncio.create_task(
                run_sync_in_detached_serial_daemon_thread(_first)
            )
            while not first_started.is_set():
                await asyncio.sleep(0)
            second = asyncio.create_task(
                run_sync_in_detached_serial_daemon_thread(_second)
            )
            await asyncio.sleep(0.05)
            assert observed == []
            release_first.set()
            assert await asyncio.gather(first, second) == [1, 2]
            assert observed == ["first", "second"]

        asyncio.run(_exercise())

    def test_async_session_db_avoids_default_executor_ownership(self):
        async def _exercise():
            import threading

            class _DB:
                def probe(self):
                    return threading.current_thread().daemon

            from hermes_state import AsyncSessionDB

            with patch(
                "asyncio.to_thread",
                side_effect=AssertionError("default executor must not be used"),
            ):
                return await AsyncSessionDB(cast(Any, _DB())).probe()

        assert asyncio.run(_exercise()) is True


