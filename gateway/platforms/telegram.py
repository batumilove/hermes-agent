"""Backward-compatible Telegram adapter import shim.

The Telegram adapter is implemented as the bundled platform plugin at
``plugins.platforms.telegram.adapter``.  Older tests and third-party callers
imported ``gateway.platforms.telegram`` directly, so keep this thin re-export
while the runtime continues to load the plugin path.
"""

from plugins.platforms.telegram.adapter import (  # noqa: F401
    TELEGRAM_AVAILABLE,
    TelegramAdapter,
    check_telegram_requirements,
    register,
)

__all__ = [
    "TELEGRAM_AVAILABLE",
    "TelegramAdapter",
    "check_telegram_requirements",
    "register",
]


def _merge_batched_telegram_events(pending, event):
    """Merge Telegram text/media batch events without dropping voice metadata."""
    from gateway.platforms.base import MessageType

    if getattr(event, "message_type", None) != MessageType.TEXT:
        if getattr(pending, "text", None):
            event.text = f"{pending.text}\n{event.text}" if getattr(event, "text", None) else pending.text
        return event
    if getattr(pending, "text", None):
        event.text = f"{pending.text}\n{event.text}" if getattr(event, "text", None) else pending.text
    return event

setattr(TelegramAdapter, "_merge_batched_telegram_events", staticmethod(_merge_batched_telegram_events))
