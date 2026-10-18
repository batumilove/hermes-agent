"""Retained fork lifecycle, durable delivery and cache ownership contracts."""
from __future__ import annotations

import logging
logger = logging.getLogger("gateway.run")
from contextlib import contextmanager as _contextmanager
from typing import Any, Dict, Optional
import asyncio

def _record_lifecycle(counters, method_name: str, /, *args, **kwargs) -> None:
    """Record optional diagnostics without affecting gateway control flow."""
    from gateway.run import logger
    if counters is None:
        return
    try:
        getattr(counters, method_name)(*args, **kwargs)
    except Exception:  # health: allow BLE001 -- Optional diagnostic callbacks must not change turn or teardown behavior; failures are traced.
        logger.debug("Lifecycle diagnostics %s failed", method_name, exc_info=True)


@_contextmanager
def gateway_cron_fire_admission():
    """Hold shutdown admission stable while a manual cron claim is committed."""
    from gateway.run import _gateway_cron_fire_admission_lock, _gateway_runner_ref
    with _gateway_cron_fire_admission_lock:
        runner = _gateway_runner_ref()
        admitted = not bool(runner and (
            getattr(runner, "_draining", False)
            or getattr(runner, "_external_drain_active", False)
        ))
        yield admitted


def _mark_gateway_draining(runner) -> None:
    """Close manual-cron admission atomically with the shutdown flag."""
    from gateway.run import _gateway_cron_fire_admission_lock
    with _gateway_cron_fire_admission_lock:
        runner._draining = True


def _publish_authoritative_startup_status(runner, *, default_state: str) -> str:
    """Publish PID-owned startup state while honoring an inherited drain.

    This is called only after the process has won the gateway runtime lock and
    PID-file claim. It deliberately runs before slow MCP/plugin/platform
    initialization so an external generation controller can distinguish a
    live, progressing startup from stale status left by the previous PID.

    A current-epoch drain marker wins over the caller's default lifecycle
    state at every startup checkpoint. That both acknowledges maintenance
    early and prevents a later ``running`` write from briefly reopening intake
    before the drain watcher starts.
    """
    from gateway.drain_control import drain_requested
    from gateway.status import write_runtime_status

    drain_active = drain_requested()
    runner._external_drain_active = drain_active
    startup_should_abort = getattr(runner, "_startup_should_abort", None)
    shutdown_active = bool(startup_should_abort()) if callable(startup_should_abort) else False
    if shutdown_active:
        state = "stopping"
    else:
        state = "draining" if drain_active else default_state
    active_work_count = getattr(runner, "_active_work_count", None)
    active_agents = active_work_count() if callable(active_work_count) else 0
    write_runtime_status(
        gateway_state=state,
        exit_reason=None,
        restart_requested=bool(getattr(runner, "_restart_requested", False)),
        active_agents=active_agents,
    )
    return state


