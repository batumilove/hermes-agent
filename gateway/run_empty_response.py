"""Normalize empty/failed agent turn results into user-facing chat replies.

Extracted from ``gateway.run`` (file-size ratchet: moved code keeps its cap); re-exported there
so existing ``from gateway.run import _normalize_empty_agent_response`` callers are unchanged.
"""
import logging

from agent.i18n import t
from agent.turn_failure_copy import SITE_FAILURE_CODES
from gateway.run_turn import is_context_overflow_failure_result

logger = logging.getLogger(__name__)

def _is_gateway_hidden_reasoning_incomplete_turn(agent_result: dict) -> bool:
    """Detect retry-exhausted turns with hidden reasoning but no visible answer.
    The loop returns the retry-exhaustion sentinel as BOTH ``final_response`` and ``error``, so a
    non-empty ``final_response`` proves nothing; any text other than the sentinel is a real answer."""
    if (not isinstance(agent_result, dict) or agent_result.get("failed")
            or agent_result.get("interrupted") or not agent_result.get("partial")):
        return False
    error_text = str(agent_result.get("error", "") or "").strip()
    if "remained incomplete after" not in error_text.lower():
        return False
    final_response = str(agent_result.get("final_response") or "").strip()
    return not final_response or final_response == error_text


def _normalize_empty_agent_response(
    agent_result: dict, response: str, *, history_len: int = 0) -> str:
    """Normalize empty/None agent responses into user-facing messages.
    Covers ``failed``, work done (api_calls > 0) with no text, and never-ran (api_calls == 0, the
    post-/stop silent-drop from a stale generation token) with a retry hint.

    Consolidates the existing ``failed`` handler and adds a catch-all for the case where the agent did work
    (api_calls > 0) but returned no text. Fix for #18765.
    Also surfaces a retry hint when the agent never ran at all (api_calls == 0) for a non-interrupted,
    non-failed turn -- this is the silent-drop pattern observed after ``/stop`` where the next user message
    hits a stale generation token and returns an empty result, leaving the platform with nothing to send.
    (#31884)

    A failed context-overflow turn whose ``final_response`` is only the raw provider envelope
    (``HTTP 400: {...}``) is rewritten too: returned unchanged, chat sanitizers turn it into a
    generic provider-failed reply and the user never sees /compact. Curated agent text survives.
    """
    from gateway.run import _context_overflow_reply, _looks_like_gateway_provider_error

    is_overflow = is_context_overflow_failure_result(agent_result, history_len)
    if response and not (is_overflow and _looks_like_gateway_provider_error(response)):
        return response
    if agent_result.get("failed"):
        # ``error`` can be an EXPLICIT None (bypasses dict.get default) -> would render "failed: None".
        error_detail = agent_result.get("error") or t("gateway.shared.unknown_error")
        error_str = str(error_detail).lower()
        # Persistence failures: suggesting /reset would destroy context without fixing storage.
        failure_reason = str(agent_result.get("failure_reason") or "")
        if failure_reason.startswith("session_persistence_failed") or "session storage" in error_str:
            if failure_reason.endswith(":disk") or "disk" in error_str:
                return t("gateway.errors.session_storage_unavailable_disk")
            return t("gateway.errors.session_storage_unavailable")
        if is_overflow:
            return _context_overflow_reply()
        # Raw exception text (class names, JSON bodies, URLs) stays in the gateway log.
        logger.warning("Agent turn failed; reply sanitized for chat. Detail: %s", str(error_detail)[:500])
        return t("gateway.errors.generic_failed")

    api_calls = int(agent_result.get("api_calls", 0) or 0)
    if agent_result.get("interrupted"):
        # Interrupted with api_calls > 0 = deliberately stopped/steered; silence is intentional (queued
        # messages arrive via the recursive drain). ZERO api_calls = never processed (stale /stop flag).
        # An interrupted run that did work (api_calls > 0) is the drain of a run the user deliberately
        # stopped or steered — its silence is intentional, and any queued/interrupting message is delivered
        # by the recursive drain inside _run_agent before this result is seen. An interrupted run with ZERO
        # api_calls never processed the user's message at all: it was killed at the top of the tool loop by
        # an interrupt flag left over from a recent /stop (#44212). Pure silence there swallows a real user
        # message, so surface it.
        # api_calls == 0, not failed, not interrupted: the agent never ran for this turn. This is the
        # post-/stop generation-race pattern where the gateway would otherwise silently drop the turn
        # (response=0 chars) and the user sees no reply at all. Surface a short retry hint so the message
        # isn't lost in silence. (#31884)
        if api_calls == 0:
            # The cause is the turn's own exit reason: a system abort (turn liveness watchdog,
            # gateway restart) is NOT a user /stop, and claiming one hides the real defect.
            exit_reason = str(agent_result.get("turn_exit_reason") or "")
            if exit_reason.startswith("interrupted_by_system("):
                issuer = exit_reason[len("interrupted_by_system("):].rstrip(")")
                return t("gateway.errors.interrupted_before_start_system", cause=issuer or "system")
            return t("gateway.errors.interrupted_before_start")
        return response
    if api_calls > 0:
        # Hidden-reasoning-only retry exhaustion: the loop's sentinel text ("Codex response remained
        # incomplete after 3 continuation attempts") doubles as final_response, so it would be delivered
        # verbatim into the channel — where peer agents can ingest it as a completed assistant turn
        # (#51628). Blank it here so the normal empty-response handling (and the suppression below) applies.
        if _is_gateway_hidden_reasoning_incomplete_turn(agent_result):
            return ""
        if agent_result.get("partial"):
            # ``error`` mirrors the loop's own final text (curated, e.g. "Response truncated due to
            # output length limit") and is kept; a raw provider envelope goes to the log instead.
            err = str(agent_result.get("error") or t("gateway.errors.processing_incomplete"))
            # A loop site code (truncated, context_overflow, ...) already wrote the full
            # what-happened / what-to-do sentence: deliver it verbatim. Wrapping it would cut it
            # mid-sentence at 200 chars and append a second, conflicting set of instructions.
            from agent.turn_failure_copy import SITE_FAILURE_CODES
            if (str(agent_result.get("failure_reason") or "") in SITE_FAILURE_CODES
                    and err.strip() and not _looks_like_gateway_provider_error(err)):
                return err if err.startswith("⚠️") else f"⚠️ {err}"
            if _looks_like_gateway_provider_error(err):
                logger.warning("Agent turn ended partially; reply sanitized for chat. Detail: %s", err[:500])
                reason = ""
            else:
                reason = f": {err[:200]}"
            return t("gateway.errors.stopped_before_finishing", reason=reason)
        return t("gateway.errors.no_response")

    # api_calls == 0, not failed/interrupted: agent never ran (post-/stop race); don't drop silently.
    if api_calls == 0 and not agent_result.get("partial"):
        return t("gateway.errors.previous_turn_cleanup")

    return response
