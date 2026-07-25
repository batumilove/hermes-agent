"""Tests for plugins.platforms.telegram.telegram_network – fallback transport layer.

Background
----------
api.telegram.org resolves to an IP (e.g. 149.154.166.110) that is unreachable
from some networks.  The workaround: route TCP through a different IP in the
same Telegram-owned 149.154.160.0/20 block (e.g. 149.154.167.220) while
keeping TLS SNI and the Host header as api.telegram.org so Telegram's edge
servers still accept the request.  This is the programmatic equivalent of:

    curl --resolve api.telegram.org:443:149.154.167.220 https://api.telegram.org/bot<token>/getMe

The TelegramFallbackTransport implements this: try the primary (DNS-resolved)
path first, and on ConnectTimeout / ConnectError fall through to configured
fallback IPs in order, then "stick" to whichever IP works.
"""

import asyncio
import logging
import socket
import threading

import httpx
import pytest

import plugins.platforms.telegram.telegram_network as tnet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePool:
    def __init__(self):
        self._network_backend = object()


class FakeTransport(httpx.AsyncBaseTransport):
    """Records calls and raises / returns based on a host→action mapping."""

    def __init__(self, calls, behavior):
        self.calls = calls
        self.behavior = behavior
        self.closed = False
        self._pool = _FakePool()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "url_host": request.url.host,
                "host_header": request.headers.get("host"),
                "sni_hostname": request.extensions.get("sni_hostname"),
                "path": request.url.path,
            }
        )
        action = self.behavior.get(request.url.host, "ok")
        if action == "timeout":
            raise httpx.ConnectTimeout("timed out")
        if action == "connect_error":
            raise httpx.ConnectError("connect error")
        if isinstance(action, Exception):
            raise action
        return httpx.Response(200, request=request, text="ok")

    async def aclose(self) -> None:
        self.closed = True


class _TrackingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        self.closed = True


class _CancellationTrackingTransport(httpx.AsyncBaseTransport):
    """Hold a request open and record whether caller cancellation reaches it."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.stream = _TrackingStream()
        self._pool = _FakePool()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return httpx.Response(200, request=request, stream=self.stream)

    async def aclose(self) -> None:
        return None


class _DiagnosticSocket:
    def getsockname(self):
        return ("127.0.0.1", 43210)


class _DiagnosticNetworkStream:
    def __init__(self):
        self.closed = False

    def get_extra_info(self, name):
        if name == "socket":
            return _DiagnosticSocket()
        return None

    async def aclose(self):
        self.closed = True
        return None


class _CloseFailingByteStream(httpx.AsyncByteStream):
    def __init__(self):
        self.close_calls = 0

    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        self.close_calls += 1
        raise RuntimeError("stream close broken")


class _RawSocketTracker:
    """Wraps a real socket to count .close() calls without actually closing."""

    def __init__(self, real_sock: socket.socket):
        self._sock = real_sock
        self.close_calls = 0

    def getsockname(self):
        return self._sock.getsockname()

    def close(self):
        self.close_calls += 1
        self._sock.close()

    def fileno(self):
        return self._sock.fileno()


class _AcloseFailingNetworkStream:
    """Network stream whose aclose() always fails, exposing a raw socket."""

    def __init__(self, raw_socket: _RawSocketTracker):
        self.raw_socket = raw_socket
        self.aclose_calls = 0

    def get_extra_info(self, name):
        if name == "socket":
            return self.raw_socket
        return None

    async def aclose(self):
        self.aclose_calls += 1
        raise RuntimeError("network stream aclose broken")


class _CancelFirstSocketByteStream(httpx.AsyncByteStream):
    """Own a real TCP socket; first close is cancelled, second closes it."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.close_calls = 0

    async def __aiter__(self):
        if False:
            yield b""

    async def aclose(self):
        self.close_calls += 1
        if self.close_calls == 1:
            raise asyncio.CancelledError
        self.sock.close()

    def get_extra_info(self, name):
        if name == "socket":
            return self.sock
        return None


