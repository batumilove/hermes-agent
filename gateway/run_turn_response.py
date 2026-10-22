"""Final response shaping and compression route compare-and-set publication."""
import sqlite3
import asyncio
import logging
import time
from typing import Optional
from agent.i18n import t
from gateway.response_filters import silence_allowed, is_machinery_display_kind
logger = logging.getLogger("gateway.run")

async def _hmwa_shape_agent_response(
    self, agent_result, source, history, session_entry, session_key,
    _quick_key, run_generation, _run_start_session_id, _platform_name, _msg_start_time,
    persist_user_display_kind: Optional[str] = None,
    reply_expected: Optional[bool] = None,
    event=None,
):
    """Turn the raw agent result into the outbound text: sentinel/silence handling, response
    logging, resume-pending clear, empty-response normalization, and identity-guarded
    post-compression session_id propagation. Returns
    ``(response, _intentional_silence, agent_messages)``."""
    from gateway.run_turn import _unexpected_silence_reply
    from gateway.run import (
        _is_gateway_hidden_reasoning_incomplete_turn, _normalize_empty_agent_response,
        _sanitize_gateway_final_response, _should_clear_resume_pending_after_turn,
    )
    response = agent_result.get("final_response") or ""
    # Hidden-reasoning-only retry exhaustion: the loop's sentinel text doubles as final_response
    # and would be delivered verbatim (peer agents would ingest it as a completed turn).
    if _is_gateway_hidden_reasoning_incomplete_turn(agent_result):
        response = ""
    _intentional_silence = self._is_intentional_silence(agent_result, response)
    # A queued (/queue) chain's TERMINAL turn owns the silence verdict, not the event that
    # opened the chain: an internal follow-up, or a message not addressed to the bot, may go
    # silent; any other human one must not.
    _silence_kind = agent_result.get("queued_terminal_display_kind", persist_user_display_kind)
    _silence_reply_expected = agent_result.get("queued_terminal_reply_expected", reply_expected)
    if _intentional_silence and not silence_allowed(_silence_kind, _silence_reply_expected):
        logger.warning(
            "silence marker rejected on a user turn: platform=%s chat=%s",
            _platform_name, source.chat_id or "unknown",
        )
        _intentional_silence = False
        response = _unexpected_silence_reply()
    elif _intentional_silence and not is_machinery_display_kind(_silence_kind):
        logger.debug(
            "silence marker suppressed on an unaddressed turn: platform=%s chat=%s",
            _platform_name, source.chat_id or "unknown",
        )

    # "(empty)" = the model produced no visible content after exhausting all retries. One
    # text with the CLI explainer and the desktop (agent/turn_explainers.py) so the user
    # reads the same words on every surface.
    if response == "(empty)" and not _intentional_silence:
        from agent.turn_explainers import EMPTY_RESPONSE_EXPLANATION

        _model = str(agent_result.get("model") or "").strip() or t("gateway.errors.empty_response_model_label")
        response = t("gateway.shared.warn_passthrough", error=EMPTY_RESPONSE_EXPLANATION.format(model=_model))
    agent_messages = agent_result.get("messages", [])
    logger.info(
        "response ready: platform=%s chat=%s session=%s time=%.1fs api_calls=%d response=%d chars",
        _platform_name, source.chat_id or "unknown", session_key or "unknown",
        time.time() - _msg_start_time, agent_result.get("api_calls", 0), len(response),
    )

    if not _intentional_silence:
        response = _normalize_empty_agent_response(agent_result, response, history_len=len(history))
        response = _sanitize_gateway_final_response(source.platform, response)
    defer_final = bool(
        response and session_key and getattr(event, "_gateway_active_turn_token", None)
        and not getattr(event, "internal", False) and not _intentional_silence
        and not agent_result.get("already_sent"))
    if event is not None:
        event._gateway_defer_final_persistence = defer_final

    # Successful turn: clear the consecutive-restart stuck-loop counter and resume_pending (set
    # by drain-timeout shutdown) so later messages don't get the restart-interruption note.
    if not defer_final and session_key and _should_clear_resume_pending_after_turn(agent_result):
        await self._clear_restart_failure_count(session_key)
        try:
            await self.async_session_store.clear_resume_pending(session_key)
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as _e:
            logger.debug("clear_resume_pending failed for %s: %s", session_key, _e)

    # Normalize empty responses: surface errors, partial failures, and work-without-text.
    # Fix for #18765.

    # Compression may publish a child only while this run still owns the original route.
    child_id = agent_result.get("session_id")
    if not defer_final and child_id and child_id != session_entry.session_id:
        rebound = await self.async_session_store.rebind_session_id(
            session_key, _run_start_session_id, child_id)
        if rebound:
            session_entry.session_id = child_id
            self._rebind_turn_lease(_quick_key, run_generation, child_id)
            await self.async_session_store._record_gateway_session_peer(child_id, session_key, source)
            await asyncio.to_thread(
                self._sync_telegram_topic_binding, source, session_entry, reason="agent-result-compression")
        else:
            logger.info("Skipping compressed child publication because route ownership changed: %s", session_key)
    return response, _intentional_silence, agent_messages
