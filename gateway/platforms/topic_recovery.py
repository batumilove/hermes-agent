"""Asynchronous recovery before publishing a replacement inbound topic."""
import asyncio
import inspect
import logging

logger = logging.getLogger("gateway.platforms.base")

async def _apply_topic_recovery_async(self, event) -> None:
    """Recover a thread off-loop, then publish the source rewrite on-loop."""
    recover = getattr(self, "_topic_recovery_fn", None)
    if recover is None:
        return
    source = getattr(event, "source", None)
    if source is None:
        return
    try:
        if inspect.iscoroutinefunction(recover):
            recovered = await recover(source)
        else:
            recovered = await asyncio.to_thread(recover, source)
            if inspect.isawaitable(recovered):
                recovered = await recovered
    except asyncio.CancelledError:
        raise
    except Exception:  # health: allow BLE001 -- External recovery hooks may raise arbitrary backend exceptions; preserve inbound delivery.
        logger.debug("topic recovery hook failed", exc_info=True)
        return
    if recovered is None or str(recovered) == str(source.thread_id or ""):
        return
    try:
        from gateway.session_identity import replace_source
        event.source = replace_source(source, thread_id=str(recovered))
    except (AttributeError, TypeError, ValueError):
        logger.debug("topic recovery rewrite failed", exc_info=True)
