"""``AIAgent.run_conversation`` / ``chat`` façade.

Turn admission around ``conversation_loop.run_conversation``: durable cross-process session turn lease +
refresher thread and liveness watchdog (``agent.turn_facade_lease``), relay/accounting/portal scopes, and
balanced start/finish marks. Extracted from ``run_agent.py``; every method resolves through ``AIAgent``'s
MRO unchanged.
"""
import logging
import uuid
from contextlib import suppress
from typing import Any, Dict, Generator, List, Optional

from agent.lazy_forward import forward as _forward

# Same logger name as the origin module so log records / caplog filters are unchanged.
logger = logging.getLogger("run_agent")


class TurnFacadeMixin:
    """run_conversation()/chat() (see module docstring)."""

    def run_conversation(
        self, user_message: Any, system_message: str | None=None,
        conversation_history: list[dict[str, Any]] | None=None, task_id: str | None=None,
        stream_callback: Optional[callable]=None, persist_user_message: Optional[Any]=None,
        persist_user_timestamp: Optional[float]=None, persist_user_display_kind: Optional[str]=None,
        persist_user_display_metadata: Optional[dict[str, Any]]=None,
        persist_user_platform_id: Optional[str]=None, moa_config: Optional[dict[str, Any]]=None,
        turn_author: Optional[dict[str, Any]] = None,
        relay_metadata: Optional[dict[str, Any]] = None,
        title_user_message: Optional[str]=None,
        prelude: Optional[Generator]=None,
    ) -> dict[str, Any]:
        """Forwarder — see ``agent.conversation_loop.run_conversation``."""
        # A review shares this session_id for cache parity: fence review startup or interrupt
        # an admitted request and await its exit before opening live-turn instrumentation.
        # Foreground priority is retained if the review does not acknowledge within the bounded deadline
        # (#84423).
        from agent.background_review import cancel_background_review_for_live_turn

        # Deferred until invocation admission below; quarantined turns do no work.

        from agent import relay_runtime
        from agent.aux_accounting import reset_accounting_context, set_accounting_context
        from agent.auxiliary_client import scoped_runtime_main
        from agent.conversation_loop import run_conversation
        from agent.portal_tags import (
            reset_affinity_scope, reset_conversation_context, set_affinity_scope,
            set_conversation_context,
        )
        from agent.prompt_cache_scope import declared_conversation_scope_safe
        from agent.relay_cwd import resolve_relay_scope_cwds
        from agent.review_idle_queue import QUEUE as _review_queue
        from agent.subagent_lifecycle import bind_subagent_parent
        from agent.interrupt_scope import track_in_interrupt_scope
        from agent.turn_facade_lease import admit_durable_turn_lease, carry_unadmitted_user_message
        from hermes_cli.observability.relay_shared_metrics import finish_task_run, start_task_run

        import threading
        import sys
        invocation_lock = self.__dict__.setdefault("_turn_invocation_lock", threading.Lock())
        if not invocation_lock.acquire(blocking=False):
            raise RuntimeError("conversation invocation already active")
        if getattr(self, "_turn_quarantine", None) is not None:
            invocation_lock.release()
            raise RuntimeError("conversation invocation quarantined; explicit cessation recovery required")
        owned_tid = None
        primary_error = None
        effective_task_id = task_id or str(uuid.uuid4())
        session_id = str(getattr(self, "session_id", None) or "")
        task_context = {
            "session_id": session_id,
            "task_id": effective_task_id,
            "platform": getattr(self, "platform", None) or "",
        }
        relay_turn_id = f"{session_id or 'session'}:{effective_task_id}:{uuid.uuid4().hex[:8]}"
        self._relay_pending_turn_id = relay_turn_id
        relay_parent_session_id = (
            str(getattr(self, "_parent_session_id", None) or "")
            if task_context["platform"] == "subagent"
            else ""
        )
        relay_lease = relay_turn = lease = None
        # Scope tokens start None: early returns leave the try before the set_*() calls and
        # the finally resets each one unconditionally.
        token = affinity_token = acct_token = None
        task_started = task_finished = False
        relay_outcome = "failed"

        try:
            # First statement of the try so the finally's note_turn_finished balances every exit.
            _review_queue.note_turn_started()
            cancel_background_review_for_live_turn(self)
            admission = admit_durable_turn_lease(
                self, session_id=session_id, relay_turn_id=relay_turn_id, task_context=task_context,
                conversation_history=conversation_history,
            )
            if admission.early_result is not None:
                carry_unadmitted_user_message(
                    admission.early_result, user_message, persist_user_message,
                    timestamp=persist_user_timestamp, display_kind=persist_user_display_kind,
                    display_metadata=persist_user_display_metadata, platform_id=persist_user_platform_id,
                )
                relay_outcome = (
                    "cancelled" if admission.early_result.get("interrupted") else "timed_out"
                )
                return admission.early_result
            lease = admission.lease
            conversation_history = admission.conversation_history
            if getattr(self, "_execution_thread_id", None) is not None:
                raise RuntimeError("execution thread already owned")
            owned_tid = threading.get_ident()

            relay_session_cwd, relay_turn_cwd = resolve_relay_scope_cwds(
                self,
                effective_task_id,
                task_context["session_id"],
                task_context["platform"],
            )
            relay_lease = relay_runtime.SESSION_COORDINATOR.acquire_conversation(
                profile_key=relay_runtime.current_profile_key(),
                session_id=task_context["session_id"], platform=task_context["platform"],
                parent_session_id=relay_parent_session_id,
                model=str(getattr(self, "model", None) or ""),
                session_cwd=relay_session_cwd,
                turn_cwd=relay_turn_cwd,
            )
            relay_turn_kwargs: dict[str, Any] = {
                "turn_id": relay_turn_id,
                "task_id": effective_task_id,
            }
            if relay_metadata:
                relay_turn_kwargs["metadata"] = relay_metadata
            relay_turn = relay_runtime.SESSION_COORDINATOR.begin_turn(
                relay_lease, **relay_turn_kwargs
            )
            # Minimal relay-runtime shims may lack the opt-out flag: default enabled.
            if getattr(relay_turn, "relay_enabled", True):
                start_task_run(
                    **task_context,
                    parent_session_id=getattr(self, "_parent_session_id", None) or "",
                )
                task_started = True
            # Ambient Nous Portal tagging: every LLM call in this turn (loop, compression,
            # vision, MoA, review forks) inherits `conversation=<root>`; host-declared
            # affinity scope falls back to it; accounting handles route aux usage to the session.
            token = set_conversation_context(self._conversation_root_id())
            affinity_token = set_affinity_scope(declared_conversation_scope_safe(self))
            # Publish the session accounting handles the same way so auxiliary calls record their token
            # usage into session_model_usage (task dimension) — the fix for aux spend being invisible in
            # analytics (issue #23270).
            acct_token = set_accounting_context(
                getattr(self, "_session_db", None), getattr(self, "session_id", None)
            )

            # Keep the ContextVar scope local (agent tokens may be observed from another thread).
            # A host that owns this thread (Hermes Console) may cancel the turn cross-thread.
            with bind_subagent_parent(self), scoped_runtime_main({}), track_in_interrupt_scope(self):
                try:
                    if lease is not None:
                        lease.start()
                    result = run_conversation(
                        self, user_message, system_message, conversation_history, effective_task_id,
                        stream_callback, persist_user_message,
                        persist_user_timestamp=persist_user_timestamp,
                        persist_user_display_kind=persist_user_display_kind,
                        persist_user_display_metadata=persist_user_display_metadata,
                        persist_user_platform_id=persist_user_platform_id, moa_config=moa_config,
                        turn_author=turn_author,
                        title_user_message=title_user_message,
                        prelude=prelude,
                    )
                finally:
                    # Post-loop relay/task finalization must not receive a late refresh interrupt;
                    # the interrupt clear itself waits for the thread join in the outer finally.
                    if lease is not None:
                        if sys.exc_info()[0] is not None:
                            with suppress(Exception):
                                lease.stop_refresher()
                        else:
                            lease.stop_refresher()
            terminal = result if isinstance(result, dict) else {}
            relay_outcome = (
                "cancelled" if terminal.get("interrupted") is True
                else "failed" if terminal.get("failed") is True
                else "success"
            )
            relay_runtime.SESSION_COORDINATOR.finish_logical_calls(relay_turn, outcome=relay_outcome)
            if task_started:
                task_finished = True
                finish_task_run(**task_context, result=result)
            return result
        except BaseException as exc:
            primary_error = exc
            if isinstance(exc, (KeyboardInterrupt, InterruptedError)) or (
                type(exc).__name__ == "CancelledError"
            ):
                relay_outcome = "cancelled"
            elif isinstance(exc, TimeoutError):
                relay_outcome = "timed_out"
            def report_secondary(fn):
                try:
                    fn()
                except BaseException as secondary:
                    # Diagnostic suppliers must never replace the original body error.
                    with suppress(BaseException):
                        exc.add_note(f"turn reporting: {type(secondary).__name__}: {secondary}")
            if relay_turn is not None:
                report_secondary(lambda: relay_runtime.SESSION_COORDINATOR.finish_logical_calls(
                    relay_turn, outcome=relay_outcome
                ))
            if task_started and not task_finished:
                task_finished = True
                report_secondary(lambda: finish_task_run(**task_context, error=exc))
            raise
        finally:
            cleanup_errors = []
            def cleanup(fn):
                try:
                    fn()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            terminal_result = locals().get("result")
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
                    cleanup(lease.clear_interrupt)
                    cleanup(lease.release)
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
            invocation_lock.release()
            if cleanup_errors:
                if primary_error is not None:
                    for error in cleanup_errors:
                        with suppress(BaseException):
                            primary_error.add_note(f"turn cleanup: {type(error).__name__}: {error}")
                else:
                    raise cleanup_errors[0]

    def recover_quarantined_turn(self) -> None:
        """Explicit operator recovery, never automatic turn admission.

        Retry retained cancellation joins. Keep quarantine/token/owner on any
        uncertainty or cleanup failure. No reaper and no force-clear escape hatch.
        """
        import threading
        from tools.interrupt import set_interrupt
        lock = self.__dict__.setdefault("_turn_invocation_lock", threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError("conversation invocation already active")
        try:
            state = getattr(self, "_turn_quarantine", None)
            if state is None:
                return
            lease, tid = state["lease"], state["owned_tid"]
            lease.stop_refresher()
            if lease.join_threads() is not True:
                raise RuntimeError("worker cessation unconfirmed; invocation remains quarantined")
            lease.clear_interrupt()
            lease.release()
            if tid is not None:
                set_interrupt(False, tid)
                if getattr(self, "_execution_thread_id", None) == tid:
                    self._execution_thread_id = None
            self._turn_quarantine = None
        finally:
            lock.release()

    def chat(self, message: str, stream_callback: Optional[callable] = None) -> str:
        """Final response string of one turn; ``stream_callback`` receives each text delta."""
        return self.run_conversation(message, stream_callback=stream_callback)["final_response"]

    _run_codex_app_server_turn = _forward("agent.codex_runtime", "run_codex_app_server_turn")

    def _release_idle_turn_client(self, result):
        """Retire idle request resources without masking a terminal result/error."""
        try:
            self._close_idle_cached_request_openai_client(reason="turn_complete")
        except Exception as exc:
            logger.warning("request client turn cleanup failed: %s", exc, exc_info=True)
            if isinstance(result, dict):
                result.setdefault("cleanup_errors", []).append(
                    f"close_request_client: {type(exc).__name__}: {exc}"
                )
