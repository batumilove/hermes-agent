"""Telegram ordered recovery lanes and teardown-owned inbound flushes."""
from __future__ import annotations

import logging
logger = logging.getLogger("gateway.run")
import asyncio
from gateway.platforms.base import MessageEvent

class TelegramTextRecoveryMixin:
    def _text_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped key for an event whose topic was recovered off-loop."""
        return super()._text_batch_key(event)


    def _text_recovery_lane_key(self, event: MessageEvent) -> str:
        """Return the stable pre-recovery session lane for a text event."""
        from gateway.session import build_session_key

        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get(
                "group_sessions_per_user", True
            ),
            thread_sessions_per_user=self.config.extra.get(
                "thread_sessions_per_user", False
            ),
            profile=event.source.profile,
        )


    async def _acquire_text_recovery_lane(self, lane_key: str) -> asyncio.Lock:
        """Acquire a raw-lane lock and account for cancellable waiters."""
        registry = self._text_recovery_lane_locks
        entry = registry.get(lane_key)
        if entry is None:
            lock = asyncio.Lock()
            users = 0
        else:
            lock, users = entry
        registry[lane_key] = (lock, users + 1)
        try:
            await lock.acquire()
        except BaseException:
            logger.debug("_acquire_text_recovery_lane boundary operation failed", exc_info=True)
            current = registry.get(lane_key)
            if current is not None and current[0] is lock:
                remaining = current[1] - 1
                if remaining:
                    registry[lane_key] = (lock, remaining)
                else:
                    registry.pop(lane_key, None)
            raise
        return lock


    def _release_text_recovery_lane(
        self, lane_key: str, lock: asyncio.Lock
    ) -> None:
        """Release a lane and remove its registry entry when it becomes idle."""
        lock.release()
        current = self._text_recovery_lane_locks.get(lane_key)
        if current is None or current[0] is not lock:
            return
        remaining = current[1] - 1
        if remaining:
            self._text_recovery_lane_locks[lane_key] = (lock, remaining)
        else:
            self._text_recovery_lane_locks.pop(lane_key, None)


    async def _recover_and_enqueue_text_event(self, event: MessageEvent) -> None:
        """Recover and enqueue fairly while owning off-loop work to completion."""
        if self._should_drop_delayed_delivery():
            return
        if not hasattr(self, "_text_recovery_lane_locks"):
            self._text_recovery_lane_locks = {}
        if not hasattr(self, "_text_recovery_tasks"):
            self._text_recovery_tasks = set()

        owner = asyncio.current_task()
        if owner is not None:
            self._text_recovery_tasks.add(owner)
        lane_key = self._text_recovery_lane_key(event)
        lane_lock = None
        try:
            lane_lock = await self._acquire_text_recovery_lane(lane_key)
            if self._should_drop_delayed_delivery():
                return

            recovery_task = asyncio.create_task(
                self._apply_topic_recovery_async(event)
            )
            try:
                await asyncio.shield(recovery_task)
            except asyncio.CancelledError as cancellation:
                # asyncio.to_thread/run_in_executor cannot cancel a started
                # SessionDB lookup. Keep ownership until it finishes, then
                # propagate the original cancellation without publishing the
                # recovered event. Overlapping stop/disconnect cancellation
                # must not detach the non-cancellable executor worker.
                while not recovery_task.done():
                    try:
                        await asyncio.shield(recovery_task)
                    except asyncio.CancelledError:
                        continue
                try:
                    recovery_task.result()
                except BaseException:  # health: allow BLE001,S110 -- Observe any worker outcome, then propagate the authoritative cancellation below.
                    logger.debug("_recover_and_enqueue_text_event boundary operation failed", exc_info=True)
                    # Cancellation has authority over a concurrent recovery
                    # failure, but the result must still be observed.
                raise cancellation

            if self._should_drop_delayed_delivery():
                return
            self._enqueue_text_event(event)
        finally:
            if lane_lock is not None:
                self._release_text_recovery_lane(lane_key, lane_lock)
            if owner is not None:
                self._text_recovery_tasks.discard(owner)


    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """Buffer a text chunk, or hold it while delayed delivery must be dropped."""
        if self._should_drop_delayed_delivery():
            self._hold_inbound_event(event, where="text-enqueue")
            return
        super()._enqueue_text_event(event)
        self._accept_update()


    async def _flush_buffered(self, pending: dict, tasks: dict, key: str, delay: float, where: str, log_fn=None) -> None:
        """Shared delayed-flush body: sleep, pop, hold if teardown started, else dispatch. A cancel after
        the pop but before durable dispatch re-holds the event (never lose it)."""
        current_task = asyncio.current_task()
        event = None
        try:
            await asyncio.sleep(delay)
            # Superseded flush (a newer chunk re-armed the timer while our sleep was already done):
            # CancelledError only lands at the next await, so check synchronously before the pop.
            owner = tasks.get(key)
            if owner is not None and owner is not current_task:
                return
            event = pending.pop(key, None)
            if not event:
                return
            if self._should_drop_delayed_delivery():
                self._hold_inbound_event(event, where=f"{where}-flush")
                event = None
                return
            if log_fn is not None:
                log_fn(event)
            await self.handle_message(event)
            event = None
        except asyncio.CancelledError:
            if event is not None:
                self._hold_inbound_event(event, where=f"{where}-flush-cancelled")
            raise
        finally:
            if tasks.get(key) is current_task:
                tasks.pop(key, None)


    async def _build_triggered_event(self, msg, update, msg_type) -> MessageEvent:
        """Event for an addressed text/command: trigger text cleaned (sole addressee only), replied-to
        media cached, attribution applied."""
        from plugins.platforms.telegram.telegram_context import group_trigger_text
        event = self._build_message_event(msg, msg_type, update_id=update.update_id)
        event.text = group_trigger_text(self, msg, event.text)
        await self._cache_replied_media(msg, event)
        return self._apply_telegram_group_observe_attribution(event)
