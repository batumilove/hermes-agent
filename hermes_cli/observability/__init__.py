"""First-party Hermes observability integrations."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    """Dispatch a Hermes lifecycle event to built-in observability features."""
    from . import relay_shared_metrics

    _safe_observe(relay_shared_metrics.observe_lifecycle, hook_name, kwargs)


async def observe_lifecycle_async(hook_name: str, **kwargs: Any) -> None:
    """Await built-in observability features with asynchronous lifecycle work."""
    from . import relay_shared_metrics

    await _safe_observe_async(
        relay_shared_metrics.observe_lifecycle_async,
        hook_name,
        kwargs,
    )


def handles_hook(hook_name: str) -> bool:
    """Return whether any built-in observability feature handles a hook."""
    from . import relay_shared_metrics

    return relay_shared_metrics.handles_hook(hook_name)


def _safe_observe(callback: Any, hook_name: str, kwargs: dict[str, Any]) -> None:
    try:
        callback(hook_name, **kwargs)
    except Exception:
        logger.warning(
            "Built-in observability hook failed: %s", hook_name, exc_info=True
        )


async def _safe_observe_async(
    callback: Any,
    hook_name: str,
    kwargs: dict[str, Any],
) -> None:
    try:
        await callback(hook_name, **kwargs)
    except Exception:
        logger.warning(
            "Built-in observability hook failed: %s", hook_name, exc_info=True
        )
