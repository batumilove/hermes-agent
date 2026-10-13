"""Telegram network helpers: a hostname-preserving fallback transport (Host + SNI stay
api.telegram.org while TCP retries known IPv4 literals) plus DoH-based IP discovery."""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import itertools
import logging
import os
import socket
import sys
import traceback
from typing import Any, Iterable, Optional

import httpx

_HTTPX_ASYNC_HTTP_TRANSPORT_TYPE = httpx.AsyncHTTPTransport
from agent.ssl_verify import platform_ssl_context

logger = logging.getLogger(__name__)
cleanup_logger = logging.getLogger("hermes.telegram.network_cleanup")


def _redacted_cleanup_exception_info():
    """Keep exception type while discarding transport messages and request objects."""
    error = sys.exception()
    safe = RuntimeError(f"{type(error).__name__}: Telegram network boundary failure (details redacted)")
    return RuntimeError, safe, None


def _redacted_cleanup_frames() -> str:
    """Expose code locations without source text, exception messages or frame locals."""
    error = sys.exception()
    return "\n".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)
    )


_TELEGRAM_API_HOST = "api.telegram.org"


def _parse_tcp_connect_deadline() -> float:
    """Parse HERMES_TELEGRAM_TCP_CONNECT_DEADLINE once at import time.

    Follows the module-level pattern of ``_DOH_TIMEOUT``: a plain float the
    hot path can read without per-connect env parsing, with the env override
    kept as an escape hatch for incident response.
    """
    raw = os.environ.get("HERMES_TELEGRAM_TCP_CONNECT_DEADLINE")  # health: allow HX002 -- Non-secret process-wide TCP shutdown budget; all profile transports share this backend deadline.
    if not raw:
        return 15.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid HERMES_TELEGRAM_TCP_CONNECT_DEADLINE %r; using 15.0s", raw
        )
        return 15.0
    if value <= 0:
        logger.warning(
            "Non-positive HERMES_TELEGRAM_TCP_CONNECT_DEADLINE %r; using 15.0s", raw
        )
        return 15.0
    return value


# Upper bound on how long the cancellation-safe connect shield may hold a
# caller before giving up (#95159). Default 15.0s exceeds httpx's 10s connect
# timeout by design: healthy-but-slow connects finish inside the caller's own
# budget, while a blackholed SYN to a sticky Telegram DC IP is abandoned here
# so the pool retry machinery actually fires instead of wedging getUpdates
# forever.
_TCP_CONNECT_DEADLINE = _parse_tcp_connect_deadline()


# Strong references for asynchronous abandoned-response closes. asyncio's loop
# only keeps weak references to tasks; without this set a cleanup can be
# garbage-collected before it closes the response stream.
_abandoned_response_cleanups: set[asyncio.Task] = set()
_diagnostic_request_ids = itertools.count(1)
_diagnostic_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "telegram_diagnostic_request_id", default="none"
)
_request_network_streams: contextvars.ContextVar[
    list[_CancellationSafeNetworkStream] | None
] = contextvars.ContextVar("telegram_request_network_streams", default=None)


