"""Aggregate shutdown deadlines and per-agent preservation for the gateway phases."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import threading
import time
from contextlib import suppress
from typing import Any, Optional

logger = logging.getLogger("gateway.run")


class GatewayShutdownBudgetMixin:
    """Own bounded interrupt, preservation and shutdown orchestration."""

    async def _interrupt_running_agents(self, reason: str, deadline: float) -> None:
        from gateway.run_runtime import stop_managed_turns, interrupt_side_agents
        from gateway.run_shutdown import _log_suppressed
        from gateway.run import _AGENT_PENDING_SENTINEL, _INTERRUPT_TOOL_REASON_GATEWAY_SHUTDOWN, request_hard_interrupt
        def interrupt(session_key, agent):
            with _log_suppressed(logging.DEBUG, "Failed interrupting agent during shutdown: %s"):
                request_hard_interrupt(agent, reason, tool_reason=_INTERRUPT_TOOL_REASON_GATEWAY_SHUTDOWN)
                logger.debug("Interrupted running agent for session %s during shutdown", session_key)

        workers = []
        for index, (session_key, agent) in enumerate(list(self._running_agents.items())):
            if agent is _AGENT_PENDING_SENTINEL:
                continue
            worker_context = contextvars.copy_context()
            worker = threading.Thread(target=worker_context.run, args=(interrupt, session_key, agent),
                                      name=f"gateway-shutdown-interrupt-{index}", daemon=True)
            worker.start()
            workers.append(worker)
        # API-server / desk turns are adapter-owned and never enter _running_agents, so the loop above
        # cannot see them even though _drain_active_agents() waited for them.
        for count, what in (
            (self._interrupt_api_server_runs(reason), "api_server run(s)"),
            (self._interrupt_deferred_agent_workers(reason), "deferred agent worker(s)"),
            (stop_managed_turns(self, reason), "managed-worker turn(s)"),
            (interrupt_side_agents(self, reason), "side agent(s)"),
        ):
            if count:
                logger.debug("Interrupted %d %s during shutdown", count, what)
        while any(worker.is_alive() for worker in workers) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)


    def _stop_post_response_writer(self, ctx) -> bool:
        """Seal durable claims and bound the join before any SessionDB closes."""
        remaining = 1.0 if ctx.teardown_deadline is None else max(
            0.0, ctx.teardown_deadline - time.monotonic(),
        )
        stopped = self.shutdown_post_response_worker(timeout=min(1.0, remaining))
        if not stopped:
            ctx.budget_exhausted = True
        return stopped


    def _stop_quiesce_and_close_session_dbs(self, timeout: float, ctx: Any) -> None:
        """Quiesce the executor, then close SessionDB handles only if no worker is still live."""
        from gateway.run import GatewayRunner, _EXECUTOR_QUIESCE_TIMEOUT
        from gateway.run_shutdown import GatewayShutdownMixin, resolve_shutdown_watchdog_delay
        post_response_stopped = self._stop_post_response_writer(ctx)
        # Quiesce the thread pool BEFORE closing session DBs: a late executor write after
        # SessionDB.close() checkpointed the WAL reopens the handle and splits the WAL generation
        # (close-time corruption). Clamped to the remaining watchdog leash minus 1s for the close.
        # This used to run *after* the close block below, which left two holes: (a) `_executor_closing` was
        # still False during the close, so any coroutine reaching `_run_in_executor_with_context` minted a
        # brand-new pool and ran more blocking DB work against handles that had just been closed; (b)
        # cancelling `self._background_tasks` above does not stop a `run_in_executor` future that already
        # started — the task dies, the worker thread keeps writing. Either way a write lands after
        # `SessionDB.close()`, which has already checkpointed the WAL and let SQLite unlink the sidecar. The
        # late write silently reopens the handle (#94736) and mints a fresh WAL generation behind that
        # checkpoint, so teardown checkpoints the same file a second time from a connection the shutdown log
        # never accounts for — the close-time page-write damage in #101093 and the split WAL generation in
        # #101064. The wait is bounded and clamped to what is left of the shutdown watchdog leash (minus a
        # second for the close itself), so a stuck worker can never cost us the post-close cleanup window
        # (#82161).
        _exec_quiesce_budget = max(
            0.0, min(_EXECUTOR_QUIESCE_TIMEOUT, resolve_shutdown_watchdog_delay(timeout) - ctx.elapsed() - 1.0),
        )
        _exec_live = GatewayRunner._shutdown_executor(self, drain_timeout=_exec_quiesce_budget)
        if _exec_live:
            # A live worker may be mid-write (the #101093 corruption sequence): skip the close and let
            # SQLite recover from its WAL on next open (at worst a transient "database is locked").
            logger.warning(
                "Shutdown phase: %d executor worker(s) still running after a %.2fs quiesce — skipping the "
                "SessionDB close/checkpoint to avoid racing a live write (#101093); handles are left "
                "open for SQLite to recover on next open", _exec_live, _exec_quiesce_budget,
            )
            return
        logger.info("Shutdown phase: executor quiesced at +%.2fs", ctx.elapsed())
        if not post_response_stopped:
            logger.warning("Post-response writer is still live; skipping SessionDB close/checkpoint")
            return
        # Cron jobs (scheduler pool), API-server runs and deferred hygiene workers (both on the loop's
        # default executor) never touch self._executor, so the join above cannot see them. A writer that
        # outlived the drain is mid-write for the same #101093 reasons; the drain already spent its
        # budget, so no second wait — leave the handles open (#102198). The API count is the snapshot
        # taken before the adapters were released; a run whose handler task was cancelled at disconnect
        # has already left it, so that term under-counts — the live worker-scoped count below covers
        # the cancelled-handler case (#116535).
        _cron_live = self._active_cron_job_count()
        _api_live = ctx.api_live
        _api_worker_live = self._active_api_worker_count()
        from gateway.run_runtime import managed_turn_count
        _deferred_live = ctx.deferred_count() + managed_turn_count(self)
        if _cron_live or _api_live or _api_worker_live or _deferred_live:
            logger.warning(
                "Shutdown phase: %d cron job(s) / %d API-server run(s) / %d API-server worker(s) / "
                "%d deferred worker(s) still running after the executor quiesce — skipping the SessionDB "
                "close/checkpoint, leaving state.db open for the live writer (#102198, #116535)",
                _cron_live, _api_live, _api_worker_live, _deferred_live,
            )
            return
        _step = GatewayShutdownMixin._quiet_step
        # Close SQLite session DBs so --replace's new gateway does not hit 'database is locked'.
        # ``_session_db`` is an AsyncSessionDB facade — unwrap; ``session_store`` holds ``_db``.
        _self_db = getattr(self, "_session_db", None)
        _self_db = getattr(_self_db, "_db", _self_db)
        store = getattr(self, "session_store", None)
        for _db in (_self_db, getattr(store, "_db", None)):
            if _db is not None and hasattr(_db, "close"):
                _step("SessionDB close error", _db.close)
        # Multiplexed session_store caches one SessionDB per profile; sweep the secondary WAL locks too.
        _sweep = getattr(store, "close_all_db_handles", None)
        if _sweep is not None:
            _step("SessionDB handle sweep error", _sweep)
        # Same sweep for the runner's own per-profile session_search handles.
        _step("Runner SessionDB handle sweep error", lambda: GatewayRunner.close_all_session_db_handles(self))

        def _close_shared() -> None:
            # Shared SessionDB instances still held by the process-wide registry (tools, cron, mirror).
            # This is the safety net that guarantees no WAL write lock survives past gateway shutdown
            # (#90837).
            from hermes_state_registry import close_all
            closed = close_all()
            if closed:
                logger.debug("Closed %d shared SessionDB instance(s) at shutdown", closed)

        _step("Shared SessionDB close error", _close_shared)
        logger.info("Shutdown phase: SessionDB close done at +%.2fs", ctx.elapsed())

    async def _finalize_shutdown_agents(self, active_agents: dict[str, Any]) -> bool:
        deadline = getattr(self, "_shutdown_cleanup_deadline", None)
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + self._CLEANUP_TIMEOUT_S
        for session_key, agent in active_agents.items():
            budget = min(self._CLEANUP_TIMEOUT_S, self._shutdown_remaining(deadline))
            if budget <= 0 or not await self._run_shutdown_sync_daemon(
                self._run_release_in_profile_scope, self._flush_agent_transcript_at_shutdown,
                (agent,), session_key, timeout=budget, context="agent transcript persistence",
            ):
                return False
            # Off-loop + bounded: plugin on_session_finalize hooks can do arbitrary synchronous work
            # (e.g. a full-session trace export) — same hang class as the memory provider below.
            messages = await self._await_shutdown_phase(self._finalize_session_off_loop(
                session_id=getattr(agent, "session_id", None), platform="gateway", reason="shutdown",
                session_key=session_key,
            ), deadline=deadline, context="agent session finalization")
            if messages is None:
                return False
            try:  # adapters are still connected here (teardown runs after this loop)
                await self._deliver_session_end_messages(messages, session_key=session_key)
            except Exception:
                logger.debug("Session-end plugin message delivery failed for %s", session_key, exc_info=True)
            # Off-loop + bounded: a wedged memory provider here used to hang the whole shutdown so
            # SIGTERM never completed.
            budget = min(self._CLEANUP_TIMEOUT_S, self._shutdown_remaining(deadline))
            if not await self._cleanup_agent_resources_off_loop(
                agent, context="shutdown finalize", session_key=session_key, timeout=budget,
            ):
                logger.warning("Shutdown agent resource cleanup stalled: session=%s phase=%s",
                               getattr(agent, "session_id", "unknown"),
                               getattr(agent, "_shutdown_cleanup_phase", "unknown"))
                return False
        return True


    async def _await_shutdown_phase(self, work, *, deadline: float, context: str):
        """Bound asynchronous teardown without waiting on cancellation-resistant work."""
        remaining = self._shutdown_remaining(deadline)
        if remaining <= 0:
            work.close()
            logger.warning("Skipping shutdown %s: aggregate deadline exhausted", context)
            return None
        task = asyncio.create_task(work)
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if done:
            return task.result()
        task.cancel()
        def settled(future):
            if not future.cancelled() and (error := future.exception()) is not None:
                logger.warning("Late shutdown %s failed: %s", context, error)
        task.add_done_callback(settled)
        logger.warning("Shutdown %s exceeded aggregate deadline; forcing forward progress", context)
        return None


    async def _stop_impl(self) -> None:
        """Run every ``_stop_*`` phase under the thread-based shutdown watchdog."""
        from gateway.run_shutdown import GatewayShutdownMixin, _effective_watchdog_leash
        from gateway.run import GatewayRunner
        from gateway.restart import effective_stop_drain_timeout
        from gateway.shutdown_watchdog import arm_shutdown_watchdog
        # Thread-based watchdog (asyncio timeouts cannot recover a frozen loop): dumps stacks and
        # os._exit past drain+grace so the service manager revives us. Skipped under pytest.
        # Arm a plain OS thread at the start of stop(); if teardown never finishes within drain+grace it
        # dumps faulthandler stacks and os._exit so KeepAlive/systemd can revive. Skip under pytest so
        # stop()-driving unit tests don't get a delayed hard-exit in the worker. See #66892.
        _watchdog_done = threading.Event()
        self._shutdown_watchdog_done = _watchdog_done
        # Shutdown-path doubles may lack the deferred-worker counter.
        ctx = GatewayShutdownMixin._StopContext(
            deferred_count=getattr(self, "_active_deferred_agent_worker_count", lambda: 0)
        )
        ctx.started_at = time.monotonic()
        ctx.shutdown_deadline = asyncio.get_running_loop().time() + _effective_watchdog_leash(self)
        attribution_reserve = max(4 * self._DRAIN_ATTRIBUTION_WRITE_TIMEOUT_S,
                                  self._SHUTDOWN_TERMINAL_ATTRIBUTION_RESERVE_S)
        ctx.teardown_deadline = ctx.shutdown_deadline - attribution_reserve
        ctx.cleanup_deadline = min(ctx.teardown_deadline, ctx.shutdown_deadline - self._SHUTDOWN_TAIL_RESERVE_S)
        self._shutdown_cleanup_deadline = ctx.cleanup_deadline
        self._shutdown_work_attribution = self._shutdown_work_attribution_snapshot()
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            arm_shutdown_watchdog(
                _effective_watchdog_leash(self), done_event=_watchdog_done,
                snapshot_fn=lambda: GatewayRunner._shutdown_watchdog_snapshot(self, ctx), exit_code=1,
            )
        try:
            await self._record_drain_attribution("begin", deadline=ctx.cleanup_deadline)
            if await self._await_shutdown_phase(
                GatewayRunner._stop_begin_teardown(self, ctx), deadline=ctx.cleanup_deadline,
                context="startup of teardown and notifications",
            ) is None and self._shutdown_remaining(ctx.cleanup_deadline) <= 0:
                ctx.budget_exhausted = True
            timeout = effective_stop_drain_timeout(self)
            if timeout < self._restart_drain_timeout:
                logger.warning(
                    "Shutdown drain capped to %.0fs (configured %.0fs) to fit the live launchd exit "
                    "timeout of %.0fs — launchd SIGKILLs past it",
                    timeout, self._restart_drain_timeout, self._launchd_exit_timeout_s,
                )
            await GatewayRunner._stop_drain_active_work(self, timeout, ctx)
            if ctx.timed_out:
                await GatewayRunner._stop_interrupt_remaining_work(self, ctx)
            await self._record_drain_attribution("post_interrupt", deadline=ctx.cleanup_deadline)
            from gateway.run_runtime import settle_gateway_runtime
            await settle_gateway_runtime(self, budget=self._shutdown_remaining(ctx.cleanup_deadline),
                                         reason=GatewayRunner._shutdown_interrupt_reason(self))
            await GatewayRunner._stop_finalize_agents_and_adapters(self, ctx)
            await self._await_shutdown_phase(
                GatewayRunner._stop_release_runtime_state(self, ctx), deadline=ctx.teardown_deadline,
                context="runtime state release",
            )
            await self._run_shutdown_sync_daemon(
                GatewayRunner._stop_quiesce_and_close_session_dbs, self, timeout, ctx,
                timeout=self._shutdown_remaining(ctx.teardown_deadline), context="SessionDB close",
            )
            await self._record_drain_attribution(
                "cleanup_incomplete" if ctx.budget_exhausted or ctx.timed_out else "clean_exit",
                deadline=ctx.shutdown_deadline,
            )
            await self._await_shutdown_phase(
                GatewayRunner._stop_persist_exit_state(self, ctx), deadline=ctx.shutdown_deadline,
                context="terminal runtime identity",
            )
        finally:
            self._running = False
            self._shutdown_event.set()
            _watchdog_done.set()



    async def _cleanup_agent_resources_off_loop(
        self, agent: Any, *, context: str = "", session_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """Run _cleanup_agent_resources in a worker thread, bounded; on timeout the worker is left alone.

        The teardown fires the memory-provider lifecycle hooks (``flush_pending`` → ``on_session_end`` →
        ``shutdown`` → ``close``), which read credentials/home at call time. In-turn callers carry the
        profile scope through ``_run_housekeeping_in_executor``; shutdown does not (it runs on the main
        loop, outside any adapter handler), so under multiplexing ``on_session_end`` failed closed and the
        session tail was never committed (#110622). ``_run_release_in_profile_scope`` enters the OWNING
        profile's scope from ``session_key`` when the caller has none, exactly like cache eviction."""
        if agent is None:
            return True
        if context.startswith("shutdown") or context == "session expiry":
            with suppress(Exception):
                agent._end_session_on_close = False
        ctx_label = f" ({context})" if context else ""
        budget = self._CLEANUP_TIMEOUT_S if timeout is None else min(self._CLEANUP_TIMEOUT_S, max(0.0, timeout))
        if budget <= 0:
            return False
        if context.startswith("shutdown"):
            return await self._run_shutdown_sync_daemon(
                self._run_release_in_profile_scope, self._cleanup_agent_resources, (agent,), session_key,
                timeout=budget, context=context,
            )
        try:
            result = await asyncio.wait_for(
                self._run_housekeeping_in_executor(
                    self._run_release_in_profile_scope, self._cleanup_agent_resources, (agent,), session_key,
                ),
                timeout=budget,
            )
        except TimeoutError:
            logger.warning(
                "Agent resource cleanup%s exceeded %ss; proceeding without blocking the event loop (the worker "
                "thread is left to finish on its own). (#53175)", ctx_label, self._CLEANUP_TIMEOUT_S,
            )
            return False
        except Exception as cleanup_exc:
            logger.warning("Agent resource cleanup%s failed: %s (#53175)", ctx_label, cleanup_exc, exc_info=True)
            return False
        return result is not False


    def _cleanup_agent_resources(self, agent: Any) -> bool:
        """Best-effort cleanup for temporary or cached agent instances."""
        if agent is None:
            return True
        from gateway.run import _record_lifecycle

        def _record_phase(phase: str) -> None:
            # The shutdown waiter may time out while this daemon thread remains
            # blocked. Keep the last entered subphase on the agent so the main
            # loop can report the exact cleanup boundary that stalled.
            try:
                agent._shutdown_cleanup_phase = phase
            except (AttributeError, TypeError):
                logger.debug("Could not record agent cleanup phase", exc_info=True)

        _lifecycle = getattr(self, "_lifecycle_counters", None)
        _record_lifecycle(_lifecycle, "record_hard_cleanup", attempted=True)
        cleanup_success = True
        _record_phase("memory.flush_pending")
        try:
            if hasattr(agent, "shutdown_memory_provider"):
                # Drain queued memory writes BEFORE tearing the provider down.
                # The memory manager persists per-turn sync and end-of-session
                # extraction on a single serialized background worker.
                # shutdown_memory_provider() -> shutdown_all() only gives that
                # worker a ~5s bounded drain and abandons (cancels) anything
                # still queued past it, so a /reset — or any gateway session
                # rotation that reaches this cleanup path — could silently drop
                # writes the session had already handed off. The next session
                # then loads stale memory (#73297). Give pending work a bounded
                # head start through the manager's own barrier first, mirroring
                # the CLI exit path (cli.py). Best-effort: a flush failure must
                # never block teardown.
                _mm = getattr(agent, "_memory_manager", None)
                if _mm is not None and hasattr(_mm, "flush_pending"):
                    try:
                        _mm.flush_pending(timeout=10)
                    except Exception:
                        logger.debug("Pending memory flush failed during cleanup", exc_info=True)
                        cleanup_success = False
                _record_phase("memory.shutdown_provider")
                # Pass the agent's own conversation transcript so memory
                # providers' ``on_session_end`` hooks see the real messages
                # instead of the empty default (#15165). ``_session_messages``
                # is set on ``AIAgent`` (run_agent.py:1518) and refreshed at
                # the end of every ``run_conversation`` turn via
                # ``_persist_session``; on an agent built through
                # ``object.__new__`` (test stubs) the attribute may be
                # absent, so ``getattr`` with a ``None`` default keeps the
                # call signature-compatible with the pre-fix behaviour
                # (``shutdown_memory_provider(messages=None)``).
                session_messages = getattr(agent, "_session_messages", None)
                if isinstance(session_messages, list):
                    memory_completed = agent.shutdown_memory_provider(session_messages)
                else:
                    memory_completed = agent.shutdown_memory_provider()
                if memory_completed is False:
                    cleanup_success = False
        except Exception:
            logger.debug("Memory provider shutdown failed during cleanup", exc_info=True)
            cleanup_success = False
        # Close tool resources (terminal sandboxes, browser daemons,
        # background processes, httpx clients) to prevent zombie
        # process accumulation.
        _record_phase("agent.close")
        close_attempted = hasattr(agent, "close")
        close_success = False
        try:
            if close_attempted:
                agent.close()
                close_success = True
        except Exception:
            logger.debug("Agent close failed during cleanup", exc_info=True)
            cleanup_success = False
        if close_attempted:
            _record_lifecycle(
                _lifecycle,
                "record_agent_close",
                attempted=True,
                success=close_success,
            )
        # Auxiliary async clients (session_search/web/vision/etc.) live in a
        # process-global cache and are created inside worker threads. Clean up
        # any entries whose event loop is now dead so their httpx transports do
        # not accumulate across gateway turns.
        _record_phase("auxiliary_client.cleanup_stale")
        try:
            from agent.auxiliary_client import cleanup_stale_async_clients
            cleanup_stale_async_clients()
        except (OSError, RuntimeError, ValueError):
            logger.debug("Stale auxiliary client cleanup failed", exc_info=True)
            cleanup_success = False
        _record_lifecycle(
            _lifecycle,
            "record_hard_cleanup",
            attempted=False,
            success=cleanup_success,
        )
        _record_phase("complete")
        return cleanup_success

    def _shutdown_watchdog_snapshot(self, ctx: Any) -> dict:
        """State dumped by the thread-based shutdown watchdog when teardown hangs."""
        from gateway.restart import effective_stop_drain_timeout
        from gateway.run_shutdown import _effective_watchdog_leash
        snapshot = getattr(self, "_shutdown_work_attribution", {})
        return {
            "work_attribution": {
                "counts": dict(snapshot.get("counts", {})),
                "attribution_complete": bool(snapshot.get("attribution_complete", False)),
                "omissions": list(snapshot.get("omissions", [])),
            },
            "restart_requested": bool(self._restart_requested),
            "draining": bool(self._draining),
            "running": bool(self._running),
            "active_agents": self._running_agent_count(),
            "active_cron_jobs": self._active_cron_job_count(),
            "active_api_runs": self._active_api_run_count(),
            "active_deferred_agent_workers": ctx.deferred_count(),
            "restart_drain_timeout": self._restart_drain_timeout,
            "effective_drain_timeout": effective_stop_drain_timeout(self),
            "launchd_exit_timeout_s": getattr(self, "_launchd_exit_timeout_s", None),
            "watchdog_delay_s": _effective_watchdog_leash(self),
            "phase_elapsed_s": ctx.elapsed() if ctx.started_at is not None else None,
        }
