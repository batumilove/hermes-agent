"""Schedule failed durable finals on the receiving bot's scoped ledger consumer."""

import asyncio
import logging
from functools import partial

from gateway.dead_targets import classify_dead_error

logger = logging.getLogger("gateway.run")


class GatewayDurableDeliveryRecoveryMixin:
    async def _run_delivery_ledger_sync(self, operation, *args, db_path=None, **kwargs):
        from gateway.run_startup_recovery import run_sync_in_detached_daemon_thread

        if db_path is None:
            return await run_sync_in_detached_daemon_thread(operation, *args, **kwargs)
        # Scoped receipt writes belong to the same pool census and closing
        # fence as other gateway writers, even after their await is cancelled.
        return await self._run_in_executor_with_context(partial(operation, *args, db_path=db_path, **kwargs))

    async def _rollback_delivery_claims(self, claimed):
        from gateway.delivery_ledger import release_recoverable_claims
        from gateway.run_startup_recovery import run_sync_in_detached_daemon_thread

        if any(row.get("db_path") is not None for row in claimed):
            return await self._run_in_executor_with_context(partial(release_recoverable_claims, claimed))
        return await run_sync_in_detached_daemon_thread(release_recoverable_claims, claimed)

    async def _claim_runtime_delivery_rows(self, platform, profile, db_path):
        from gateway.delivery_ledger import sweep_failed_for_runtime

        pending = asyncio.create_task(self._run_delivery_ledger_sync(
            sweep_failed_for_runtime, platform.value, profile=profile, db_path=db_path,
        ))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await asyncio.wait({pending})
            if not pending.cancelled() and pending.exception() is None and pending.result():
                rollback = asyncio.create_task(self._rollback_delivery_claims(pending.result()))
                await asyncio.wait({rollback})
                if not rollback.cancelled() and rollback.exception() is not None:
                    logger.error("Cancelled runtime delivery rollback failed (%s)", type(rollback.exception()).__name__)
            raise

    async def _schedule_final_response_redelivery(self, handoff, error):
        from gateway.config import Platform
        from gateway.delivery_ledger import is_reconnect_only

        if not getattr(self, "_running", False) or classify_dead_error(error):
            return  # Explicit terminal abandonment is not a transient retry.
        if is_reconnect_only(error):
            platform = Platform(handoff.delivery_platform)
            live = self._authorization_adapter(platform, handoff.delivery_adapter_profile)
            if live is not None and live is not getattr(handoff, "delivery_adapter_ref", None):
                await self._redeliver_failed_obligations_for_platform(
                    platform, profile=handoff.delivery_adapter_profile, db_path=handoff.post_response_db_path,
                )
            return  # Otherwise the next reconnect owns this category, not a timer.
        self._schedule_flood_redelivery(
            handoff.delivery_platform, profile=handoff.delivery_adapter_profile,
            db_path=handoff.post_response_db_path,
        )

    def _registered_delivery_databases(self):
        controller = getattr(self, "_post_response_controller", None)
        paths = getattr(controller, "_round_robin_db_paths", None)
        return paths() if callable(paths) else [None]

    async def _redeliver_registered_databases(self, platform, profile):
        total = 0
        for path in self._registered_delivery_databases():
            total += await self._redeliver_failed_obligations_for_platform(
                platform, profile=profile, db_path=path,
            )
        return total

    async def _sweep_delivery_databases(self, platforms, targets):
        from gateway.delivery_ledger import sweep_recoverable

        claimed = []
        pending = None
        try:
            for path in self._registered_delivery_databases():
                pending = asyncio.create_task(self._run_delivery_ledger_sync(
                    sweep_recoverable, None, deliverable_platforms=platforms,
                    deliverable_targets=targets, **({"db_path": path} if path is not None else {}),
                ))
                claimed.extend(await asyncio.shield(pending))
                pending = None
        except BaseException:
            # Recover a commit that raced cancellation, then roll back ALL
            # earlier databases before propagating the original failure.
            if pending is not None:
                await asyncio.wait({pending})
                if not pending.cancelled() and pending.exception() is None:
                    claimed.extend(pending.result())
            if claimed:
                rollback = asyncio.create_task(self._rollback_delivery_claims(claimed))
                await asyncio.wait({rollback})
                if not rollback.cancelled() and rollback.exception() is not None:
                    logger.error("Delivery claim rollback failed (%s)", type(rollback.exception()).__name__)
            raise
        return claimed
