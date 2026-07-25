"""Real-runtime regression for Telegram request cancellation.

Both supported entry points execute the same child-process diagnostic cases:

    python tests/gateway/test_telegram_request_phase_real_socket.py
    python -m pytest -q tests/gateway/test_telegram_request_phase_real_socket.py

The child process is intentional. ``tests/gateway/conftest.py`` installs Telegram
MagicMocks for adapter unit tests; a fresh interpreter proves these regressions
use the installed python-telegram-bot/httpx/httpcore stack instead.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import inspect
import ipaddress
import json
import logging
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
from typing import Any

import httpx
import psutil
import pytest

# When this file is executed directly, sys.path[0] is tests/gateway and this
# host's PYTHONPATH may point at the live checkout. Force the isolated worktree
# root first so standalone and pytest execute the exact same production module.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

import plugins.platforms.telegram.telegram_network as tnet

_DRAIN_SECONDS = 0.5
_LEAK_STATUSES = {"ESTABLISHED", "CLOSE_WAIT", "FIN_WAIT1", "FIN_WAIT2"}
_CASES = (
    "provenance",
    "tls_handshake",
    "response_headers",
    "response_headers_peer_fin_race",
    "response_headers_peer_fin_wave",
    "response_body",
    "peer_fin_idle",
    "repeated",
    "concurrent",
    "proxy_tls_handshake",
    "proxy_https_healthy",
)
_PTB_CASES = frozenset(
    {
        "provenance",
        "tls_handshake",
        "response_headers",
        "response_headers_peer_fin_race",
        "response_headers_peer_fin_wave",
        "response_body",
        "peer_fin_idle",
        "repeated",
        "concurrent",
    }
)


class _SocketLifecycleCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []
        self._old_level = logging.NOTSET

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("[Telegram socket] "):
            self.messages.append(message)

    def __enter__(self):
        self._old_level = tnet.logger.level
        tnet.logger.setLevel(logging.INFO)
        tnet.logger.addHandler(self)
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        tnet.logger.removeHandler(self)
        tnet.logger.setLevel(self._old_level)


def _ptb_request_available() -> bool:
    return subprocess.run(
        [sys.executable, "-c", "from telegram.request import HTTPXRequest"],
        cwd=_REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    ).returncode == 0


def _case_params():
    ptb_available = _ptb_request_available()
    return tuple(
        pytest.param(
            case,
            marks=pytest.mark.skip(reason="python-telegram-bot is not installed"),
        )
        if case in _PTB_CASES and not ptb_available
        else pytest.param(case)
        for case in _CASES
    )


def _allow_loopback_fallback_ips(values):
    normalized = []
    for value in values:
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError:
            continue
        if address.version == 4:
            normalized.append(str(address))
    return normalized


def _connections_to_port(port: int) -> list[dict[str, Any]]:
    connections = []
    for connection in psutil.Process().net_connections(kind="tcp"):
        if connection.raddr and connection.raddr.port == port and connection.status in _LEAK_STATUSES:
            connections.append(
                {
                    "local_port": connection.laddr.port,
                    "remote_port": connection.raddr.port,
                    "status": connection.status,
                }
            )
    return connections


async def _drain() -> None:
    await asyncio.sleep(_DRAIN_SECONDS)
    gc.collect()
    await asyncio.sleep(0.1)


class _ProtocolServer:
    """Local server with deterministic TLS/header/body cancellation points."""

    def __init__(self, mode: str, *, target_connections: int = 1):
        self.mode = mode
        self.target_connections = target_connections
        self.started = asyncio.Event()
        self.allow_peer_fin = asyncio.Event()
        self.peer_fin_sent = asyncio.Event()
        self.client_eof = asyncio.Event()
        self.disconnects = 0
        self.accepted = 0
        self.server: asyncio.Server | None = None
        self.port = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._peer_fin_writers: set[asyncio.StreamWriter] = set()

    def url(self, path: str = "/botTOKEN/getMe") -> str:
        scheme = "https" if self.mode == "tls_handshake" else "http"
        return f"{scheme}://api.telegram.org:{self.port}{path}"

    async def start(self) -> "_ProtocolServer":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _wait_for_disconnect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while await reader.read(4096):
                pass
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.disconnects += 1
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepted += 1
        self._writers.add(writer)
        try:
            if self.mode == "tls_handshake":
                # A raw server receives the real TLS ClientHello but never sends
                # ServerHello. This is deterministic TLS-handshake cancellation,
                # not a claim about cancellation during the TCP SYN handshake.
                hello = await reader.read(4096)
                if hello:
                    self.started.set()
                await self._wait_for_disconnect(reader, writer)
                return

            request = await reader.readuntil(b"\r\n\r\n")
            first_line = request.split(b"\r\n", 1)[0]

            if self.mode == "response_headers_peer_fin_race":
                # Reproduce the production boundary: the peer queues a partial
                # pre-response TLS/HTTP record and FIN while the caller is about
                # to cancel.  The client has a live socket but no httpx.Response
                # yet.  Queue exactly 631 bytes, matching the unread receive
                # queue preserved from every affected Telegram socket.
                payload = b"HTTP/1.1 200 OK\r\nX-Incomplete: " + b"x" * 600
                assert len(payload) == 631
                writer.write(payload)
                writer.close()
                self.started.set()
                self.peer_fin_sent.set()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self._writers.discard(writer)
                self.disconnects += 1
                return

            if self.mode == "response_headers_peer_fin_wave":
                # Hold a pool-width wave just before response creation. Once
                # every request has 631 unread bytes queued, close all peers
                # synchronously and wake the already-waiting canceller. This
                # orders cancellation ahead of the clients' next socket read.
                payload = b"HTTP/1.1 200 OK\r\nX-Incomplete: " + b"x" * 600
                assert len(payload) == 631
                writer.write(payload)
                self._peer_fin_writers.add(writer)
                if len(self._peer_fin_writers) == self.target_connections:
                    for peer in tuple(self._peer_fin_writers):
                        peer.close()
                    self.started.set()
                    self.peer_fin_sent.set()
                await self.started.wait()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self._writers.discard(writer)
                self.disconnects += 1
                return

            if self.mode == "concurrent" and b"/healthy" in first_line:
                body = b'{"ok":true,"result":{}}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self._writers.discard(writer)
                self.disconnects += 1
                return

            if self.mode == "response_body":
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 1048576\r\nConnection: keep-alive\r\n\r\n"
                    b'{"ok":true,"result":"'
                )
                await writer.drain()

            if self.mode == "peer_fin_idle":
                body = b'{"ok":true,"result":{}}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
                self.started.set()
                await self.allow_peer_fin.wait()
                writer.write_eof()
                await writer.drain()
                self.peer_fin_sent.set()
                if not await reader.read(1):
                    self.client_eof.set()
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self._writers.discard(writer)
                self.disconnects += 1
                return

            self.started.set()
            await self._wait_for_disconnect(reader, writer)
        except BaseException:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def aclose(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for writer in tuple(self._writers):
            writer.close()
        for writer in tuple(self._writers):
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._writers.clear()


class _ConnectProxy:
    """Bounded real HTTP CONNECT proxy that either stalls TLS or relays it."""

    def __init__(self, relay_port: int | None = None):
        self.relay_port = relay_port
        self.tls_started = asyncio.Event()
        self.accepted = 0
        self.disconnects = 0
        self.connect_targets: list[str] = []
        self.server: asyncio.Server | None = None
        self.port = 0
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> "_ConnectProxy":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _pipe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepted += 1
        self._writers.add(writer)
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
            first_line = request.split(b"\r\n", 1)[0].decode("ascii")
            method, target, _version = first_line.split(" ", 2)
            assert method == "CONNECT"
            self.connect_targets.append(target)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            if self.relay_port is None:
                hello = await asyncio.wait_for(reader.read(4096), 5.0)
                if hello:
                    self.tls_started.set()
                while await reader.read(4096):
                    pass
                return

            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", self.relay_port
            )
            await asyncio.wait_for(
                asyncio.gather(
                    self._pipe(reader, upstream_writer),
                    self._pipe(upstream_reader, writer),
                ),
                10.0,
            )
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.disconnects += 1
            self._writers.discard(writer)
            writer.close()
            if upstream_writer is not None:
                upstream_writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if upstream_writer is not None:
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()

    async def aclose(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for writer in tuple(self._writers):
            writer.close()
        for writer in tuple(self._writers):
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._writers.clear()


class _TlsOrigin:
    """Local HTTPS origin used to prove healthy CONNECT traffic still works."""

    def __init__(self, ssl_context: ssl.SSLContext):
        self.ssl_context = ssl_context
        self.server: asyncio.Server | None = None
        self.port = 0
        self.requests = 0

    async def start(self) -> "_TlsOrigin":
        self.server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=self.ssl_context
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
            self.requests += 1
            body = b"proxy tls ok"
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def aclose(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


def _local_server_ssl_context(directory: str) -> ssl.SSLContext:
    cert = Path(directory) / "cert.pem"
    key = Path(directory) / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # RSA key generation can exceed 10s under the intentionally concurrent
        # gateway test load; keep it bounded without creating a false harness
        # failure before the network lifecycle case starts.
        timeout=30,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


class _RealRuntime:
    def __init__(self):
        from telegram.request import HTTPXRequest

        self.HTTPXRequest = HTTPXRequest
        self.original_normalize = tnet._normalize_fallback_ips
        tnet._normalize_fallback_ips = _allow_loopback_fallback_ips
        self.transport = tnet.TelegramFallbackTransport(
            ["127.0.0.1"],
            limits=httpx.Limits(
                max_connections=8,
                max_keepalive_connections=4,
                keepalive_expiry=1.0,
            ),
        )
        self.transport._sticky_ip = "127.0.0.1"
        self.request = HTTPXRequest(
            connection_pool_size=8,
            pool_timeout=5.0,
            connect_timeout=10.0,
            read_timeout=20.0,
            write_timeout=20.0,
            httpx_kwargs={"transport": self.transport},
        )

    async def initialize(self) -> None:
        await self.request.initialize()

    async def aclose(self) -> None:
        await self.request.shutdown()
        tnet._normalize_fallback_ips = self.original_normalize


async def _cancel_request(runtime: _RealRuntime, server: _ProtocolServer, path: str = "/botTOKEN/getMe") -> None:
    task = asyncio.create_task(runtime.request.retrieve(server.url(path)))
    try:
        await asyncio.wait_for(server.started.wait(), timeout=5.0)
    except BaseException:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        raise
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _provenance_case() -> dict[str, Any]:
    import httpcore
    from telegram.request import HTTPXRequest

    assert httpx.__version__ == "0.28.1"
    assert httpcore.__version__ == "1.0.9"
    root = Path(__file__).resolve().parents[2]
    paths = {
        "production_module": str(Path(inspect.getfile(tnet)).resolve()),
        "ptb_class": str(Path(inspect.getfile(HTTPXRequest)).resolve()),
        "httpx_module": str(Path(inspect.getfile(httpx)).resolve()),
        "httpcore_module": str(Path(inspect.getfile(httpcore)).resolve()),
    }
    expected_production = str(
        (root / "plugins/platforms/telegram/telegram_network.py").resolve()
    )
    assert paths["production_module"] == expected_production, {
        "actual": paths["production_module"],
        "expected": expected_production,
        "test_file": str(Path(__file__).resolve()),
    }
    assert HTTPXRequest.__module__ == "telegram.request._httpxrequest"
    assert "site-packages/telegram/request/_httpxrequest.py" in paths["ptb_class"]
    assert "unittest.mock" not in repr(HTTPXRequest)
    return {
        "case": "provenance",
        "ok": True,
        "versions": {"httpx": httpx.__version__, "httpcore": httpcore.__version__},
        "paths": paths,
    }


async def _single_cancel_case(mode: str) -> dict[str, Any]:
    runtime = _RealRuntime()
    await runtime.initialize()
    server = await _ProtocolServer(mode).start()
    try:
        await _cancel_request(runtime, server)
        await _drain()
        leaks = _connections_to_port(server.port)
        result = {
            "case": mode,
            "ok": not leaks and server.disconnects >= server.accepted,
            "accepted": server.accepted,
            "disconnects": server.disconnects,
            "leaks": leaks,
        }
    finally:
        await server.aclose()
        await runtime.aclose()
    return result


async def _peer_fin_cancel_wave_case() -> dict[str, Any]:
    count = 8
    runtime = _RealRuntime()
    await runtime.initialize()
    server = await _ProtocolServer(
        "response_headers_peer_fin_wave", target_connections=count
    ).start()
    tasks = [
        asyncio.create_task(runtime.request.retrieve(server.url(f"/botTOKEN/wave-{i}")))
        for i in range(count)
    ]
    try:
        await asyncio.wait_for(server.started.wait(), timeout=5.0)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _drain()
        leaks = _connections_to_port(server.port)
        result = {
            "case": "response_headers_peer_fin_wave",
            "ok": (
                not leaks
                and server.accepted == count
                and server.disconnects == count
            ),
            "accepted": server.accepted,
            "disconnects": server.disconnects,
            "leaks": leaks,
        }
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.aclose()
        await runtime.aclose()
    return result


async def _peer_fin_idle_case() -> dict[str, Any]:
    """A fully consumed response must not strand an idle CLOSE_WAIT socket."""
    runtime = _RealRuntime()
    await runtime.initialize()
    server = await _ProtocolServer("peer_fin_idle").start()
    try:
        data = await runtime.request.retrieve(server.url())
        await asyncio.wait_for(server.started.wait(), timeout=5.0)
        server.allow_peer_fin.set()
        await asyncio.wait_for(server.peer_fin_sent.wait(), timeout=5.0)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server.client_eof.wait(), timeout=1.0)
        await asyncio.sleep(0.1)
        leaks = _connections_to_port(server.port)
        result = {
            "case": "peer_fin_idle",
            "ok": (
                data == b'{"ok":true,"result":{}}'
                and server.client_eof.is_set()
                and not leaks
            ),
            "data": data.decode(),
            "peer_fin_sent": server.peer_fin_sent.is_set(),
            "client_eof": server.client_eof.is_set(),
            "leaks": leaks,
        }
    finally:
        await server.aclose()
        await runtime.aclose()
    return result


async def _repeated_case() -> dict[str, Any]:
    runtime = _RealRuntime()
    await runtime.initialize()
    server = await _ProtocolServer("response_headers").start()
    try:
        for _ in range(5):
            server.started = asyncio.Event()
            await _cancel_request(runtime, server)
        await _drain()
        leaks = _connections_to_port(server.port)
        result = {
            "case": "repeated",
            "ok": not leaks and server.accepted == 5 and server.disconnects >= 5,
            "accepted": server.accepted,
            "disconnects": server.disconnects,
            "leaks": leaks,
        }
    finally:
        await server.aclose()
        await runtime.aclose()
    return result


async def _concurrent_case() -> dict[str, Any]:
    runtime = _RealRuntime()
    await runtime.initialize()
    server = await _ProtocolServer("concurrent").start()
    stalled = asyncio.create_task(runtime.request.retrieve(server.url("/stall")))
    try:
        await asyncio.wait_for(server.started.wait(), timeout=5.0)
        healthy = asyncio.create_task(runtime.request.retrieve(server.url("/healthy")))
        stalled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stalled
        healthy_result = await asyncio.wait_for(healthy, timeout=5.0)
        await _drain()
        leaks = _connections_to_port(server.port)
        result = {
            "case": "concurrent",
            "ok": healthy_result == b'{"ok":true,"result":{}}' and not leaks,
            "healthy_result": healthy_result.decode(),
            "leaks": leaks,
        }
    finally:
        stalled.cancel()
        with contextlib.suppress(BaseException):
            await stalled
        await server.aclose()
        await runtime.aclose()
    return result


async def _proxy_tls_handshake_case() -> dict[str, Any]:
    """Cancel while real httpcore CONNECT traffic is blocked in TLS setup."""
    proxy = await _ConnectProxy().start()
    transport = tnet._new_async_http_transport(
        proxy=proxy.url,
        http2=True,
        verify=False,
        socket_diagnostics=True,
        diagnostic_owner="polling",
        diagnostic_route="primary",
    )
    client = httpx.AsyncClient(transport=transport, timeout=10.0)
    capture = _SocketLifecycleCapture()
    capture.__enter__()
    task = asyncio.create_task(client.get("https://localhost:443/stall"))
    started_task = asyncio.create_task(proxy.tls_started.wait())
    try:
        done, _pending = await asyncio.wait(
            {task, started_task}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            # Before the production fix, this re-raises the exact real-stack
            # contract failure: start_tls() takes 1 positional argument but 4 were given.
            await task
        assert started_task in done and started_task.result() is True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await _drain()
        leaks = _connections_to_port(proxy.port)
        opened = [message for message in capture.messages if "event=socket-opened" in message]
        closed = [message for message in capture.messages if "event=socket-closed" in message]
        result = {
            "case": "proxy_tls_handshake",
            "ok": (
                not leaks
                and proxy.accepted == 1
                and proxy.disconnects == 1
                and proxy.connect_targets == ["localhost:443"]
                and len(opened) == 1
                and len(closed) == 1
                and "owner=polling" in opened[0]
                and "route=primary" in opened[0]
                and opened[0].split("local_port=", 1)[1]
                == closed[0].split("local_port=", 1)[1]
            ),
            "http2_requested": True,
            "proxy_url": proxy.url,
            "connect_targets": proxy.connect_targets,
            "accepted": proxy.accepted,
            "disconnects": proxy.disconnects,
            "leaks": leaks,
            "socket_events": capture.messages,
        }
    finally:
        capture.__exit__(None, None, None)
        started_task.cancel()
        task.cancel()
        with contextlib.suppress(BaseException):
            await started_task
        with contextlib.suppress(BaseException):
            await task
        await client.aclose()
        await proxy.aclose()
    return result


async def _proxy_https_healthy_case() -> dict[str, Any]:
    """Complete one real HTTPS request through CONNECT with http2 enabled."""
    with tempfile.TemporaryDirectory() as directory:
        origin = await _TlsOrigin(_local_server_ssl_context(directory)).start()
        proxy = await _ConnectProxy(relay_port=origin.port).start()
        transport = tnet._new_async_http_transport(
            proxy=proxy.url, http2=True, verify=False
        )
        try:
            async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
                response = await client.get(f"https://localhost:{origin.port}/healthy")
            await _drain()
            leaks = _connections_to_port(proxy.port)
            result = {
                "case": "proxy_https_healthy",
                "ok": (
                    response.status_code == 200
                    and response.content == b"proxy tls ok"
                    and origin.requests == 1
                    and proxy.accepted == 1
                    and proxy.disconnects == 1
                    and not leaks
                ),
                "http2_requested": True,
                "proxy_url": proxy.url,
                "connect_targets": proxy.connect_targets,
                "status_code": response.status_code,
                "body": response.text,
                "origin_requests": origin.requests,
                "accepted": proxy.accepted,
                "disconnects": proxy.disconnects,
                "leaks": leaks,
            }
        finally:
            await proxy.aclose()
            await origin.aclose()
    return result


async def _run_case(case: str) -> dict[str, Any]:
    if case == "provenance":
        return await _provenance_case()
    if case in {
        "tls_handshake",
        "response_headers",
        "response_headers_peer_fin_race",
        "response_body",
    }:
        return await _single_cancel_case(case)
    if case == "response_headers_peer_fin_wave":
        return await _peer_fin_cancel_wave_case()
    if case == "peer_fin_idle":
        return await _peer_fin_idle_case()
    if case == "repeated":
        return await _repeated_case()
    if case == "concurrent":
        return await _concurrent_case()
    if case == "proxy_tls_handshake":
        return await _proxy_tls_handshake_case()
    if case == "proxy_https_healthy":
        return await _proxy_https_healthy_case()
    raise ValueError(case)


def _child_command(case: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "--case", case]


def _run_child(case: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _child_command(case),
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


@pytest.mark.parametrize("case", _case_params())
def test_installed_real_runtime_case(case: str) -> None:
    completed = _run_child(case)
    assert completed.returncode == 0, completed.stdout
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["case"] == case
    assert result["ok"] is True


async def _standalone_main() -> int:
    failed = False
    for case in _CASES:
        result = await _run_case(case)
        print(json.dumps(result, sort_keys=True), flush=True)
        failed |= not result["ok"]
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=_CASES)
    args = parser.parse_args()
    if args.case:
        result = asyncio.run(_run_case(args.case))
        print(json.dumps(result, sort_keys=True), flush=True)
        return int(not result["ok"])
    return asyncio.run(_standalone_main())


if __name__ == "__main__":
    raise SystemExit(main())
