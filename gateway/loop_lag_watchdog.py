"""Bounded pre-recovery diagnostics for gateway event-loop stalls."""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from collections.abc import Callable
from types import FrameType


class PrestallLoopLagWatchdog:
    """Sample the main-thread stack once while an event-loop heartbeat is stale.

    The gateway event loop calls :meth:`beat` after each successful monitor tick.
    A single daemon thread observes that monotonic heartbeat.  If it becomes
    stale, the thread records one bounded main-thread stack and remains disarmed
    until the event loop recovers and calls :meth:`beat` again.
    """

    MAX_STACK_FRAMES = 32
    MAX_STACK_BYTES = 24 * 1024
    MAX_LOG_BYTES = 32 * 1024
    _TRUNCATION_MARKER = "\n--- pre-stall stack truncated ---"

    def __init__(
        self,
        *,
        threshold: float,
        poll_interval: float,
        logger: logging.Logger,
        time_fn: Callable[[], float] = time.monotonic,
        main_thread_ident: int | None = None,
    ) -> None:
        self.threshold = max(0.0, float(threshold))
        self.poll_interval = max(0.01, float(poll_interval))
        self._logger = logger
        self._time_fn = time_fn
        self._main_thread_ident = (
            threading.main_thread().ident
            if main_thread_ident is None
            else main_thread_ident
        )
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_heartbeat = self._time_fn()
        self._reported = False
        self.thread: threading.Thread | None = None

    def beat(self) -> None:
        """Record event-loop progress and re-arm a previously fired episode."""
        now = self._time_fn()
        with self._lock:
            self._last_heartbeat = now
            self._reported = False

    def start(self) -> bool:
        """Start exactly one watchdog thread; return whether it is active."""
        if self.threshold <= 0:
            return False
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return True
            self._stop_event.clear()
            self._last_heartbeat = self._time_fn()
            self._reported = False
            thread = threading.Thread(
                target=self._run,
                name="gateway-loop-lag-prestall",
                daemon=True,
            )
            self.thread = thread
            thread.start()
        return True

    def stop(self, *, timeout: float = 2.0) -> bool:
        """Stop and join the watchdog without blocking indefinitely."""
        self._stop_event.set()
        with self._lock:
            thread = self.thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            return False
        with self._lock:
            if self.thread is thread:
                self.thread = None
        return True

    def sample_once(self) -> bool:
        """Capture one stale-heartbeat episode; exposed for deterministic tests."""
        if self.threshold <= 0 or self._stop_event.is_set():
            return False
        now = self._time_fn()
        with self._lock:
            age = max(0.0, now - self._last_heartbeat)
            if age < self.threshold or self._reported:
                return False
            self._reported = True

        try:
            stack = self._bound_stack(self._format_main_thread_stack())
            self._logger.warning(
                "Gateway event loop pre-stall heartbeat stale %.3fs "
                "(threshold %.3fs); main thread stack follows\n%s",
                age,
                self.threshold,
                stack,
            )
        except Exception:
            self._logger.warning(
                "Gateway event loop pre-stall stack capture failed",
                exc_info=True,
            )
        return True

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            self.sample_once()

    def _format_main_thread_stack(self) -> str:
        ident = self._main_thread_ident
        frame: FrameType | None = (
            sys._current_frames().get(ident) if ident is not None else None
        )
        if frame is None:
            return "<main thread frame unavailable>"
        return "".join(traceback.format_stack(frame, limit=self.MAX_STACK_FRAMES))

    @classmethod
    def _bound_stack(cls, stack: str) -> str:
        encoded = stack.encode("utf-8", errors="replace")
        if len(encoded) <= cls.MAX_STACK_BYTES:
            return stack
        marker = cls._TRUNCATION_MARKER.encode("utf-8")
        budget = max(0, cls.MAX_STACK_BYTES - len(marker))
        return encoded[:budget].decode("utf-8", errors="ignore") + cls._TRUNCATION_MARKER
