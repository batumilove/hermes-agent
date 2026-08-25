"""RED test: send_path_degraded must not be retried as a network error.

When ``_send_path_degraded`` is True, the Telegram adapter's ``send()``
returns ``SendResult(success=False, error="send_path_degraded",
retryable=True)``.  The base-class ``_send_with_retry()`` classifies this
as a retryable network error (because ``result.retryable`` is True), then
burns through ``max_retries`` identical short-circuited attempts — each
immediately returning the same degraded result — and finally logs
``"Failed to deliver response after 2 retries: send_path_degraded"`` and
emits a user-visible "Message delivery failed" notice.

That is wrong on two counts:

1. **Wasted retries.** The flag is a *health gate*, not a transient I/O
   error. No amount of retrying within the same ``_send_with_retry`` call
   will clear it — it only clears when ``_record_polling_progress()``
   fires from a *separate* getUpdates I/O path. The retries add
   ``base_delay + base_delay*2`` seconds of delay for zero benefit.

2. **False delivery-failure notice.** After exhausting retries the code
   sends the user
   ``"⚠️ Message delivery failed after multiple attempts. Please try
   again…"`` even though the gateway is still alive and will resume normal
   delivery as soon as polling health returns. This is a false alarm.

The correct behaviour: a ``send_path_degraded`` error should
short-circuit ``_send_with_retry`` immediately (no retries, no failure
notice) so the caller's own backoff/queue logic handles re-delivery.

This test is RED against base ``55f7d9d323e3d98e71878598a4eee9909f7267b2``.
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig  # noqa: E402
from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


def _make_degraded_adapter() -> TelegramAdapter:
    """An adapter whose send() short-circuits with send_path_degraded."""
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._send_path_degraded = True
    return adapter


@pytest.mark.asyncio
async def test_send_path_degraded_not_retried_by_send_with_retry(monkeypatch):
    """``_send_with_retry`` must NOT retry a send_path_degraded result.

    The degraded flag is a health gate cleared by an external getUpdates
    I/O event, not something that a same-call retry can resolve. Retrying
    wastes backoff time and then falsely logs "delivery failed".
    """
    adapter = _make_degraded_adapter()

    # Track sleep calls — if the retry loop fires, sleep will be called.
    sleep_calls = []
    original_sleep = asyncio.sleep

    async def _tracking_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(
        "gateway.platforms.base.asyncio.sleep", _tracking_sleep
    )

    result = await adapter._send_with_retry(
        chat_id="123",
        content="hello world",
        reply_to=None,
        metadata=None,
        max_retries=2,
        base_delay=2.0,
    )

    # The result must still be a failure — we're not "fixing" the degraded
    # state, just not wasting retries on it.
    assert result.success is False
    assert result.error == "send_path_degraded"

    # RED: The current code retries twice, calling sleep(2) and sleep(4).
    # After the fix, sleep should never be called for this error class.
    assert sleep_calls == [], (
        f"_send_with_retry must not retry send_path_degraded, "
        f"but slept {len(sleep_calls)} time(s): {sleep_calls}"
    )


@pytest.mark.asyncio
async def test_send_path_degraded_no_false_delivery_failure_notice(monkeypatch):
    """``_send_with_retry`` must NOT send a delivery-failure notice for
    send_path_degraded — the gateway is alive and will resume delivery
    once polling health returns."""
    adapter = _make_degraded_adapter()

    # Track all send() calls to detect the failure-notice path.
    send_contents = []
    original_send = adapter.send

    async def _tracking_send(chat_id, content, reply_to=None, metadata=None):
        send_contents.append(content)
        return await original_send(chat_id, content, reply_to, metadata)

    adapter.send = _tracking_send

    # Speed up any retry sleep.
    monkeypatch.setattr(
        "gateway.platforms.base.asyncio.sleep", AsyncMock()
    )

    await adapter._send_with_retry(
        chat_id="123",
        content="hello world",
        reply_to=None,
        metadata=None,
        max_retries=2,
        base_delay=0.01,
    )

    # The failure-notice path (base.py ~4442) calls send() with the
    # "⚠️ Message delivery failed" text.
    failure_notices = [
        c for c in send_contents
        if c and "Message delivery failed" in str(c)
    ]
    assert failure_notices == [], (
        f"_send_with_retry must not emit a delivery-failure notice for "
        f"send_path_degraded, but found {len(failure_notices)} notice(s)"
    )
