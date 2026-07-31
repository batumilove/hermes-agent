"""Keep reserved test endpoint names out of the host DNS resolver.

Tests intentionally use names such as ``stub.invalid`` and
``custom.example.com`` to represent unreachable providers. Calling the real
resolver for those names is unnecessary and noisy: libc may append the
operator's DNS search suffix and send the resulting queries to production DNS.
This guard returns ``EAI_NONAME`` locally before libc performs resolution. All
non-fixture names retain normal resolver behavior.
"""

from __future__ import annotations

import socket
from typing import Any

_REAL_GETADDRINFO = socket.getaddrinfo
_EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")
_EXACT_TEST_HOSTS = frozenset({"test", "local", "llm-proxy.company.com"})


def _normalized_hostname(host: Any) -> str | None:
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(host, str):
        return None
    return host.rstrip(".").lower()


def _is_reserved_test_hostname(host: Any) -> bool:
    hostname = _normalized_hostname(host)
    if not hostname:
        return False
    if hostname in _EXACT_TEST_HOSTS:
        return True
    if hostname == "invalid" or hostname.endswith(".invalid"):
        return True
    if hostname == "test" or hostname.endswith(".test"):
        return True
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in _EXAMPLE_DOMAINS
    )


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any):
    if _is_reserved_test_hostname(host):
        raise socket.gaierror(
            socket.EAI_NONAME,
            f"reserved test hostname is intentionally unresolved: {host!r}",
        )
    return _REAL_GETADDRINFO(host, *args, **kwargs)


def install_reserved_test_host_guard() -> None:
    """Install the process-wide guard before pytest imports test modules."""
    if socket.getaddrinfo is not _guarded_getaddrinfo:
        socket.getaddrinfo = _guarded_getaddrinfo
