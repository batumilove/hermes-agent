"""Admission of new turns behind exact durable response finalization."""
from agent.async_utils import run_sync_in_detached_daemon_thread
from hermes_state import SessionDB
import asyncio
import logging
logger = logging.getLogger("gateway.run")

async def _wait_for_ready_finalization(runner, db, entry):
    controller = getattr(runner, "_post_response_controller", None)
    deadline = asyncio.get_running_loop().time() + 2.0
    while await run_sync_in_detached_daemon_thread(
            db.has_post_response_barrier, entry.session_key, entry.session_id):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        # The next admitted turn can arrive between the platform send and its
        # durable settlement. Wait through that transition without admitting on
        # delivery state alone: only removal of the finalization barrier opens it.
        ready = await run_sync_in_detached_daemon_thread(
            db.post_response_finalization_ready, entry.session_key, entry.session_id)
        if ready and controller is not None:
            controller.wake()
        await asyncio.sleep(0.02)
    return True

async def _post_response_gate(self, event, source, is_internal):
    # Existing maintenance/control notifications must still run while draining.
    if is_internal and (event.metadata or {}).get("drain_admission") != "new_agent_turn":
        return None
    try:
        entry = await self.async_session_store.get_or_create_session(source)
        db = await self._async_session_db_for_active_scope()
        if isinstance(db, SessionDB) and await run_sync_in_detached_daemon_thread(
                db.has_post_response_barrier, entry.session_key, entry.session_id):
            # A queued next message may arrive just before the worker finishes
            # a delivered/definitively abandoned receipt. Wait for the actual
            # barrier removal; never admit based on delivery state alone.
            if not await _wait_for_ready_finalization(self, db, entry):
                return "⏳ This session is finishing its previous response. Please resend shortly."
    except Exception as exc:  # health: allow BLE001 -- Arbitrary scoped store failures must fail closed before model work; suppress sensitive session details.
        logger.warning("Session finalization admission failed (%s)", type(exc).__name__)
        return "⏳ Session finalization is unavailable. Please resend shortly."
    return None

def _external_drain_rejects(self, event, is_internal):
    return bool(self._external_drain_active and (
        not is_internal or (event.metadata or {}).get("drain_admission") == "new_agent_turn"))

def _turn_marker_handed_off(self, event):
    return bool(getattr(event, "_turn_marker_handoff", False)
                or getattr(event, "_gateway_post_response_handoff", None))

async def _claimed_turn_gate(self, event, source, is_internal, session_key):
    reply = await self._post_response_gate(event, source, is_internal)
    if reply:
        return True, reply
    await self._persist_active_agents_async()
    if not is_internal:
        return await self._hm_post_admission_consume(event, source, session_key)
    return False, None
