"""Durable startup delivery claim ownership and crash-ambiguity send transitions."""
from __future__ import annotations

import asyncio
import logging
from agent.async_utils import run_sync_in_detached_daemon_thread
from gateway.config import Platform
from gateway.delivery_failure import delivery_failure_token
from gateway.run_delivery_recovery import GatewayDurableDeliveryRecoveryMixin

logger = logging.getLogger("gateway.run")


async def _receipt_ledger_call(runner, row, operation, *args, **kwargs):
    """Carry the exact receipt database through every claim/send checkpoint."""
    if row.get("db_path") is not None:
        kwargs["db_path"] = row["db_path"]
    return await runner._run_delivery_ledger_sync(operation, *args, **kwargs)


class GatewayRecoveryMixin(GatewayDurableDeliveryRecoveryMixin):
    async def _claim_pending_obligations(self) -> list:
        """Claim recoverable delivery-ledger rows and clear their
        ``resume_pending`` flags. Pure DB work — no network sends.

        Runs INLINE at startup BEFORE ``_schedule_resume_pending_sessions``
        and before the (bounded, abandonable) boot-send task exists. A
        session with a recoverable obligation already produced its answer —
        the turn completed and only delivery is owed — so clearing
        ``resume_pending`` here prevents the resume path from re-running
        (and re-paying for) a turn whose output we hold, regardless of how
        long the sends ahead of redelivery take (#91969).

        Crash-ambiguity contract (see gateway/delivery_ledger.py):
        rows that were mid-send or previously rejected carry a visible
        recovered-reply marker so a possible duplicate is labeled, never
        silent. Returns the claimed rows for redelivery.
        """
        claimed = []
        _sweep_future = None
        _release_claims = None
        try:
            from gateway.delivery_ledger import (
                ledger_enabled,
                release_recoverable_claims,
                sweep_recoverable,
            )
            _release_claims = release_recoverable_claims

            if not await run_sync_in_detached_daemon_thread(ledger_enabled):
                return []
            # Only claim rows we can actually send this boot: self.adapters
            # holds a platform only after its connect() succeeded, and each
            # claim spends one of the row's three redelivery attempts.
            profile_adapters = getattr(self, "_profile_adapters", None) or {}
            platform_value = lambda platform: getattr(platform, "value", str(platform))
            targets = {(platform_value(platform), "default") for platform in self.adapters}
            if not profile_adapters:
                targets.update((platform_value(platform), None) for platform in self.adapters)
            for profile, adapters in profile_adapters.items():
                targets.update((platform_value(platform), profile) for platform in adapters)
            _deliverable = {platform for platform, _ in targets}
            # Shield the thread future so orderly task cancellation can first
            # recover its committed result and roll the claims back.  A plain
            # A plain cancellable await loses that result if cancellation lands
            # after SQLite commit but before the await resumes.
            _sweep_future = asyncio.ensure_future(
                self._sweep_delivery_databases(_deliverable, targets)
            )
            claimed = await asyncio.shield(_sweep_future)

            if not claimed:
                return []

            # Clear resume_pending for EVERY claimed row up front, before any
            # send. Claiming already spent one of the row's redelivery attempts —
            # the answer is in the ledger, so the resume path must never re-run
            # these turns (#91969).
            for row in claimed:
                session_key = row.get("session_key") or ""
                if not session_key:
                    continue
                try:
                    await self.async_session_store.clear_resume_pending(session_key)
                except Exception:
                    logger.debug(
                        "clear_resume_pending failed for %s", session_key,
                        exc_info=True,
                    )
            return claimed
        except asyncio.CancelledError:
            # If cancellation raced the thread's commit, retrieve the exact
            # rows it claimed and restore their pre-claim state/attempt count.
            # No network send has started while this method is running.
            if not claimed and _sweep_future is not None:
                try:
                    claimed = await asyncio.shield(_sweep_future)
                except Exception:
                    logger.debug(
                        "delivery ledger sweep failed during cancellation",
                        exc_info=True,
                    )
            if claimed and _release_claims is not None:
                try:
                    await asyncio.shield(
                        self._rollback_delivery_claims(claimed)
                    )
                except Exception:
                    logger.error(
                        "Failed to roll back cancelled delivery-ledger claims",
                        exc_info=True,
                    )
            raise
        except Exception:
            logger.debug("delivery ledger sweep failed", exc_info=True)
            return []

    async def _redeliver_claimed_obligations(self, claimed: list) -> int:
        """Redeliver final responses for rows already claimed (and
        resume-cleared) by :meth:`_claim_pending_obligations`.

        Network half of the split — runs inside the bounded boot-send task,
        so a flood-limited send can be abandoned by the restore gate without
        reopening the turn-replay window. Returns redeliveries attempted.
        """
        try:
            from gateway.delivery_ledger import (
                RECOVERED_MARKER,
                mark_attempting,
                mark_delivered,
                mark_failed,
                release_recoverable_claims,
                release_runtime_claim,
                is_flood_error,
            )
        except Exception:
            logger.debug("delivery ledger import failed", exc_info=True)
            return 0

        db_paths = {row.get("db_path") for row in claimed}
        redelivered = 0
        # ``claimed`` is also the caller's rollback set.  A row leaves it only
        # once the durable state says a network send is about to begin.
        for row in list(claimed):
            if row.get("adopted"):
                continue
            try:
                platform = Platform(row["platform"])
            except (ValueError, TypeError, KeyError):
                logger.debug(
                    "obligation %s: unknown platform %r",
                    row["obligation_id"], row.get("platform"),
                )
                continue
            adapter = (self._authorization_adapter(platform, row.get("profile"))
                       if "profile" in row else self.adapters.get(platform))
            if adapter is None:
                # The adapter was connected when the sweep built its
                # deliverable set but disappeared before send. Restore this
                # unconsumed claim so it neither spends an attempt nor remains
                # owned by a still-live process.
                try:
                    last_error = row.get("last_error")
                    await _receipt_ledger_call(self, row,
                        release_runtime_claim, row["obligation_id"],
                        last_error if is_flood_error(last_error) else "send_path_degraded",
                    )
                    claimed.remove(row)
                except Exception:
                    logger.error(
                        "obligation %s: failed to release claim after adapter disappeared",
                        row["obligation_id"],
                        exc_info=True,
                    )
                continue
            content = row["content"]
            if row.get("needs_marker"):
                content = row.get("marker", RECOVERED_MARKER) + content
            metadata = (
                {"thread_id": row["thread_id"]} if row.get("thread_id") else None
            )

            try:
                transitioned = await _receipt_ledger_call(self, row,
                    mark_attempting, row["obligation_id"], row["attempts"]
                )
            except Exception:
                logger.warning(
                    "obligation %s: failed to persist attempting state; suppressing send",
                    row["obligation_id"],
                    exc_info=True,
                )
                try:
                    await _receipt_ledger_call(self, row,
                        release_recoverable_claims, [row]
                    )
                    claimed.remove(row)
                except Exception:
                    logger.error(
                        "obligation %s: failed to release claim after attempting write error",
                        row["obligation_id"],
                        exc_info=True,
                    )
                continue
            if not transitioned:
                # The exact claimed row no longer exists. Never send unless
                # the durable crash-ambiguity transition is proven.
                claimed.remove(row)
                logger.warning(
                    "obligation %s: claimed row changed before attempting; suppressing send",
                    row["obligation_id"],
                )
                continue
            claimed.remove(row)
            try:
                result = await adapter.send(
                    chat_id=row["chat_id"],
                    content=content,
                    metadata=metadata,
                )
            except Exception as send_err:
                logger.warning(
                    "obligation %s: redelivery send raised: %s",
                    row["obligation_id"], send_err, exc_info=True,
                )
                result = None
            try:
                if result is not None and getattr(result, "success", False):
                    settled = await _receipt_ledger_call(self, row,
                        mark_delivered, row["obligation_id"], expected_attempts=row["attempts"],
                    )
                    if settled is False:
                        continue
                    # The post-response worker may be sleeping after it
                    # correctly observed this receipt as failed.  Delivery is
                    # the transition that releases finalize_turn, so wake it
                    # rather than waiting for its recovery poll.
                    controller = getattr(self, "_post_response_controller", None)
                    if controller is not None:
                        controller.wake()
                    redelivered += 1
                    logger.info(
                        "Redelivered recovered final response to %s:%s "
                        "(obligation %s, attempt %d)",
                        row["platform"], row["chat_id"],
                        row["obligation_id"], row["attempts"],
                    )
                else:
                    await _receipt_ledger_call(self, row,
                        mark_failed,
                        row["obligation_id"],
                        (delivery_failure_token(result) if row.get("db_path") is not None
                         else str(getattr(result, "error", "") or "send failed")),
                        expected_attempts=row["attempts"],
                    )
            except Exception:
                logger.debug("delivery ledger update failed", exc_info=True)
        from gateway.run_shutdown import _log_suppressed
        with _log_suppressed(logging.DEBUG, "arming flood redelivery timers failed", exc_info=True):
            for db_path in db_paths:
                await self._arm_flood_timers_for_waiting_rows(
                    **({"db_path": db_path} if db_path is not None else {}))
        return redelivered

    async def _redeliver_pending_obligations(self) -> int:
        """Claim + redeliver in one call — composition of
        :meth:`_claim_pending_obligations` and
        :meth:`_redeliver_claimed_obligations`.

        Kept as the stable public shape (tests and any external callers
        drive this name); the startup path calls the two halves separately
        so the DB half completes before the resume scheduler proceeds.
        """
        async def _claim_and_redeliver() -> int:
            return await self._redeliver_claimed_obligations(
                await self._claim_pending_obligations()
            )

        task = asyncio.create_task(_claim_and_redeliver())
        tasks = getattr(self, "_background_tasks", None)
        if tasks is None:
            self._background_tasks = set()
            tasks = self._background_tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        task.add_done_callback(self._late_failure_callback("background obligation redelivery failed"))
        return await asyncio.shield(task)


    async def _await_startup_boot_sends(
        self,
        *,
        planned_restart_notification_pending: bool,
    ) -> None:
        """Run boot-path sends without letting them pin the inbound restore gate.

        ``_send_restart_notification`` and ``_redeliver_pending_obligations``
        used to be awaited inline *before* ``_finish_startup_restore``
        released the gate. A single Telegram flood-control sleep on either
        send froze inbound on every platform for the full ``retry_after``
        (#91969).

        This uses the same bounded ``asyncio.wait`` the resume gate already
        uses: on timeout we return and let the sends finish in the
        background. Tasks are not cancelled.

        The ledger claim + ``resume_pending`` clear happen before any send
        and before this method releases the caller to schedule resumes. The
        worker-owned task is shielded and retained so caller cancellation
        cannot abandon a sweep that has already claimed rows. A prior design
        left a window where a hung restart notification ahead of the
        redelivery step let the gate expire with zero rows claimed — the
        resume scheduler then replayed turns whose answers were already in
        the ledger, and the background task later redelivered them too
        (duplicate delivery + re-paid turn).
        """
        from gateway.run import _startup_restore_drain_timeout_secs

        claim_ready = asyncio.Event()

        async def _boot_sends() -> None:
            claimed: list[dict] = []
            try:
                claimed = await self._claim_pending_obligations()
                # The resume scheduler may run only after claims are durable and
                # their resume flags have been cleared.
                claim_ready.set()
                await self._send_restart_notification()
                if planned_restart_notification_pending:
                    await self._replay_pending_planned_restart_notification()
                await self._redeliver_claimed_obligations(claimed)
            except asyncio.CancelledError:
                if claimed:
                    await self._rollback_delivery_claims(claimed)
                raise
            finally:
                # Shutdown may cancel this worker while its claim awaits.
                # Never strand the startup scheduler on claim_ready.wait().
                claim_ready.set()

        boot_task = asyncio.create_task(_boot_sends())
        tasks = getattr(self, "_background_tasks", None)
        if tasks is None:
            self._background_tasks = set()
            tasks = self._background_tasks
        tasks.add(boot_task)
        boot_task.add_done_callback(tasks.discard)
        # A task cancelled before its coroutine's first instruction never runs
        # _boot_sends()'s finally block.  The done callback closes that narrow
        # pre-start cancellation window so startup cannot wait forever.
        boot_task.add_done_callback(lambda _done: claim_ready.set())
        boot_task.add_done_callback(self._late_failure_callback("background boot-path send failed", level=logging.DEBUG))

        # Shield the worker-owned claim/send workflow from caller
        # cancellation. If startup is cancelled while SQLite is still
        # sweeping, the task continues to clear resume flags and redeliver the
        # claimed response instead of abandoning a half-completed claim.
        await asyncio.shield(claim_ready.wait())
        timeout = _startup_restore_drain_timeout_secs()
        if timeout > 0:
            _done, pending = await asyncio.wait({boot_task}, timeout=timeout)
            if pending:
                logger.warning(
                    "Boot-path sends still running after %.0fs; releasing "
                    "inbound gate so other platforms are not frozen. "
                    "Restart notification / obligation redelivery continue "
                    "in the background.",
                    timeout,
                )
        else:
            await asyncio.shield(boot_task)
