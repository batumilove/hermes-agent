"""Event-loop responsiveness contracts for gateway SessionDB adapters."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway.run import GatewayRunner


@pytest.mark.asyncio
async def test_sync_session_db_method_does_not_block_event_loop():
    """A contended synchronous DB call must run outside the gateway loop."""
    heartbeat_ran = threading.Event()
    heartbeat_seen_while_db_blocked = []

    def contended_lookup(*, key: str) -> str:
        heartbeat_seen_while_db_blocked.append(heartbeat_ran.wait(timeout=0.5))
        return key

    runner = object.__new__(GatewayRunner)
    runner._session_db = SimpleNamespace(contended_lookup=contended_lookup)

    async def heartbeat() -> None:
        await asyncio.sleep(0)
        heartbeat_ran.set()

    db_task = asyncio.create_task(
        runner._maybe_call_session_db("contended_lookup", key="value")
    )
    heartbeat_task = asyncio.create_task(heartbeat())

    result, _ = await asyncio.gather(db_task, heartbeat_task)

    assert result == "value"
    assert heartbeat_seen_while_db_blocked == [True]


@pytest.mark.asyncio
async def test_async_session_db_method_stays_awaitable():
    runner = object.__new__(GatewayRunner)

    class AsyncDB:
        async def lookup(self, *, key: str) -> str:
            await asyncio.sleep(0)
            return key

    runner._session_db = AsyncDB()

    assert await runner._maybe_call_session_db("lookup", key="value") == "value"
