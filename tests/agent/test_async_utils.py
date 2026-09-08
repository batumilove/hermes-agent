"""Tests for agent.async_utils.safe_schedule_threadsafe."""

from __future__ import annotations

import asyncio
import gc
import warnings
from concurrent.futures import Future
from typing import Any, cast
from unittest.mock import patch


import pytest

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

    def test_stalled_serial_job_times_out_without_poisoning_following_work(self):
        async def _exercise():
            import threading

            first_started = threading.Event()
            release_first = threading.Event()
            second_finished = threading.Event()

            def _stalled():
                first_started.set()
                release_first.wait()

            def _second():
                second_finished.set()
                return 2

            try:
                with patch("agent.async_utils._SERIAL_JOB_TIMEOUT_SECONDS", 0.05):
                    first = asyncio.create_task(
                        run_sync_in_detached_serial_daemon_thread(_stalled)
                    )
                    while not first_started.is_set():
                        await asyncio.sleep(0)
                    second = asyncio.create_task(
                        run_sync_in_detached_serial_daemon_thread(_second)
                    )

                    with pytest.raises(TimeoutError, match="serial daemon work"):
                        await asyncio.wait_for(first, timeout=1)
                    assert await asyncio.wait_for(second, timeout=1) == 2
                    assert second_finished.is_set()
            finally:
                release_first.set()

        asyncio.run(_exercise())

    def test_late_timed_out_status_write_cannot_overwrite_newer_work(self):
        async def _exercise():
            import threading
            import gateway.status as status

            first_started = threading.Event()
            release_first = threading.Event()
            first_finished = threading.Event()
            observed = []

            @status._serialize_runtime_status_write
            def _write(value):
                observed.append(value)

            def _stalled_old_write():
                first_started.set()
                release_first.wait()
                _write("old")
                first_finished.set()

            try:
                with (
                    patch("agent.async_utils._SERIAL_JOB_TIMEOUT_SECONDS", 0.05),
                    patch.object(status, "_latest_serial_status_sequence", 0),
                ):
                    first = asyncio.create_task(
                        run_sync_in_detached_serial_daemon_thread(_stalled_old_write)
                    )
                    while not first_started.is_set():
                        await asyncio.sleep(0)
                    second = asyncio.create_task(
                        run_sync_in_detached_serial_daemon_thread(_write, "new")
                    )
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(first, timeout=1)
                    await asyncio.wait_for(second, timeout=1)
                    release_first.set()
                    assert first_finished.wait(timeout=1)
                    assert observed == ["new"]
            finally:
                release_first.set()

        asyncio.run(_exercise())

    def test_direct_status_write_supersedes_equal_submitted_sequence(self):
        async def _exercise():
            import threading
            import gateway.status as status

            old_started = threading.Event()
            release_old = threading.Event()
            old_finished = threading.Event()
            observed = []

            @status._serialize_runtime_status_write
            def _write(value):
                observed.append(value)

            def _stalled_old_write():
                old_started.set()
                release_old.wait()
                _write("old")
                old_finished.set()

            try:
                with (
                    patch("agent.async_utils._SERIAL_JOB_TIMEOUT_SECONDS", 0.05),
                    patch.object(status, "_latest_serial_status_sequence", 0),
                ):
                    old = asyncio.create_task(
                        run_sync_in_detached_serial_daemon_thread(_stalled_old_write)
                    )
                    while not old_started.is_set():
                        await asyncio.sleep(0)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(old, timeout=1)
                    _write("new-direct")
                    release_old.set()
                    assert old_finished.wait(timeout=1)
                    assert observed == ["new-direct"]
            finally:
                release_old.set()

        asyncio.run(_exercise())

    def test_repeated_stalls_have_bounded_worker_capacity(self):
        async def _exercise():
            import threading
            import agent.async_utils as async_utils

            release = threading.Event()
            started = [threading.Event(), threading.Event()]

            def _stall(index):
                started[index].set()
                release.wait()

            try:
                with (
                    patch.object(
                        async_utils,
                        "_serial_job_slots",
                        threading.BoundedSemaphore(value=2),
                    ),
                    patch.object(async_utils, "_SERIAL_JOB_TIMEOUT_SECONDS", 0.05),
                ):
                    for index in range(2):
                        task = asyncio.create_task(
                            run_sync_in_detached_serial_daemon_thread(_stall, index)
                        )
                        while not started[index].is_set():
                            await asyncio.sleep(0)
                        with pytest.raises(TimeoutError):
                            await asyncio.wait_for(task, timeout=1)

                    with pytest.raises(TimeoutError, match="capacity"):
                        await asyncio.wait_for(
                            run_sync_in_detached_serial_daemon_thread(lambda: 3),
                            timeout=1,
                        )
            finally:
                release.set()

        asyncio.run(_exercise())

    def test_capacity_exhaustion_does_not_admit_unbounded_queue_work(self):
        async def _exercise():
            import threading
            import agent.async_utils as async_utils

            release = threading.Event()
            started = threading.Event()

            def _stall():
                started.set()
                release.wait()

            tasks = []
            try:
                with (
                    patch.object(
                        async_utils,
                        "_serial_job_slots",
                        threading.BoundedSemaphore(value=1),
                    ),
                    patch.object(async_utils, "_SERIAL_JOB_TIMEOUT_SECONDS", 0.5),
                ):
                    first = asyncio.create_task(
                        run_sync_in_detached_serial_daemon_thread(_stall)
                    )
                    tasks.append(first)
                    while not started.is_set():
                        await asyncio.sleep(0)
                    tasks.extend(
                        asyncio.create_task(
                            run_sync_in_detached_serial_daemon_thread(lambda: index)
                        )
                        for index in range(4)
                    )
                    await asyncio.sleep(0.05)
                    assert async_utils._SERIAL_DAEMON_QUEUE.qsize() == 0
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)

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


