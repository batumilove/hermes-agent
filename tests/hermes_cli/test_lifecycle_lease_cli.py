from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from gateway.lifecycle_lease import acquire_lifecycle_lease, inspect_lifecycle_lease
from hermes_cli import lifecycle_lease_cmd
from hermes_cli.subcommands.lifecycle_lease import build_lifecycle_lease_parser


_PROVENANCE = {
    "source_head": "a" * 40,
    "source_tree": "b" * 40,
    "artifact_sha256": "c" * 64,
    "evidence_id": "lease-cli-test",
}


def _parse(*argv: str):
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command")
    build_lifecycle_lease_parser(
        subparsers,
        cmd_lifecycle_lease=lifecycle_lease_cmd.run_lifecycle_lease_command,
    )
    return root.parse_args(["lifecycle-lease", *argv])


def _orphan(home):
    lease = acquire_lifecycle_lease(
        home=home,
        owner_token="operator-test",
        purpose="lcm-activation",
        provenance=_PROVENANCE,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    lease._handle.close()
    return inspect_lifecycle_lease(home=home)


def test_inspect_json_uses_active_profile_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lifecycle_lease_cmd, "get_hermes_home", lambda: tmp_path)
    args = _parse("inspect", "--json")

    exit_code = args.func(args)

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "absent",
        "lock_state": "absent",
        "metadata": None,
        "metadata_sha256": None,
    }


def test_reconcile_requires_all_exact_confirmations_at_parse_time():
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command")
    build_lifecycle_lease_parser(
        subparsers,
        cmd_lifecycle_lease=lifecycle_lease_cmd.run_lifecycle_lease_command,
    )

    try:
        root.parse_args(["lifecycle-lease", "reconcile"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover
        raise AssertionError("reconcile accepted missing confirmations")


def test_reconcile_json_removes_exact_orphan(tmp_path, monkeypatch, capsys):
    observed = _orphan(tmp_path)
    monkeypatch.setattr(lifecycle_lease_cmd, "get_hermes_home", lambda: tmp_path)
    args = _parse(
        "reconcile",
        "--expected-metadata-sha256",
        observed["metadata_sha256"],
        "--expected-owner-token",
        "operator-test",
        "--expected-purpose",
        "lcm-activation",
        "--json",
    )

    exit_code = args.func(args)

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "reconciled"
    assert inspect_lifecycle_lease(home=tmp_path)["status"] == "absent"


def test_reconcile_failure_is_nonzero_and_machine_readable(
    tmp_path, monkeypatch, capsys
):
    observed = _orphan(tmp_path)
    monkeypatch.setattr(lifecycle_lease_cmd, "get_hermes_home", lambda: tmp_path)
    args = _parse(
        "reconcile",
        "--expected-metadata-sha256",
        observed["metadata_sha256"],
        "--expected-owner-token",
        "wrong-owner",
        "--expected-purpose",
        "lcm-activation",
        "--json",
    )

    exit_code = args.func(args)

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "blocked"
    assert "confirmation" in payload["error"]
    assert inspect_lifecycle_lease(home=tmp_path)["status"] == "orphaned"


def test_root_cli_exposes_lifecycle_lease_inspect(tmp_path):
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path / "profile-home")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "lifecycle-lease",
            "inspect",
            "--json",
        ],
        cwd=str(__file__).split("/tests/", 1)[0],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "absent"


class _Lease:
    def __init__(self, events):
        self.events = events

    def release(self):
        self.events.append("lease-release")


def _run_args(*, purpose="lcm-activation"):
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    return _parse(
        "run",
        "--purpose",
        purpose,
        "--owner-token",
        f"{purpose}-controller",
        "--source-head",
        "a" * 40,
        "--source-tree",
        "b" * 40,
        "--artifact-sha256",
        "c" * 64,
        "--evidence-id",
        f"{purpose}-evidence",
        "--expires-at",
        expires_at,
        "--",
        sys.executable,
        "-c",
        "raise SystemExit(7)",
    )


@pytest.mark.parametrize("purpose", ["lcm-activation", "soak"])
def test_run_holds_exact_external_controller_lease(purpose, tmp_path, monkeypatch):
    events = []
    captured = {}

    def acquire(**kwargs):
        captured.update(kwargs)
        events.append("lease-acquire")
        return _Lease(events)

    def run(command, **kwargs):
        events.append(("command", command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(lifecycle_lease_cmd, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle_lease_cmd, "acquire_lifecycle_lease", acquire)
    monkeypatch.setattr(lifecycle_lease_cmd.subprocess, "run", run)

    exit_code = lifecycle_lease_cmd.run_lifecycle_lease_command(
        _run_args(purpose=purpose)
    )

    assert exit_code == 7
    assert events[0] == "lease-acquire"
    assert events[1][0] == "command"
    assert events[1][1][0] == sys.executable
    assert events[1][2] == {"check": False}
    assert events[2] == "lease-release"
    assert captured["home"] == tmp_path
    assert captured["purpose"] == purpose
    assert captured["owner_token"] == f"{purpose}-controller"
    assert captured["provenance"] == {
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "artifact_sha256": "c" * 64,
        "evidence_id": f"{purpose}-evidence",
    }


def test_run_releases_lease_on_base_exception(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(lifecycle_lease_cmd, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        lifecycle_lease_cmd,
        "acquire_lifecycle_lease",
        lambda **kwargs: _Lease(events),
    )

    def interrupted(command, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle_lease_cmd.subprocess, "run", interrupted)

    with pytest.raises(KeyboardInterrupt):
        lifecycle_lease_cmd.run_lifecycle_lease_command(_run_args())

    assert events == ["lease-release"]


def test_root_cli_run_executes_inert_controller_and_cleans_lease(tmp_path):
    home = tmp_path / "profile-home"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "lifecycle-lease",
            "run",
            "--purpose",
            "lcm-activation",
            "--owner-token",
            "lcm-activation-inert-test",
            "--source-head",
            "a" * 40,
            "--source-tree",
            "b" * 40,
            "--artifact-sha256",
            "c" * 64,
            "--evidence-id",
            "lcm-activation-inert-evidence",
            "--expires-at",
            expires_at,
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
        cwd=str(__file__).split("/tests/", 1)[0],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 7, result.stderr
    assert inspect_lifecycle_lease(home=home)["status"] == "absent"
