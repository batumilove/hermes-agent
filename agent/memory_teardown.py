"""Bounded, retryable ownership of an agent's memory-provider teardown."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("run_agent")


class MemoryTeardownMixin:
    def _memory_manager_release_guard(self):
        """Return the per-agent lock used to claim memory-manager teardown."""
        return getattr(
            self,
            "_memory_manager_release_lock",
            self._memory_manager_release_fallback_lock,
        )

    def _shutdown_memory_manager_serialized(
        self,
        *,
        messages: list | None = None,
        notify_session_end: bool = False,
    ) -> bool:
        """Run one bounded provider teardown while preserving retry ownership.

        The lock protects only the monotonic ownership state. Provider callbacks
        run on one daemon worker outside the lock; concurrent claimants wait on
        the same event up to the global bound and never spawn duplicate workers.
        A failed worker restores the manager, while a timed-out in-flight worker
        remains owned by the state object for a later retry observation.
        """
        guard = self._memory_manager_release_guard()
        with guard:
            state = getattr(self, "_memory_manager_shutdown_state", None)
            owner = state is None
            if owner:
                manager = getattr(self, "_memory_manager", None)
                if manager is None:
                    return True
                self._memory_manager = None
                state = {
                    "manager": manager,
                    "event": threading.Event(),
                    "result": None,
                }
                self._memory_manager_shutdown_state = state

        if owner:

            def _shutdown_worker() -> None:
                completed = False
                manager = state["manager"]
                try:
                    manager.flush_pending(timeout=10)
                    if notify_session_end:
                        try:
                            manager.on_session_end(messages or [])
                        except Exception as e:
                            logger.warning(
                                "Memory provider on_session_end failed during shutdown: %s",
                                e,
                                exc_info=True,
                            )
                            raise
                        with guard:
                            self._memory_provider_cache_evict_committed = True
                    result = manager.shutdown_all()
                    completed = result is not False
                except Exception:
                    logger.warning("Memory provider shutdown failed", exc_info=True)
                    completed = False
                finally:
                    with guard:
                        state["result"] = completed
                        if not completed:
                            self._memory_manager = manager
                        if (
                            getattr(self, "_memory_manager_shutdown_state", None)
                            is state
                        ):
                            self._memory_manager_shutdown_state = None
                        state["event"].set()

            try:
                from agent.memory_provider import spawn_context_thread

                spawn_context_thread(
                    _shutdown_worker,
                    name=f"memory-provider-shutdown-{id(self):x}",
                ).start()
            except Exception:
                logger.warning(
                    "Memory provider shutdown worker could not start", exc_info=True
                )
                with guard:
                    self._memory_manager = state["manager"]
                    self._memory_manager_shutdown_state = None
                    state["result"] = False
                    state["event"].set()
                return False

        from run_agent import _MEMORY_PROVIDER_SHUTDOWN_TIMEOUT_S

        if not state["event"].wait(timeout=_MEMORY_PROVIDER_SHUTDOWN_TIMEOUT_S):
            return False
        if state["result"] is True:
            return True
        if not owner:
            return self._shutdown_memory_manager_serialized(
                messages=messages,
                notify_session_end=notify_session_end,
            )
        return False

    def release_memory_provider_for_cache_evict(
        self,
        messages: list | None = None,
        *,
        commit: bool = False,
    ) -> bool:
        """Stop and detach per-agent memory workers during soft cache eviction.

        Cache eviction preserves resumable terminal/browser/process state, but
        the MemoryManager belongs to this Python AIAgent instance. Leaving it
        attached after the cache drops the agent strands its ``mem-sync``
        executor and external-provider workers (for Honcho, the async writer).

        With commit=True, the claimed worker commits session extraction before
        draining the provider. Successful commit is remembered across retries;
        an incomplete drain keeps teardown ownership available to retry.
        With commit=False, this method only stops provider workers.
        """
        state = getattr(self, "_memory_manager_shutdown_state", None)
        manager = state["manager"] if state else getattr(self, "_memory_manager", None)
        # A reset claims extraction before its pre-shutdown flush. Its worker
        # owns the remaining teardown even after the caller stops waiting.
        if getattr(manager, "session_end_teardown_claimed", False) is True:
            return True
        if not self._shutdown_memory_manager_serialized(
            messages=messages,
            notify_session_end=(
                commit
                and not bool(
                    getattr(self, "_memory_provider_cache_evict_committed", False)
                )
            ),
        ):
            raise RuntimeError("memory provider shutdown incomplete")
        return True
