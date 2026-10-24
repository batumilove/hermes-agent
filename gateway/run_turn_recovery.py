"""Gateway hmwa compression exhaustion reset contract."""
from agent.i18n import t
import asyncio
import logging
logger = logging.getLogger("gateway.run")

async def _hmwa_compression_exhaustion_reset(
    self, agent_result, response, session_entry, session_key, source,
):
    """Auto-reset a permanently oversized session so the next message starts fresh instead of
    replaying the oversized context forever. Never on a lock-contended defer — that is the
    OPPOSITE case (a concurrent path holds the lock and is shrinking it). Returns
    ``(response, session_entry)``."""
    # When compression is exhausted, the session is permanently too large to process. (#9893) Never wipe
    # the session for that — retry-next-message semantics apply (#69870 lock-skip consumer; salvaged
    # from #49874).
    if agent_result.get("compression_deferred"):
        logger.info(
            "Compression deferred for session %s — the compression "
            "lock is held by a concurrent compressor. Keeping the "
            "session intact; the next message retries normally.",
            session_entry.session_id if session_entry else "?",
        )
    elif agent_result.get("compression_exhausted") and session_entry and session_key:
        logger.info("Auto-resetting session %s after compression exhaustion.", session_entry.session_id)
        new_entry = await self.async_session_store.reset_session(session_key)
        self._evict_cached_agent(session_key)
        # Conversation boundary: the funnel clears every conversation-scoped per-session dict.
        self._clear_conversation_scope(session_key, reason="compression_exhausted_reset")
        if new_entry is not None:
            # Re-point the Telegram topic binding at the fresh session, or the binding-heal walk
            # switches the next message back onto the bloated child and re-triggers exhaustion
            # forever. No-op on non-topic lanes.
            # Compression rotated session_entry.session_id to the oversized compressed child earlier
            # this turn (the agent-result sync above), and that _sync also rewrote the (chat_id,
            # thread_id) -> bloated-child binding. reset_session swaps in a clean, parentless session,
            # but without re-syncing the binding the next inbound message in this topic gets
            # switch_session'd back onto the bloated child by the binding-heal walk, reloads the
            # oversized transcript, and re-triggers compression exhaustion forever (#35809 — regression
            # of the #9893/#10063 auto-reset).
            session_entry = new_entry
            await asyncio.to_thread(
                self._sync_telegram_topic_binding, source, session_entry, reason="compression-exhausted-reset",
            )
        response = (response or "") + t("gateway.session.auto_reset_context_exhausted")
    return response, session_entry