class _IdempotentInterruptedSocketByteStream(httpx.AsyncByteStream):
    """Model httpcore: mark closed before cancellable pool/socket cleanup."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.close_calls = 0
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.cleanup_finished = asyncio.Event()
        self.cleanup_completed = False
        self._closed = False

    async def __aiter__(self):
        if False:
            yield b""

    async def aclose(self):
        self.close_calls += 1
        if self._closed:
            return
        self._closed = True
        self.close_started.set()
        await self.release_close.wait()
        self.sock.close()
        self.cleanup_completed = True
        self.cleanup_finished.set()

    def get_extra_info(self, name):
        if name == "socket":
            return self.sock
        return None


class _IdempotentLateFailingByteStream(httpx.AsyncByteStream):
    """Mark closed, then fail after an externally-cancelled shield waiter left."""

    def __init__(self):
        self.close_calls = 0
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self._closed = False

    async def __aiter__(self):
        if False:
            yield b""

    async def aclose(self):
        self.close_calls += 1
        if self._closed:
            return
        self._closed = True
        self.close_started.set()
        await self.release_close.wait()
        raise RuntimeError("late close failure")


class _SingleResponseTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response):
        self.response = response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self.response


class _RawDiagnosticStream:
    def __init__(
        self,
        *,
        tls_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ):
        self.closed = False
        self.tls_error = tls_error
        self.close_error = close_error

    async def read(self, _max_bytes, timeout=None):
        return b""

    async def write(self, _buffer, timeout=None):
        return None

    async def aclose(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    async def start_tls(self, _ssl_context, _server_hostname=None, _timeout=None):
        if self.tls_error is not None:
            raise self.tls_error
        return self

    def get_extra_info(self, name):
        if name == "socket":
            return _DiagnosticSocket()
        return None


class _RawDiagnosticBackend:
    def __init__(self, stream):
        self.stream = stream

    async def connect_tcp(self, *args, **kwargs):
        return self.stream

    async def connect_unix_socket(self, *args, **kwargs):
        return self.stream

    async def sleep(self, _seconds):
        return None


class _DiagnosticTrackingStream(_TrackingStream):
    def __init__(self, *, close_error: Exception | None = None):
        super().__init__()
        self.close_error = close_error

    async def aclose(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _DiagnosticTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, close_error: Exception | None = None):
        self._pool = _FakePool()
        self.stream = _DiagnosticTrackingStream(close_error=close_error)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            stream=self.stream,
            extensions={"network_stream": _DiagnosticNetworkStream()},
        )

    async def aclose(self) -> None:
        return None


def test_new_transport_wraps_expected_httpcore_backend():
    transport = tnet._new_async_http_transport()
    assert isinstance(
        transport._pool._network_backend,
        tnet._CancellationSafeNetworkBackend,
    )


def test_new_transport_fails_closed_when_httpcore_shape_changes(monkeypatch):
    class UnsupportedTransport:
        pass

    monkeypatch.setattr(
        tnet.httpx,
        "AsyncHTTPTransport",
        lambda **_kwargs: UnsupportedTransport(),
    )

    with pytest.raises(RuntimeError, match=r"_pool\._network_backend"):
        tnet._new_async_http_transport()


def _fake_transport_factory(calls, behavior):
    """Returns a factory that creates FakeTransport instances."""
    instances = []

    def factory(**kwargs):
        t = FakeTransport(calls, behavior)
        instances.append(t)
        return t

    factory.instances = instances
    return factory


def _telegram_request(path="/botTOKEN/getMe"):
    return httpx.Request("GET", f"https://api.telegram.org{path}")


class _LifecycleLogCapture(logging.Handler):
    def __init__(self, stream_getter):
        super().__init__(level=logging.NOTSET)
        self.stream_getter = stream_getter
        self.records = []
        self._old_level = logging.NOTSET

    def emit(self, record):
        self.records.append((record, self.stream_getter().closed))

    def __enter__(self):
        self._old_level = tnet.logger.level
        tnet.logger.setLevel(logging.DEBUG)
        tnet.logger.addHandler(self)
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        tnet.logger.removeHandler(self)
        tnet.logger.setLevel(self._old_level)


def _record_payload(record):
    formatted = logging.Formatter(
        "%(name)s %(levelname)s %(message)s"
    ).format(record)
    return "\n".join(
        (
            formatted,
            repr(record.__dict__),
            repr(record.args),
            repr(record.exc_info),
            repr(record.exc_text),
        )
    )


def _assert_records_redacted(captured, forbidden):
    for record, _stream_closed in captured:
        payload = _record_payload(record)
        assert all(value not in payload for value in forbidden)


def _assert_lifecycle_record(record, *, event, owner, route):
    fields = [part for part in record.getMessage().split() if "=" in part]
    event_fields = [field for field in fields if field.startswith("event=")]
    owner_fields = [field for field in fields if field.startswith("owner=")]
    route_fields = [field for field in fields if field.startswith("route=")]
    port_fields = [field for field in fields if field.startswith("local_port=")]
    assert event_fields == [f"event={event}"]
    assert owner_fields == [f"owner={owner}"]
    assert route_fields == [f"route={route}"]
    assert port_fields == ["local_port=43210"]


def _diagnostic_request():
    # Construct a syntactically realistic, inert token without storing a
    # token-shaped literal that repository secret scanners would flag.
    token = "1234567890:" + "AAE" + "abcdefghijklmnopqrstuvwxyz" + "123456"
    query = "secret_query=QUERY_CANARY"
    request = httpx.Request(
        "GET", f"https://api.telegram.org/bot{token}/getUpdates?{query}"
    )
    forbidden = (
        str(request.url),
        request.url.host,
        request.url.path,
        request.url.query.decode(),
        token,
        query,
        "getUpdates",
    )
    return request, forbidden


def test_socket_lifecycle_is_visible_at_default_gateway_stderr_level(caplog):
    with caplog.at_level(logging.WARNING, logger=tnet.logger.name):
        tnet._log_socket_lifecycle(
            event="socket-opened",
            owner="general",
            route="primary",
            local_port="43210",
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    _assert_lifecycle_record(
        caplog.records[0], event="socket-opened", owner="general", route="primary"
    )


@pytest.mark.asyncio
async def test_socket_diagnostics_cover_pre_response_network_lifecycle(monkeypatch):
    """A socket must be attributable before any HTTP response exists."""
    stream = _RawDiagnosticStream()
    backend = _RawDiagnosticBackend(stream)

    class Transport:
        def __init__(self):
            self._pool = _FakePool()
            self._pool._network_backend = backend

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", lambda **_kwargs: Transport())
    transport = tnet._new_async_http_transport(
        socket_diagnostics=True,
        diagnostic_owner="polling",
        diagnostic_route="primary",
    )
    wrapped_backend = transport._pool._network_backend

    with _LifecycleLogCapture(lambda: stream) as capture:
        token = tnet._diagnostic_request_id.set("42")
        try:
            raw_stream = await wrapped_backend.connect_tcp("api.telegram.org", 443)
            tls_stream = await raw_stream.start_tls(object(), "api.telegram.org", 1.0)
            await tls_stream.aclose()
        finally:
            tnet._diagnostic_request_id.reset(token)

    assert [record.getMessage().split(" event=", 1)[1].split()[0] for record, _ in capture.records] == [
        "socket-opened",
        "socket-close-started",
        "socket-closed",
    ]
    for record, _closed in capture.records:
        assert "request_id=42" in record.getMessage()
        event = record.getMessage().split(" event=", 1)[1].split()[0]
        _assert_lifecycle_record(
            record,
            event=event,
            owner="polling",
            route="primary",
        )
    payload = "\n".join(_record_payload(record) for record, _ in capture.records)
    assert "api.telegram.org" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_route",
    [
        "https://api.telegram.org/bot/endpoint?credential=CANARY",
        "api.telegram.org?query=CANARY",
        "primary\ncredential=CANARY",
        "127.0.0.1",
        "100.64.0.1",
    ],
)
async def test_pre_response_diagnostic_route_is_allowlisted(bad_route):
    stream = _RawDiagnosticStream()
    backend = tnet._DiagnosticCancellationSafeNetworkBackend(
        _RawDiagnosticBackend(stream), owner="general", route=bad_route
    )

    with _LifecycleLogCapture(lambda: stream) as capture:
        wrapped = await backend.connect_tcp("api.telegram.org", 443)
        await wrapped.aclose()

    messages = [record.getMessage() for record, _ in capture.records]
    assert messages
    assert all("route=unknown" in message for message in messages)
    assert bad_route not in "\n".join(messages)


@pytest.mark.parametrize("invalid_port", [0, -1, 65536, "43210", True, None])
def test_pre_response_diagnostic_local_port_requires_valid_integer(invalid_port):
    class Socket:
        def getsockname(self):
            return ("127.0.0.1", invalid_port)

    class Stream:
        def get_extra_info(self, name):
            return Socket() if name == "socket" else None

    assert tnet._stream_local_port(Stream()) == "unknown"


def test_pre_response_diagnostic_local_port_accepts_valid_integer():
    class Socket:
        def getsockname(self):
            return ("127.0.0.1", 43210)

    class Stream:
        def get_extra_info(self, name):
            return Socket() if name == "socket" else None

    assert tnet._stream_local_port(Stream()) == "43210"


@pytest.mark.asyncio
async def test_socket_diagnostics_close_pre_response_socket_on_tls_cancellation(monkeypatch):
    stream = _RawDiagnosticStream(tls_error=asyncio.CancelledError())
    backend = _RawDiagnosticBackend(stream)

    class Transport:
        def __init__(self):
            self._pool = _FakePool()
            self._pool._network_backend = backend

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", lambda **_kwargs: Transport())
    transport = tnet._new_async_http_transport(
        socket_diagnostics=True,
        diagnostic_owner="general",
        diagnostic_route="149.154.167.220",
    )

    with _LifecycleLogCapture(lambda: stream) as capture:
        raw_stream = await transport._pool._network_backend.connect_tcp(
            "149.154.167.220", 443
        )
        with pytest.raises(asyncio.CancelledError):
            await raw_stream.start_tls(object(), "api.telegram.org", 1.0)

    assert stream.closed is True
    assert [record.getMessage().split(" event=", 1)[1].split()[0] for record, _ in capture.records] == [
        "socket-opened",
        "socket-close-started",
        "socket-closed",
    ]
    for record, _closed in capture.records:
        assert "owner=general" in record.getMessage()
        assert "route=149.154.167.220" in record.getMessage()
        assert "local_port=43210" in record.getMessage()


@pytest.mark.asyncio
async def test_pre_response_socket_close_error_is_logged_exactly_once(monkeypatch):
    stream = _RawDiagnosticStream(close_error=RuntimeError("close canary"))
    backend = _RawDiagnosticBackend(stream)

    class Transport:
        def __init__(self):
            self._pool = _FakePool()
            self._pool._network_backend = backend

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", lambda **_kwargs: Transport())
    transport = tnet._new_async_http_transport(
        socket_diagnostics=True,
        diagnostic_owner="general",
        diagnostic_route="primary",
    )

    with _LifecycleLogCapture(lambda: stream) as capture:
        raw_stream = await transport._pool._network_backend.connect_tcp(
            "api.telegram.org", 443
        )
        with pytest.raises(RuntimeError, match="close canary"):
            await raw_stream.aclose()
        with pytest.raises(RuntimeError, match="close canary"):
            await raw_stream.aclose()

    messages = [record.getMessage() for record, _ in capture.records]
    assert sum("event=socket-opened" in message for message in messages) == 1
    assert sum("event=socket-close-started" in message for message in messages) == 1
    assert sum("event=socket-close-error" in message for message in messages) == 1
    assert "close canary" not in "\n".join(messages)


@pytest.mark.asyncio
async def test_pre_response_socket_diagnostics_are_default_off(monkeypatch):
    stream = _RawDiagnosticStream()
    backend = _RawDiagnosticBackend(stream)

    class Transport:
        def __init__(self):
            self._pool = _FakePool()
            self._pool._network_backend = backend

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", lambda **_kwargs: Transport())
    transport = tnet._new_async_http_transport()
    assert type(transport._pool._network_backend) is tnet._CancellationSafeNetworkBackend

    with _LifecycleLogCapture(lambda: stream) as capture:
        raw_stream = await transport._pool._network_backend.connect_tcp(
            "api.telegram.org", 443
        )
        assert type(raw_stream) is tnet._CancellationSafeNetworkStream
        assert not hasattr(raw_stream, "_lifecycle")
        await raw_stream.aclose()

    assert capture.records == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sticky_ip", "expected_route", "selected_index"),
    [
        (None, "primary", 0),
        ("149.154.167.220", "149.154.167.220", 1),
    ],
)
async def test_socket_diagnostics_bind_owner_route_and_local_port_without_url(
    monkeypatch, sticky_ip, expected_route, selected_index
):
    """Opt-in diagnostics must identify a socket without logging request data."""
    instances = []

    def factory(**_kwargs):
        instance = _DiagnosticTransport()
        instances.append(instance)
        return instance

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)
    transport = tnet.TelegramFallbackTransport(
        ["149.154.167.220"],
        owner_role="general",
        socket_diagnostics=True,
    )
    transport._sticky_ip = sticky_ip
    request, forbidden = _diagnostic_request()
    selected_stream = instances[selected_index].stream

    with _LifecycleLogCapture(lambda: selected_stream) as capture:
        response = await transport.handle_async_request(request)

        assert selected_stream.closed is False
        assert len(capture.records) == 2
        started_record, started_closed_state = capture.records[0]
        assert "event=request-started" in started_record.getMessage()
        assert started_closed_state is False
        created_record, created_closed_state = capture.records[1]
        assert created_closed_state is False
        _assert_lifecycle_record(
            created_record,
            event="response-created",
            owner="general",
            route=expected_route,
        )
        _assert_records_redacted(capture.records, forbidden)

        await response.aclose()

        assert selected_stream.closed is True
        assert len(capture.records) == 3
        closed_record, closed_state = capture.records[2]
        assert closed_state is True
        _assert_lifecycle_record(
            closed_record,
            event="response-closed",
            owner="general",
            route=expected_route,
        )
        _assert_records_redacted(capture.records, forbidden)


@pytest.mark.asyncio
async def test_socket_diagnostics_are_disabled_by_default(monkeypatch):
    instances = []

    def factory(**_kwargs):
        instance = _DiagnosticTransport()
        instances.append(instance)
        return instance

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)
    transport = tnet.TelegramFallbackTransport(["149.154.167.220"])
    transport._sticky_ip = "149.154.167.220"
    request, _forbidden = _diagnostic_request()
    selected_stream = instances[1].stream

    with _LifecycleLogCapture(lambda: selected_stream) as capture:
        response = await transport.handle_async_request(request)
        await response.aclose()

    assert capture.records == []


@pytest.mark.asyncio
async def test_socket_close_error_diagnostic_redacts_request_and_exception(monkeypatch):
    close_error = "SECRET_CLOSE_ERROR_CANARY"
    instances = []

    def factory(**_kwargs):
        instance = _DiagnosticTransport(close_error=RuntimeError(close_error))
        instances.append(instance)
        return instance

    monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)
    transport = tnet.TelegramFallbackTransport(
        ["149.154.167.220"],
        owner_role="polling",
        socket_diagnostics=True,
    )
    transport._sticky_ip = "149.154.167.220"
    request, forbidden = _diagnostic_request()
    selected_stream = instances[1].stream

    with _LifecycleLogCapture(lambda: selected_stream) as capture:
        response = await transport.handle_async_request(request)

        assert selected_stream.closed is False
        assert len(capture.records) == 2
        started_record, started_closed_state = capture.records[0]
        assert "event=request-started" in started_record.getMessage()
        assert started_closed_state is False
        created_record, created_closed_state = capture.records[1]
        assert created_closed_state is False
        _assert_lifecycle_record(
            created_record,
            event="response-created",
            owner="polling",
            route="149.154.167.220",
        )

        with pytest.raises(RuntimeError, match=close_error):
            await response.aclose()

        assert selected_stream.closed is True
        assert len(capture.records) == 3
        error_record, error_closed_state = capture.records[2]
        assert error_closed_state is True
        _assert_lifecycle_record(
            error_record,
            event="response-close-error",
            owner="polling",
            route="149.154.167.220",
        )
        _assert_records_redacted(capture.records, forbidden + (close_error,))


class _ErrorThenSuccessInnerStream(httpx.AsyncByteStream):
    """Inner stream: first aclose() raises, second succeeds."""

    def __init__(self, error: Exception):
        self._error = error
        self.close_calls = 0
        self.closed = False

    async def __aiter__(self):
        if False:
            yield b""

    async def aclose(self):
        self.close_calls += 1
        if self.close_calls == 1:
            raise self._error
        self.closed = True


@pytest.mark.asyncio
async def test_diagnostic_response_close_error_then_success_logs_both_events():
    """A failed close followed by a successful retry must emit both events.

    The _DiagnosticResponseStream must use separate bookkeeping for error
    and closed so a later successful retry still reports response-closed.
    Exactly one response-close-error and exactly one response-closed, with
    no request data in the log.
    """
    inner = _ErrorThenSuccessInnerStream(RuntimeError("transient close failure"))
    diag_stream = tnet._DiagnosticResponseStream(
        inner,
        owner="general",
        route="primary",
        local_port="43210",
    )

    with _LifecycleLogCapture(lambda: inner) as capture:
        # First close raises.
        with pytest.raises(RuntimeError, match="transient close failure"):
            await diag_stream.aclose()
        # Second close succeeds.
        await diag_stream.aclose()

    messages = [record.getMessage() for record, _ in capture.records]
    assert sum("event=response-close-error" in m for m in messages) == 1
    assert sum("event=response-closed" in m for m in messages) == 1
    assert inner.close_calls == 2
    _assert_records_redacted(capture.records, ("transient close failure",))


class _SlowBodyServer:
    """Threaded TCP server that accepts one connection and stalls mid-body."""

    def __init__(self, body: bytes = b"x" * 256, sent_bytes: int = 1):
        self.body = body
        self.sent_bytes = sent_bytes
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                self._sock.settimeout(0.5)
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            self._connected.set()
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                headers = (
                    b"HTTP/1.1 200 OK\r\n"
                    + f"Content-Length: {len(self.body)}\r\n".encode()
                    + b"\r\n"
                )
                conn.sendall(headers)
                conn.sendall(self.body[: self.sent_bytes])
                # Stall; the client will be cancelled while reading the rest.
                self._stop.wait(30.0)
            finally:
                conn.close()

    def wait_for_connection(self, timeout: float = 2.0) -> None:
        self._connected.wait(timeout)


class _PeerFinServer:
    """Accept one real TCP connection, send FIN, and stop."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._closed_peer = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def connect(self) -> socket.socket:
        client = socket.create_connection(("127.0.0.1", self.port), timeout=2.0)
        assert self._closed_peer.wait(2.0)
        client.settimeout(2.0)
        assert client.recv(1) == b""
        return client

    def stop(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        try:
            conn, _addr = self._sock.accept()
            conn.close()
        finally:
            self._closed_peer.set()


def _tcp_state_for_ports(local_port: int, remote_port: int) -> str | None:
    """Read the kernel TCP state for one localhost connection from /proc."""
    try:
        with open("/proc/net/tcp", encoding="ascii") as proc_tcp:
            rows = proc_tcp.readlines()[1:]
    except OSError:
        pytest.skip("Linux /proc/net/tcp is required for CLOSE_WAIT proof")
    local_suffix = f":{local_port:04X}"
    remote_suffix = f":{remote_port:04X}"
    for row in rows:
        fields = row.split()
        if fields[1].endswith(local_suffix) and fields[2].endswith(remote_suffix):
            return fields[3]
    return None


async def _wait_for_tcp_state(
    local_port: int,
    remote_port: int,
    expected: str | None,
    *,
    timeout: float = 2.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if _tcp_state_for_ports(local_port, remote_port) == expected:
            return
        await asyncio.sleep(0.01)
    actual = _tcp_state_for_ports(local_port, remote_port)
    assert actual == expected


# ═══════════════════════════════════════════════════════════════════════════
# IP parsing & validation
# ═══════════════════════════════════════════════════════════════════════════

class TestParseFallbackIpEnv:
    def test_filters_invalid_and_ipv6(self, caplog):
        ips = tnet.parse_fallback_ip_env("149.154.167.220, bad, 2001:67c:4e8:f004::9,149.154.167.220")
        assert ips == ["149.154.167.220", "149.154.167.220"]
        assert "Ignoring invalid Telegram fallback IP" in caplog.text
        assert "Ignoring non-IPv4 Telegram fallback IP" in caplog.text

    def test_none_returns_empty(self):
        assert tnet.parse_fallback_ip_env(None) == []

    def test_empty_string_returns_empty(self):
        assert tnet.parse_fallback_ip_env("") == []

    def test_whitespace_only_returns_empty(self):
        assert tnet.parse_fallback_ip_env("  ,  , ") == []

    def test_single_valid_ip(self):
        assert tnet.parse_fallback_ip_env("149.154.167.220") == ["149.154.167.220"]

    def test_multiple_valid_ips(self):
        ips = tnet.parse_fallback_ip_env("149.154.167.220, 149.154.167.221")
        assert ips == ["149.154.167.220", "149.154.167.221"]

    def test_rejects_leading_zeros(self, caplog):
        """Leading zeros are ambiguous (octal?) so ipaddress rejects them."""
        ips = tnet.parse_fallback_ip_env("149.154.167.010")
        assert ips == []
        assert "Ignoring invalid" in caplog.text


class TestNormalizeFallbackIps:
    def test_deduplication_happens_at_transport_level(self):
        """_normalize does not dedup; TelegramFallbackTransport.__init__ does."""
        raw = ["149.154.167.220", "149.154.167.220"]
        assert tnet._normalize_fallback_ips(raw) == ["149.154.167.220", "149.154.167.220"]

    def test_empty_strings_skipped(self):
        assert tnet._normalize_fallback_ips(["", "  ", "149.154.167.220"]) == ["149.154.167.220"]


# ═══════════════════════════════════════════════════════════════════════════
# Request rewriting
# ═══════════════════════════════════════════════════════════════════════════

class TestRewriteRequestForIp:
    def test_preserves_host_and_sni(self):
        request = _telegram_request()
        rewritten = tnet._rewrite_request_for_ip(request, "149.154.167.220")

        assert rewritten.url.host == "149.154.167.220"
        assert rewritten.headers["host"] == "api.telegram.org"
        assert rewritten.extensions["sni_hostname"] == "api.telegram.org"
        assert rewritten.url.path == "/botTOKEN/getMe"

    def test_preserves_method_and_path(self):
        request = httpx.Request("POST", "https://api.telegram.org/botTOKEN/sendMessage")
        rewritten = tnet._rewrite_request_for_ip(request, "149.154.167.220")

        assert rewritten.method == "POST"
        assert rewritten.url.path == "/botTOKEN/sendMessage"


# ═══════════════════════════════════════════════════════════════════════════
# Fallback transport – core behavior
# ═══════════════════════════════════════════════════════════════════════════

class TestFallbackTransport:
    """Primary path fails → try fallback IPs → stick to whichever works."""

    @pytest.mark.asyncio
    async def test_cancelled_caller_cancels_inflight_transport_request(self, monkeypatch):
        """Caller cancellation must unwind an in-flight transport request.

        Shielding the request from its caller is useful only while a response
        can finish promptly. During connect or response-header stalls, leaving
        the shielded request alive retains its socket indefinitely. Explicitly
        cancelling the transport task lets httpcore unwind and release it.
        """
        inner = _CancellationTrackingTransport()
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", lambda **kw: inner)

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])
        transport._sticky_ip = "149.154.167.220"
        request_task = asyncio.create_task(
            transport.handle_async_request(_telegram_request())
        )
        await inner.started.wait()

        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        assert inner.cancelled is True
        for _ in range(10):
            if not tnet._abandoned_response_cleanups:
                break
            await asyncio.sleep(0)
        assert not tnet._abandoned_response_cleanups

    @pytest.mark.asyncio
    async def test_pre_response_cancellation_has_correlated_request_terminal_telemetry(
        self, monkeypatch
    ):
        """A socket opened before response creation must remain attributable.

        The request identifier is deliberately opaque and the terminal record
        must not contain URL, token, query, or exception contents.
        """
        inner = _CancellationTrackingTransport()
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", lambda **_kw: inner)
        transport = tnet.TelegramFallbackTransport(
            ["149.154.167.220"],
            owner_role="general",
            socket_diagnostics=True,
        )
        request, forbidden = _diagnostic_request()

        with _LifecycleLogCapture(lambda: inner.stream) as capture:
            request_task = asyncio.create_task(transport.handle_async_request(request))
            await inner.started.wait()
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

        payloads = [_record_payload(record) for record, _ in capture.records]
        events = [
            payload.split(" event=", 1)[1].split()[0]
            for payload in payloads
            if " event=" in payload
        ]
        assert events == ["request-started", "request-cancelled"]
        request_ids = {
            token.split("=", 1)[1]
            for record, _closed in capture.records
            for token in record.getMessage().split()
            if token.startswith("request_id=")
        }
        assert len(request_ids) == 1
        assert request_ids != {"none"}
        _assert_records_redacted(capture.records, forbidden)

    @pytest.mark.asyncio
    async def test_caller_body_read_cancellation_closes_real_socket_response(self):
        """A caller cancelled while reading the response body must close the socket.

        PTB's ``HTTPXRequest.do_request`` does ``res = await client.request(...)``
        and then ``return res.status_code, res.content``. If its task is cancelled
        during ``res.content``, the response is abandoned and the underlying socket
        stays in CLOSE_WAIT. Our transport must close the response when the
        caller's task ends abnormally.
        """
        server = _SlowBodyServer(body=b"x" * 256, sent_bytes=1)
        server.start()
        try:
            transport = httpx.AsyncHTTPTransport()
            request = httpx.Request("GET", server.url)
            response_holder: dict[str, httpx.Response] = {}

            async def _read_body():
                response = await tnet._handle_transport_request(transport, request)
                response_holder["response"] = response
                return await response.aread()

            task = asyncio.create_task(_read_body())
            server.wait_for_connection(timeout=2.0)
            await asyncio.sleep(0.05)  # ensure body reading has started
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # Yield to let the response-close callback fire.
            await asyncio.sleep(0)

            assert response_holder["response"].is_closed, (
                "Response was abandoned on caller cancellation and its socket was not closed"
            )
            await transport.aclose()
        finally:
            server.stop()

    @pytest.mark.asyncio
    async def test_caught_close_cancellation_retries_and_clears_real_close_wait(self):
        """A caught first-close cancellation must not strand a real TCP socket."""
        server = _PeerFinServer()
        server.start()
        client = server.connect()
        local_port = client.getsockname()[1]
        remote_port = client.getpeername()[1]
        stream = _CancelFirstSocketByteStream(client)
        request = _telegram_request()
        response = httpx.Response(
            200,
            request=request,
            stream=stream,
            extensions={"network_stream": stream},
        )
        transport = _SingleResponseTransport(response)

        try:
            await _wait_for_tcp_state(local_port, remote_port, "08")

            async def _caller_catches_close_cancellation():
                owned_response = await tnet._handle_transport_request(
                    transport, request
                )
                try:
                    await owned_response.aclose()
                except asyncio.CancelledError:
                    return "cancelled-close-caught"

            result = await asyncio.create_task(
                _caller_catches_close_cancellation()
            )
            assert result == "cancelled-close-caught"

            # The caller task completed normally, so its done callback cannot
            # identify the abandoned response. The stream wrapper must retry.
            await _wait_for_tcp_state(local_port, remote_port, None)
            assert stream.close_calls == 2
            assert client.fileno() == -1
            for _ in range(20):
                if not tnet._abandoned_response_cleanups:
                    break
                await asyncio.sleep(0)
            assert not tnet._abandoned_response_cleanups
        finally:
            client.close()
            server.stop()

    @pytest.mark.asyncio
    async def test_caller_cancellation_cannot_interrupt_idempotent_stream_cleanup(self):
        """External cancellation must not make an idempotent retry false-green.

        httpcore marks ``PoolByteStream`` closed before its cancellable pool
        bookkeeping completes. A second ``aclose()`` then returns successfully
        without finishing the first cleanup. Keep that first close alive instead
        of treating the no-op retry as proof that the real socket was released.
        """
        server = _PeerFinServer()
        server.start()
        client = server.connect()
        local_port = client.getsockname()[1]
        remote_port = client.getpeername()[1]
        stream = _IdempotentInterruptedSocketByteStream(client)
        retrying = tnet._RetryingCloseResponseStream(
            stream,
            network_stream=stream,
        )

        try:
            await _wait_for_tcp_state(local_port, remote_port, "08")
            close_task = asyncio.create_task(retrying.aclose())
            await asyncio.wait_for(stream.close_started.wait(), timeout=1.0)
            close_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close_task

            stream.release_close.set()
            await asyncio.wait_for(stream.cleanup_finished.wait(), timeout=2.0)
            await _wait_for_tcp_state(
                local_port,
                remote_port,
                None,
                timeout=5.0,
            )
            assert stream.cleanup_completed is True
            assert stream.close_calls == 1
            for _ in range(20):
                if not tnet._abandoned_response_cleanups:
                    break
                await asyncio.sleep(0)
            assert not tnet._abandoned_response_cleanups
        finally:
            stream.release_close.set()
            client.close()
            server.stop()

    @pytest.mark.asyncio
    async def test_shielded_close_late_failure_cannot_false_green_idempotent_retry(self):
        """A late first-close failure must still close the exact raw socket."""
        left, right = socket.socketpair()
        tracked_socket = _RawSocketTracker(left)
        network_stream = _AcloseFailingNetworkStream(tracked_socket)
        stream = _IdempotentLateFailingByteStream()
        retrying = tnet._RetryingCloseResponseStream(
            stream,
            network_stream=network_stream,
        )
        try:
            waiter = asyncio.create_task(retrying.aclose())
            await asyncio.wait_for(stream.close_started.wait(), timeout=1.0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

            stream.release_close.set()
            for _ in range(200):
                if tracked_socket.close_calls == 1:
                    break
                await asyncio.sleep(0.01)

            assert stream.close_calls == 2
            assert network_stream.aclose_calls == 1
            assert tracked_socket.close_calls == 1
            assert tracked_socket.fileno() == -1
            for _ in range(200):
                if not tnet._abandoned_response_cleanups:
                    break
                await asyncio.sleep(0.01)
            assert not tnet._abandoned_response_cleanups
        finally:
            left.close()
            right.close()

    @pytest.mark.asyncio
    async def test_retry_failure_forces_network_stream_close(self):
        """If both byte-stream closes fail, force-close the raw network stream."""
        network_stream = _DiagnosticNetworkStream()
        stream = _CloseFailingByteStream()
        retrying = tnet._RetryingCloseResponseStream(
            stream,
            network_stream=network_stream,
        )

        with pytest.raises(RuntimeError, match="stream close broken"):
            await retrying.aclose()

        for _ in range(20):
            if network_stream.closed and not tnet._abandoned_response_cleanups:
                break
            await asyncio.sleep(0)
        assert stream.close_calls == 2
        assert network_stream.closed is True
        assert not tnet._abandoned_response_cleanups

    @pytest.mark.asyncio
    async def test_force_close_falls_back_to_exact_raw_socket_on_network_aclose_failure(self):
        """When network_stream.aclose() fails, close the exact raw OS socket.

        The terminal fallback in _force_close_network_stream must close the
        exact socket returned by get_extra_info('socket') — once, idempotently
        — and no unrelated socket.
        """
        sock_a, sock_b = socket.socketpair()
        try:
            raw_tracker = _RawSocketTracker(sock_a)
            network_stream = _AcloseFailingNetworkStream(raw_tracker)
            stream = _CloseFailingByteStream()
            retrying = tnet._RetryingCloseResponseStream(
                stream,
                network_stream=network_stream,
            )

            with pytest.raises(RuntimeError, match="stream close broken"):
                await retrying.aclose()

            for _ in range(30):
                if raw_tracker.close_calls > 0 and not tnet._abandoned_response_cleanups:
                    break
                await asyncio.sleep(0)

            # The exact raw socket was closed exactly once.
            assert raw_tracker.close_calls == 1
            # The network-stream aclose was attempted exactly once (idempotent).
            assert network_stream.aclose_calls == 1
            # The unrelated socket (sock_b) was never touched.
            assert sock_b.fileno() != -1
            assert not tnet._abandoned_response_cleanups
        finally:
            sock_a.close()
            sock_b.close()

    @pytest.mark.asyncio
    async def test_falls_back_on_connect_timeout_and_becomes_sticky(self, monkeypatch):
        calls = []
        behavior = {"api.telegram.org": "timeout", "149.154.167.220": "ok"}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])
        resp = await transport.handle_async_request(_telegram_request())

        assert resp.status_code == 200
        assert transport._sticky_ip == "149.154.167.220"
        # First attempt was primary (api.telegram.org), second was fallback
        assert calls[0]["url_host"] == "api.telegram.org"
        assert calls[1]["url_host"] == "149.154.167.220"
        assert calls[1]["host_header"] == "api.telegram.org"
        assert calls[1]["sni_hostname"] == "api.telegram.org"

        # Second request goes straight to sticky IP
        calls.clear()
        resp2 = await transport.handle_async_request(_telegram_request())
        assert resp2.status_code == 200
        assert calls[0]["url_host"] == "149.154.167.220"

    @pytest.mark.asyncio
    async def test_falls_back_on_connect_error(self, monkeypatch):
        calls = []
        behavior = {"api.telegram.org": "connect_error", "149.154.167.220": "ok"}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])
        resp = await transport.handle_async_request(_telegram_request())

        assert resp.status_code == 200
        assert transport._sticky_ip == "149.154.167.220"

    @pytest.mark.asyncio
    async def test_does_not_fallback_on_non_connect_error(self, monkeypatch):
        """Errors like ReadTimeout are not connection issues — don't retry."""
        calls = []
        behavior = {"api.telegram.org": httpx.ReadTimeout("read timeout"), "149.154.167.220": "ok"}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])

        with pytest.raises(httpx.ReadTimeout):
            await transport.handle_async_request(_telegram_request())

        assert [c["url_host"] for c in calls] == ["api.telegram.org"]

    @pytest.mark.asyncio
    async def test_all_ips_fail_raises_last_error(self, monkeypatch):
        calls = []
        behavior = {"api.telegram.org": "timeout", "149.154.167.220": "timeout"}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])

        with pytest.raises(httpx.ConnectTimeout):
            await transport.handle_async_request(_telegram_request())

        assert [c["url_host"] for c in calls] == ["api.telegram.org", "149.154.167.220"]
        assert transport._sticky_ip is None

    @pytest.mark.asyncio
    async def test_multiple_fallback_ips_tried_in_order(self, monkeypatch):
        calls = []
        behavior = {
            "api.telegram.org": "timeout",
            "149.154.167.220": "timeout",
            "149.154.167.221": "ok",
        }
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220", "149.154.167.221"])
        resp = await transport.handle_async_request(_telegram_request())

        assert resp.status_code == 200
        assert transport._sticky_ip == "149.154.167.221"
        assert [c["url_host"] for c in calls] == [
            "api.telegram.org",
            "149.154.167.220",
            "149.154.167.221",
        ]

    @pytest.mark.asyncio
    async def test_sticky_ip_tried_first_but_falls_through_if_stale(self, monkeypatch):
        """If the sticky IP stops working, the transport retries others."""
        calls = []
        behavior = {
            "api.telegram.org": "timeout",
            "149.154.167.220": "ok",
            "149.154.167.221": "ok",
        }
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220", "149.154.167.221"])

        # First request: primary fails → .220 works → becomes sticky
        await transport.handle_async_request(_telegram_request())
        assert transport._sticky_ip == "149.154.167.220"

        # Now .220 goes bad too
        calls.clear()
        behavior["149.154.167.220"] = "timeout"

        resp = await transport.handle_async_request(_telegram_request())
        assert resp.status_code == 200
        # After #24511: when sticky fails the transport also resets and
        # re-tries the primary DNS path before falling through to other IPs.
        # Path: sticky (.220) → primary (api.telegram.org) → .221
        assert [c["url_host"] for c in calls] == ["149.154.167.220", "api.telegram.org", "149.154.167.221"]
        assert transport._sticky_ip == "149.154.167.221"


