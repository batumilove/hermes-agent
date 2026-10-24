"""Async/sync bridging helpers.

``asyncio.run_coroutine_threadsafe`` can raise ``RuntimeError`` (loop closed during a
shutdown race); the coroutine is then never awaited or closed, which triggers a
"coroutine was never awaited" RuntimeWarning and leaks its frame. The helpers here
close the coroutine on scheduling failure. ``future.result()`` failures are deliberately
NOT handled: once the loop accepts the coroutine its lifecycle belongs to the loop.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable, Coroutine, Optional, ParamSpec, TypeVar

from agent.memory_provider import spawn_context_thread


_DEFAULT_LOGGER = logging.getLogger(__name__)
_P = ParamSpec("_P")
_T = TypeVar("_T")
_SerialDelivery = Callable[[bool, Any], None]
_SERIAL_DAEMON_QUEUE: queue.SimpleQueue[tuple[int, contextvars.Context, Callable[[], Any], Optional[_SerialDelivery], Any]] = queue.SimpleQueue()
_SERIAL_DAEMON_LOCK = threading.Lock()
_SERIAL_SEQUENCE_LOCK = threading.Lock()
_SERIAL_JOB_TIMEOUT_SECONDS = 5.0
_SERIAL_JOB_CAPACITY = 8
_serial_daemon_thread: Optional[threading.Thread] = None
_serial_daemon_sequence = 0
_serial_job_slots = threading.BoundedSemaphore(value=_SERIAL_JOB_CAPACITY)
_current_serial_daemon_sequence: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("hermes_serial_daemon_sequence", default=None)
)


def _next_serial_daemon_sequence() -> int:
    global _serial_daemon_sequence
    with _SERIAL_SEQUENCE_LOCK:
        _serial_daemon_sequence += 1
        return _serial_daemon_sequence


def latest_serial_daemon_sequence() -> int:
    """Return the latest sequence allocated to serial daemon work."""
    with _SERIAL_SEQUENCE_LOCK:
        return _serial_daemon_sequence


def current_serial_daemon_sequence() -> Optional[int]:
    """Return this serial job's submission sequence, if any."""
    return _current_serial_daemon_sequence.get()


