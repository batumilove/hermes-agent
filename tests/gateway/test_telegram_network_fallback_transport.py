"""Telegram fallback request, body-read and cancellation lifecycle contracts."""
from tests.gateway import test_telegram_network as _suite

class TestFallbackTransport:
    """IPv4 literals first → hostname last → stick to whichever works."""

    @_suite.pytest.mark.asyncio
    async def test_cancellation_after_connect_before_tls_detaches_raw_stream_close(self):
        """Cancellation in httpcore's connect trace hook must not strand its socket.

        httpcore awaits the async ``connect_tcp.complete`` trace callback after
        ``connect_tcp()`` returns but before ``start_tls()`` begins. Production
        cancellation waves hit this ownership gap: no protected raw-stream
        operation is active yet, and no HTTP response exists to close.
        """
        left, right = _suite.socket.socketpair()
        stream = _suite._RepeatedCancellationSocketStream(left, "trace")
        transport = _suite.httpx.AsyncHTTPTransport()
        transport._pool._network_backend = _suite.tnet._CancellationSafeNetworkBackend(
            _suite._RawDiagnosticBackend(stream)
        )
        trace_started = _suite.asyncio.Event()

        async def _hold_after_connect(name, _info):
            if name.endswith("connect_tcp.complete"):
                trace_started.set()
                await _suite.asyncio.Event().wait()

        request = _suite._telegram_request()
        request.extensions["trace"] = _hold_after_connect
        orchestration_cancelled_once = _suite.asyncio.Event()

        async def _orchestrate_request():
            try:
                return await _suite.tnet._handle_transport_request(transport, request)
            except _suite.asyncio.CancelledError:
                await stream.close_started.wait()
                orchestration_cancelled_once.set()
                await _suite.asyncio.Event().wait()

        request_task = _suite.asyncio.create_task(_orchestrate_request())

        try:
            await _suite.asyncio.wait_for(trace_started.wait(), timeout=1.0)
            request_task.cancel()
            await _suite.asyncio.wait_for(orchestration_cancelled_once.wait(), timeout=1.0)

            # Re-cancel the still-active caller while request-scoped cleanup is
            # blocked. The detached close must remain independently owned.
            request_task.cancel()
            with _suite.pytest.raises(_suite.asyncio.CancelledError):
                await request_task
            stream.release_close.set()
            for _ in range(100):
                if left.fileno() == -1:
                    break
                await _suite.asyncio.sleep(0.01)

            assert left.fileno() == -1
            assert not _suite.tnet._abandoned_response_cleanups
        finally:
            stream.release_close.set()
            left.close()
            right.close()
            await transport.aclose()

    @_suite.pytest.mark.asyncio
    @_suite.pytest.mark.parametrize("operation", ["read", "write", "start_tls"])
    async def test_repeated_cancellation_cannot_interrupt_raw_stream_close(
        self, operation
    ):
        """A second cancellation must not strand pre-response socket cleanup."""
        left, right = _suite.socket.socketpair()
        stream = _suite._RepeatedCancellationSocketStream(left, operation)
        wrapped = _suite.tnet._CancellationSafeNetworkStream(stream)
        if operation == "read":
            operation_coro = wrapped.read(1)
        elif operation == "write":
            operation_coro = wrapped.write(b"request")
        else:
            operation_coro = wrapped.start_tls(None)
        waiter = _suite.asyncio.create_task(operation_coro)

        try:
            await _suite.asyncio.wait_for(stream.operation_started.wait(), timeout=1.0)
            waiter.cancel()
            await _suite.asyncio.wait_for(stream.close_started.wait(), timeout=1.0)

            # Production issues overlapping cancellations while fallback requests
            # unwind. This second cancellation must not interrupt raw close.
            waiter.cancel()
            with _suite.pytest.raises(_suite.asyncio.CancelledError):
                await waiter

            stream.release_close.set()
            for _ in range(100):
                if left.fileno() == -1:
                    break
                await _suite.asyncio.sleep(0.01)
            assert left.fileno() == -1
            assert not _suite.tnet._abandoned_response_cleanups
        finally:
            left.close()
            right.close()

    @_suite.pytest.mark.asyncio
    async def test_cancelled_caller_cancels_inflight_transport_request(self, monkeypatch):
        """Caller cancellation must unwind an in-flight transport request.

        Shielding the request from its caller is useful only while a response
        can finish promptly. During connect or response-header stalls, leaving
        the shielded request alive retains its socket indefinitely. Explicitly
        cancelling the transport task lets httpcore unwind and release it.
        """
        inner = _suite._CancellationTrackingTransport()
        monkeypatch.setattr(_suite.tnet.httpx, "AsyncHTTPTransport", lambda **kw: inner)

        transport = _suite.tnet.TelegramFallbackTransport(["149.154.167.220"])
        transport._sticky_ip = "149.154.167.220"
        request_task = _suite.asyncio.create_task(
            transport.handle_async_request(_suite._telegram_request())
        )
        await inner.started.wait()

        request_task.cancel()
        with _suite.pytest.raises(_suite.asyncio.CancelledError):
            await request_task

        assert inner.cancelled is True
        for _ in range(10):
            if not _suite.tnet._abandoned_response_cleanups:
                break
            await _suite.asyncio.sleep(0)
        assert not _suite.tnet._abandoned_response_cleanups

    @_suite.pytest.mark.asyncio
    async def test_pre_response_cancellation_has_correlated_request_terminal_telemetry(
        self, monkeypatch
    ):
        """A socket opened before response creation must remain attributable.

        The request identifier is deliberately opaque and the terminal record
        must not contain URL, token, query, or exception contents.
        """
        inner = _suite._CancellationTrackingTransport()
        monkeypatch.setattr(_suite.tnet.httpx, "AsyncHTTPTransport", lambda **_kw: inner)
        transport = _suite.tnet.TelegramFallbackTransport(
            ["149.154.167.220"],
            owner_role="general",
            socket_diagnostics=True,
        )
        request, forbidden = _suite._diagnostic_request()

        with _suite._LifecycleLogCapture(lambda: inner.stream) as capture:
            request_task = _suite.asyncio.create_task(transport.handle_async_request(request))
            await inner.started.wait()
            request_task.cancel()
            with _suite.pytest.raises(_suite.asyncio.CancelledError):
                await request_task

        payloads = [_suite._record_payload(record) for record, _ in capture.records]
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
        _suite._assert_records_redacted(capture.records, forbidden)

    @_suite.pytest.mark.asyncio
    async def test_caller_body_read_cancellation_closes_real_socket_response(self):
        """A caller cancelled while reading the response body must close the socket.

        PTB's ``HTTPXRequest.do_request`` does ``res = await client.request(...)``
        and then ``return res.status_code, res.content``. If its task is cancelled
        during ``res.content``, the response is abandoned and the underlying socket
        stays in CLOSE_WAIT. Our transport must close the response when the
        caller's task ends abnormally.
        """
        server = _suite._SlowBodyServer(body=b"x" * 256, sent_bytes=1)
        server.start()
        try:
            transport = _suite.httpx.AsyncHTTPTransport()
            request = _suite.httpx.Request("GET", server.url)
            response_holder: dict[str, _suite.httpx.Response] = {}

            async def _read_body():
                response = await _suite.tnet._handle_transport_request(transport, request)
                response_holder["response"] = response
                return await response.aread()

            task = _suite.asyncio.create_task(_read_body())
            server.wait_for_connection(timeout=2.0)
            await _suite.async_wait_until(lambda: "response" in response_holder, timeout=2, what="body reader reached response")
            task.cancel()
            with _suite.pytest.raises(_suite.asyncio.CancelledError):
                await task

            # Yield to let the response-close callback fire.
            await _suite.asyncio.sleep(0)

            assert response_holder["response"].is_closed, (
                "Response was abandoned on caller cancellation and its socket was not closed"
            )
            await transport.aclose()
        finally:
            server.stop()

    @_suite.pytest.mark.asyncio
    async def test_caught_close_cancellation_retries_and_clears_real_close_wait(self):
        """A caught first-close cancellation must not strand a real TCP socket."""
        server = _suite._PeerFinServer()
        server.start()
        client = server.connect()
        local_port = client.getsockname()[1]
        remote_port = client.getpeername()[1]
        stream = _suite._CancelFirstSocketByteStream(client)
        request = _suite._telegram_request()
        response = _suite.httpx.Response(
            200,
            request=request,
            stream=stream,
            extensions={"network_stream": stream},
        )
        transport = _suite._SingleResponseTransport(response)

        try:
            await _suite._wait_for_tcp_state(local_port, remote_port, "08")

            async def _caller_catches_close_cancellation():
                owned_response = await _suite.tnet._handle_transport_request(
                    transport, request
                )
                try:
                    await owned_response.aclose()
                except _suite.asyncio.CancelledError:
                    return "cancelled-close-caught"

            result = await _suite.asyncio.create_task(
                _caller_catches_close_cancellation()
            )
            assert result == "cancelled-close-caught"

            # The caller task completed normally, so its done callback cannot
            # identify the abandoned response. The stream wrapper must retry.
            await _suite._wait_for_tcp_state(local_port, remote_port, None)
            assert stream.close_calls == 2
            assert client.fileno() == -1
            for _ in range(20):
                if not _suite.tnet._abandoned_response_cleanups:
                    break
                await _suite.asyncio.sleep(0)
            assert not _suite.tnet._abandoned_response_cleanups
        finally:
            client.close()
            server.stop()

    @_suite.pytest.mark.asyncio
    async def test_caller_cancellation_cannot_interrupt_idempotent_stream_cleanup(self):
        """External cancellation must not make an idempotent retry false-green.

        httpcore marks ``PoolByteStream`` closed before its cancellable pool
        bookkeeping completes. A second ``aclose()`` then returns successfully
        without finishing the first cleanup. Keep that first close alive instead
        of treating the no-op retry as proof that the real socket was released.
        """
        server = _suite._PeerFinServer()
        server.start()
        client = server.connect()
        local_port = client.getsockname()[1]
        remote_port = client.getpeername()[1]
        stream = _suite._IdempotentInterruptedSocketByteStream(client)
        retrying = _suite.tnet._RetryingCloseResponseStream(
            stream,
            network_stream=stream,
        )

        try:
            await _suite._wait_for_tcp_state(local_port, remote_port, "08")
            close_task = _suite.asyncio.create_task(retrying.aclose())
            await _suite.asyncio.wait_for(stream.close_started.wait(), timeout=1.0)
            close_task.cancel()
            with _suite.pytest.raises(_suite.asyncio.CancelledError):
                await close_task

            stream.release_close.set()
            await _suite.asyncio.wait_for(stream.cleanup_finished.wait(), timeout=2.0)
            await _suite._wait_for_tcp_state(
                local_port,
                remote_port,
                None,
                timeout=5.0,
            )
            assert stream.cleanup_completed is True
            assert stream.close_calls == 1
            for _ in range(20):
                if not _suite.tnet._abandoned_response_cleanups:
                    break
                await _suite.asyncio.sleep(0)
            assert not _suite.tnet._abandoned_response_cleanups
        finally:
            stream.release_close.set()
            client.close()
            server.stop()

    @_suite.pytest.mark.asyncio
    async def test_shielded_close_late_failure_cannot_false_green_idempotent_retry(self):
        """A late first-close failure must still close the exact raw socket."""
        left, right = _suite.socket.socketpair()
        tracked_socket = _suite._RawSocketTracker(left)
        network_stream = _suite._AcloseFailingNetworkStream(tracked_socket)
        stream = _suite._IdempotentLateFailingByteStream()
        retrying = _suite.tnet._RetryingCloseResponseStream(
            stream,
            network_stream=network_stream,
        )
        try:
            waiter = _suite.asyncio.create_task(retrying.aclose())
            await _suite.asyncio.wait_for(stream.close_started.wait(), timeout=1.0)
            waiter.cancel()
            with _suite.pytest.raises(_suite.asyncio.CancelledError):
                await waiter

            stream.release_close.set()
            for _ in range(200):
                if tracked_socket.close_calls == 1:
                    break
                await _suite.asyncio.sleep(0.01)

            assert stream.close_calls == 2
            assert network_stream.aclose_calls == 1
            assert tracked_socket.close_calls == 1
            assert tracked_socket.fileno() == -1
            for _ in range(200):
                if not _suite.tnet._abandoned_response_cleanups:
                    break
                await _suite.asyncio.sleep(0.01)
            assert not _suite.tnet._abandoned_response_cleanups
        finally:
            left.close()
            right.close()

    @_suite.pytest.mark.asyncio
    async def test_retry_failure_forces_network_stream_close(self):
        """If both byte-stream closes fail, force-close the raw network stream."""
        network_stream = _suite._DiagnosticNetworkStream()
        stream = _suite._CloseFailingByteStream()
        retrying = _suite.tnet._RetryingCloseResponseStream(
            stream,
            network_stream=network_stream,
        )

        with _suite.pytest.raises(RuntimeError, match="stream close broken"):
            await retrying.aclose()

        for _ in range(20):
            if network_stream.closed and not _suite.tnet._abandoned_response_cleanups:
                break
            await _suite.asyncio.sleep(0)
        assert stream.close_calls == 2
        assert network_stream.closed is True
        assert not _suite.tnet._abandoned_response_cleanups

    @_suite.pytest.mark.asyncio
    async def test_force_close_falls_back_to_exact_raw_socket_on_network_aclose_failure(self):
        """When network_stream.aclose() fails, close the exact raw OS socket.

        The terminal fallback in _force_close_network_stream must close the
        exact socket returned by get_extra_info('socket') — once, idempotently
        — and no unrelated socket.
        """
        sock_a, sock_b = _suite.socket.socketpair()
        try:
            raw_tracker = _suite._RawSocketTracker(sock_a)
            network_stream = _suite._AcloseFailingNetworkStream(raw_tracker)
            stream = _suite._CloseFailingByteStream()
            retrying = _suite.tnet._RetryingCloseResponseStream(
                stream,
                network_stream=network_stream,
            )

            with _suite.pytest.raises(RuntimeError, match="stream close broken"):
                await retrying.aclose()

            for _ in range(30):
                if raw_tracker.close_calls > 0 and not _suite.tnet._abandoned_response_cleanups:
                    break
                await _suite.asyncio.sleep(0)

            # The exact raw socket was closed exactly once.
            assert raw_tracker.close_calls == 1
            # The network-stream aclose was attempted exactly once (idempotent).
            assert network_stream.aclose_calls == 1
            # The unrelated socket (sock_b) was never touched.
            assert sock_b.fileno() != -1
            assert not _suite.tnet._abandoned_response_cleanups
        finally:
            sock_a.close()
            sock_b.close()

    @_suite.pytest.mark.asyncio
    async def test_ipv4_literal_tried_before_hostname_and_becomes_sticky(self, monkeypatch):
        calls = []
        behavior = {"api.telegram.org": "timeout", "149.154.167.220": "ok"}
        monkeypatch.setattr(_suite.tnet.httpx, "AsyncHTTPTransport", _suite._fake_transport_factory(calls, behavior))

        transport = _suite.tnet.TelegramFallbackTransport(["149.154.167.220"])
        resp = await transport.handle_async_request(_suite._telegram_request())

        assert resp.status_code == 200
        assert transport._sticky_ip == "149.154.167.220"
        assert [c["url_host"] for c in calls] == ["149.154.167.220"]
        assert calls[0]["host_header"] == "api.telegram.org"
        assert calls[0]["sni_hostname"] == "api.telegram.org"

        # Second request goes straight to sticky IP
        calls.clear()
        resp2 = await transport.handle_async_request(_suite._telegram_request())
        assert resp2.status_code == 200
        assert calls[0]["url_host"] == "149.154.167.220"

    @_suite.pytest.mark.asyncio
    async def test_sticky_ip_tried_first_but_falls_through_if_stale(self, monkeypatch):
        """If the sticky IP stops working, the transport retries others."""
        calls = []
        behavior = {
            "api.telegram.org": "timeout",
            "149.154.167.220": "ok",
            "149.154.167.221": "ok",
        }
        monkeypatch.setattr(_suite.tnet.httpx, "AsyncHTTPTransport", _suite._fake_transport_factory(calls, behavior))

        transport = _suite.tnet.TelegramFallbackTransport(["149.154.167.220", "149.154.167.221"])

        # First request: .220 works immediately (IPv4-first) → becomes sticky
        await transport.handle_async_request(_suite._telegram_request())
        assert transport._sticky_ip == "149.154.167.220"

        # Now .220 goes bad too
        calls.clear()
        behavior["149.154.167.220"] = "timeout"

        resp = await transport.handle_async_request(_suite._telegram_request())
        assert resp.status_code == 200
        # Sticky .220 fails → remaining IPv4 .221 works. Hostname is last
        # and is never needed.
        assert [c["url_host"] for c in calls] == ["149.154.167.220", "149.154.167.221"]
        assert transport._sticky_ip == "149.154.167.221"

    @_suite.pytest.mark.asyncio
    async def test_hostname_tried_last_when_ipv4_fails(self, monkeypatch):
        """IPv6-only / seed-IP-blocked hosts still reach the hostname last."""
        calls = []
        behavior = {"149.154.167.220": "timeout", "api.telegram.org": "ok"}
        monkeypatch.setattr(_suite.tnet.httpx, "AsyncHTTPTransport", _suite._fake_transport_factory(calls, behavior))
        transport = _suite.tnet.TelegramFallbackTransport(["149.154.167.220"])
        resp = await transport.handle_async_request(_suite._telegram_request())
        assert resp.status_code == 200
        assert [c["url_host"] for c in calls] == ["149.154.167.220", "api.telegram.org"]
        assert transport._sticky_ip is None