class TestFallbackTransportPassthrough:
    """Requests that don't need fallback behavior."""

    @pytest.mark.asyncio
    async def test_non_telegram_host_bypasses_fallback(self, monkeypatch):
        calls = []
        behavior = {}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])
        request = httpx.Request("GET", "https://example.com/path")
        resp = await transport.handle_async_request(request)

        assert resp.status_code == 200
        assert calls[0]["url_host"] == "example.com"
        assert transport._sticky_ip is None

    @pytest.mark.asyncio
    async def test_empty_fallback_list_uses_primary_only(self, monkeypatch):
        calls = []
        behavior = {}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport([])
        resp = await transport.handle_async_request(_telegram_request())

        assert resp.status_code == 200
        assert calls[0]["url_host"] == "api.telegram.org"

    @pytest.mark.asyncio
    async def test_primary_succeeds_no_fallback_needed(self, monkeypatch):
        calls = []
        behavior = {"api.telegram.org": "ok"}
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", _fake_transport_factory(calls, behavior))

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])
        resp = await transport.handle_async_request(_telegram_request())

        assert resp.status_code == 200
        assert transport._sticky_ip is None
        assert len(calls) == 1


class TestFallbackTransportInit:
    def test_deduplicates_fallback_ips(self, monkeypatch):
        monkeypatch.setattr(
            tnet.httpx, "AsyncHTTPTransport", lambda **kw: FakeTransport([], {})
        )
        transport = tnet.TelegramFallbackTransport(["149.154.167.220", "149.154.167.220"])
        assert transport._fallback_ips == ["149.154.167.220"]

    def test_filters_invalid_ips_at_init(self, monkeypatch):
        monkeypatch.setattr(
            tnet.httpx, "AsyncHTTPTransport", lambda **kw: FakeTransport([], {})
        )
        transport = tnet.TelegramFallbackTransport(["149.154.167.220", "not-an-ip"])
        assert transport._fallback_ips == ["149.154.167.220"]

    def test_uses_proxy_env_for_primary_and_fallback_transports(self, monkeypatch):
        seen_kwargs = []

        def factory(**kwargs):
            seen_kwargs.append(kwargs.copy())
            return FakeTransport([], {})

        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "TELEGRAM_PROXY", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])

        assert transport._fallback_ips == ["149.154.167.220"]
        assert len(seen_kwargs) == 2
        assert all(kwargs["proxy"] == "http://proxy.example:8080" for kwargs in seen_kwargs)

    def test_no_proxy_bypasses_fallback_ip_cidr(self, monkeypatch):
        seen_kwargs = []

        def factory(**kwargs):
            seen_kwargs.append(kwargs.copy())
            return FakeTransport([], {})

        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "TELEGRAM_PROXY", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
        monkeypatch.setenv("NO_PROXY", "149.154.160.0/20")
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)

        transport = tnet.TelegramFallbackTransport(["149.154.167.220"])

        assert transport._fallback_ips == ["149.154.167.220"]
        assert len(seen_kwargs) == 2
        assert all("proxy" not in kwargs for kwargs in seen_kwargs)

    def test_preserves_concurrency_limits_but_disables_idle_keepalive(self, monkeypatch):
        """Inner fallback pools retain caller concurrency without idle sockets.

        httpx ignores client-level limits when a custom transport is supplied,
        so the fallback transport must forward the connection ceiling while
        forcing zero idle keepalive to avoid route-stranded CLOSE_WAIT sockets.
        """
        seen_kwargs = []

        def factory(**kwargs):
            seen_kwargs.append(kwargs.copy())
            return FakeTransport([], {})

        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "TELEGRAM_PROXY", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)

        custom_limits = httpx.Limits(
            max_connections=42,
            max_keepalive_connections=10,
            keepalive_expiry=30.0,
        )
        transport = tnet.TelegramFallbackTransport(
            ["149.154.167.220"], limits=custom_limits
        )

        # 1 primary + 1 fallback = 2 AsyncHTTPTransport instances
        assert len(seen_kwargs) == 2
        for kw in seen_kwargs:
            assert "limits" in kw
            limits = kw["limits"]
            assert limits.max_connections == custom_limits.max_connections
            assert limits.max_keepalive_connections == 0
            assert limits.keepalive_expiry == custom_limits.keepalive_expiry

    def test_default_limits_remain_bounded_without_idle_keepalive(self, monkeypatch):
        seen_kwargs = []

        def factory(**kwargs):
            seen_kwargs.append(kwargs.copy())
            return FakeTransport([], {})

        for key in (
            "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy",
            "http_proxy", "all_proxy", "TELEGRAM_PROXY", "NO_PROXY", "no_proxy",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)

        tnet.TelegramFallbackTransport(["149.154.167.220"])

        assert len(seen_kwargs) == 2
        for kw in seen_kwargs:
            limits = kw["limits"]
            assert limits.max_connections == 100
            assert limits.max_keepalive_connections == 0
            assert limits.keepalive_expiry == 5.0


