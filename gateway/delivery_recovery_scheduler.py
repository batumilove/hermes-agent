"""Loop-owned operator retry tasks, fenced by the post-response writer lifetime."""

import asyncio
import logging
import threading
import time

logger = logging.getLogger("gateway.run")


class RecoveryScheduler:
    def __init__(self, worker):
        self.worker = worker
        self._lock = threading.Lock()
        self._tasks = {}
        self._next_poll_at = 0.0

    def poll(self, db_paths):
        # Action draining can run much faster than request discovery. One
        # bounded pass per poll interval avoids reloading empty profile stores.
        now = time.monotonic()
        if now < self._next_poll_at:
            return
        self._next_poll_at = now + self.worker._ACTION_POLL_SECONDS
        for db_path in db_paths:
            self.schedule(db_path)

    def schedule(self, db_path):
        runner = self.worker.runner
        loop = getattr(runner, "_gateway_loop", None)
        if loop is None or loop.is_closed() or not loop.is_running():
            return
        with self._lock:
            if self.worker._stop.is_set() or db_path in self._tasks:
                return
            # Include callbacks not yet admitted by the owner loop in the census.
            self._tasks[db_path] = None
            try:
                loop.call_soon_threadsafe(self._start, db_path)
            except RuntimeError:
                self._tasks.pop(db_path, None)

    def _start(self, db_path):
        from gateway.delivery_recovery_consumer import consume_requests

        with self._lock:
            runner = self.worker.runner
            if self.worker._stop.is_set() or not getattr(runner, "_running", False):
                self._tasks.pop(db_path, None)
                return
            task = asyncio.create_task(consume_requests(runner, db_path))
            self._tasks[db_path] = task
            task.add_done_callback(lambda done: self._finished(db_path, done))

    def _finished(self, db_path, task):
        with self._lock:
            self._tasks.pop(db_path, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Operator recovery consumer stopped (%s)", type(task.exception()).__name__)

    def stop(self):
        with self._lock:
            tasks = [task for task in self._tasks.values() if task is not None]
        for task in tasks:
            loop = task.get_loop()
            if not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)

    def is_idle(self):
        # Future.cancel() is not completion: a transport may still be unwinding.
        with self._lock:
            return not self._tasks
