"""Async/sync bridging helpers.

The codebase has ~30 sites that schedule a coroutine onto an event loop from a
worker thread via :func:`asyncio.run_coroutine_threadsafe`.  That function can
raise :class:`RuntimeError` (e.g. the loop was closed during a shutdown race),
and when it does the coroutine object is never awaited and never closed —
which triggers a ``"coroutine '<name>' was never awaited"`` RuntimeWarning and
leaks the coroutine's frame until GC.

:func:`safe_schedule_threadsafe` wraps the call, closes the coroutine on
scheduling failure, and returns ``None`` (instead of a half-formed future) so
callers can branch cleanly:

    fut = safe_schedule_threadsafe(coro, loop)
    if fut is None:
        return  # or fallback behavior
    fut.result(timeout=5)

The helper deliberately does NOT also handle ``future.result()`` failures —
that is a separate concern.  Once the loop has accepted the coroutine, its
lifecycle belongs to the loop, not the scheduling thread.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable, Coroutine, Optional, ParamSpec, TypeVar


_DEFAULT_LOGGER = logging.getLogger(__name__)
_P = ParamSpec("_P")
_T = TypeVar("_T")


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
    context = contextvars.copy_context()
    bound = functools.partial(func, *args, **kwargs)

    def _deliver_result(value: _T) -> None:
        if not future.done():
            future.set_result(value)

    def _deliver_error(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _worker() -> None:
        try:
            outcome = context.run(bound)
        except BaseException as exc:
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

    threading.Thread(
        target=_worker,
        name="hermes-detached-sync",
        daemon=True,
    ).start()
    return await future


def safe_schedule_threadsafe(
    coro: Coroutine[Any, Any, Any],
    loop: Optional[asyncio.AbstractEventLoop],
    *,
    logger: Optional[logging.Logger] = None,
    log_message: str = "Failed to schedule coroutine on loop",
    log_level: int = logging.DEBUG,
) -> Optional[Future]:
    """Schedule ``coro`` on ``loop`` from a sync context, leak-safe.

    Returns the :class:`concurrent.futures.Future` on success, or ``None`` if
    the loop is missing or :func:`asyncio.run_coroutine_threadsafe` raised
    (e.g. the loop was closed during a shutdown race).  In all failure paths
    the coroutine is :meth:`close`-d so it does not trigger
    ``"coroutine was never awaited"`` warnings or leak its frame.

    Callers retain full control over what to do with the returned future
    (call ``.result(timeout=...)``, attach ``add_done_callback``, ignore it
    fire-and-forget, etc.).
    """
    log = logger if logger is not None else _DEFAULT_LOGGER

    if loop is None:
        if asyncio.iscoroutine(coro):
            coro.close()
        log.log(log_level, "%s: loop is None", log_message)
        return None

    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        if asyncio.iscoroutine(coro):
            coro.close()
        log.log(log_level, "%s: %s", log_message, exc)
        return None


def consume_detached_task_result(task: "asyncio.Future[Any]") -> None:
    """Retrieve a detached task's result without surfacing cancellation.

    Used as an ``add_done_callback`` on tasks that were cancelled and
    detached (e.g. an adapter close path that swallows ``CancelledError``
    past its teardown deadline). Observing ``task.exception()`` prevents
    "exception was never retrieved" noise on the event loop; cancellation
    and any terminal error are deliberately swallowed — the task's owner
    already gave up on it.
    """
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass
