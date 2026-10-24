"""Explicit operator recovery for lifecycle controllers interrupted by an E2E fixture."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.e2e.core.upgrade import _install_helpers as I

if TYPE_CHECKING:
    from collections.abc import Callable
    import subprocess

    from tests.e2e.core.upgrade.handoff._nshost import NamespaceHost


def _operator(sb: I.Sandbox, *args: str) -> dict:
    # The ordinary launcher owes a source tail and remains blocked. The installed
    # managed interpreter makes the explicit operator CLI accessible without that tail.
    result = sb.run(
        [sb.python, "-c", "from hermes_cli.main import main; main()",
         "-p", "default", "lifecycle-lease", *args, "--json"],
        cwd=sb.checkout,
        timeout=60,
    )
    assert result.returncode == 0 and I.TRACEBACK not in result.stdout + result.stderr, I.describe(result)
    return json.loads(result.stdout)


def inspect(sb: I.Sandbox) -> dict:
    return _operator(sb, "inspect")


def recover_exact_orphan(
    sb: I.Sandbox,
    expected: dict,
    retry: Callable[[], subprocess.CompletedProcess],
) -> subprocess.CompletedProcess:
    """Confirm one exact orphan, prove retry stays blocked, then invoke operator recovery."""
    observed = inspect(sb)
    assert observed["status"] == "orphaned" and observed["lock_state"] == "available", observed
    assert observed["metadata"] == expected["metadata"], (expected, observed)
    assert observed["metadata_sha256"] == expected["metadata_sha256"], (expected, observed)
    assert observed["metadata"]["purpose"] == "deployment", observed
    blocked = retry()
    assert blocked.returncode != 0, I.describe(blocked)
    assert "orphaned or stale ownership metadata" in blocked.stdout + blocked.stderr, I.describe(blocked)
    assert inspect(sb) == observed, "default retry changed the exact orphan identity"
    reconciled = _operator(
        sb, "reconcile",
        "--expected-metadata-sha256", observed["metadata_sha256"],
        "--expected-owner-token", observed["metadata"]["owner_token"],
        "--expected-purpose", observed["metadata"]["purpose"],
    )
    assert reconciled["status"] == "reconciled", reconciled
    assert reconciled["metadata_sha256"] == observed["metadata_sha256"], reconciled
    assert inspect(sb)["status"] == "absent"
    return blocked


def remember_update_lease(sb: I.Sandbox, host: NamespaceHost) -> dict:
    """Record the live controller's exact identity before this fixture kills it."""
    observed = inspect(sb)
    assert observed["status"] == "active" and observed["lock_state"] == "busy", observed
    assert observed["metadata"]["purpose"] == "deployment", observed
    assert observed["metadata"]["pid"] in {p["pid"] for p in host.procs()}, observed
    host.fixture_update_lease = observed
    return observed


def close_namespace(
    sb: I.Sandbox,
    host: NamespaceHost,
    retry: Callable[[], subprocess.CompletedProcess],
) -> list[str]:
    """Close fixture processes and explicitly reconcile only their remembered exact orphan."""
    expected = getattr(host, "fixture_update_lease", None)
    try:
        before = inspect(sb)
        if before["status"] == "active":
            if expected is None or before["metadata_sha256"] != expected["metadata_sha256"]:
                expected = remember_update_lease(sb, host)
        elif before["status"] == "orphaned":
            assert expected is not None, "unremembered orphan is not owned by this fixture"
            assert before["metadata_sha256"] == expected["metadata_sha256"], (expected, before)
        else:
            assert before["status"] == "absent", before
    finally:
        leaked = host.close()
    after = inspect(sb)
    if after["status"] != "absent":
        assert expected is not None, "namespace teardown found an unverified lease"
        recover_exact_orphan(sb, expected, retry)
    return leaked