class TestFallbackTransportClose:
    @pytest.mark.asyncio
    async def test_aclose_closes_all_transports(self, monkeypatch):
        factory = _fake_transport_factory([], {})
        monkeypatch.setattr(tnet.httpx, "AsyncHTTPTransport", factory)

        transport = tnet.TelegramFallbackTransport(["149.154.167.220", "149.154.167.221"])
        await transport.aclose()

        # 1 primary + 2 fallback transports
        assert len(factory.instances) == 3
        assert all(t.closed for t in factory.instances)


# ═══════════════════════════════════════════════════════════════════════════
# Config layer – TELEGRAM_FALLBACK_IPS env → config.extra
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigFallbackIps:
    def test_env_var_populates_config_extra(self, monkeypatch):
        from gateway.config import GatewayConfig, Platform, PlatformConfig, _apply_env_overrides

        monkeypatch.setenv("TELEGRAM_FALLBACK_IPS", "149.154.167.220,149.154.167.221")
        config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="tok")})
        _apply_env_overrides(config)

        assert config.platforms[Platform.TELEGRAM].extra["fallback_ips"] == [
            "149.154.167.220", "149.154.167.221",
        ]

    def test_env_var_creates_platform_if_missing(self, monkeypatch):
        from gateway.config import GatewayConfig, Platform, _apply_env_overrides

        monkeypatch.setenv("TELEGRAM_FALLBACK_IPS", "149.154.167.220")
        config = GatewayConfig(platforms={})
        _apply_env_overrides(config)

        assert Platform.TELEGRAM in config.platforms
        assert config.platforms[Platform.TELEGRAM].extra["fallback_ips"] == ["149.154.167.220"]

    def test_env_var_strips_whitespace(self, monkeypatch):
        from gateway.config import GatewayConfig, Platform, PlatformConfig, _apply_env_overrides

        monkeypatch.setenv("TELEGRAM_FALLBACK_IPS", "  149.154.167.220 , 149.154.167.221  ")
        config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="tok")})
        _apply_env_overrides(config)

        assert config.platforms[Platform.TELEGRAM].extra["fallback_ips"] == [
            "149.154.167.220", "149.154.167.221",
        ]

    def test_empty_env_var_does_not_populate(self, monkeypatch):
        from gateway.config import GatewayConfig, Platform, PlatformConfig, _apply_env_overrides

        monkeypatch.setenv("TELEGRAM_FALLBACK_IPS", "")
        config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="tok")})
        _apply_env_overrides(config)

        assert "fallback_ips" not in config.platforms[Platform.TELEGRAM].extra


