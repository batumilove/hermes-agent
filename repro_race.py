"""Reproduce #7483 queue admission race directly on the adapter.

With max_concurrent_runs=1, if one slot frees and several HTTP requests are
waiting, exactly one should proceed. The bug is that _await_run_queue_slot
can return None to multiple waiters because the release and re-check are not
atomic.
"""
import asyncio
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _api_agent_request_reservation


def _make_adapter() -> APIServerAdapter:
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._max_concurrent_runs = 1
    adapter._inflight_agent_runs = 1
    return adapter


@pytest.mark.asyncio
async def test_only_one_waiter_admits_when_slot_frees():
    adapter = _make_adapter()
    blocker = asyncio.Event()
    admitted = []

    async def waiter(label: str):
        resp = await adapter._await_run_queue_slot()
        if resp is not None:
            return label, "queued_response"
        reservation = _api_agent_request_reservation.get()
        assert reservation and reservation["active"], f"{label} has no reservation"
        admitted.append(label)
        await blocker.wait()
        reservation["active"] = False
        adapter._pending_agent_requests = max(0, adapter._pending_agent_requests - 1)
        adapter._inflight_agent_runs -= 1
        adapter._notify_run_queue_slot()
        return label, "admitted"

    tasks = [asyncio.create_task(waiter(f"w{i}")) for i in range(3)]
    # Wait until all waiters are in the queue.
    for _ in range(200):
        if len(adapter._run_queue_entries) >= 3:
            break
        await asyncio.sleep(0.01)
    assert len(adapter._run_queue_entries) >= 3

    # Free the external slot so the queue can advance.
    adapter._inflight_agent_runs = 0
    adapter._notify_run_queue_slot()

    # Wait until exactly one waiter has been admitted and is holding the slot.
    for _ in range(200):
        if len(admitted) >= 1:
            break
        await asyncio.sleep(0.01)
    assert len(admitted) == 1, f"admitted={admitted}"
    assert adapter._pending_agent_requests == 1
    # The other two should still be queued.
    assert len(adapter._run_queue_snapshot()) == 2

    # Let the admitted run finish; the queue should advance one by one.
    blocker.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=3.0)

    # Sequential admission means the order is the same as enqueue order.
    assert admitted == ["w0", "w1", "w2"], admitted


if __name__ == "__main__":
    asyncio.run(test_only_one_waiter_admits_when_slot_frees())
