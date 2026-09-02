from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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