async def _publish_authoritative_startup_status_async(
    runner, *, default_state: str
) -> str:
    """Publish startup state without blocking the gateway event loop."""
    from gateway.run import asyncio, logger, run_sync_in_detached_daemon_thread, run_sync_in_detached_serial_daemon_thread
    from gateway.drain_control import drain_requested
    from gateway.status import write_runtime_status

    try:
        drain_active = await asyncio.wait_for(
            run_sync_in_detached_daemon_thread(drain_requested),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        # Fail closed for intake while allowing startup to make forward progress.
        drain_active = True
        logger.warning("Gateway startup drain probe timed out; preserving drain")
    # Gateway accept-state is event-loop-owned; mutate it only after the
    # detached filesystem probe has returned or timed out.
    runner._external_drain_active = drain_active
    startup_should_abort = getattr(runner, "_startup_should_abort", None)
    shutdown_active = (
        bool(startup_should_abort()) if callable(startup_should_abort) else False
    )
    state = (
        "stopping"
        if shutdown_active
        else ("draining" if drain_active else default_state)
    )
    active_work_count = getattr(runner, "_active_work_count", None)
    active_agents = active_work_count() if callable(active_work_count) else 0
    try:
        await asyncio.wait_for(
            run_sync_in_detached_serial_daemon_thread(
                write_runtime_status,
                gateway_state=state,
                exit_reason=None,
                restart_requested=bool(getattr(runner, "_restart_requested", False)),
                active_agents=active_agents,
            ),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Gateway startup status write timed out; continuing startup")
    return state


def _build_live_control_status(runner, payload=None) -> dict[str, Any]:
    """Return a control-socket status with a live, attributed work census."""
    from gateway.run import os
    from gateway.control_socket import build_status_payload

    if payload is None:
        payload = build_status_payload()
    foreground = max(0, int(runner._running_agent_count()))
    cron_runs = max(0, int(runner._active_cron_job_count()))
    api_runs = max(0, int(runner._active_api_run_count()))
    detached_workers = max(0, int(runner._active_delegation_count()))
    total = foreground + cron_runs + api_runs + detached_workers
    cron_thread = getattr(runner, "_cron_thread", None)
    scheduler_running = bool(cron_thread is not None and cron_thread.is_alive())
    payload.update(
        pid=os.getpid(),
        answering_pid=os.getpid(),
        active_agents=total,
        scheduler={
            "status": "running" if scheduler_running else "failed",
            "writer_pid": os.getpid(),
        },
        occupancy={
            "foreground_agents": foreground,
            "cron_runs": cron_runs,
            "api_runs": api_runs,
            "detached_workers": detached_workers,
            "total": total,
        },
    )
    return payload


class GatewayForkContractsMixin:
    _RESTART_HANDOFF_STEER_TEXT = (
        "Gateway restart requested. Stop starting new work. Preserve a concise "
        "handoff in your final response with current status, artifacts, blockers, "
        "and next steps, then finish this turn as soon as safely possible."
    )
    _SYSTEMD_SHUTDOWN_MARGIN_S = 5.0
    _STOPSEC_HEADROOM = 10.0
    _DRAIN_ATTRIBUTION_WRITE_TIMEOUT_S = 0.05
    _SHUTDOWN_TAIL_RESERVE_S = 10.0
    _SHUTDOWN_TERMINAL_ATTRIBUTION_RESERVE_S = 0.1
    @property
    def _stopsec_headroom(self) -> float:
        return type(self)._STOPSEC_HEADROOM

    @property
    def _effective_restart_drain_timeout(self) -> float:
        """Return the drain timeout that fits inside systemd TimeoutStopSec.

        The systemd unit generated by ``generate_systemd_unit`` sets
        ``TimeoutStopSec = max(60, drain_timeout) + 30``.  We subtract a fixed
        headroom so the gateway always has time to hard-interrupt agents and
        finish post-interrupt cleanup before systemd can send SIGKILL.
        """
        configured = self._restart_drain_timeout
        # Mirror the formula in hermes_cli/gateway.py so the hard deadline
        # lines up with the unit's TimeoutStopSec. The configured drain
        # timeout must never extend that budget; clamp it to the stop budget
        # minus a fixed headroom so we never exceed the systemd stop budget.
        systemd_stop_timeout = max(60.0, configured) + 30.0
        return max(0.0, min(configured, systemd_stop_timeout - self._stopsec_headroom))

    async def _async_session_db_for_active_scope(self) -> Any:
        """Resolve the raw task-local DB for cancellation-safe daemon operations."""
        from gateway.run import _SESSION_DB_UNPINNED, run_sync_in_detached_daemon_thread
        from hermes_state import AsyncSessionDB
        handle = getattr(self, "_session_db_pinned", _SESSION_DB_UNPINNED)
        if handle is _SESSION_DB_UNPINNED:
            handle = await run_sync_in_detached_daemon_thread(self._open_session_db_for_active_scope)
        return handle._db if isinstance(handle, AsyncSessionDB) else handle

    def _active_delegation_count(self) -> int:
        """Count detached async-delegation units running outside this gateway.

        Background delegation fan-outs keep running after the dispatching
        session's turn ends, so neither ``_running_agents`` nor the API-run
        registry can see them. Without this the maintenance-drain total is
        structurally blind to detached work and can declare quiescence (or
        time out without attributing it) while a delegation is still live.
        Best-effort: returns 0 if the delegation module is unavailable.
        """
        try:
            from tools.async_delegation import active_count

            return max(0, int(active_count()))
        except Exception:
            logger.debug("_active_delegation_count boundary operation failed", exc_info=True)
            return 0

    def _shutdown_work_attribution_snapshot(self) -> dict[str, Any]:
        """Collect exact shutdown work on the gateway event-loop thread."""
        from gateway.run import _AGENT_PENDING_SENTINEL
        try:
            from gateway.drain_attribution import collect_gateway_work

            snapshot = collect_gateway_work(
                self,
                pending_sentinel=_AGENT_PENDING_SENTINEL,
            )
            return {
                "counts": dict(snapshot.counts),
                "units": [dict(unit) for unit in snapshot.units],
                "attribution_complete": snapshot.attribution_complete,
                "omissions": list(snapshot.omissions),
            }
        except BaseException as exc:
            logger.debug("_shutdown_work_attribution_snapshot boundary operation failed", exc_info=True)
            counts = {
                "agent": max(0, int(self._running_agent_count())),
                "cron": max(0, int(self._active_cron_job_count())),
                "api": max(0, int(self._active_api_run_count())),
            }
            counts["total"] = sum(counts.values())
            return {
                "counts": counts,
                "units": [],
                "attribution_complete": False,
                "omissions": [f"snapshot_failed:{type(exc).__name__}"],
            }

    async def _record_drain_attribution(
        self,
        phase: str,
        *,
        deadline: float,
    ):
        """Persist one exact work snapshot without extending shutdown's deadline."""
        from gateway.run import GatewayRunner, Path, asyncio
        from gateway.drain_attribution import (
            DrainAttributionRecorder,
            DrainAttributionWriteResult,
            current_drain_generation,
            generation_is_current,
            record_snapshot_bounded,
        )
        from hermes_constants import get_hermes_home

        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return DrainAttributionWriteResult(
                status="attribution_incomplete",
                error="shutdown_deadline_exhausted",
            )

        recorder = getattr(self, "_drain_attribution_recorder", None)
        if recorder is None:
            home = Path(get_hermes_home())
            generation = current_drain_generation()
            recorder = DrainAttributionRecorder(
                home=home,
                generation=generation,
                owner_probe=lambda expected: generation_is_current(
                    expected,
                    home=home,
                ),
            )
            self._drain_attribution_recorder = recorder

        snapshot_helper = getattr(self, "_shutdown_work_attribution_snapshot", None)
        snapshot = (
            snapshot_helper()
            if callable(snapshot_helper)
            else GatewayRunner._shutdown_work_attribution_snapshot(self)
        )
        # One immutable-by-convention pointer assignment lets the watchdog
        # thread report exact identities without traversing event-loop-owned
        # agent/API containers after the loop has wedged.
        self._shutdown_work_attribution = snapshot
        return await record_snapshot_bounded(
            recorder,
            timeout_seconds=min(
                GatewayRunner._DRAIN_ATTRIBUTION_WRITE_TIMEOUT_S,
                remaining,
            ),
            phase=phase,
            counts=snapshot["counts"],
            units=snapshot["units"],
            attribution_complete=snapshot["attribution_complete"],
            omissions=snapshot["omissions"],
        )

    async def _update_runtime_status_async(
        self,
        gateway_state: Optional[str] = None,
        exit_reason: Optional[str] = None,
    ) -> None:
        """Persist lifecycle status without blocking the gateway event loop."""
        from gateway.run import run_sync_in_detached_serial_daemon_thread
        await run_sync_in_detached_serial_daemon_thread(
            self._update_runtime_status,
            gateway_state,
            exit_reason,
        )

    async def _persist_active_agents_async(self) -> None:
        """Persist the work census without disrupting message handling.

        Runtime status is diagnostic and explicitly best-effort.  The shared
        serial persistence lane is bounded, so a burst of simultaneous turn
        boundaries can temporarily exhaust its capacity.  Do not let that
        backpressure replace an otherwise valid platform response with an
        error; a later boundary will publish the current authoritative count.
        """
        from gateway.run import logger, run_sync_in_detached_serial_daemon_thread
        try:
            await run_sync_in_detached_serial_daemon_thread(
                self._persist_active_agents
            )
        except Exception as exc:  # health: allow BLE001 -- Diagnostic persistence worker failures must not replace the accepted platform response; log and retry at the next boundary.
            logger.debug("_persist_active_agents_async boundary operation failed", exc_info=True)
            logger.warning(
                "Active-agent status persistence skipped after diagnostic "
                "worker failure: %s",
                exc,
            )

    def _shutdown_interrupt_timeout_secs(self) -> float:
        return 5.0

    def _shutdown_watchdog_delay_secs(self) -> float:
        """Return a hard-exit leash that precedes systemd's SIGKILL boundary."""
        from gateway.run import GatewayRunner, resolve_shutdown_watchdog_delay
        delay = resolve_shutdown_watchdog_delay(self._restart_drain_timeout)
        timeout_stop = getattr(self, "_systemd_timeout_stop_s", None)
        try:
            timeout_stop = float(timeout_stop) if timeout_stop is not None else None
        except (TypeError, ValueError):
            timeout_stop = None
        if timeout_stop is not None and timeout_stop > 0.0:
            delay = min(
                delay,
                max(
                    0.0,
                    timeout_stop
                    - getattr(
                        self,
                        "_SYSTEMD_SHUTDOWN_MARGIN_S",
                        GatewayRunner._SYSTEMD_SHUTDOWN_MARGIN_S,
                    ),
                ),
            )
        return max(0.0, delay)

    @staticmethod
    def _shutdown_remaining(deadline: Optional[float]) -> float:
        from gateway.run import asyncio
        if deadline is None:
            return float("inf")
        return max(0.0, deadline - asyncio.get_running_loop().time())

    async def _run_shutdown_sync_daemon(
        self,
        func,
        *args,
        timeout: float,
        context: str,
    ) -> bool:
        """Bound shutdown-only synchronous work on an exit-safe daemon thread."""
        from gateway.run import Any, asyncio, copy_context, logger, threading
        done = threading.Event()
        errors: list[BaseException] = []
        results: list[Any] = []
        ctx = copy_context()

        def _worker() -> None:
            try:
                results.append(ctx.run(func, *args))
            except BaseException as exc:  # health: allow BLE001 -- This shutdown worker owns arbitrary callback outcomes and forwards every exception to the awaiting owner.
                logger.debug("_worker boundary operation failed", exc_info=True)
                errors.append(exc)
            finally:
                done.set()

        threading.Thread(
            target=_worker,
            name=f"gateway-shutdown-{context.replace(' ', '-')}",
            daemon=True,
        ).start()
        loop = asyncio.get_running_loop()
        wait_deadline = loop.time() + max(0.0, timeout)
        while not done.is_set() and loop.time() < wait_deadline:
            await asyncio.sleep(min(0.05, max(0.0, wait_deadline - loop.time())))
        if not done.is_set():
            logger.warning(
                "Shutdown %s exceeded %.2fs; forcing forward progress",
                context,
                timeout,
            )
            return False
        if errors:
            logger.warning("Shutdown %s failed: %s", context, errors[0])
            return False
        return not results or results[0] is not False

    async def _drain_hygiene_future_after_cancellation(
        self,
        future: asyncio.Future,
        agent: Any,
        cancellation: asyncio.CancelledError,
    ) -> None:
        """Keep ownership of non-cancellable hygiene work through cleanup."""
        from gateway.run import asyncio
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except BaseException:  # health: allow BLE001 -- Await boundary observes arbitrary terminal worker failures before restoring caller cancellation.
                break
        try:
            future.result()
        except BaseException as error:  # health: allow BLE001 -- Terminal worker errors are consumed; the caller's original cancellation remains authoritative.
            logger.debug("Cancelled hygiene worker ended with %s", type(error).__name__)

        cleanup_task = asyncio.create_task(
            self._cleanup_agent_resources_off_loop(
                agent,
                context="session hygiene cancelled",
            )
        )
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
            except BaseException:  # health: allow BLE001 -- Cleanup await boundary must preserve the caller's original cancellation after arbitrary cleanup failure.
                break
        try:
            cleanup_task.result()
        except BaseException as error:  # health: allow BLE001 -- Consume cleanup failures without replacing the caller's original cancellation.
            logger.warning("Cancelled hygiene cleanup ended with %s", type(error).__name__)
        raise cancellation

    async def _suspend_stuck_loop_sessions_async(self) -> int:
        """Run startup stuck-loop persistence off the gateway event loop."""
        from gateway.run import run_sync_in_detached_daemon_thread
        return await run_sync_in_detached_daemon_thread(
            self._suspend_stuck_loop_sessions
        )

    def _launch_systemd_restart_shortcut(self) -> None:
        """Best-effort helper to bypass systemd's automatic restart delay.

        For planned in-chat restarts, the gateway exits cleanly so systemd does
        not record a failure.  However, units with RestartSteps still count
        automatic restarts and can delay repeated /restart tests.  A transient
        user service survives our cgroup teardown and explicitly starts the
        gateway as soon as this PID exits, while the unit keeps its normal
        backoff for real crash loops.
        """
        from gateway.run import logger, os, shlex, sys
        if sys.platform != "linux" or not os.environ.get("INVOCATION_ID"):
            return

        try:
            import subprocess
            from hermes_platform.resolver import locate_command

            systemd_run = next(iter(locate_command("systemd-run").command), None)
            systemctl = next(iter(locate_command("systemctl").command), None)
            if not systemd_run or not systemctl:
                return

            try:
                from hermes_cli.gateway import get_service_name

                service_name = get_service_name()
            except Exception:  # health: allow BLE001 -- Systemd restart is an optional platform boundary; process or service probe failures retain normal shutdown behavior.
                logger.debug("_launch_systemd_restart_shortcut boundary operation failed", exc_info=True)
                service_name = "hermes-gateway"

            current_pid = os.getpid()

            # Detect whether the gateway unit is registered as a system or
            # user service.  Daemon-style deployments are typically system
            # units (e.g. /etc/systemd/system/hermes-gateway.service), while
            # `hermes setup` under a non-root account may register a user
            # unit.  Hard-coding ``--user`` broke system-unit deployments:
            # systemctl returned an empty MainPID, the PID-equality check
            # below failed, and the planned-restart helper was never
            # launched — leaving the gateway dead until a manual reboot.
            def _query_pid(scope_flags):
                try:
                    out = subprocess.run(
                        [systemctl, *scope_flags, "show", service_name,
                         "--property=MainPID", "--value"],
                        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2,
                    )
                    return (out.stdout or "").strip()
                except Exception:  # health: allow BLE001 -- Optional service PID probes may fail on unsupported hosts; the fallback scope must remain available.
                    logger.debug("_query_pid boundary operation failed", exc_info=True)
                    return ""

            system_pid = _query_pid([])
            user_pid = _query_pid(["--user"])
            if str(current_pid) == system_pid:
                scope_flags = []
                systemctl_scope = "systemctl"
            elif str(current_pid) == user_pid:
                scope_flags = ["--user"]
                systemctl_scope = "systemctl --user"
            else:
                # MainPID does not match in either scope — likely invoked
                # outside of systemd or the unit was renamed.  Bail out
                # rather than restart the wrong unit.
                return

            service_arg = shlex.quote(service_name)
            shell_cmd = (
                f"while kill -0 {current_pid} 2>/dev/null; do sleep 0.2; done; "
                f"{systemctl_scope} reset-failed {service_arg}; "
                f"{systemctl_scope} restart {service_arg}"
            )
            unit_name = f"{service_name}-planned-restart-{current_pid}".replace(".", "-")
            subprocess.Popen(
                [
                    systemd_run,
                    *scope_flags,
                    "--collect",
                    "--unit",
                    unit_name,
                    "/bin/sh",
                    "-lc",
                    shell_cmd,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(
                "Launched systemd planned-restart helper for %s (pid=%s, scope=%s)",
                service_name,
                current_pid,
                "user" if scope_flags else "system",
            )
        except Exception as e:  # health: allow BLE001 -- Systemd restart is an optional platform boundary; process or service probe failures retain normal shutdown behavior.
            logger.debug("_launch_systemd_restart_shortcut boundary operation failed", exc_info=True)
            logger.debug("Failed to launch systemd planned-restart helper: %s", e)

    def _steer_active_agents_for_restart(
        self,
        seen_agent_ids: Optional[set[int]] = None,
    ) -> int:
        """Ask newly observed active agent turns to hand off and finish."""
        from gateway.run import logger
        steered = 0
        if seen_agent_ids is None:
            seen_agent_ids = set()
        for session_key, agent in self._snapshot_running_agents().items():
            agent_id = id(agent)
            if agent_id in seen_agent_ids:
                continue
            seen_agent_ids.add(agent_id)
            steer = getattr(agent, "steer", None)
            if not callable(steer):
                logger.debug(
                    "Active agent for %s cannot accept restart handoff steer",
                    session_key,
                )
                continue
            try:
                if steer(self._RESTART_HANDOFF_STEER_TEXT):
                    steered += 1
            except Exception as exc:  # health: allow BLE001 -- Third-party agent steer callbacks may fail arbitrarily; continue steering all other active owners and trace this one.
                logger.debug("_steer_active_agents_for_restart boundary operation failed", exc_info=True)
                logger.warning(
                    "Failed to steer active agent for restart (%s): %s",
                    session_key,
                    exc,
                )
        if steered:
            logger.info(
                "Restart handoff steer delivered to %d active agent turn(s)",
                steered,
            )
        return steered

    def _start_loop_heartbeat_supervised(self) -> None:
        """(Re)start the loop-liveness heartbeat task if it is not running.

        Called once at the top of ``start()`` — before adapter/plugin bring-up
        — and again from the supervisor callback if the task ever dies, so a
        gateway life cannot silently run heartbeat-less (2026-08-25 incident:
        7h life, zero heartbeat writes, only discovered when it failed to
        drain). Idempotent: an alive task is left alone.
        """
        from gateway.run import DEFAULT_HEARTBEAT_INTERVAL_S, asyncio, logger, loop_heartbeat_forever
        existing = getattr(self, "_loop_heartbeat_task", None)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            loop_heartbeat_forever(
                interval_s=DEFAULT_HEARTBEAT_INTERVAL_S,
                start_time=getattr(self, "_gateway_started_at", 0.0),
            )
        )
        task._hermes_supervised_watcher = True
        self._loop_heartbeat_task = task
        _bg = getattr(self, "_background_tasks", None)
        if _bg is not None:
            _bg.add(task)
            task.add_done_callback(_bg.discard)

        def _supervise(t: asyncio.Task[None]) -> None:
            if t.cancelled():
                return  # normal stop() teardown
            if t.exception() is not None:
                logger.warning(
                    "Gateway loop heartbeat task died (%r); restarting it",
                    t.exception(),
                )
                try:
                    self._start_loop_heartbeat_supervised()
                except Exception:  # health: allow BLE001 -- Heartbeat restart callbacks are a liveness boundary; trace arbitrary failures without failing the event-loop callback.
                    logger.error(
                        "Failed to restart gateway loop heartbeat", exc_info=True
                    )

        task.add_done_callback(_supervise)

    def _loop_freeze_hard_deadline_s(self) -> Optional[float]:
        """Bound the loop-freeze backstop before systemd's SIGKILL boundary.

        A frozen event loop cannot run the shutdown path, so the out-of-loop
        liveness watchdog must hard-exit while the service supervisor still
        honors the restart. When the systemd stop timeout is unknown, return
        ``None`` and keep the probe-strike behavior as the only backstop.
        """
        from gateway.run import GatewayRunner
        timeout_stop = getattr(self, "_systemd_timeout_stop_s", None)
        try:
            timeout_stop = float(timeout_stop) if timeout_stop is not None else None
        except (TypeError, ValueError):
            timeout_stop = None
        if timeout_stop is None or timeout_stop <= 0.0:
            return None
        margin = getattr(
            self,
            "_SYSTEMD_SHUTDOWN_MARGIN_S",
            GatewayRunner._SYSTEMD_SHUTDOWN_MARGIN_S,
        )
        return max(0.0, timeout_stop - margin)

    def shutdown_post_response_worker(self, timeout: float = 1.0) -> bool:
        """Bounded stop for this runner's sole durable post-response worker.

        A timed-out action is intentionally not cancelled: its row remains
        ``running`` under this process and becomes reclaimable after death.
        Unclaimed rows remain pending for the next runner.
        """
        from gateway.run import logger
        controller = getattr(self, "_post_response_controller", None)
        if controller is None:
            return True
        stopped = controller.shutdown(timeout)
        if not stopped:
            logger.warning("post-response worker shutdown timed out")
        return stopped

    def _prepare_final_response_handoff(
        self,
        handoff: Any,
        *,
        text_content: str,
        event: Any,
    ) -> bool:
        """Atomically commit the exact final-delivery receipt and action plan.

        Called by ``BasePlatformAdapter`` after media extraction establishes the
        exact text that will be sent, and before it crosses the network
        ambiguity boundary.  Failure is fail-closed: the durable active token
        stays in place for restart recovery and no platform send is attempted.
        """
        from gateway.run import Path, logger
        if not text_content:
            return False
        home_token = None
        try:
            handoff.receipt_attempted = True
            from gateway.delivery_ledger import compute_obligation_id
            from hermes_constants import (
                reset_hermes_home_override,
                set_hermes_home_override,
            )

            # The adapter completes delivery in a deferred thread.  Reinstall
            # the home captured when the handoff was built so this commit
            # cannot fall through to root merely because the turn scope has
            # already unwound.  A mismatch is fail-closed: a receipt must
            # never be committed to a DB other than its captured parent.
            captured_db_path = getattr(handoff, "post_response_db_path", None)
            if captured_db_path is not None:
                captured_db_path = Path(captured_db_path).absolute()
                home_token = set_hermes_home_override(str(captured_db_path.parent))
            db = self.session_store._db
            if captured_db_path is not None:
                actual_db_path = getattr(db, "db_path", None)
                if actual_db_path is None or Path(actual_db_path).absolute() != captured_db_path:
                    raise RuntimeError("final response receipt DB scope mismatch")

            message_ref = str(getattr(event, "message_id", "") or "")
            if not message_ref:
                # Attachment-only recovery content is intentionally constant
                # and safe.  Preserve distinct handoff identities without
                # retaining the original MEDIA path/URL by deriving a stable
                # hash from the complete original handoff plan.
                message_ref = compute_obligation_id(
                    handoff.session_key,
                    "post-response-handoff",
                    str(getattr(handoff, "delivery_identity", "")),
                )
            obligation_id = compute_obligation_id(
                handoff.session_key, message_ref, text_content
            )
            source = event.source
            from gateway.session_identity import transport_profile_of
            adapter = self._delivery_adapter_for(source)
            adapter_profile = getattr(adapter, "_owner_profile", None)
            if not isinstance(adapter_profile, str) or not adapter_profile.strip():
                adapter_profile = transport_profile_of(source) or "default"
            handoff.delivery_platform = str(getattr(source.platform, "value", source.platform))
            handoff.delivery_adapter_profile = adapter_profile
            handoff.delivery_adapter_ref = adapter
            db.create_final_delivery_and_barrier(
                obligation_id=obligation_id,
                session_key=handoff.session_key,
                session_lineage=handoff.session_lineage,
                platform=str(getattr(source.platform, "value", source.platform)),
                chat_id=str(source.chat_id),
                thread_id=getattr(source, "thread_id", None),
                content=text_content,
                adapter_profile=adapter_profile,
                actions=handoff.actions_for_text(text_content),
            )
            handoff.obligation_id = obligation_id
            controller = getattr(self, "_post_response_controller", None)
            if controller is not None:
                if captured_db_path is not None:
                    register = getattr(controller, "register_db_path", None)
                    if callable(register) and register(captured_db_path) is False:
                        raise RuntimeError("final response receipt DB is outside worker scope")
                controller.wake()
            return True
        except Exception as exc:  # health: allow BLE001 -- Scoped receipt callbacks must fail closed before output; log only error type to avoid private response payloads.
            # Payloads contain chat/session identifiers and response text; do
            # not put any of them in a warning.
            logger.warning("final delivery receipt commit failed (%s)", type(exc).__name__)
            return False
        finally:
            if home_token is not None:
                reset_hermes_home_override(home_token)

    def _mark_final_response_handoff_attempting(self, handoff: Any) -> bool:
        """CAS delivery state in the exact DB captured by the handoff."""
        from gateway.run import Path
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        captured_db_path = getattr(handoff, "post_response_db_path", None)
        token = None
        try:
            if captured_db_path is not None:
                captured_db_path = Path(captured_db_path).absolute()
                token = set_hermes_home_override(str(captured_db_path.parent))
            db = self.session_store._db
            if captured_db_path is not None:
                actual_db_path = getattr(db, "db_path", None)
                if actual_db_path is None or Path(actual_db_path).absolute() != captured_db_path:
                    return False
            return bool(db.mark_final_delivery_attempting(handoff.obligation_id))
        finally:
            if token is not None:
                reset_hermes_home_override(token)

    def _settle_final_response_handoff(
        self,
        handoff: Any,
        *,
        delivered: bool,
        error: Optional[str] = None,
    ) -> bool:
        """Settle delivery in the same exact DB that owns the receipt."""
        from gateway.run import Path
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        captured_db_path = getattr(handoff, "post_response_db_path", None)
        token = None
        try:
            if captured_db_path is not None:
                captured_db_path = Path(captured_db_path).absolute()
                token = set_hermes_home_override(str(captured_db_path.parent))
            db = self.session_store._db
            if captured_db_path is not None:
                actual_db_path = getattr(db, "db_path", None)
                if actual_db_path is None or Path(actual_db_path).absolute() != captured_db_path:
                    return False
            return bool(
                db.settle_final_delivery(
                    handoff.obligation_id, delivered=delivered, error=error,
                    operational_error=error, expected_attempts=1,
                )
            )
        finally:
            if token is not None:
                reset_hermes_home_override(token)

    def _cancel_uncommitted_final_response_handoff(self, handoff: Any) -> bool:
        """Release a handoff that Base suppressed before receipt creation.

        A receipt transaction failure is intentionally *not* cancellable: its
        active token is recovery evidence.  Only pre-extraction suppression
        and empty/media-only paths arrive here with no attempted receipt.
        """
        if getattr(handoff, "obligation_id", None) is not None:
            return False
        if bool(getattr(handoff, "receipt_attempted", False)):
            return False
        token = getattr(handoff, "active_turn_token", None)
        if not token:
            return False
        return bool(
            self.session_store.clear_turn_active(
                str(handoff.session_key), str(token)
            )
        )

    def _rebaseline_agent_cache_message_count_sync(
        self, session_key: str, session_id: Optional[str]
    ) -> None:
        """Synchronous worker-side counterpart of the cache re-baseline.

        The durable finalizer runs outside the gateway event loop.  It must
        update the same 3-/4-tuple cache entry under the same lock only after
        all gateway-owned transcript actions have completed.
        """
        from gateway.run import _AGENT_PENDING_SENTINEL
        if not session_key or not session_id:
            return
        db_candidates = (
            getattr(self.session_store, "_db", None),
            getattr(getattr(self, "_session_db", None), "_db", None),
            getattr(self, "_session_db", None),
        )
        live = None
        for db in db_candidates:
            getter = getattr(db, "get_session", None)
            if getter is None:
                continue
            try:
                row = getter(session_id)
                live = row.get("message_count", 0) if row else None
            except Exception:
                logger.debug("_rebaseline_agent_cache_message_count_sync boundary operation failed", exc_info=True)
                continue
            break
        if live is None:
            return
        cache_lock = getattr(self, "_agent_cache_lock", None)
        cache = getattr(self, "_agent_cache", None)
        if cache_lock is None or cache is None:
            return
        with cache_lock:
            cached = cache.get(session_key)
            if not (
                isinstance(cached, tuple)
                and len(cached) > 2
                and cached[0] is not _AGENT_PENDING_SENTINEL
            ):
                return
            snapshot_sid = cached[3] if len(cached) > 3 else None
            if snapshot_sid is not None and snapshot_sid != session_id:
                return
            if snapshot_sid is None:
                cache[session_key] = (cached[0], cached[1], live)
            else:
                cache[session_key] = (cached[0], cached[1], live, snapshot_sid)

    def _clear_restart_failure_count_sync(self, session_key: str) -> None:
        """Worker-safe durable completion cleanup (no event-loop dependency)."""
        from gateway.run import atomic_json_write, get_hermes_home
        import json

        # This runs on the durable worker after it installs the receipt's
        # profile home.  Resolve now rather than using the module's process
        # root snapshot so same-key profiles cannot clear each other's file.
        path = get_hermes_home() / self._STUCK_LOOP_FILE
        if not path.exists():
            return
        try:
            counts = json.loads(path.read_text(encoding="utf-8-sig"))
            if session_key not in counts:
                return
            del counts[session_key]
            if counts:
                atomic_json_write(path, counts, indent=None)
            else:
                path.unlink(missing_ok=True)
        except Exception:
            logger.debug("_clear_restart_failure_count_sync boundary operation failed", exc_info=True)
            return

    def _finalize_post_response_turn(self, payload: dict[str, Any]) -> None:
        """Terminal durable action: release exact token, then re-baseline."""
        session_key = str(payload["session_key"])
        token = payload.get("active_turn_token") or payload.get("token")
        if token:
            cleared = self.session_store.clear_turn_active(session_key, str(token))
            if not cleared and self.session_store.peek_active_turn_token(session_key) == str(token):
                raise RuntimeError("post-response active-turn finalization was not persisted")
        self._rebaseline_agent_cache_message_count_sync(
            session_key, payload.get("session_id")
        )

    async def _release_running_agent_state_async(
        self,
        session_key: str,
        *,
        run_generation: Optional[int] = None,
    ) -> bool:
        """Async event-loop-safe counterpart to ``_release_running_agent_state``."""
        released = self._release_running_agent_state(
            session_key,
            run_generation=run_generation,
            _persist=False,
        )
        if released:
            await self._persist_active_agents_async()
        return released

    def _schedule_evicted_agent_soft_release(self, agent: Any, session_key: str = "") -> None:
        """Retain the owner until cleanup settles under its profile scope."""
        self._retain_failed_evicted_agent_cleanup(agent, session_key)
        self._spawn_release_thread(self._commit_then_release_soft, (agent, session_key),
                                   f"agent-evict-{str(session_key)[:24]}",
                                   inline_fallback=True, session_key=session_key)

    def _failed_evicted_agent_cleanup_state(self) -> tuple[dict, Any]:
        """Return the durable registry for every evicted cleanup owner."""
        from gateway.run import threading
        registry = self.__dict__.get("_failed_evicted_agent_cleanups")
        lock = self.__dict__.get("_failed_evicted_agent_cleanups_lock")
        if registry is None or lock is None:
            self.__dict__.setdefault("_failed_evicted_agent_cleanups", {})
            self.__dict__.setdefault(
                "_failed_evicted_agent_cleanups_lock", threading.Lock()
            )
            registry = self.__dict__["_failed_evicted_agent_cleanups"]
            lock = self.__dict__["_failed_evicted_agent_cleanups_lock"]
        return registry, lock

    def _retain_failed_evicted_agent_cleanup(
        self, agent: Any, session_key: str = "", *, boundary: bool = False
    ) -> None:
        """Retain complete cleanup ownership before cache removal or launch."""
        registry, lock = self._failed_evicted_agent_cleanup_state()
        with lock:
            old = registry.get(id(agent))
            boundary = boundary or bool(old and len(old) > 2 and old[2])
            registry[id(agent)] = (agent, session_key, True) if boundary else (agent, session_key)

    def _complete_evicted_agent_cleanup(self, agent: Any) -> None:
        registry, lock = self._failed_evicted_agent_cleanup_state()
        with lock:
            entry = registry.get(id(agent))
            if entry is not None and entry[0] is agent:
                registry.pop(id(agent), None)

    def _retry_failed_evicted_agent_cleanups(self) -> int:
        """Retry retained cleanup owners under the profile that created them."""
        registry, lock = self._failed_evicted_agent_cleanup_state()
        with lock:
            pending = list(registry.values())
        completed = 0
        for entry in pending:
            agent, session_key = entry[:2]
            self._run_release_in_profile_scope(self._commit_then_release_soft, (agent, session_key), session_key)
            with lock:
                completed += int(id(agent) not in registry)
        return completed

    def _pending_evicted_release_state(self) -> tuple[dict, Any]:
        """Return the lazy deferred-release registry and its lock."""
        from gateway.run import threading
        registry = self.__dict__.get("_pending_evicted_agent_releases")
        lock = self.__dict__.get("_pending_evicted_agent_releases_lock")
        if registry is None or lock is None:
            self.__dict__.setdefault("_pending_evicted_agent_releases", {})
            self.__dict__.setdefault(
                "_pending_evicted_agent_releases_lock", threading.Lock()
            )
            registry = self.__dict__["_pending_evicted_agent_releases"]
            lock = self.__dict__["_pending_evicted_agent_releases_lock"]
        return registry, lock

    def _defer_evicted_agent_release(
        self, agent: Any, session_key: str = ""
    ) -> None:
        """Remember an active evicted agent and key for post-turn cleanup."""
        registry, lock = self._pending_evicted_release_state()
        with lock:
            registry[id(agent)] = (agent, session_key)

    def _drain_pending_evicted_agent_releases(self) -> None:
        """Schedule deferred agents that no longer own a running turn."""
        from gateway.run import Any, _AGENT_PENDING_SENTINEL
        registry, lock = self._pending_evicted_release_state()
        running_ids = {
            id(agent)
            for _, agent in self._running_agent_items()
            if agent is not None and agent is not _AGENT_PENDING_SENTINEL
        }
        ready: list[tuple[Any, str]] = []
        with lock:
            for agent_id, entry in list(registry.items()):
                if agent_id not in running_ids:
                    ready.append(registry.pop(agent_id))
        for agent, session_key in ready:
            self._schedule_evicted_agent_soft_release(agent, session_key)

    def _soft_evict_memory_commit_plan(self, agent: Any, key: str, *, boundary: bool = False) -> tuple[bool, list | None]:
        """Finalize provider work at resource eviction without resetting the session.

        Upstream sessions no longer expire on idle/daily policies. The provider's
        atomic release and dirty marker guard extraction from repeated evictions.
        """
        if agent is None or (getattr(agent, "_memory_manager", None) is None
                             and not (boundary and getattr(agent, "context_compressor", None) is not None)):
            return False, None
        messages = getattr(agent, "_session_messages", None)
        return True, messages if isinstance(messages, list) else None