# ═══════════════════════════════════════════════════════════════════════════
# Adapter layer – _fallback_ips() reads config correctly
# ═══════════════════════════════════════════════════════════════════════════

class TestAdapterFallbackIps:
    def _make_adapter(self, extra=None):
        import sys
        from unittest.mock import MagicMock

        # Ensure telegram mock is in place
        if "telegram" not in sys.modules or not hasattr(sys.modules["telegram"], "__file__"):
            mod = MagicMock()
            mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
            mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
            mod.constants.ChatType.GROUP = "group"
            mod.constants.ChatType.SUPERGROUP = "supergroup"
            mod.constants.ChatType.CHANNEL = "channel"
            mod.constants.ChatType.PRIVATE = "private"
            for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
                sys.modules.setdefault(name, mod)

        from gateway.config import PlatformConfig
        from plugins.platforms.telegram.adapter import TelegramAdapter

        config = PlatformConfig(enabled=True, token="test-token")
        if extra:
            config.extra.update(extra)
        return TelegramAdapter(config)

    def test_list_in_extra(self):
        adapter = self._make_adapter(extra={"fallback_ips": ["149.154.167.220"]})
        assert adapter._fallback_ips() == ["149.154.167.220"]

    def test_csv_string_in_extra(self):
        adapter = self._make_adapter(extra={"fallback_ips": "149.154.167.220,149.154.167.221"})
        assert adapter._fallback_ips() == ["149.154.167.220", "149.154.167.221"]

    def test_empty_extra(self):
        adapter = self._make_adapter()
        assert adapter._fallback_ips() == []

    def test_no_extra_attr(self):
        adapter = self._make_adapter()
        adapter.config.extra = None
        assert adapter._fallback_ips() == []

    def test_invalid_ips_filtered(self):
        adapter = self._make_adapter(extra={"fallback_ips": ["149.154.167.220", "not-valid"]})
        assert adapter._fallback_ips() == ["149.154.167.220"]


