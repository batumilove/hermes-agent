"""Adapter-owned preparation and settlement of durable final delivery."""
from __future__ import annotations

import logging
import os
from typing import Any

from agent.async_utils import run_sync_in_detached_daemon_thread

logger = logging.getLogger("gateway.platforms.base")
_ATTACHMENT_RECOVERY_NOTICE = "⚠️ The gateway was interrupted while delivering an attachment response. Please ask me to resend it."


def normalize_handler_response(adapter, event, response, session_key, interrupt_event):
    """Apply notification/ephemeral/stale-response policy before final extraction."""
    from gateway.platforms.base import EphemeralReply, diagnostic_wake_muted
    with adapter._media_delivery_scope(event.source):
        if diagnostic_wake_muted(event):
            response = None
    ephemeral = isinstance(response, EphemeralReply)
    response, ttl = adapter._unwrap_ephemeral(response)
    if response and interrupt_event.is_set() and session_key in adapter._pending_messages:
        logger.info("[%s] Suppressing stale response for interrupted session %s", adapter.name, session_key)
        response = None
    return response, ephemeral, ttl


class FinalDeliveryState:
    """One final's receipt, complete component outcome and generated-file ownership."""

    def __init__(self):
        self.handoff: Any = None
        self.outcomes: list[bool] = []
        self.failures: list[str] = []
        self.suppressed = False
        self.generated_files: set[str] = set()

    def bind(self, response, event):
        from gateway.post_response import FinalResponseHandoff
        if isinstance(response, FinalResponseHandoff):
            self.handoff = response
            event._gateway_post_response_handoff = response

    def observe(self, result):
        if result is not None:
            self.outcomes.append(bool(getattr(result, "success", False)))
            if not getattr(result, "success", False):
                from gateway.delivery_failure import delivery_failure_token
                self.failures.append(delivery_failure_token(result))

    async def prepare(self, event, text_content, extracted, tts_paths, requested_tts_path):
        """Commit before the first text, image, document or voice output."""
        self.generated_files.update(tts_paths)
        if requested_tts_path:
            self.generated_files.add(requested_tts_path)
        handoff = self.handoff
        if handoff is None:
            return True
        has_component = bool(text_content or extracted.images or extracted.media_files or extracted.local_files or tts_paths)
        if not has_component:
            self.suppressed = True
            return True
        handoff.receipt_attempted = True
        try:
            ready = await run_sync_in_detached_daemon_thread(
                handoff.runner._prepare_final_response_handoff, handoff,
                text_content=text_content or _ATTACHMENT_RECOVERY_NOTICE, event=event)
            if ready:
                ready = await run_sync_in_detached_daemon_thread(
                    handoff.runner._mark_final_response_handoff_attempting, handoff)
            return ready
        except Exception as exc:  # health: allow BLE001 -- Arbitrary store/adapter receipt callbacks must fail closed before any output.
            logger.warning("Final receipt could not persist (%s)", type(exc).__name__)
            return False

    async def finish(self):
        """Settle only complete delivery; retain every uncertain receipt for recovery."""
        for path in self.generated_files:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.debug("Generated voice artifact cleanup failed", exc_info=True)
        handoff = self.handoff
        if handoff is None:
            return
        if getattr(handoff, "obligation_id", None):
            delivered = bool(self.outcomes) and all(self.outcomes)
            try:
                error = self.failures[0] if self.failures else "final delivery incomplete"
                settled = await run_sync_in_detached_daemon_thread(
                    handoff.runner._settle_final_response_handoff, handoff,
                    delivered=delivered, error=None if delivered else error)
                if settled is False:
                    return  # A stale producer cannot schedule or finalize a newer claim.
                controller = getattr(handoff.runner, "_post_response_controller", None)
                if controller is not None:
                    controller.wake()
                if not delivered:
                    schedule = getattr(handoff.runner, "_schedule_final_response_redelivery", None)
                    if callable(schedule):
                        await schedule(handoff, error)
            except Exception:  # health: allow BLE001 -- A late store callback failure leaves the receipt replayable and must not prevent adapter cleanup.
                logger.warning("Final response settlement remains pending", exc_info=True)
        elif self.suppressed or not getattr(handoff, "receipt_attempted", False):
            await run_sync_in_detached_daemon_thread(
                handoff.runner._cancel_uncommitted_final_response_handoff, handoff)