def _stream_local_port(stream: Any) -> str:
    """Return a stream's local TCP port without exposing its peer or request."""
    try:
        get_extra_info = getattr(stream, "get_extra_info", None)
        if get_extra_info is None:
            return "unknown"
        sock = get_extra_info("socket")
        if sock is None:
            return "unknown"
        sockname = sock.getsockname()
        if isinstance(sockname, tuple) and len(sockname) >= 2:
            port = sockname[1]
            if type(port) is int and 1 <= port <= 65535:
                return str(port)
    except Exception:
        cleanup_logger.debug("_stream_local_port boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
    return "unknown"


def _normalize_diagnostic_route(route: Any) -> str:
    """Return only the primary marker or a public IPv4 fallback route."""

    if route == "primary":
        return "primary"
    try:
        addr = ipaddress.ip_address(route)
    except (TypeError, ValueError):
        return "unknown"
    if addr.version != 4 or not addr.is_global or addr.is_multicast:
        return "unknown"
    return str(addr)


class _SocketLifecycleState:
    """Shared lifecycle identity across raw and TLS wrappers for one socket."""

    def __init__(self, *, owner: str, route: str, stream: Any):
        self.owner = owner
        self.route = route
        self.request_id = _diagnostic_request_id.get()
        self.local_port = _stream_local_port(stream)
        self._close_started_reported = False
        self._closed_reported = False
        _log_socket_lifecycle(
            event="socket-opened",
            owner=self.owner,
            route=self.route,
            local_port=self.local_port,
            request_id=self.request_id,
        )

    def report_close_started(self) -> None:
        if self._close_started_reported:
            return
        self._close_started_reported = True
        _log_socket_lifecycle(
            event="socket-close-started",
            owner=self.owner,
            route=self.route,
            local_port=self.local_port,
            request_id=self.request_id,
        )

    def report_closed(self, *, error: bool = False) -> None:
        if self._closed_reported:
            return
        self._closed_reported = True
        _log_socket_lifecycle(
            event="socket-close-error" if error else "socket-closed",
            owner=self.owner,
            route=self.route,
            local_port=self.local_port,
            request_id=self.request_id,
        )


class _CancellationSafeNetworkStream:
    """Close the raw TCP stream if TLS setup is cancelled.

    httpcore 1.0.9's AnyIO stream closes on ``Exception`` during ``start_tls``
    but not on asyncio's ``CancelledError`` (a ``BaseException``). Retaining the
    pre-TLS stream here lets this Telegram transport close exactly that socket;
    no shared pool or concurrent healthy request is interrupted.
    """

    def __init__(self, stream: Any):
        self._stream = stream
        self._close_task: asyncio.Task | None = None
        request_streams = _request_network_streams.get()
        if request_streams is not None:
            request_streams.append(self)

    async def _close_after_failure(self) -> None:
        try:
            await self.aclose()
        except BaseException:
            cleanup_logger.debug("_close_after_failure boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            return await self._stream.read(max_bytes, timeout=timeout)
        except BaseException:
            cleanup_logger.debug("read boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            # A caller cancellation can arrive after the peer has queued bytes
            # plus FIN but before httpcore creates a Response. Close this exact
            # stream while ownership is still local; no response-level cleanup
            # exists yet, and an inactive pool route may never be reaped.
            await self._close_after_failure()
            raise

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        try:
            await self._stream.write(buffer, timeout=timeout)
        except BaseException:
            cleanup_logger.debug("write boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            await self._close_after_failure()
            raise

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_started()
            self._close_task = asyncio.create_task(self._stream.aclose())
            _abandoned_response_cleanups.add(self._close_task)
            self._close_task.add_done_callback(self._close_done)
        await asyncio.shield(self._close_task)

    def _close_started(self) -> None:
        return None

    def _close_done(self, task: asyncio.Task) -> None:
        _abandoned_response_cleanups.discard(task)
        try:
            task.result()
        except BaseException:
            cleanup_logger.debug("_close_done boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            self._close_finished(error=True)
        else:
            self._close_finished(error=False)

    def _close_finished(self, *, error: bool) -> None:
        return None

    async def start_tls(
        self, ssl_context, server_hostname=None, timeout=None
    ) -> _CancellationSafeNetworkStream:
        try:
            stream = await self._stream.start_tls(
                ssl_context, server_hostname, timeout
            )
        except BaseException:
            cleanup_logger.debug("start_tls boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            await self._close_after_failure()
            raise
        return _CancellationSafeNetworkStream(stream)

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _DiagnosticCancellationSafeNetworkStream(_CancellationSafeNetworkStream):
    """Opt-in raw-stream wrapper that reports one socket lifecycle."""

    def __init__(self, stream: Any, lifecycle: _SocketLifecycleState):
        super().__init__(stream)
        self._lifecycle = lifecycle

    def _close_started(self) -> None:
        self._lifecycle.report_close_started()

    def _close_finished(self, *, error: bool) -> None:
        self._lifecycle.report_closed(error=error)

    async def start_tls(
        self, ssl_context, server_hostname=None, timeout=None
    ) -> _DiagnosticCancellationSafeNetworkStream:
        try:
            stream = await self._stream.start_tls(
                ssl_context, server_hostname, timeout
            )
        except BaseException:
            cleanup_logger.debug("start_tls boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            await self._close_after_failure()
            raise
        return _DiagnosticCancellationSafeNetworkStream(stream, self._lifecycle)


class _CancellationSafeNetworkBackend:
    """Wrap httpcore-created streams with connect/TLS cancellation cleanup."""

    def __init__(self, backend: Any):
        self._backend = backend

    def _wrap(self, stream: Any) -> _CancellationSafeNetworkStream:
        return _CancellationSafeNetworkStream(stream)

    async def _close_abandoned_connect_result(self, connect_task: asyncio.Task) -> None:
        try:
            stream = connect_task.result()
        except BaseException:
            cleanup_logger.debug("_close_abandoned_connect_result boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            return
        try:
            await self._wrap(stream).aclose()
        except BaseException:
            cleanup_logger.debug("_close_abandoned_connect_result boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())

    def _abandoned_connect_done(self, connect_task: asyncio.Task) -> None:
        _abandoned_response_cleanups.discard(connect_task)
        cleanup_task = connect_task.get_loop().create_task(
            self._close_abandoned_connect_result(connect_task)
        )
        _abandoned_response_cleanups.add(cleanup_task)
        cleanup_task.add_done_callback(_abandoned_response_cleanups.discard)

    async def _connect(self, awaitable: Any) -> _CancellationSafeNetworkStream:
        # The backend may own a live socket before connect_* returns it. Shield
        # that ownership transfer so caller cancellation cannot destroy the only
        # coroutine able to return the socket. If the caller exits first, retain
        # the connect task and close any eventual result independently.
        #
        # The shield itself is bounded (#95159). A blackholed SYN to a sticky
        # Telegram DC IP never completes and never errors: without a deadline,
        # the shielded connect holds the caller forever while deflecting every
        # external cancellation, wedging the pool's retry machinery (getUpdates
        # never returns, so PTB retry never fires). asyncio.wait_for raising
        # TimeoutError here is indistinguishable, to the caller, from the
        # shielded task failing — the except clause below keeps the exact same
        # abandoned-connect retention: the connect task stays alive and its
        # done callback closes the eventual socket if the SYN ever completes.
        connect_task = asyncio.ensure_future(awaitable)
        _abandoned_response_cleanups.add(connect_task)
        try:
            stream = await asyncio.wait_for(
                asyncio.shield(connect_task),
                timeout=_TCP_CONNECT_DEADLINE,
            )
        except BaseException:
            cleanup_logger.debug("_connect boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            # A done callback captures its registration Context by default. Do not
            # retain the failed request's stream registry while an abandoned
            # backend connect finishes, or append the eventual wrapper to a stale
            # request list after that request has already unwound.
            connect_task.add_done_callback(
                self._abandoned_connect_done,
                context=contextvars.Context(),
            )
            raise
        _abandoned_response_cleanups.discard(connect_task)
        return self._wrap(stream)

    async def connect_tcp(self, *args, **kwargs) -> _CancellationSafeNetworkStream:
        return await self._connect(self._backend.connect_tcp(*args, **kwargs))

    async def connect_unix_socket(self, *args, **kwargs) -> _CancellationSafeNetworkStream:
        return await self._connect(self._backend.connect_unix_socket(*args, **kwargs))

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _DiagnosticCancellationSafeNetworkBackend(_CancellationSafeNetworkBackend):
    """Opt-in backend that binds raw sockets to a request owner and route."""

    def __init__(self, backend: Any, *, owner: str, route: str):
        super().__init__(backend)
        self._owner = owner if owner in {"general", "polling"} else "unknown"
        self._route = _normalize_diagnostic_route(route)

    def _wrap(self, stream: Any) -> _DiagnosticCancellationSafeNetworkStream:
        lifecycle = _SocketLifecycleState(
            owner=self._owner,
            route=self._route,
            stream=stream,
        )
        return _DiagnosticCancellationSafeNetworkStream(stream, lifecycle)


def _new_async_http_transport(
    *,
    socket_diagnostics: bool = False,
    diagnostic_owner: str = "unknown",
    diagnostic_route: str = "primary",
    **kwargs,
) -> httpx.AsyncBaseTransport:
    """Build an HTTPX transport whose raw TLS sockets are cancellation-safe."""
    transport = httpx.AsyncHTTPTransport(**kwargs)
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is None or backend is None:
        # Tests and extension code may intentionally replace the concrete HTTPX
        # transport with another AsyncBaseTransport. Only fail closed when the
        # real HTTPX type changes shape; custom transports own their lifecycle.
        if isinstance(transport, httpx.AsyncBaseTransport) and not isinstance(
            transport, _HTTPX_ASYNC_HTTP_TRANSPORT_TYPE
        ):
            return transport
        raise RuntimeError(
            "Unsupported httpx/httpcore transport internals: expected "
            "AsyncHTTPTransport._pool._network_backend"
        )
    if socket_diagnostics:
        pool._network_backend = _DiagnosticCancellationSafeNetworkBackend(
            backend,
            owner=diagnostic_owner,
            route=diagnostic_route,
        )
    else:
        # Preserve the exact pre-diagnostics hot path when the opt-in is off.
        pool._network_backend = _CancellationSafeNetworkBackend(backend)
    return transport


async def _close_response(response: httpx.Response) -> None:
    """Close an abandoned response, observing terminal exceptions.

    This helper runs inside a fire-and-forget cleanup task, so it must not let
    caller cancellation (``asyncio.CancelledError``) leave the response socket
    open. All exceptions are swallowed after the close attempt so that the
    cleanup task terminates cleanly.
    """
    try:
        await response.aclose()
    except BaseException:
        cleanup_logger.debug("Failed to close abandoned Telegram response\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())


class _RetryingCloseResponseStream(httpx.AsyncByteStream):
    """Guarantee a response socket is released even under close cancellation.

    httpcore 1.0.9's ``PoolByteStream.aclose()`` marks itself closed *before*
    its cancellable pool bookkeeping runs. A caller cancellation delivered
    during that bookkeeping interrupts the first close while the raw OS socket
    lingers in ``CLOSE_WAIT``; a second ``aclose()`` then no-ops because the
    stream already believes it is closed. Retrying ``aclose()`` would therefore
    report false success without releasing the socket.

    The first ``aclose()`` is run inside ``asyncio.shield`` so an *external*
    cancellation cannot strand it: the shielded coroutine keeps running to
    completion on its own, releasing the socket. Only when the close fails for
    a real reason — the coroutine itself raised ``CancelledError`` (e.g. a
    transport-level cancel injected into the stream) or any other exception —
    does a detached bounded task retry the close once and, if that also fails,
    force-close the exact raw OS socket via ``get_extra_info('socket')`` as a
    guaranteed terminal fallback.
    """

    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        *,
        network_stream: Any | None = None,
    ):
        self._stream = stream
        self._network_stream = network_stream
        self._close_task: asyncio.Task | None = None
        self._cleanup_scheduled = False
        self._raw_socket_closed = False

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stream.aclose())
            _abandoned_response_cleanups.add(self._close_task)
            self._close_task.add_done_callback(self._initial_close_done)
        await asyncio.shield(self._close_task)

    def _initial_close_done(self, task: asyncio.Task) -> None:
        """Observe the first close and recover any synchronous or late failure."""
        _abandoned_response_cleanups.discard(task)
        try:
            task.result()
        except BaseException:
            cleanup_logger.debug("_initial_close_done boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            self._schedule_detached_retry()

    def _schedule_detached_retry(self) -> None:
        if self._cleanup_scheduled:
            return
        self._cleanup_scheduled = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # The response close has already failed and no event loop remains
            # to drive async recovery. Close only its exact raw socket.
            self._close_raw_socket_fallback()
            return
        task = loop.create_task(self._retry_close())
        _abandoned_response_cleanups.add(task)
        task.add_done_callback(_abandoned_response_cleanups.discard)

    async def _retry_close(self) -> None:
        # Terminal cleanup: swallow any exception thrown at us while we are
        # trying to close the socket, so caller cancellation or process exit
        # cannot abandon an open FD.
        try:
            await asyncio.wait_for(self._stream.aclose(), timeout=5.0)
        except BaseException:
            cleanup_logger.debug("_retry_close boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
        if self._raw_socket_is_closed():
            return
        # A response stream may mark itself closed before failing, making this
        # retry return successfully without completing pool/socket cleanup.
        # Once the first close failed, always drive the exact network-stream
        # terminal path and verify the raw OS socket is really gone before
        # returning.
        await self._force_close_network_stream()

    def _raw_socket_is_closed(self) -> bool:
        """Return true only when the exact exposed OS socket is already closed."""
        try:
            get_extra_info = getattr(self._network_stream, "get_extra_info", None)
            if get_extra_info is None:
                return False
            raw_socket = get_extra_info("socket")
            fileno = getattr(raw_socket, "fileno", None)
            return fileno is not None and fileno() == -1
        except Exception:
            cleanup_logger.debug("_raw_socket_is_closed boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            return False

    async def _force_close_network_stream(self) -> None:
        if self._network_stream is None:
            return

        # Prefer the network stream's cooperative async close path.  A
        # "successful" return is not enough: a pooled connection may be
        # released rather than closed, leaving the OS socket in CLOSE_WAIT.
        try:
            aclose = getattr(self._network_stream, "aclose", None)
            if aclose is not None:
                await asyncio.wait_for(aclose(), timeout=2.0)
        except asyncio.CancelledError:
            # We are racing caller cancellation. Continue to synchronous close
            # so the socket does not outlive the cancelled task.
            pass
        except BaseException:
            cleanup_logger.debug("_force_close_network_stream boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())

        if self._raw_socket_is_closed():
            return

        # Fall back to the synchronous close() hook if the stream provides one.
        close = getattr(self._network_stream, "close", None)
        if close is not None:
            try:
                close()
            except BaseException:
                cleanup_logger.debug("_force_close_network_stream boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())

        if self._raw_socket_is_closed():
            return

        # Last resort: close the exact raw OS socket exposed by the network stream.
        self._close_raw_socket_fallback()

    def _close_raw_socket_fallback(self) -> None:
        """Close the exact raw OS socket exposed by the network stream.

        This is the terminal fallback when cooperative stream close did not
        actually close the FD.  asyncio exposes a ``TransportSocket`` view
        whose disruptive ``close()`` method is intentionally absent; in that
        case close its exact wrapped socket object instead.  Idempotent once
        the exposed socket reports ``fileno() == -1``.
        """
        if self._raw_socket_closed:
            return
        try:
            get_extra_info = getattr(self._network_stream, "get_extra_info", None)
            if get_extra_info is None:
                return
            raw_socket = get_extra_info("socket")
            if raw_socket is None:
                return

            wrapped_socket = getattr(raw_socket, "_sock", None)
            close_targets = [raw_socket]
            if isinstance(wrapped_socket, socket.socket):
                close_targets.append(wrapped_socket)
            for target in close_targets:
                close = getattr(target, "close", None)
                if close is None:
                    continue
                try:
                    close()
                except Exception:
                    cleanup_logger.debug("_close_raw_socket_fallback boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
                    continue
                if self._raw_socket_is_closed():
                    self._raw_socket_closed = True
                    return
        except Exception:
            cleanup_logger.debug("_close_raw_socket_fallback boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())

        logger.debug("Failed to close raw Telegram response socket fallback")


async def _close_request_network_streams(
    streams: tuple[_CancellationSafeNetworkStream, ...],
) -> None:
    """Close only raw streams created by one failed pre-response request."""
    await asyncio.gather(
        *(stream.aclose() for stream in streams),
        return_exceptions=True,
    )


async def _handle_transport_request(
    transport: httpx.AsyncBaseTransport,
    request: httpx.Request,
    *,
    diagnostic_context: tuple[str, str, str] | None = None,
) -> httpx.Response:
    """Run one transport request without leaking sockets on caller cancellation.

    Cancelling httpcore while it is connecting or reading response headers can
    detach the live socket from its pool. Under Telegram progress/edit churn,
    those sockets persist in CLOSE_WAIT until the gateway reaches RLIMIT_NOFILE.
    ``_CancellationSafeNetworkStream`` closes the exact raw socket when caller
    cancellation interrupts a stream operation. A request-scoped registry also
    retains every stream returned before response creation, so cancellation in
    httpcore's async trace hooks detaches a strongly referenced cleanup task
    for only those streams; concurrent requests and shared pools are untouched.

    After the response has been returned, a caller that is cancelled while
    reading the response body (e.g. PTB's
    ``HTTPXRequest.do_request`` awaiting ``res.content``) may abandon the
    response and leave its socket in CLOSE_WAIT. The caller-task done callback
    closes that otherwise-unclaimed response, and the response stream wrapper
    retries an interrupted close in a detached bounded task.
    """
    request_streams: list[_CancellationSafeNetworkStream] = []
    streams_token = _request_network_streams.set(request_streams)
    try:
        response = await transport.handle_async_request(request)
    except BaseException:
        cleanup_logger.debug("_handle_transport_request boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
        if request_streams:
            cleanup_task = asyncio.create_task(
                _close_request_network_streams(tuple(reversed(request_streams)))
            )
            _abandoned_response_cleanups.add(cleanup_task)
            cleanup_task.add_done_callback(_abandoned_response_cleanups.discard)
        raise
    finally:
        _request_network_streams.reset(streams_token)
    network_stream = response.extensions.get("network_stream")
    response_stream = response.stream
    if diagnostic_context is not None:
        owner, route, request_id = diagnostic_context
        response_stream = _DiagnosticResponseStream(
            response_stream,
            owner=owner,
            route=route,
            local_port=_response_local_port(response),
            request_id=request_id,
        )
    response.stream = _RetryingCloseResponseStream(
        response_stream,
        network_stream=network_stream,
    )
    # Guard the response against caller task cancellation/exception after
    # we have returned it. If the caller is cancelled while consuming the
    # body, its done callback will close the otherwise-abandoned response.
    current_task = asyncio.current_task()
    if current_task is not None:
        _close_response_on_task_done(current_task, response)
    return response


def _close_response_on_task_done(task: asyncio.Task, response: httpx.Response) -> None:
    """Attach a callback that closes *response* if it is still open at task end."""
    def _on_done(finished_task: asyncio.Task) -> None:
        # Always close if the response was not fully closed, even on normal
        # completion: a caller may have returned without consuming the body.
        if getattr(response, "is_closed", True):
            return
        close_task = asyncio.create_task(_close_response(response))
        close_task.add_done_callback(_abandoned_response_cleanups.discard)
        _abandoned_response_cleanups.add(close_task)

    task.add_done_callback(_on_done)


def _log_socket_lifecycle(
    *,
    event: str,
    owner: str,
    route: str,
    local_port: str,
    request_id: str = "none",
) -> None:
    """Emit one deliberately request-content-free socket lifecycle record."""
    # Diagnostics are explicitly opt-in and must remain visible at the gateway's
    # default WARNING stderr level so bounded staging collection can use Docker logs.
    logger.warning(
        "[Telegram socket] event=%s owner=%s route=%s request_id=%s local_port=%s",
        event,
        owner,
        route,
        request_id,
        local_port,
    )


def _response_local_port(response: httpx.Response) -> str:
    """Return the response socket's local ephemeral port, or ``unknown``."""
    try:
        network_stream = response.extensions.get("network_stream")
        if network_stream is None:
            return "unknown"
        get_extra_info = getattr(network_stream, "get_extra_info", None)
        if get_extra_info is None:
            return "unknown"
        sock = get_extra_info("socket")
        if sock is None:
            return "unknown"
        sockname = sock.getsockname()
        if isinstance(sockname, tuple) and len(sockname) >= 2:
            return str(sockname[1])
    except Exception:
        cleanup_logger.debug("_response_local_port boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
    return "unknown"


class _DiagnosticResponseStream(httpx.AsyncByteStream):
    """Observe response closure without changing stream semantics."""

    def __init__(
        self,
        stream,
        *,
        owner: str,
        route: str,
        local_port: str,
        request_id: str,
    ):
        self._stream = stream
        self._owner = owner
        self._route = route
        self._local_port = local_port
        self._request_id = request_id
        # A failed close may later succeed during the guarded retry. Report the
        # error and terminal success independently while preserving PR #122's
        # request correlation on both records.
        self._close_error_reported = False
        self._close_success_reported = False

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        except BaseException:
            cleanup_logger.debug("aclose boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            if not self._close_error_reported:
                self._close_error_reported = True
                _log_socket_lifecycle(
                    event="response-close-error",
                    owner=self._owner,
                    route=self._route,
                    local_port=self._local_port,
                    request_id=self._request_id,
                )
            raise
        if not self._close_success_reported:
            self._close_success_reported = True
            _log_socket_lifecycle(
                event="response-closed",
                owner=self._owner,
                route=self._route,
                local_port=self._local_port,
                request_id=self._request_id,
            )

# DNS-over-HTTPS providers used to discover Telegram API IPs that may differ
# from the (potentially unreachable) IP returned by the local system resolver.
_DOH_TIMEOUT = 4.0  # seconds — bounded so connect() isn't noticeably delayed

def _describe_transport_error(error: Exception) -> str:
    """Return a non-empty, secret-safe exception representation for diagnostics."""
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(repr(error), force=True)
    except Exception:
        cleanup_logger.debug("_describe_transport_error boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
        return type(error).__name__

# TCP keepalive so a half-open/CLOSE-WAIT long-poll errors out instead of blocking getUpdates forever
# (Windows leaves SO_KEEPALIVE off). Idle/interval knobs are best-effort per Python/OS combo.
# Windows does not enable SO_KEEPALIVE on new sockets by default, so a dead api.telegram.org peer can hang
# forever (#87057).
_TCP_KEEPALIVE_IDLE_S = 30
_TCP_KEEPALIVE_INTERVAL_S = 10
_TCP_KEEPALIVE_COUNT = 3


def tcp_keepalive_socket_options() -> list[tuple[int, int, int]]:
    """``setsockopt`` tuples for httpx ``socket_options``: always SO_KEEPALIVE, plus idle/interval/count
    when the interpreter exposes those option names."""
    options: list[tuple[int, int, int]] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    for opt, value in ((idle, _TCP_KEEPALIVE_IDLE_S), (getattr(socket, "TCP_KEEPINTVL", None), _TCP_KEEPALIVE_INTERVAL_S),
                       (getattr(socket, "TCP_KEEPCNT", None), _TCP_KEEPALIVE_COUNT)):
        if opt is not None:
            options.append((socket.IPPROTO_TCP, opt, value))
    return options

# DNS-over-HTTPS providers: discover Telegram API IPs the (possibly unreachable) local resolver may not
# return. Bounded so connect() isn't delayed.
_DOH_TIMEOUT = 4.0
_DOH_PROVIDERS: list[dict] = [
    {"url": "https://dns.google/resolve", "params": {"name": _TELEGRAM_API_HOST, "type": "A"}, "headers": {}},
    {
        "url": "https://cloudflare-dns.com/dns-query", "params": {"name": _TELEGRAM_API_HOST, "type": "A"},
        "headers": {"Accept": "application/dns-json"}},
]
# Last-resort IPv4 Bot API endpoints (149.154.160.0/20). Used when DoH is blocked AND as
# first-try connect targets so a blackholed IPv6 AAAA for the hostname can't pin initialize().
SEED_FALLBACK_IPS: list[str] = ["149.154.166.110", "149.154.167.220"]
_UNSET = object()


def _resolve_proxy_url(target_hosts=None) -> str | None:
    from gateway.platforms.base import resolve_proxy_url  # env vars + macOS system proxy
    return resolve_proxy_url("TELEGRAM_PROXY", target_hosts=target_hosts)


class TelegramFallbackTransport(httpx.AsyncBaseTransport):
    """Reach the Bot API via known IPv4 literals first, dual-stack hostname last. Host + SNI stay on
    api.telegram.org (like ``curl --resolve``) so a blackholed IPv6 AAAA can't pin initialize()."""

    _POOL_LIMITS = httpx.Limits(
        max_connections=8,
        max_keepalive_connections=0,
        keepalive_expiry=5.0,
    )

    def __init__(
        self,
        fallback_ips: Iterable[str],
        *,
        owner_role: str = "unknown",
        socket_diagnostics: bool = False,
        **transport_kwargs,
    ):
        self._owner_role = (
            owner_role if owner_role in {"general", "polling"} else "unknown"
        )
        self._socket_diagnostics = bool(socket_diagnostics)
        self._fallback_ips = list(dict.fromkeys(_normalize_fallback_ips(fallback_ips)))
        # Each logical PTB request owns a primary pool plus one pool per fallback
        # route. httpcore only reaps expired/peer-closed idle connections when
        # that same pool receives another request, so an inactive route can hold
        # CLOSE_WAIT sockets indefinitely. Keep active-request concurrency, but
        # do not retain completed Telegram connections as idle pool members.
        limits = transport_kwargs.get("limits")
        if limits is None:
            transport_kwargs["limits"] = self._POOL_LIMITS
        else:
            transport_kwargs["limits"] = httpx.Limits(
                max_connections=limits.max_connections,
                max_keepalive_connections=0,
                keepalive_expiry=limits.keepalive_expiry,
            )
        proxy_url = _resolve_proxy_url(target_hosts=[_TELEGRAM_API_HOST, *self._fallback_ips])
        if proxy_url and "proxy" not in transport_kwargs:
            transport_kwargs["proxy"] = proxy_url
        transport_kwargs.setdefault("socket_options", tcp_keepalive_socket_options())
        self._primary = _new_async_http_transport(
            socket_diagnostics=self._socket_diagnostics,
            diagnostic_owner=self._owner_role,
            diagnostic_route="primary",
            **transport_kwargs,
        )
        self._transport_kwargs = dict(transport_kwargs)
        self._fallbacks: dict[str, httpx.AsyncBaseTransport] = {}
        self._primary_lock = asyncio.Lock()
        self._primary_closed = False
        self._fallback_lock = asyncio.Lock()
        # ``_UNSET`` / ``None`` / ``str`` = no sticky yet / sticky hostname / sticky IPv4.
        self._sticky_ip: object = _UNSET
        self._sticky_lock = asyncio.Lock()
        self._last_failure: tuple[str, str] | None = None

    async def _get_fallback(self, ip: str) -> httpx.AsyncBaseTransport:
        async with self._fallback_lock:
            transport = self._fallbacks.get(ip)
            if transport is None:
                transport = _new_async_http_transport(
                    socket_diagnostics=self._socket_diagnostics,
                    diagnostic_owner=self._owner_role,
                    diagnostic_route=ip,
                    **self._transport_kwargs,
                )
                self._fallbacks[ip] = transport
            return transport

    async def _reset_primary(self, transport: httpx.AsyncBaseTransport) -> None:
        # Retryable primary failures can leave half-closed sockets in the pool;
        # replace and close the failed generation before trying fallback.
        async with self._primary_lock:
            if self._primary_closed or transport is not self._primary:
                return
            self._primary = _new_async_http_transport(
                socket_diagnostics=self._socket_diagnostics,
                diagnostic_owner=self._owner_role,
                diagnostic_route="primary",
                **self._transport_kwargs,
            )
        try:
            await transport.aclose()
        except Exception as exc:
            cleanup_logger.debug("_reset_primary boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            logger.debug("[Telegram] Error closing primary transport: %s", exc)

    async def _reset_fallback(self, ip: str) -> None:
        """Discard a failed fallback pool: a peer-closed connect leaves a CLOSE_WAIT socket in it, and the
        poisoned pool would leak one fd per retry.

        Retaining the poisoned pool leaks one descriptor per retry until the process hits its file limit and
        can no longer accept connections or resolve DNS (#63311).
        """
        async with self._fallback_lock:
            transport = self._fallbacks.pop(ip, None)
        if transport is None:
            return
        try:
            await transport.aclose()
        except Exception as exc:
            cleanup_logger.debug("_reset_fallback boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
            logger.debug(
                "[Telegram] Error closing fallback transport %s: %s",
                ip,
                exc,
            )

    async def _request_for_route(
        self,
        transport: httpx.AsyncBaseTransport,
        request: httpx.Request,
        route: str,
    ) -> httpx.Response:
        if not self._socket_diagnostics:
            return await _handle_transport_request(transport, request)

        request_id = str(next(_diagnostic_request_ids))
        token = _diagnostic_request_id.set(request_id)
        _log_socket_lifecycle(
            event="request-started",
            owner=self._owner_role,
            route=route,
            request_id=request_id,
            local_port="none",
        )
        try:
            try:
                response = await _handle_transport_request(
                    transport,
                    request,
                    diagnostic_context=(self._owner_role, route, request_id),
                )
            except asyncio.CancelledError:
                _log_socket_lifecycle(
                    event="request-cancelled",
                    owner=self._owner_role,
                    route=route,
                    request_id=request_id,
                    local_port="none",
                )
                raise
            except BaseException:
                cleanup_logger.debug("_request_for_route boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
                _log_socket_lifecycle(
                    event="request-failed",
                    owner=self._owner_role,
                    route=route,
                    request_id=request_id,
                    local_port="none",
                )
                raise

            local_port = _response_local_port(response)
            _log_socket_lifecycle(
                event="response-created",
                owner=self._owner_role,
                route=route,
                local_port=local_port,
                request_id=request_id,
            )
            return response
        finally:
            _diagnostic_request_id.reset(token)

    def _attempt_order(self) -> list[Optional[str]]:
        """Sticky path first, then IPv4 literals, dual-stack hostname last (a blackholed IPv6 path never
        errors — Happy Eyeballs waits on AAAA until the OS TCP timeout and can pin the loop)."""
        order: list[Optional[str]] = []
        if self._sticky_ip is not _UNSET:
            order.append(None if self._sticky_ip is None else str(self._sticky_ip))
        order.extend(ip for ip in self._fallback_ips if ip not in order)
        if None not in order:
            order.append(None)
        return order

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != _TELEGRAM_API_HOST or not self._fallback_ips:
            return await self._request_for_route(self._primary, request, "primary")


        last_error: Exception | None = None
        for ip in self._attempt_order():
            candidate = request if ip is None else _rewrite_request_for_ip(request, ip)
            transport = self._primary if ip is None else await self._get_fallback(ip)
            try:
                response = await self._request_for_route(
                    transport,
                    candidate,
                    "primary" if ip is None else ip,
                )
                if self._last_failure is not None:
                    failed_path, failure = self._last_failure
                    self._last_failure = None
                    logger.info(
                        "[Telegram] Telegram API transport recovered via %s after %s failed: %s",
                        ip or _TELEGRAM_API_HOST,
                        failed_path,
                        failure,
                    )
                if self._sticky_ip is _UNSET or self._sticky_ip != ip:
                    async with self._sticky_lock:
                        if self._sticky_ip is _UNSET or self._sticky_ip != ip:
                            self._sticky_ip = ip
                            if ip is not None:
                                log = logger.warning if last_error is not None else logger.info
                                log("[Telegram] Using sticky IPv4 Telegram API path %s (dual-stack hostname tried last — #87015)", ip)
                return response
            except Exception as exc:
                cleanup_logger.debug("handle_async_request boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
                last_error = exc
                if not _is_retryable_connect_error(exc):
                    raise
                path = ip or _TELEGRAM_API_HOST
                failure = _describe_transport_error(exc)
                self._last_failure = (path, failure)
                if self._sticky_ip is not _UNSET and ip == self._sticky_ip:
                    async with self._sticky_lock:
                        if self._sticky_ip is not _UNSET and self._sticky_ip == ip:
                            self._sticky_ip = _UNSET
                            logger.warning(
                                "[Telegram] Sticky Telegram path %s failed; re-walking IPv4 literals before the hostname",
                                ip if ip is not None else "api.telegram.org")
                if ip is None:
                    await self._reset_primary(transport)
                    logger.warning("[Telegram] Dual-stack api.telegram.org path failed (%s)", failure)
                    continue
                logger.warning("[Telegram] IPv4 Telegram API IP %s failed: %s", ip, failure)
                await self._reset_fallback(ip)
                continue
        if last_error is None:
            raise RuntimeError("All Telegram fallback IPs exhausted but no error was recorded")
        raise last_error

    async def aclose(self) -> None:
        async with self._primary_lock:
            self._primary_closed = True
            primary = self._primary
        await primary.aclose()
        async with self._fallback_lock:
            transports = list(self._fallbacks.values())
            self._fallbacks.clear()
        for transport in transports:
            await transport.aclose()


def _normalize_fallback_ips(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            logger.warning("Ignoring invalid Telegram fallback IP: %r", raw)
            continue
        if addr.version != 4:
            logger.warning("Ignoring non-IPv4 Telegram fallback IP: %s", raw)
        elif addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            logger.warning("Ignoring private/internal Telegram fallback IP: %s", raw)
        else:
            normalized.append(str(addr))
    return normalized


def parse_fallback_ip_env(value: str | None) -> list[str]:
    return _normalize_fallback_ips(part.strip() for part in value.split(",")) if value else []


def _resolve_system_dns() -> set[str]:
    """Return the IPv4 addresses that the OS resolver gives for api.telegram.org."""
    try:
        results = socket.getaddrinfo(_TELEGRAM_API_HOST, 443, socket.AF_INET)
        return {addr[4][0] for addr in results}
    except Exception:
        cleanup_logger.debug("_resolve_system_dns boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
        return set()


async def _query_doh_provider(client: httpx.AsyncClient, provider: dict) -> list[str]:
    """Query one DoH provider and return A-record IPs."""
    try:
        resp = await client.get(provider["url"], params=provider["params"], headers=provider["headers"])
        resp.raise_for_status()
        data = resp.json()
        ips: list[str] = []
        for answer in data.get("Answer", []):
            if answer.get("type") != 1:  # A record
                continue
            raw = answer.get("data", "").strip()
            try:
                ipaddress.ip_address(raw)
            except ValueError:
                continue
            ips.append(raw)
        return ips
    except Exception as exc:
        cleanup_logger.debug("_query_doh_provider boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
        logger.debug("DoH query to %s failed: %s", provider["url"], exc)
        return []


async def discover_fallback_ips() -> list[str]:
    """Resolve api.telegram.org via Google + Cloudflare DoH; unique A records, in order. IPs matching the
    system resolver are deliberately KEPT (often the most reliable path). Falls back to
    ``SEED_FALLBACK_IPS`` only when DoH yields nothing usable.

    IPs that match the local system resolver are kept rather than excluded: in many networks the system-DNS
    IP is the most reliable path to api.telegram.org and a transient primary-path failure should be retried
    against the same address via the IP-rewrite path before the seed list is consulted (#14520).
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(_DOH_TIMEOUT), verify=platform_ssl_context()) as client:
        system_dns_task = asyncio.ensure_future(asyncio.to_thread(_resolve_system_dns))
        results = await asyncio.gather(*[_query_doh_provider(client, p) for p in _DOH_PROVIDERS], return_exceptions=True)
    # The getaddrinfo leg has no timeout of its own and only feeds the log line below — bound it.
    # The system-resolver leg runs socket.getaddrinfo in a worker thread with no timeout of its own — a
    # wedged OS resolver (broken VPN/DNS) can sit for minutes. Its result only feeds the no-usable-answers
    # log line below, so it must never gate discovery: bound it and move on (#63309). The DoH legs are
    # already bounded by the client timeout above.
    system_ips: set[str] = set()
    try:
        system_result = await asyncio.wait_for(system_dns_task, timeout=_DOH_TIMEOUT)
        if isinstance(system_result, set):
            system_ips = system_result
    except Exception:
        cleanup_logger.debug("discover_fallback_ips boundary operation failed\n%s", _redacted_cleanup_frames(), exc_info=_redacted_cleanup_exception_info())
        logger.debug("System-DNS resolution for %s did not complete in time", _TELEGRAM_API_HOST)
    doh_ips = [ip for r in results if isinstance(r, list) for ip in r]
    validated = _normalize_fallback_ips(list(dict.fromkeys(doh_ips)))  # dedupe, keep order
    if validated:
        logger.debug("Discovered Telegram fallback IPs via DoH: %s", ", ".join(validated))
        return validated
    logger.info(
        "DoH discovery yielded no usable IPs (system DNS: %s); using seed fallback IPs %s",
        ", ".join(system_ips) or "unknown", ", ".join(SEED_FALLBACK_IPS))
    return list(SEED_FALLBACK_IPS)


def _rewrite_request_for_ip(request: httpx.Request, ip: str) -> httpx.Request:
    original_host = request.url.host or _TELEGRAM_API_HOST
    url = request.url.copy_with(host=ip)
    headers = request.headers.copy()
    headers["host"] = original_host
    extensions = dict(request.extensions)
    extensions["sni_hostname"] = original_host
    return httpx.Request(method=request.method, url=url, headers=headers, stream=request.stream, extensions=extensions)


def _is_retryable_connect_error(exc: Exception) -> bool:
    # asyncio.TimeoutError is the raw form that the bounded cancellation-safe
    # connect shield raises when a blackholed SYN outlives
    # _TCP_CONNECT_DEADLINE (#95159): httpcore surfaces it before it can wrap
    # it into httpx.ConnectTimeout. Treating it as retryable lets the fallback
    # transport rotate off the dead sticky IP and reset its wedged pool.
    return isinstance(
        exc, (httpx.ConnectTimeout, httpx.ConnectError, asyncio.TimeoutError)
    )
