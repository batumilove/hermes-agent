"""Startup pause recovery preserves explicit common lifecycle ownership.

A real dead process writes pause debt in a tiny committed Git checkout. Only
native gateway relaunch is substituted at its semantic completion seam.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gateway.lifecycle_lease import inspect_lifecycle_lease, reconcile_lifecycle_lease
from hermes_cli import update_pause_record as records
from hermes_cli.lifecycle_coordination import acquire_cli_lifecycle_transaction

pytestmark = pytest.mark.platforms("posix")


@pytest.fixture
def paused(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "source.txt").write_text("source\n", encoding="utf-8")
    for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"), ("commit", "-qm", "source")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=30)
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(records, "install_root", lambda: root)
    code = (
        "from pathlib import Path\nfrom hermes_cli import update_pause_record as records\n"
        f"records.install_root = lambda: Path({str(root)!r})\n"
        "records.write(records.stamp_tree({'resume_needed': True, 'profiles': {'default': 4242}}))\n"
    )
    written = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ), timeout=30, check=False)
    assert written.returncode == 0, written.stdout + written.stderr
    path = records.record_path()
    body = records.read(path)
    assert body is not None and not records.identity_is_live(body["owner"])
    calls = []

    def resume(token):
        observed = inspect_lifecycle_lease(home=home)
        calls.append(observed)
        token["resume_needed"] = False

    monkeypatch.setattr("hermes_cli.update_cmd_windows._resume_windows_gateways_after_update", resume)
    return root, home, path, body, calls


@pytest.mark.parametrize("command", ["inspect", "reconcile"])
def test_operator_commands_never_resume_or_retire_paused_gateways(paused, command):
    root, home, path, _body, calls = paused
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="deployment", operation="interrupted-update",
    )
    lease._handle.close()
    metadata = home / ".lifecycle_transaction_lease.json"
    before = {entry: entry.read_bytes() for entry in (path, metadata)}
    records.recover(["-p", "default", "lifecycle-lease", command])
    assert calls == []
    assert {entry: entry.read_bytes() for entry in before} == before
    assert records._claims(path) == []


@pytest.mark.parametrize("state", ["active", "orphaned"])
def test_ambiguous_profile_ownership_keeps_the_exact_pause_debt_unattempted(paused, state):
    root, home, _path, body, calls = paused
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="gateway-restart", operation="other-controller",
    )
    if state == "orphaned":
        lease._handle.close()
    observed = inspect_lifecycle_lease(home=home)
    try:
        records.recover(["status"])
        assert calls == [], "gateway recovery ran without common ownership"
        [(claimed, held)] = records.orphans()
        assert held["token"] == body["token"]
        assert held["claimer"] == records.UNOWNED
        assert records.read(claimed)["token"] == body["token"]
        assert "relaunch_retry" not in held["token"]
        assert inspect_lifecycle_lease(home=home) == observed
    finally:
        if state == "active":
            lease.release()


def test_exact_operator_reconciliation_allows_one_owned_gateway_recovery(paused):
    root, home, path, _body, calls = paused
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="deployment", operation="interrupted-update",
    )
    lease._handle.close()
    observed = inspect_lifecycle_lease(home=home)
    reconcile_lifecycle_lease(
        home=home, expected_metadata_sha256=observed["metadata_sha256"],
        expected_owner_token=observed["metadata"]["owner_token"],
        expected_purpose=observed["metadata"]["purpose"],
    )
    records.recover(["status"])
    assert len(calls) == 1 and calls[0]["status"] == "active"
    assert calls[0]["metadata"]["purpose"] == "deployment"
    assert not path.exists() and records._claims(path) == []
    assert inspect_lifecycle_lease(home=home)["status"] == "absent"
    records.recover(["status"])
    assert len(calls) == 1, "a discharged paused set restarted twice"


@pytest.mark.parametrize("failure", [RuntimeError("readiness failed"), KeyboardInterrupt()])
def test_failed_or_cancelled_resume_releases_lease_without_losing_debt(paused, monkeypatch, failure):
    _root, home, path, body, _calls = paused

    def fail(token):
        assert inspect_lifecycle_lease(home=home)["status"] == "active"
        raise failure

    monkeypatch.setattr("hermes_cli.update_cmd_windows._resume_windows_gateways_after_update", fail)
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            records.recover(["status"])
    else:
        records.recover(["status"])
    assert inspect_lifecycle_lease(home=home)["status"] == "absent"
    [claim] = records._claims(path)
    assert records.read(claim)["token"]["profiles"] == body["token"]["profiles"]
    assert records.read(claim)["token"]["resume_needed"]


@pytest.mark.parametrize("failure", [OSError("lease entry failed"), KeyboardInterrupt()])
def test_failed_or_cancelled_lease_entry_hands_back_exact_unattempted_claim(paused, monkeypatch, failure):
    _root, _home, _path, body, calls = paused

    class FailedOwnership:
        def __enter__(self):
            raise failure

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("hermes_cli.lifecycle_coordination.cli_update_transaction",
                        lambda **kwargs: FailedOwnership())
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            records.recover(["status"])
    else:
        records.recover(["status"])
    [(claim, deferred)] = records.orphans()
    assert deferred["claimer"] == records.UNOWNED
    assert deferred["token"] == body["token"]
    assert records.read(claim)["token"] == body["token"]
    assert "relaunch_retry" not in deferred["token"] and calls == []