def _run_serial_daemon_job(sequence, context, bound, deliver, slots) -> None:
    """Keep each abandoned worker's reservation and completion state in its own frame."""
    completed = threading.Event()
    abandoned = threading.Event()
    result_lock = threading.Lock()
    result: list[tuple[bool, Any]] = []
    late_failure_logged = False
    def _run_job() -> None:
        nonlocal late_failure_logged

        def _invoke() -> Any:
            token = _current_serial_daemon_sequence.set(sequence)
            try:
                return bound()
            finally:
                _current_serial_daemon_sequence.reset(token)

        try:
            outcome = context.run(_invoke)
        except BaseException as exc:
            with result_lock:
                result.append((False, exc))
                if abandoned.is_set():
                    late_failure_logged = True
                    _DEFAULT_LOGGER.error(
                        "Timed-out serial daemon work failed after abandonment",
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )
        else:
            with result_lock:
                result.append((True, outcome))
        finally:
            slots.release()
            completed.set()

    thread = spawn_context_thread(
        target=_run_job,
        name="hermes-detached-serial-sync-job",
        daemon=True,
    )
    try:
        thread.start()
    except BaseException as exc:
        slots.release()
        if deliver is None:
            _DEFAULT_LOGGER.error(
                "Detached serial daemon worker failed to start",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        else:
            deliver(False, exc)
        return
    if completed.wait(timeout=_SERIAL_JOB_TIMEOUT_SECONDS):
        ok, value = result[0]
        if deliver is not None:
            deliver(ok, value)
        elif not ok:
            _DEFAULT_LOGGER.error(
                "Detached serial daemon work failed",
                exc_info=(type(value), value, value.__traceback__),
            )
    else:
        abandoned.set()
        with result_lock:
            if result and not result[0][0] and not late_failure_logged:
                value = result[0][1]
                late_failure_logged = True
                _DEFAULT_LOGGER.error(
                    "Timed-out serial daemon work failed after abandonment",
                    exc_info=(type(value), value, value.__traceback__),
                )
        error = TimeoutError(
            "serial daemon work exceeded "
            f"{_SERIAL_JOB_TIMEOUT_SECONDS:.3f}s deadline"
        )
        if deliver is None:
            _DEFAULT_LOGGER.error("Detached serial daemon work timed out")
        else:
            deliver(False, error)



def _serial_daemon_worker() -> None:
    while True:
        _run_serial_daemon_job(*_SERIAL_DAEMON_QUEUE.get())


def _ensure_serial_daemon_worker() -> None:
    global _serial_daemon_thread
    with _SERIAL_DAEMON_LOCK:
        if _serial_daemon_thread is None or not _serial_daemon_thread.is_alive():
            # The lane serves many profiles; each queued job carries its own
            # context. Do not retain the first submitter's scope forever.
            _serial_daemon_thread = contextvars.Context().run(
                spawn_context_thread,
                _serial_daemon_worker,
                name="hermes-detached-serial-sync",
                daemon=True,
            )
            _serial_daemon_thread.start()


def _reserve_serial_daemon_capacity(
    deliver: Optional[_SerialDelivery],
) -> Optional[Any]:
    """Reserve bounded queue/worker capacity before admitting serial work."""
    slots = _serial_job_slots
    if slots.acquire(blocking=False):
        return slots
    error = TimeoutError("serial daemon worker capacity exhausted")
    if deliver is None:
        _DEFAULT_LOGGER.error("Detached serial daemon worker capacity exhausted")
    else:
        deliver(False, error)
    return None


def submit_sync_to_detached_serial_daemon(
    func: Callable[_P, Any], /, *args: _P.args, **kwargs: _P.kwargs
) -> None:
    """Queue bounded sync work FIFO on an abandonable daemon lane.

    Healthy work is ordered by submission. Timed-out calls may remain alive,
    but their total is capacity-bounded and runtime-status writes carry a
    sequence guard so late completion cannot overwrite a newer transition.
    """
    slots = _reserve_serial_daemon_capacity(None)
    if slots is None:
        return
    try:
        _ensure_serial_daemon_worker()
        sequence = _next_serial_daemon_sequence()
        context = contextvars.copy_context()
        bound = functools.partial(func, *args, **kwargs)
        _SERIAL_DAEMON_QUEUE.put((sequence, context, bound, None, slots))
    except BaseException:
        slots.release()
        raise


async def run_sync_in_detached_serial_daemon_thread(
    func: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs
) -> _T:
    """Run sync work FIFO on the abandonable serial daemon and await it.

    FIFO submission preserves lifecycle transition order while work finishes
    within the lane deadline. A permanently stalled call is abandoned after
    that deadline so later lifecycle/status work can still progress. Orphaned
    workers are capacity-bounded; late failures are logged, results are ignored,
    and cancellation only abandons the waiter.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_T] = loop.create_future()

    def _deliver_result(value: _T) -> None:
        if not future.done():
            future.set_result(value)

    def _deliver_error(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _deliver(ok: bool, value: Any) -> None:
        callback: Callable[[Any], None] = _deliver_result if ok else _deliver_error
        try:
            loop.call_soon_threadsafe(callback, value)
        except RuntimeError:
            pass

    slots = _reserve_serial_daemon_capacity(_deliver)
    if slots is not None:
        try:
            _ensure_serial_daemon_worker()
            sequence = _next_serial_daemon_sequence()
            context = contextvars.copy_context()
            bound = functools.partial(func, *args, **kwargs)
            _SERIAL_DAEMON_QUEUE.put((sequence, context, bound, _deliver, slots))
        except BaseException:
            slots.release()
            raise
    return await future


def start_sync_in_detached_daemon_thread(
    func: Callable[_P, Any], /, *args: _P.args, **kwargs: _P.kwargs
) -> threading.Thread:
    """Start abandonable sync work and return its daemon thread."""
    bound = functools.partial(func, *args, **kwargs)
    thread = spawn_context_thread(
        target=bound,
        name="hermes-detached-sync",
        daemon=True,
    )
    thread.start()
    return thread


async def run_sync_in_detached_daemon_thread(
    func: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs
) -> _T:
    """Run blocking work off-loop without owning default-executor shutdown.

    A stuck call submitted through ``asyncio.to_thread`` keeps the loop's
    default executor alive during shutdown.  Process-lifecycle control I/O
    must instead be abandonable: this one-shot daemon thread publishes its
    result back to the loop, while late completion after cancellation is
    discarded safely.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_T] = loop.create_future()
    bound = functools.partial(func, *args, **kwargs)

    def _deliver_result(value: _T) -> None:
        if not future.done():
            future.set_result(value)

    def _deliver_error(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _worker() -> None:
        try:
            outcome = bound()
        except BaseException as exc:  # health: allow BLE001 -- Worker boundary forwards arbitrary failures unchanged to the awaiting future.
            callback: Callable[[Any], None] = _deliver_error
            value: Any = exc
        else:
            callback = _deliver_result
            value = outcome
        try:
            loop.call_soon_threadsafe(callback, value)
        except RuntimeError:
            # The loop closed after cancellation; no waiter remains.
            pass

    spawn_context_thread(
        target=_worker,
        name="hermes-detached-sync",
        daemon=True,
    ).start()
    return await future


def safe_schedule_threadsafe(
    coro: Coroutine[Any, Any, Any], loop: Optional[asyncio.AbstractEventLoop], *,
    logger: Optional[logging.Logger] = None,
    log_message: str = "Failed to schedule coroutine on loop", log_level: int = logging.DEBUG,
) -> Optional[Future]:
    """Schedule ``coro`` on ``loop`` from a sync context, leak-safe.

    Returns the Future on success, or ``None`` if the loop is missing or scheduling
    raised; in every failure path the coroutine is closed. Callers keep full control
    over the returned future (``.result(timeout=...)``, callbacks, fire-and-forget).
    """
    log = logger if logger is not None else _DEFAULT_LOGGER
    try:
        if loop is None:
            raise RuntimeError("loop is None")
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        if asyncio.iscoroutine(coro):
            coro.close()
        log.log(log_level, "%s: %s", log_message, exc)
        return None


def consume_detached_task_result(task: asyncio.Future[Any]) -> None:
    """``add_done_callback`` for cancelled-and-detached tasks: observe the exception so the
    loop does not log "exception was never retrieved"; cancellation and terminal errors
    are swallowed because the task's owner already gave up on it."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass
