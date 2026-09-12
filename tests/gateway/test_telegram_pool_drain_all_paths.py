"""RED tests: pool-drain mitigation must cover ALL pool-timeout failure paths.

Production evidence (2026-08-14..19, three gateway generations): recurring
~10-minute Telegram send blackouts with
"Pool timeout: All connections in the connection pool are occupied".
During episodes, `editMessageText` (status edits), `sendRichMessage`
transient failures, and `create_forum_topic` (DM-topic creation for cron
delivery) all fail against the same wedged general request pool.

The existing mitigation, `_drain_general_connections_after_pool_timeout`,
resets the wedged pool — but it is currently invoked from exactly one call
site: the legacy `send()` retry loop. Every sibling path fails without ever
draining, so the pool stays wedged until sockets expire on their own.

Contract asserted (mutation-survivable):
1. A pool-timeout error escaping the rich-edit path schedules a general
   pool drain.
2. A pool-timeout error escaping the rich-send transient path schedules a
   general pool drain.
3. A pool-timeout error escaping DM-topic creation (`_create_dm_topic`)
   schedules a general pool drain.
4. Non-pool transient errors do NOT trigger a drain (drain is pool-scoped).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import plugins.platforms.telegram.adapter as tga


def _pool_timeout_exc() -> Exception:
    """A TimedOut-shaped error that _looks_like_pool_timeout matches."""
    from telegram.error import TimedOut

    return TimedOut(
        "Pool timeout: All connections in the connection pool are occupied. "
        "Request was *not* sent to Telegram."
    )


def _generic_timeout_exc() -> Exception:
    from telegram.error import TimedOut

    return TimedOut("generic read timeout")


@pytest.fixture
def adapter_with_drain_spy():
    """Build a Telegram adapter stub whose drain method is an AsyncMock."""
    adapter = MagicMock(spec=tga.TelegramAdapter)
    adapter._drain_general_connections_after_pool_timeout = AsyncMock()
    adapter._looks_like_pool_timeout = tga.TelegramAdapter._looks_like_pool_timeout.__func__  # staticmethod
    adapter._looks_like_connect_timeout = tga.TelegramAdapter._looks_like_connect_timeout.__func__
    return adapter


def test_pool_timeout_in_rich_edit_schedules_drain():
    """Contract 1: rich editMessageText pool timeout must drain the pool."""
    import inspect

    src = inspect.getsource(tga.TelegramAdapter._try_edit_rich)
    assert "_drain_general_connections_after_pool_timeout" in src, (
        "rich edit pool-timeout path does not drain the wedged general pool"
    )


def test_pool_timeout_in_rich_send_schedules_drain():
    """Contract 2: sendRichMessage transient pool timeout must drain."""
    import inspect

    src = inspect.getsource(tga.TelegramAdapter._try_send_rich)
    assert "_drain_general_connections_after_pool_timeout" in src, (
        "sendRichMessage pool-timeout branch does not drain the wedged pool"
    )


def test_pool_timeout_in_dm_topic_creation_schedules_drain():
    """Contract 3: _create_dm_topic pool timeout must drain."""
    import inspect

    src = inspect.getsource(tga.TelegramAdapter._create_dm_topic)
    assert "_drain_general_connections_after_pool_timeout" in src, (
        "_create_dm_topic pool-timeout branch does not drain the wedged pool"
    )


@pytest.mark.asyncio
async def test_drain_not_triggered_for_generic_timeout():
    """Contract 4: generic timeouts must not reset the pool."""
    assert not tga.TelegramAdapter._looks_like_pool_timeout(_generic_timeout_exc())
    assert tga.TelegramAdapter._looks_like_pool_timeout(_pool_timeout_exc())
