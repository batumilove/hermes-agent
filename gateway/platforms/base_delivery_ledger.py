"""Best-effort legacy delivery obligations; durable handoffs own their receipt DB."""
from __future__ import annotations
import logging
from typing import Any, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from gateway.platforms.base import BasePlatformAdapter, MessageEvent
logger = logging.getLogger("gateway.platforms.base")

async def _record_delivery_obligation(
    self, event: MessageEvent, session_key: str, text_content: str,
    delivery_adapter: BasePlatformAdapter, is_ephemeral_response: bool) -> Optional[str]:
    """Ledger the final response BEFORE the send so a crash before platform ACK redelivers on
    next boot; best-effort, skips slash-command and ephemeral replies. Returns the obligation id
    or None."""
    if is_ephemeral_response or str(event.text or "").lstrip().startswith(
        ("/", self.typed_command_prefix or "!")):
        return None
    obligation_id = None
    try:
        from agent.async_utils import run_sync_in_detached_daemon_thread
        from gateway.delivery_ledger import (
            compute_obligation_id, ledger_enabled, mark_attempting, record_obligation)
        if not await run_sync_in_detached_daemon_thread(ledger_enabled):
            return None
        source = event.source
        # ``ledger_message_id`` wins when set: a queued chain's final answers the last message
        # of the chain, not the event that opened it (see ``MessageEvent.ledger_message_id``).
        _ledger_id = getattr(event, "ledger_message_id", None)
        if _ledger_id is None:
            _ledger_id = getattr(event, "message_id", "")
        obligation_id = compute_obligation_id(
            session_key, str(_ledger_id or ""), text_content)
        await run_sync_in_detached_daemon_thread(
            record_obligation, obligation_id=obligation_id, session_key=session_key,
            platform=str(getattr(source.platform, "value", source.platform)),
            chat_id=source.chat_id, thread_id=getattr(source, "thread_id", None),
            content=text_content,
            adapter_profile=getattr(delivery_adapter, "_owner_profile", None))
        await run_sync_in_detached_daemon_thread(mark_attempting, obligation_id)
        return obligation_id
    except Exception:
        logger.debug("delivery ledger record failed", exc_info=True)
        return obligation_id

async def _finalize_delivery_obligation(
    self, obligation_id: str, result: Any, event: MessageEvent,
    delivery_adapter: BasePlatformAdapter) -> None:
    """Mark the ledger row delivered/failed (best-effort). On ``send_path_degraded`` with a
    replacement adapter live, trigger another redelivery sweep (the watcher's may have run
    before this failure landed; atomic claiming keeps it idempotent). On any other rejection arm
    the runner's timed redelivery, so the reply goes out once the flood penalty or the retry
    backoff has passed instead of waiting for the next restart (#91653)."""
    try:
        from agent.async_utils import run_sync_in_detached_daemon_thread
        from gateway.dead_targets import classify_dead_error
        from gateway.delivery_ledger import is_reconnect_only, mark_delivered, mark_failed
        if getattr(result, "success", False):
            await run_sync_in_detached_daemon_thread(mark_delivered, obligation_id)
            return
        error = str(getattr(result, "error", "") or "")
        await run_sync_in_detached_daemon_thread(mark_failed, obligation_id, error)
        if is_reconnect_only(error):
            redeliver = getattr(
                self.gateway_runner, "_redeliver_failed_obligations_for_platform", None)
            live = self._final_delivery_adapter(event.source)
            if live is not delivery_adapter and callable(redeliver):
                await redeliver(event.source.platform,
                                profile=getattr(delivery_adapter, "_owner_profile", None))
        elif classify_dead_error(error) is None:  # a dead chat is never retried: no timer to wake
            schedule = getattr(self.gateway_runner, "_schedule_flood_redelivery", None)
            if callable(schedule):
                schedule(event.source.platform,
                         profile=getattr(delivery_adapter, "_owner_profile", None))
    except Exception:
        logger.debug("delivery ledger update failed", exc_info=True)
