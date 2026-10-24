"""Primary restoration and fallback episode observer lifecycle."""
from __future__ import annotations
import logging
logger = logging.getLogger("agent.agent_runtime_helpers")
from agent import agent_runtime_helpers as _source

def restore_primary_runtime(agent) -> bool:
    """Restore the primary runtime at the start of a new turn so fallback stays turn-scoped
    (long-lived CLI agents and the gateway's cached agents)."""
    if not agent._fallback_activated:
        # Reset the index even without activation: a failed _try_activate_fallback() can strand
        # _fallback_index past the chain end and silently block future fallbacks (#20465).
        from agent.chat_completion_helpers import _reset_fallback_episode
        _reset_fallback_episode(agent)
        _source._revert_credential_rotation(agent)
        return False
    rt = agent._primary_runtime
    primary_provider = str((rt or {}).get("provider") or "").strip().lower()
    primary_model = str((rt or {}).get("model") or "").strip()
    from agent.fallback_cooldown import _is_entitlement_rejected
    from hermes_cli.chat_catalog import is_known_non_chat_model
    if primary_model and (
        _is_entitlement_rejected(agent, primary_provider, primary_model)
        or is_known_non_chat_model(primary_model)
    ):
        # Unentitled (#106475) or already known non-chat: restoring would announce a recovery
        # that was never verified and re-fail every turn. Stay on the fallback.
        return False
    primary_runtime_base_url = str((rt or {}).get("base_url") or "")

    def _matches_primary(candidate) -> bool:
        return _source.credential_pool_matches_provider(candidate, primary_provider, base_url=primary_runtime_base_url)

    def _load_primary_pool():
        """Load the primary provider's pool; None when absent or provider-mismatched."""
        from agent.credential_pool import load_pool
        key = _source.resolve_runtime_pool_key(primary_provider, primary_runtime_base_url)
        loaded = load_pool(key) if key else None
        return loaded if loaded is not None and _matches_primary(loaded) else None
    if _source._primary_quota_reopened_early(agent, primary_provider, primary_model, _matches_primary, _load_primary_pool):
        agent._rate_limited_until = 0
    if getattr(agent, "_rate_limited_until", 0) > _source.time.monotonic():
        return False  # primary still in rate-limit cooldown, stay on fallback
    blocked, prefetched_pool, prefetched = _source._primary_reset_gate_blocks(
        agent, rt, primary_provider, primary_runtime_base_url, _matches_primary, _load_primary_pool
    )
    if blocked:
        return False
    agent._restore_wait_logged = False
    fallback_route = getattr(agent, "_provider_fallback_route", None)
    if not (isinstance(fallback_route, (list, tuple)) and len(fallback_route) == 2):
        fallback_route = (getattr(agent, "model", ""), getattr(agent, "provider", ""))
    previous_model, previous_provider = (str(v or "unknown") for v in fallback_route)
    provider_fallback_active = bool(getattr(agent, "_provider_fallback_active", False))
    try:
        from agent.route_binding import reinstall_primary_runtime
        reinstall_primary_runtime(
            agent, rt, primary_provider, primary_model, _matches_primary, _load_primary_pool, prefetched_pool, prefetched,
        )
        logger.info("Primary runtime restored for new turn: %s (%s)", agent.model, agent.provider)
        from agent.chat_completion_helpers import _reset_fallback_episode, _emit_fallback_observer
        _reset_fallback_episode(agent)
        _emit_fallback_observer(
            "on_primary_restored", provider=agent.provider, model=agent.model,
            session_id=getattr(agent, "session_id", None) or "", platform=getattr(agent, "platform", None) or "",
        )
        agent._provider_fallback_active = False
        agent._provider_fallback_route = None
        if provider_fallback_active:
            # Notification surfaces are best-effort and must never undo a successful restore.
            with _source.contextlib.suppress(Exception):
                agent._emit_diagnostic_status(
                    f"✅ Primary model restored: {agent.model} via {agent.provider}; "
                    f"fallback {previous_model} via {previous_provider} is no longer active."
                )
        return True
    except Exception as e:
        logger.warning("Failed to restore primary runtime: %s", e, exc_info=True)
        return False
