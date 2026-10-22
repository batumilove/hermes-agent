"""Fallback transition observers and exhaustion cooldown."""
from __future__ import annotations
from agent.error_classifier import FailoverReason
from agent import chat_completion_helpers as _source

def _reset_fallback_episode(agent) -> None:
    """Re-arm the observer once a new fallback episode can begin."""
    agent._fallback_activated = False
    agent._fallback_index = 0
    agent._fallback_exhaustion_notified = False

def _emit_fallback_observer(name: str, **payload) -> None:
    """Telemetry at the edges is fail-open and never changes routing."""
    with _source.contextlib.suppress(Exception):
        from hermes_cli.plugins import has_hook, invoke_hook
        if has_hook(name):
            invoke_hook(name, **payload)

def _fallback_chain_exhausted(agent, reason: FailoverReason | None) -> bool:
    """Chain exhausted (always False). A non-empty chain walked on a non-rate-limit failure arms a
    short cooldown so next turn's restore_primary_runtime stays gated instead of replaying the whole
    context across every provider again."""
    if agent._fallback_chain and not getattr(agent, "_fallback_exhaustion_notified", False):
        agent._fallback_exhaustion_notified = True
        primary = getattr(agent, "_primary_runtime", None) or {}
        _source._emit_fallback_observer(
            "on_fallback_chain_exhausted", provider=getattr(agent, "provider", "") or "",
            model=getattr(agent, "model", "") or "", primary_provider=primary.get("provider") or "",
            primary_model=primary.get("model") or "", reason=getattr(reason, "value", reason),
            chain_length=len(agent._fallback_chain), session_id=getattr(agent, "session_id", None) or "",
            platform=getattr(agent, "platform", None) or "",
        )
    from agent.fallback_cooldown import _RATE_LIMIT_FAILOVER_REASONS
    if agent._fallback_chain and reason not in _RATE_LIMIT_FAILOVER_REASONS:
        agent._rate_limited_until = max(
            getattr(agent, "_rate_limited_until", 0) or 0, _source.time.monotonic() + _source._FALLBACK_EXHAUSTED_COOLDOWN_S)
    return False
