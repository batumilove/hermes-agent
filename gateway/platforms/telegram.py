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
