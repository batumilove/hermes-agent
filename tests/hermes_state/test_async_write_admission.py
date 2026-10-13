"""Async admission is task-scoped, cancellation-safe and preserves FIFO fairness."""

import asyncio

import pytest

from hermes_state import SessionDBWriteAdmission


@pytest.mark.asyncio
async def test_unrelated_http_task_waits_without_blocking_owner_and_worker_reentry():
    controller = SessionDBWriteAdmission(capacity=1, queue_limit=2)
    entered = asyncio.Event()
    release = asyncio.Event()
    registered = asyncio.Event()
    second_entered = asyncio.Event()

    async def owner():
        async with controller.acquire("first"):

            def nested_write():
                with controller.acquire("first"):
                    return controller.stats()["in_flight"]

            assert await asyncio.to_thread(nested_write) == 1
            entered.set()
            await release.wait()

    async def sibling():
        await entered.wait()
        ticket = controller.acquire("second")
        registered.set()
        async with ticket:
            second_entered.set()

    first = asyncio.create_task(owner())
    second = asyncio.create_task(sibling())
    await asyncio.wait_for(registered.wait(), timeout=5)
    assert controller.stats()["waiting"] == 1
    assert not second_entered.is_set()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert second_entered.is_set()
    assert controller.stats()["in_flight"] == 0


@pytest.mark.asyncio
async def test_cancelled_async_waiter_never_strands_a_slot():
    controller = SessionDBWriteAdmission(capacity=1, queue_limit=2)
    holder = controller.acquire("held")
    registered = asyncio.Event()

    async def waiter():
        ticket = controller.acquire("waiting")
        registered.set()
        async with ticket:
            pytest.fail("cancelled waiter was admitted")

    task = asyncio.create_task(waiter())
    await asyncio.wait_for(registered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    holder.release()
    assert controller.stats()["in_flight"] == 0
    assert controller.stats()["waiting"] == 0
