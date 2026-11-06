"""Owned invocation cleanup; keep every cleanup attempt independent."""
import logging
from contextlib import suppress

logger = logging.getLogger("run_agent")


def cleanup_owned_turn(self, *, result, relay_turn, relay_lease, relay_outcome,
                       lease, owned_tid, relay_turn_id, acct_token, token,
                       affinity_token, primary_error):
    from agent import relay_runtime
    from agent.aux_accounting import reset_accounting_context
    from agent.portal_tags import reset_affinity_scope, reset_conversation_context
    from agent.review_idle_queue import QUEUE as _review_queue

    cleanup_errors = []
    def cleanup(fn):
        try:
            fn()
        except BaseException as exc:
            logger.warning("turn cleanup failed", exc_info=True)
            cleanup_errors.append(exc)
    terminal_result = result
    cleanup(lambda: self._release_idle_turn_client(terminal_result))
    if relay_turn is not None:
        cleanup(lambda: relay_runtime.SESSION_COORDINATOR.end_turn(relay_turn, outcome=relay_outcome))
    if relay_lease is not None:
        cleanup(lambda: relay_runtime.SESSION_COORDINATOR.release_conversation(relay_lease))
    ceased = lease is None
    if lease is not None:
        cleanup(lease.stop_refresher)
        try:
            ceased = lease.join_threads() is True
        except BaseException as exc:
            logger.warning("turn cleanup failed", exc_info=True)
            cleanup_errors.append(exc)
        if not ceased:
            # Keep cancellation and lease identity reachable; no background reaper.
            self._turn_quarantine = {"lease": lease, "owned_tid": owned_tid,
                                     "reason": "worker cessation unconfirmed"}
            if owned_tid is not None:
                from tools.interrupt import set_interrupt
                cleanup(lambda: set_interrupt(True, owned_tid))
            cleanup_errors.append(RuntimeError("worker cessation unconfirmed; invocation quarantined"))
        else:
            ownership_errors = len(cleanup_errors)
            cleanup(lease.release)
            # Clear cancellation only after holder cleanup has returned successfully.
            if len(cleanup_errors) == ownership_errors:
                cleanup(lease.clear_interrupt)
            if len(cleanup_errors) != ownership_errors:
                ceased = False
                self._turn_quarantine = {"lease": lease, "owned_tid": owned_tid,
                                         "reason": "ownership cleanup unconfirmed"}
                if owned_tid is not None:
                    from tools.interrupt import set_interrupt
                    cleanup(lambda: set_interrupt(True, owned_tid))
    # Only this admitted invocation with confirmed cessation may clear its token.
    if ceased and owned_tid is not None and getattr(self, "_execution_thread_id", None) == owned_tid:
        from tools.interrupt import set_interrupt
        cleanup(lambda: set_interrupt(False, owned_tid))
        self._execution_thread_id = None
    if owned_tid is not None:
        cleanup(self._reset_activity_labels_after_turn)
    if getattr(self, "_relay_pending_turn_id", None) == relay_turn_id:
        self._relay_pending_turn_id = None
    if acct_token is not None:
        cleanup(lambda: reset_accounting_context(acct_token))
    if token is not None:
        cleanup(lambda: reset_conversation_context(token))
    if affinity_token is not None:
        cleanup(lambda: reset_affinity_scope(affinity_token))
    cleanup(_review_queue.note_turn_finished)
    if cleanup_errors:
        if primary_error is not None:
            for error in cleanup_errors:
                with suppress(BaseException):
                    primary_error.add_note(f"turn cleanup: {type(error).__name__}: {error}")
        else:
            raise cleanup_errors[0]
