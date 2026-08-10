"""Regression tests for the suite-wide reserved-host DNS guard."""

import socket

import pytest

from test_support import dns_guard


@pytest.mark.parametrize(
    "hostname",
    [
        "test",
        "local",
        "stub.invalid",
        "api.example.invalid",
        "custom.example.com",
        "llm.internal.example.com",
        "fb.example.com",
        "llm-proxy.company.com",
    ],
)
def test_reserved_test_hostname_is_rejected_without_system_resolution(monkeypatch, hostname):
    calls = []

    def unexpected_resolution(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("system resolver was called")

    monkeypatch.setattr(dns_guard, "_REAL_GETADDRINFO", unexpected_resolution)

    with pytest.raises(socket.gaierror) as exc_info:
        dns_guard._guarded_getaddrinfo(hostname, 443)

    assert exc_info.value.errno == socket.EAI_NONAME
    assert calls == []


def test_install_guard_is_idempotent_and_normalizes_reserved_bytes(monkeypatch):
    calls = []

    def unexpected_resolution(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("system resolver was called")

    monkeypatch.setattr(dns_guard, "_REAL_GETADDRINFO", unexpected_resolution)

    dns_guard.install_reserved_test_host_guard()
    dns_guard.install_reserved_test_host_guard()

    with pytest.raises(socket.gaierror) as exc_info:
        socket.getaddrinfo(b"STUB.INVALID.", 443)

    assert exc_info.value.errno == socket.EAI_NONAME
    assert calls == []


@pytest.mark.parametrize(
    "hostname",
    ["localhost", "example.com", "example.net", "example.org"],
)
def test_ordinary_hostname_still_uses_system_resolver(monkeypatch, hostname):
    sentinel = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]
    calls = []

    def fake_resolution(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(dns_guard, "_REAL_GETADDRINFO", fake_resolution)

    assert dns_guard._guarded_getaddrinfo(hostname, 443) == sentinel
    assert calls == [((hostname, 443), {})]
