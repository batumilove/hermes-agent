"""Admission of new turns behind exact durable response finalization."""
from agent.async_utils import run_sync_in_detached_daemon_thread
from hermes_state import SessionDB
import logging
logger = logging.getLogger("gateway.run")

async def _post_response_gate(self, event, source, is_internal):
    # Existing maintenance/control notifications must still run while draining.
    if is_internal and (event.metadata or {}).get("drain_admission") != "new_agent_turn":
        return None
    try:
        entry = await self.async_session_store.get_or_create_session(source)
        db = await self._async_session_db_for_active_scope()
        if isinstance(db, SessionDB) and await run_sync_in_detached_daemon_thread(
                db.has_post_response_barrier, entry.session_key, entry.session_id):
            return "⏳ This session is finishing its previous response. Please resend shortly."
    except Exception as exc:  # health: allow BLE001 -- Arbitrary scoped store failures must fail closed before model work; suppress sensitive session details.
        logger.warning("Session finalization admission failed (%s)", type(exc).__name__)
        return "⏳ Session finalization is unavailable. Please resend shortly."
    return None

def _external_drain_rejects(self, event, is_internal):
    return bool(self._external_drain_active and (
        not is_internal or (event.metadata or {}).get("drain_admission") == "new_agent_turn"))
