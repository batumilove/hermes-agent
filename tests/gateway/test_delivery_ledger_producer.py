"""Producer-hook tests: _process_message_background records delivery
obligations around the final send (gateway/platforms/base.py).

Contract: obligation recorded (pending→attempting) BEFORE the send await,
delivered/failed by SendResult afterward; slash commands, ephemeral
replies, and empty responses are never recorded; ledger failures never
block the send.
"""

import asyncio
import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway import delivery_ledger as dl
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(dl, "_db_path", lambda: home / "state.db")
    yield


class _Adapter(BasePlatformAdapter):  # type: ignore[misc]
    """Minimal concrete adapter driving the real base-class pipeline."""

    def __init__(self):
        super().__init__(PlatformConfig(enabled=True), Platform.SLACK)
        self.sent = []

    async def connect(self, *, is_reconnect: bool = False):  # pragma: no cover
        return True

    async def disconnect(self):  # pragma: no cover - unused
        return None

    async def get_chat_info(self, chat_id):  # pragma: no cover - unused
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append(content)
        return SendResult(success=True, message_id="m1")


def _event(text="hello agent"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.SLACK, chat_id="C1", chat_type="channel"
        ),
        message_id="msg-42",
    )


def _rows():
    with dl._connect() as conn:
        return conn.execute(
            "SELECT obligation_id, state, content FROM delivery_obligations"
        ).fetchall()


async def _run(adapter, event, response="final answer"):
    adapter._message_handler = AsyncMock(return_value=response)
    session_key = "agent:main:slack:channel:C1"
    adapter._active_sessions[session_key] = asyncio.Event()
    await adapter._process_message_background(event, session_key)


class TestProducerHook:
    @pytest.mark.asyncio
    async def test_normal_turn_records_and_delivers(self):
        adapter = _Adapter()
        await _run(adapter, _event())

        assert adapter.sent == ["final answer"]
        rows = _rows()
        assert len(rows) == 1
        assert rows[0][1] == "delivered"
        assert rows[0][2] == "final answer"

    @pytest.mark.asyncio
    async def test_send_failure_leaves_failed_row(self):
        adapter = _Adapter()
        adapter.send = AsyncMock(
            return_value=SendResult(success=False, error="chat_not_found")
        )
        await _run(adapter, _event())

        rows = _rows()
        assert len(rows) == 1
        assert rows[0][1] == "failed"

    @pytest.mark.asyncio
    async def test_attempting_failure_still_reconciles_after_send(self, monkeypatch):
        adapter = _Adapter()

        def fail_attempting(obligation_id):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(dl, "mark_attempting", fail_attempting)
        await _run(adapter, _event())

        rows = _rows()
        assert adapter.sent == ["final answer"]
        assert len(rows) == 1
        assert rows[0][1] == "delivered"

    @pytest.mark.asyncio
    async def test_locked_ledger_fails_open_before_writer_releases(self):
        adapter = _Adapter()
        send_started = asyncio.Event()

        async def send(chat_id, content, reply_to=None, metadata=None):
            send_started.set()
            return SendResult(success=True, message_id="m1")

        adapter.send = send
        with dl._connect():
            pass
        blocker = sqlite3.connect(dl._db_path(), timeout=1, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")

        task = asyncio.create_task(_run(adapter, _event()))
        try:
            await asyncio.wait_for(send_started.wait(), timeout=1.0)
            assert blocker.in_transaction
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

        await task
        assert _rows() == []

    @pytest.mark.asyncio
    async def test_in_process_ledger_lock_fails_open_before_release(self):
        adapter = _Adapter()
        send_started = asyncio.Event()
        lock_held = threading.Event()
        release_lock = threading.Event()

        async def send(chat_id, content, reply_to=None, metadata=None):
            send_started.set()
            return SendResult(success=True, message_id="m1")

        def hold_lock():
            with dl._DB_LOCK:
                lock_held.set()
                release_lock.wait(timeout=5.0)

        adapter.send = send
        holder = threading.Thread(target=hold_lock)
        holder.start()
        assert lock_held.wait(timeout=1.0)

        task = asyncio.create_task(_run(adapter, _event()))
        try:
            await asyncio.wait_for(send_started.wait(), timeout=1.0)
            assert holder.is_alive()
        finally:
            release_lock.set()
            holder.join(timeout=1.0)

        await task
        assert _rows() == []


    @pytest.mark.asyncio
    async def test_crash_between_attempting_and_ack_is_recoverable(self):
        """The core scenario (#58818): process dies mid-send. The row must
        be claimable by a later process and carry the ambiguity marker."""
        adapter = _Adapter()

        async def _dies_mid_send(chat_id, content, reply_to=None, metadata=None):
            raise ConnectionError("gateway killed mid-await")

        adapter.send = _dies_mid_send
        # _send_with_retry raising propagates; the background task catches
        # broadly — drive only through the send block by tolerating the error.
        try:
            await _run(adapter, _event())
        except Exception:
            pass

        rows = _rows()
        assert len(rows) == 1
        # Row is stuck in 'attempting' (or failed if retry wrapper caught it):
        # either way it is non-delivered and recoverable.
        assert rows[0][1] in ("attempting", "failed")
        with dl._connect() as conn:
            conn.execute(
                "UPDATE delivery_obligations SET owner_pid=999999999, owner_started_at=1"
            )
        claimed = dl.sweep_recoverable()
        assert len(claimed) == 1
        assert claimed[0]["needs_marker"] is True
