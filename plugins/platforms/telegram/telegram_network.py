"""Telegram-specific network helpers.

Provides a hostname-preserving fallback transport for networks where
api.telegram.org resolves to an endpoint that is unreachable from the current
host. The transport keeps the logical request host and TLS SNI as
api.telegram.org while retrying the TCP connection against one or more fallback
IPv4 addresses.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import itertools
import logging
import socket
from typing import Any, Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API_HOST = "api.telegram.org"

# Strong references for asynchronous abandoned-response closes. asyncio's loop
# only keeps weak references to tasks; without this set a cleanup can be
# garbage-collected before it closes the response stream.
_abandoned_response_cleanups: set[asyncio.Task] = set()
_diagnostic_request_ids = itertools.count(1)
_diagnostic_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "telegram_diagnostic_request_id", default="none"
)


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
        pass
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

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            return await self._stream.read(max_bytes, timeout=timeout)
        except BaseException:
            # A caller cancellation can arrive after the peer has queued bytes
            # plus FIN but before httpcore creates a Response.  Close this exact
            # stream while ownership is still local; no response-level cleanup
            # exists yet, and an inactive pool route may never be reaped.
            try:
                await self.aclose()
            except BaseException:
                pass
            raise

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._stream.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def start_tls(
        self, ssl_context, server_hostname=None, timeout=None
    ) -> "_CancellationSafeNetworkStream":
        try:
            stream = await self._stream.start_tls(
                ssl_context, server_hostname, timeout
            )
        except BaseException:
            # We have caught the delivered cancellation, so this bounded local
            # socket close can run before cancellation is propagated upward.
            try:
                await self._stream.aclose()
            except BaseException:
                pass
            raise
        return _CancellationSafeNetworkStream(stream)

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _DiagnosticCancellationSafeNetworkStream(_CancellationSafeNetworkStream):
    """Opt-in raw-stream wrapper that reports one socket lifecycle."""

    def __init__(self, stream: Any, lifecycle: _SocketLifecycleState):
        super().__init__(stream)
        self._lifecycle = lifecycle

    async def aclose(self) -> None:
        self._lifecycle.report_close_started()
        try:
            await self._stream.aclose()
        except BaseException:
            self._lifecycle.report_closed(error=True)
            raise
        self._lifecycle.report_closed()

    async def start_tls(
        self, ssl_context, server_hostname=None, timeout=None
    ) -> "_DiagnosticCancellationSafeNetworkStream":
        try:
            stream = await self._stream.start_tls(
                ssl_context, server_hostname, timeout
            )
        except BaseException:
            self._lifecycle.report_close_started()
            try:
                await self._stream.aclose()
            except BaseException:
                self._lifecycle.report_closed(error=True)
            else:
                self._lifecycle.report_closed()
            raise
        return _DiagnosticCancellationSafeNetworkStream(stream, self._lifecycle)


class _CancellationSafeNetworkBackend:
    """Wrap httpcore-created streams with TLS cancellation cleanup."""

    def __init__(self, backend: Any):
        self._backend = backend

    async def connect_tcp(self, *args, **kwargs) -> _CancellationSafeNetworkStream:
        stream = await self._backend.connect_tcp(*args, **kwargs)
        return _CancellationSafeNetworkStream(stream)

    async def connect_unix_socket(self, *args, **kwargs) -> _CancellationSafeNetworkStream:
        stream = await self._backend.connect_unix_socket(*args, **kwargs)
        return _CancellationSafeNetworkStream(stream)

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

    async def connect_tcp(
        self, *args, **kwargs
    ) -> _DiagnosticCancellationSafeNetworkStream:
        stream = await self._backend.connect_tcp(*args, **kwargs)
        return self._wrap(stream)

    async def connect_unix_socket(
        self, *args, **kwargs
    ) -> _DiagnosticCancellationSafeNetworkStream:
        stream = await self._backend.connect_unix_socket(*args, **kwargs)
        return self._wrap(stream)


def _new_async_http_transport(
    *,
    socket_diagnostics: bool = False,
    diagnostic_owner: str = "unknown",
    diagnostic_route: str = "primary",
    **kwargs,
) -> httpx.AsyncHTTPTransport:
    """Build an HTTPX transport whose raw TLS sockets are cancellation-safe."""
    transport = httpx.AsyncHTTPTransport(**kwargs)
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is None or backend is None:
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
    """Close an abandoned response, swallowing any error."""
    try:
        await response.aclose()
    except Exception:
        logger.debug("Failed to close abandoned Telegram response", exc_info=True)


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
        try:
            await asyncio.wait_for(self._stream.aclose(), timeout=5.0)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        if self._raw_socket_is_closed():
            return
        # A response stream may mark itself closed before failing, making this
        # retry return successfully without completing pool/socket cleanup.
        # Once the first close failed, always drive the exact network-stream
        # terminal path; a successful network close is left to its own pooling
        # semantics, while its failure falls back to the exact raw OS socket.
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
            return False

    async def _force_close_network_stream(self) -> None:
        if self._network_stream is None:
            return
        try:
            aclose = getattr(self._network_stream, "aclose", None)
            if aclose is not None:
                await asyncio.wait_for(aclose(), timeout=2.0)
                # network_stream.aclose() succeeded — do NOT raw-close the
                # underlying socket: a pooled success may keep an established
                # connection intentionally. Only the failure path below falls
                # back to the raw OS socket.
                return
            close = getattr(self._network_stream, "close", None)
            if close is not None:
                close()
                return
        except asyncio.CancelledError:
            logger.debug("Telegram response network-stream force-close cancelled")
            self._close_raw_socket_fallback()
            raise
        except Exception:
            logger.debug(
                "Failed to force-close Telegram response network stream",
                exc_info=True,
            )
            self._close_raw_socket_fallback()

    def _close_raw_socket_fallback(self) -> None:
        """Close the exact raw OS socket exposed by the network stream.

        Called only from the failure branch of _force_close_network_stream
        when network_stream.aclose() raised, was cancelled, or timed out.
        Idempotent: a second call is a no-op once the socket has been closed.
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
            raw_socket.close()
            self._raw_socket_closed = True
        except Exception:
            logger.debug(
                "Failed to close raw Telegram response socket fallback",
                exc_info=True,
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
    cancellation interrupts TLS setup; cancellation otherwise propagates into
    httpcore so its normal connect/header cleanup runs.

    After the response has been returned, a caller that is cancelled while
    reading the response body (e.g. PTB's
    ``HTTPXRequest.do_request`` awaiting ``res.content``) may abandon the
    response and leave its socket in CLOSE_WAIT. The caller-task done callback
    closes that otherwise-unclaimed response, and the response stream wrapper
    retries an interrupted close in a detached bounded task.
    """
    response = await transport.handle_async_request(request)
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
    """Attach a callback that closes *response* if *task* ends abnormally."""
    def _on_done(finished_task: asyncio.Task) -> None:
        if finished_task.cancelled():
            should_close = True
        else:
            exc = finished_task.exception()
            should_close = exc is not None
        if should_close and not getattr(response, "is_closed", True):
            close_task = asyncio.create_task(_close_response(response))
            _abandoned_response_cleanups.add(close_task)
            close_task.add_done_callback(_abandoned_response_cleanups.discard)

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
        pass
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

_DOH_PROVIDERS: list[dict] = [
    {
        "url": "https://dns.google/resolve",
        "params": {"name": _TELEGRAM_API_HOST, "type": "A"},
        "headers": {},
    },
    {
        "url": "https://cloudflare-dns.com/dns-query",
        "params": {"name": _TELEGRAM_API_HOST, "type": "A"},
        "headers": {"Accept": "application/dns-json"},
    },
]

# Last-resort IPs when DoH is also blocked.  These are stable Telegram Bot API
# endpoints in the 149.154.160.0/20 block (same seed used by OpenClaw).
_SEED_FALLBACK_IPS: list[str] = ["149.154.166.110", "149.154.167.220"]


def _resolve_proxy_url(target_hosts=None) -> str | None:
    # Delegate to shared implementation (env vars + macOS system proxy detection)
    from gateway.platforms.base import resolve_proxy_url
    return resolve_proxy_url("TELEGRAM_PROXY", target_hosts=target_hosts)


class TelegramFallbackTransport(httpx.AsyncBaseTransport):
    """Retry Telegram Bot API requests via fallback IPs while preserving TLS/SNI.

    Requests continue to target https://api.telegram.org/... logically, but on
    connect failures the underlying TCP connection is retried against a known
    reachable IP. This is effectively the programmatic equivalent of
    ``curl --resolve api.telegram.org:443:<ip>``.
    """

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
            # Match AsyncHTTPTransport's httpx 0.28 bounded defaults except for
            # idle keepalive ownership, which is deliberately disabled here.
            transport_kwargs["limits"] = httpx.Limits(
                max_connections=100,
                max_keepalive_connections=0,
                keepalive_expiry=5.0,
            )
        else:
            transport_kwargs["limits"] = httpx.Limits(
                max_connections=limits.max_connections,
                max_keepalive_connections=0,
                keepalive_expiry=limits.keepalive_expiry,
            )
        proxy_url = _resolve_proxy_url(target_hosts=[_TELEGRAM_API_HOST, *self._fallback_ips])
        if proxy_url and "proxy" not in transport_kwargs:
            transport_kwargs["proxy"] = proxy_url
        self._primary = _new_async_http_transport(
            socket_diagnostics=self._socket_diagnostics,
            diagnostic_owner=self._owner_role,
            diagnostic_route="primary",
            **transport_kwargs,
        )
        self._fallbacks = {
            ip: _new_async_http_transport(
                socket_diagnostics=self._socket_diagnostics,
                diagnostic_owner=self._owner_role,
                diagnostic_route=ip,
                **transport_kwargs,
            )
            for ip in self._fallback_ips
        }
        self._sticky_ip: Optional[str] = None
        self._sticky_lock = asyncio.Lock()

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

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != _TELEGRAM_API_HOST or not self._fallback_ips:
            return await self._request_for_route(self._primary, request, "primary")

        sticky_ip = self._sticky_ip
        attempt_order: list[Optional[str]] = [sticky_ip] if sticky_ip else [None]
        if sticky_ip:
            attempt_order.append(None)  # retry primary DNS after sticky failure
        for ip in self._fallback_ips:
            if ip != sticky_ip:
                attempt_order.append(ip)

        last_error: Exception | None = None
        for ip in attempt_order:
            candidate = request if ip is None else _rewrite_request_for_ip(request, ip)
            transport = self._primary if ip is None else self._fallbacks[ip]
            try:
                response = await self._request_for_route(
                    transport,
                    candidate,
                    "primary" if ip is None else ip,
                )
                if ip is not None and self._sticky_ip != ip:
                    async with self._sticky_lock:
                        if self._sticky_ip != ip:
                            self._sticky_ip = ip
                            logger.warning(
                                "[Telegram] Primary api.telegram.org path unreachable; using sticky fallback IP %s",
                                ip,
                            )
                return response
            except Exception as exc:
                last_error = exc
                if not _is_retryable_connect_error(exc):
                    raise
                if ip is not None and ip == self._sticky_ip:
                    async with self._sticky_lock:
                        if self._sticky_ip == ip:
                            self._sticky_ip = None
                            logger.warning(
                                "[Telegram] Sticky fallback IP %s failed; resetting to primary DNS path",
                                ip,
                            )
                if ip is None:
                    logger.warning(
                        "[Telegram] Primary api.telegram.org connection failed (%s); trying fallback IPs %s",
                        exc,
                        ", ".join(self._fallback_ips),
                    )
                    continue
                logger.warning("[Telegram] Fallback IP %s failed: %s", ip, exc)
                continue

        if last_error is None:
            raise RuntimeError("All Telegram fallback IPs exhausted but no error was recorded")
        raise last_error

    async def aclose(self) -> None:
        await self._primary.aclose()
        for transport in self._fallbacks.values():
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
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            logger.warning("Ignoring private/internal Telegram fallback IP: %s", raw)
            continue
        normalized.append(str(addr))
    return normalized


def parse_fallback_ip_env(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [part.strip() for part in value.split(",")]
    return _normalize_fallback_ips(parts)


def _resolve_system_dns() -> set[str]:
    """Return the IPv4 addresses that the OS resolver gives for api.telegram.org."""
    try:
        results = socket.getaddrinfo(_TELEGRAM_API_HOST, 443, socket.AF_INET)
        return {addr[4][0] for addr in results}
    except Exception:
        return set()


async def _query_doh_provider(
    client: httpx.AsyncClient, provider: dict
) -> list[str]:
    """Query one DoH provider and return A-record IPs."""
    try:
        resp = await client.get(
            provider["url"], params=provider["params"], headers=provider["headers"]
        )
        resp.raise_for_status()
        data = resp.json()
        ips: list[str] = []
        for answer in data.get("Answer", []):
            if answer.get("type") != 1:  # A record
                continue
            raw = answer.get("data", "").strip()
            try:
                ipaddress.ip_address(raw)
                ips.append(raw)
            except ValueError:
                continue
        return ips
    except Exception as exc:
        logger.debug("DoH query to %s failed: %s", provider["url"], exc)
        return []


async def discover_fallback_ips() -> list[str]:
    """Auto-discover Telegram API IPs via DNS-over-HTTPS.

    Resolves api.telegram.org through Google and Cloudflare DoH and returns all
    unique A records.  IPs that match the local system resolver are kept rather
    than excluded: in many networks the system-DNS IP is the most reliable path
    to api.telegram.org and a transient primary-path failure should be retried
    against the same address via the IP-rewrite path before the seed list is
    consulted (#14520).  Falls back to a hardcoded seed list only when DoH
    yields no usable answers.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(_DOH_TIMEOUT)) as client:
        doh_tasks = [_query_doh_provider(client, p) for p in _DOH_PROVIDERS]
        system_dns_task = asyncio.ensure_future(asyncio.to_thread(_resolve_system_dns))
        results = await asyncio.gather(*doh_tasks, return_exceptions=True)

    # The system-resolver leg runs socket.getaddrinfo in a worker thread with
    # no timeout of its own — a wedged OS resolver (broken VPN/DNS) can sit for
    # minutes. Its result only feeds the no-usable-answers log line below, so
    # it must never gate discovery: bound it and move on (#63309). The DoH legs
    # are already bounded by the client timeout above.
    system_ips: set[str] = set()
    try:
        system_result = await asyncio.wait_for(system_dns_task, timeout=_DOH_TIMEOUT)
        if isinstance(system_result, set):
            system_ips = system_result
    except Exception:
        logger.debug("System-DNS resolution for %s did not complete in time", _TELEGRAM_API_HOST)

    doh_ips: list[str] = []
    for r in results:
        if isinstance(r, list):
            doh_ips.extend(r)

    # Deduplicate preserving order
    seen: set[str] = set()
    candidates: list[str] = []
    for ip in doh_ips:
        if ip not in seen:
            seen.add(ip)
            candidates.append(ip)

    # Validate through existing normalization
    validated = _normalize_fallback_ips(candidates)

    if validated:
        logger.debug("Discovered Telegram fallback IPs via DoH: %s", ", ".join(validated))
        return validated

    logger.info(
        "DoH discovery yielded no usable IPs (system DNS: %s); using seed fallback IPs %s",
        ", ".join(system_ips) or "unknown",
        ", ".join(_SEED_FALLBACK_IPS),
    )
    return list(_SEED_FALLBACK_IPS)


def _rewrite_request_for_ip(request: httpx.Request, ip: str) -> httpx.Request:
    original_host = request.url.host or _TELEGRAM_API_HOST
    url = request.url.copy_with(host=ip)
    headers = request.headers.copy()
    headers["host"] = original_host
    extensions = dict(request.extensions)
    extensions["sni_hostname"] = original_host
    return httpx.Request(
        method=request.method,
        url=url,
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


def _is_retryable_connect_error(exc: Exception) -> bool:
    return isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError))