# ═══════════════════════════════════════════════════════════════════════════
# DoH auto-discovery
# ═══════════════════════════════════════════════════════════════════════════

def _doh_answer(*ips: str) -> dict:
    """Build a minimal DoH JSON response with A records."""
    return {"Answer": [{"type": 1, "data": ip} for ip in ips]}


class FakeDoHClient:
    """Mock httpx.AsyncClient for DoH queries."""

    def __init__(self, responses: dict):
        # responses: URL prefix → (status, json_body) | Exception
        self._responses = responses
        self.requests_made: list[dict] = []

    @staticmethod
    def _make_response(status, body, url):
        """Build an httpx.Response with a request attached (needed for raise_for_status)."""
        request = httpx.Request("GET", url)
        return httpx.Response(status, json=body, request=request)

    async def get(self, url, *, params=None, headers=None, **kwargs):
        self.requests_made.append({"url": url, "params": params, "headers": headers})
        for prefix, action in self._responses.items():
            if url.startswith(prefix):
                if isinstance(action, Exception):
                    raise action
                status, body = action
                return self._make_response(status, body, url)
        return self._make_response(200, {}, url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestDiscoverFallbackIps:
    """Tests for discover_fallback_ips() — DoH-based auto-discovery."""

    def _patch_doh(self, monkeypatch, responses, system_dns_ips=None):
        """Wire up fake DoH client and system DNS."""
        client = FakeDoHClient(responses)
        monkeypatch.setattr(tnet.httpx, "AsyncClient", lambda **kw: client)

        if system_dns_ips is not None:
            addrs = [(None, None, None, None, (ip, 443)) for ip in system_dns_ips]
            monkeypatch.setattr(tnet.socket, "getaddrinfo", lambda *a, **kw: addrs)
        else:
            def _fail(*a, **kw):
                raise OSError("dns failed")
            monkeypatch.setattr(tnet.socket, "getaddrinfo", _fail)
        return client

    @pytest.mark.asyncio
    async def test_google_and_cloudflare_ips_collected(self, monkeypatch):
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.167.220")),
            "https://cloudflare-dns.com": (200, _doh_answer("149.154.167.221")),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert "149.154.167.220" in ips
        assert "149.154.167.221" in ips

    @pytest.mark.asyncio
    async def test_system_dns_ip_kept_when_doh_confirms(self, monkeypatch):
        """DoH-confirmed IPs are kept even when they match system DNS (#14520).

        The system-DNS IP is often the most reliable path; including it as a
        fallback lets the IP-rewrite retry recover from transient primary-path
        failures instead of jumping straight to the hardcoded seed list.
        """
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.166.110", "149.154.167.220")),
            "https://cloudflare-dns.com": (200, _doh_answer("149.154.166.110")),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == ["149.154.166.110", "149.154.167.220"]

    @pytest.mark.asyncio
    async def test_doh_results_deduplicated(self, monkeypatch):
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.167.220")),
            "https://cloudflare-dns.com": (200, _doh_answer("149.154.167.220")),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == ["149.154.167.220"]

    @pytest.mark.asyncio
    async def test_doh_timeout_falls_back_to_seed(self, monkeypatch):
        self._patch_doh(monkeypatch, {
            "https://dns.google": httpx.TimeoutException("timeout"),
            "https://cloudflare-dns.com": httpx.TimeoutException("timeout"),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == tnet._SEED_FALLBACK_IPS

    @pytest.mark.asyncio
    async def test_doh_connect_error_falls_back_to_seed(self, monkeypatch):
        self._patch_doh(monkeypatch, {
            "https://dns.google": httpx.ConnectError("refused"),
            "https://cloudflare-dns.com": httpx.ConnectError("refused"),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == tnet._SEED_FALLBACK_IPS

    @pytest.mark.asyncio
    async def test_doh_malformed_json_falls_back_to_seed(self, monkeypatch):
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, {"Status": 0}),  # no Answer key
            "https://cloudflare-dns.com": (200, {"garbage": True}),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == tnet._SEED_FALLBACK_IPS

    @pytest.mark.asyncio
    async def test_one_provider_fails_other_succeeds(self, monkeypatch):
        self._patch_doh(monkeypatch, {
            "https://dns.google": httpx.TimeoutException("timeout"),
            "https://cloudflare-dns.com": (200, _doh_answer("149.154.167.220")),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == ["149.154.167.220"]

    @pytest.mark.asyncio
    async def test_system_dns_failure_keeps_all_doh_ips(self, monkeypatch):
        """If system DNS fails, nothing gets excluded — all DoH IPs kept."""
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.166.110", "149.154.167.220")),
            "https://cloudflare-dns.com": (200, _doh_answer()),
        }, system_dns_ips=None)  # triggers OSError

        ips = await tnet.discover_fallback_ips()
        assert "149.154.166.110" in ips
        assert "149.154.167.220" in ips

    @pytest.mark.asyncio
    async def test_all_doh_ips_same_as_system_dns_kept(self, monkeypatch):
        """DoH agrees with system DNS — keep that IP instead of seed list (#14520).

        Previous behavior fell through to ``_SEED_FALLBACK_IPS`` here, but the
        seed addresses are not routable on every network.  When DoH confirms
        the system IP, that IP is the best candidate we have and should be
        used as the fallback target.
        """
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.166.110")),
            "https://cloudflare-dns.com": (200, _doh_answer("149.154.166.110")),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == ["149.154.166.110"]

    @pytest.mark.asyncio
    async def test_cloudflare_gets_accept_header(self, monkeypatch):
        client = self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.167.220")),
            "https://cloudflare-dns.com": (200, _doh_answer("149.154.167.221")),
        }, system_dns_ips=["149.154.166.110"])

        await tnet.discover_fallback_ips()

        cf_reqs = [r for r in client.requests_made if "cloudflare" in r["url"]]
        assert cf_reqs
        assert cf_reqs[0]["headers"]["Accept"] == "application/dns-json"

    @pytest.mark.asyncio
    async def test_non_a_records_ignored(self, monkeypatch):
        """AAAA records (type 28) and CNAME (type 5) should be skipped."""
        answer = {
            "Answer": [
                {"type": 5, "data": "telegram.org"},  # CNAME
                {"type": 28, "data": "2001:67c:4e8:f004::9"},  # AAAA
                {"type": 1, "data": "149.154.167.220"},  # A ✓
            ]
        }
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, answer),
            "https://cloudflare-dns.com": (200, _doh_answer()),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == ["149.154.167.220"]

    @pytest.mark.asyncio
    async def test_invalid_ip_in_doh_response_skipped(self, monkeypatch):
        answer = {"Answer": [
            {"type": 1, "data": "not-an-ip"},
            {"type": 1, "data": "149.154.167.220"},
        ]}
        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, answer),
            "https://cloudflare-dns.com": (200, _doh_answer()),
        }, system_dns_ips=["149.154.166.110"])

        ips = await tnet.discover_fallback_ips()
        assert ips == ["149.154.167.220"]

    @pytest.mark.asyncio
    async def test_hung_system_dns_does_not_gate_doh_results(self, monkeypatch):
        """#63309: socket.getaddrinfo has no timeout of its own — a wedged OS
        resolver must not stall discovery. DoH answers must come back promptly
        even while the system-DNS worker thread is still hanging."""
        import time as _time

        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, _doh_answer("149.154.167.220")),
            "https://cloudflare-dns.com": (200, _doh_answer()),
        }, system_dns_ips=["149.154.166.110"])
        monkeypatch.setattr(tnet, "_DOH_TIMEOUT", 0.2)

        def _hung_getaddrinfo(*a, **kw):
            _time.sleep(1.5)  # far beyond the discovery bound
            raise OSError("resolver wedged")

        monkeypatch.setattr(tnet.socket, "getaddrinfo", _hung_getaddrinfo)

        start = _time.monotonic()
        ips = await tnet.discover_fallback_ips()
        elapsed = _time.monotonic() - start

        assert ips == ["149.154.167.220"]
        assert elapsed < 1.4, f"discovery gated on hung system DNS ({elapsed:.2f}s)"

    @pytest.mark.asyncio
    async def test_hung_system_dns_with_no_doh_answers_bounded_seed_fallback(self, monkeypatch):
        """Worst case — resolver wedged AND no DoH answers — must still return
        the seed list within the bound instead of hanging connect()."""
        import time as _time

        self._patch_doh(monkeypatch, {
            "https://dns.google": (200, {"Status": 0}),
            "https://cloudflare-dns.com": (200, {"garbage": True}),
        }, system_dns_ips=["149.154.166.110"])
        monkeypatch.setattr(tnet, "_DOH_TIMEOUT", 0.2)

        def _hung_getaddrinfo(*a, **kw):
            _time.sleep(1.5)
            raise OSError("resolver wedged")

        monkeypatch.setattr(tnet.socket, "getaddrinfo", _hung_getaddrinfo)

        start = _time.monotonic()
        ips = await tnet.discover_fallback_ips()
        elapsed = _time.monotonic() - start

        assert ips == tnet._SEED_FALLBACK_IPS
        assert elapsed < 1.4, f"seed fallback gated on hung system DNS ({elapsed:.2f}s)"
