"""Real-socket regression tests for the Telegram terminal close path.

Live gateway evidence (d4e8d24a) shows close events starting but sockets
remaining in CLOSE_WAIT against Telegram 149.154.166.110:443. The leak
manifests when a response close is interrupted: the first close attempt
fails, the retry runs, and the network stream reports a successful close
without actually releasing the raw OS socket, or the cleanup callback is
hit by caller cancellation and never drives the socket to terminal close.

These tests use real socket pairs so the assertion is the actual socket
file descriptor state, not a mock or log line.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

import plugins.platforms.telegram.telegram_network as tnet


class _FailingResponseStream(httpx.AsyncByteStream):
    """Async byte stream whose ``aclose()`` always raises."""

    async def __aiter__(self):
        if False:  # Make this a real async generator.
            yield b""

    async def aclose(self) -> None:
        raise RuntimeError("simulated response-stream close failure")


class _CancellingResponseStream(httpx.AsyncByteStream):
    """Async byte stream whose ``aclose()`` raises ``asyncio.CancelledError``."""

    async def __aiter__(self):
        if False:
            yield b""

    async def aclose(self) -> None:
        raise asyncio.CancelledError("simulated caller cancellation")


class _OpenAsyncByteStream(httpx.AsyncByteStream):
    """Async byte stream that keeps an httpx.Response marked as unclosed."""

    async def __aiter__(self):
        yield b"ok"

    async def aclose(self) -> None:
        pass


class _NoopAcloseNetworkStream:
    """Network stream whose ``aclose()`` succeeds but never closes the socket."""

    def __init__(self, sock: Any) -> None:
        self._sock = sock

    def get_extra_info(self, name: str) -> Any:
        if name == "socket":
            return self._sock
        return None

    async def aclose(self) -> None:
        # Deliberately does NOT close self._sock.
        return

    def close(self) -> None:
        # Also deliberately does NOT close self._sock.
        return


class _RaisingAcloseNetworkStream(_NoopAcloseNetworkStream):
    async def aclose(self) -> None:
        raise OSError("simulated network-stream aclose failure")


class _TransportSocketLike:
    """Model asyncio.TransportSocket: exposes ``_sock`` but bans ``close()``."""

    __slots__ = ("_sock",)

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def fileno(self) -> int:
        return self._sock.fileno()


def _assert_socket_closed(sock: socket.socket, label: str) -> None:
    assert sock.fileno() == -1, f"{label}: raw OS socket was not closed"


@pytest.mark.asyncio
async def test_retry_closes_raw_socket_when_network_aclose_does_nothing() -> None:
    """Terminal retry must close the raw socket even when network close succeeds."""
    parent, child = socket.socketpair()
    try:
        wrapper = tnet._RetryingCloseResponseStream(
            _FailingResponseStream(),
            network_stream=_NoopAcloseNetworkStream(child),
        )

        # On the buggy base, the first close failure propagates out of aclose();
        # a detached retry task is scheduled but its network_stream.aclose() no-op
        # leaves the raw socket open. Catch the exception so we can assert the
        # socket state, which is the real leak signal.
        try:
            await wrapper.aclose()
        except BaseException:
            pass
        # Give the detached retry task (base) or inline retry (fixed) time.
        await asyncio.sleep(0.2)

        _assert_socket_closed(child, "network_stream.aclose() no-op")
    finally:
        parent.close()
        try:
            child.close()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_retry_closes_raw_socket_when_network_aclose_raises() -> None:
    """Terminal retry must close the raw socket when network_stream.aclose() raises."""
    parent, child = socket.socketpair()
    try:
        wrapper = tnet._RetryingCloseResponseStream(
            _FailingResponseStream(),
            network_stream=_RaisingAcloseNetworkStream(child),
        )

        try:
            await wrapper.aclose()
        except BaseException:
            pass
        await asyncio.sleep(0.2)

        _assert_socket_closed(child, "network_stream.aclose() raised")
    finally:
        parent.close()
        try:
            child.close()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_retry_closes_asyncio_transport_socket_wrapper() -> None:
    """The fallback must close asyncio's non-closeable TransportSocket view."""
    parent, child = socket.socketpair()
    try:
        wrapper = tnet._RetryingCloseResponseStream(
            _FailingResponseStream(),
            network_stream=_NoopAcloseNetworkStream(_TransportSocketLike(child)),
        )

        try:
            await wrapper.aclose()
        except BaseException:
            pass
        await asyncio.sleep(0.2)

        _assert_socket_closed(child, "asyncio.TransportSocket-compatible wrapper")
    finally:
        parent.close()
        try:
            child.close()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_abandoned_cleanup_closes_socket_despite_cancellation() -> None:
    """The abandoned-response cleanup callback must close the socket even if aclose() raises CancelledError."""
    parent, child = socket.socketpair()
    try:
        wrapper = tnet._RetryingCloseResponseStream(
            _CancellingResponseStream(),
            network_stream=_NoopAcloseNetworkStream(child),
        )
        # Use stream= so httpx.Response starts unclosed and aclose() actually runs.
        response = httpx.Response(200, stream=_OpenAsyncByteStream())
        response.stream = wrapper
        assert not response.is_closed

        try:
            await tnet._close_response(response)
        except BaseException:
            # On base, _close_response catches Exception only and lets CancelledError
            # propagate; swallow it so we can inspect the socket state.
            pass
        await asyncio.sleep(0.2)

        _assert_socket_closed(child, "abandoned cleanup with CancelledError")
    finally:
        parent.close()
        try:
            child.close()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_close_leak_soak_under_bounded_concurrency() -> None:
    """Many concurrent interrupted closes must not leak raw OS sockets.

    This is a scaled-up version of the single-call regressions: on d4e8 the
    retry's network_stream.aclose() no-op leaves the socket open, so after a
    burst of parallel calls the process would accumulate CLOSE_WAIT sockets.
    On the fix, every child socket is closed before the test finishes.
    """
    N = 50

    async def _one_call() -> socket.socket:
        parent, child = socket.socketpair()
        parent.close()
        wrapper = tnet._RetryingCloseResponseStream(
            _FailingResponseStream(),
            network_stream=_NoopAcloseNetworkStream(child),
        )
        try:
            await wrapper.aclose()
        except BaseException:
            pass
        return child

    sockets = await asyncio.gather(*(_one_call() for _ in range(N)))
    # Allow any detached retry tasks from the base path to finish before
    # asserting socket state. The candidate closes inline, but this keeps the
    # test deterministic across both implementations.
    await asyncio.sleep(0.3)

    leaked = 0
    for sock in sockets:
        if sock.fileno() != -1:
            leaked += 1
            try:
                sock.close()
            except OSError:
                pass
    assert leaked == 0, f"{leaked}/{N} raw sockets leaked after concurrent close"
