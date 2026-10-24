"""Auxiliary pool credential refresh with the actual failed bearer."""
from __future__ import annotations
import logging
logger = logging.getLogger("agent.auxiliary_client")
from typing import Optional
from agent import auxiliary_client as _source

def _resolve_nous_pool_runtime_api(*, force_refresh: bool = False, stale_access_token: Optional[str] = None) -> Optional[tuple[str, str]]:
    """Resolve Nous auxiliary credentials from the selected pool entry."""
    try:
        from hermes_cli.auth import _agent_key_is_usable
        pool = _source.load_pool("nous")
    except Exception as exc:
        logger.debug("Auxiliary Nous pool credential resolution failed: %s", exc, exc_info=True)
        return None
    if not pool or not pool.has_credentials():
        return None
    try:
        entry = pool.select()
    except Exception as exc:
        logger.debug("Auxiliary Nous pool selection failed: %s", exc, exc_info=True)
        return None
    if entry is None:
        return None

    def _entry_state(e: _source.Any) -> dict[str, _source.Any]:
        return {k: getattr(e, k, None) for k in (
            "agent_key", "agent_key_expires_at", "access_token", "expires_at", "scope")}

    if force_refresh or not _agent_key_is_usable(_entry_state(entry), _source._nous_min_key_ttl_seconds()):
        try:
            refreshed = (pool.try_refresh_matching(api_key_hint=stale_access_token, credential_id=entry.id)
                         if stale_access_token else pool.try_refresh_current())
        except Exception as exc:
            logger.debug("Auxiliary Nous pool refresh failed: %s", exc, exc_info=True)
            refreshed = None
        if refreshed is None:
            return None
        entry = refreshed
    api_key = _source._nous_api_key(_entry_state(entry))
    base_url = _source._pool_runtime_base_url(entry, _source._NOUS_DEFAULT_BASE_URL)
    if not api_key or not base_url:
        return None
    return api_key, base_url
