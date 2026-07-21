"""Tests for authoritative, restart-safe Hermes update verification."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.update_verification import (
    compare_with_upstream,
    fetch_and_verify_remote_ref,
    write_update_result,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_fetch_and_verify_remote_ref_refreshes_stale_tracking_ref(tmp_path):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"

    remote.mkdir()
    _git(remote, "init", "--bare")
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.name", "Test User")
    _git(seed, "config", "user.email", "test@example.com")
    (seed / "state.txt").write_text("one\n")
    _git(seed, "add", "state.txt")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", "--branch", "main", str(remote), str(checkout))
    stale_sha = _git(checkout, "rev-parse", "origin/main")

    (seed / "state.txt").write_text("two\n")
    _git(seed, "commit", "-am", "second")
    _git(seed, "push", "origin", "main")
    live_sha = _git(seed, "rev-parse", "HEAD")
    assert stale_sha != live_sha

    result = fetch_and_verify_remote_ref(["git"], checkout, "origin", "main")

    assert result.verified is True
    assert result.fetched_sha == live_sha
    assert result.live_sha == live_sha
    assert _git(checkout, "rev-parse", "origin/main") == live_sha


def test_write_update_result_replaces_previous_record_atomically(tmp_path):
    first = {
        "schema_version": 1,
        "status": "started",
        "started_sha": "a" * 40,
    }
    second = {
        "schema_version": 1,
        "status": "success",
        "started_sha": "a" * 40,
        "completed_sha": "b" * 40,
        "remote_verified": True,
    }

    path = write_update_result(tmp_path, first)
    assert path == tmp_path / ".update_result.json"
    assert json.loads(path.read_text()) == first

    replaced = write_update_result(tmp_path, second)
    assert replaced == path
    assert json.loads(path.read_text()) == second
    assert not list(tmp_path.glob(".update_result.json.tmp*"))


def test_write_update_result_redacts_url_credentials_from_raw_payload(tmp_path):
    secret = "writer-secret-token"
    credential_url = f"https://writer-user:{secret}@github.com/org/repo.git"

    path = write_update_result(
        tmp_path,
        {
            "status": "failed",
            "remote_error": f"fatal: unable to access '{credential_url}/': HTTP 401",
        },
    )

    persisted = path.read_text()
    assert secret not in persisted
    assert "writer-user" not in persisted
    assert credential_url not in persisted
    assert "github.com/org/repo.git" in persisted


def test_write_update_result_cleans_partial_temp_when_fsync_fails(
    tmp_path, monkeypatch
):
    from hermes_cli import update_verification as uv

    def fail_fsync(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(uv.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="disk full"):
        write_update_result(tmp_path, {"status": "started"})

    assert not (tmp_path / ".update_result.json").exists()
    assert not list(tmp_path.glob(".update_result.json.tmp*"))


def test_compare_with_upstream_records_boundary_and_both_deltas(tmp_path):
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    deploy = tmp_path / "deploy"

    upstream.mkdir()
    _git(upstream, "init", "--bare")
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.name", "Test User")
    _git(seed, "config", "user.email", "test@example.com")
    (seed / "state.txt").write_text("base\n")
    _git(seed, "add", "state.txt")
    _git(seed, "commit", "-m", "base")
    base_sha = _git(seed, "rev-parse", "HEAD")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", "--branch", "main", str(upstream), str(deploy))
    _git(deploy, "remote", "rename", "origin", "upstream")
    _git(deploy, "config", "user.name", "Fork User")
    _git(deploy, "config", "user.email", "fork@example.com")
    (deploy / "fork.txt").write_text("fork\n")
    _git(deploy, "add", "fork.txt")
    _git(deploy, "commit", "-m", "fork patch")

    (seed / "upstream.txt").write_text("new\n")
    _git(seed, "add", "upstream.txt")
    _git(seed, "commit", "-m", "new upstream")
    upstream_sha = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "origin", "main")

    result = compare_with_upstream(["git"], deploy)

    assert result.verified is True
    assert result.upstream_sha == upstream_sha
    assert result.merged_through_sha == base_sha
    assert result.behind == 1
    assert result.carried == 1


def test_compare_with_upstream_returns_unknown_for_non_numeric_divergence(
    tmp_path, monkeypatch
):
    from hermes_cli import update_verification as uv

    verified = uv.RemoteRefVerification(
        "upstream", "main", "a" * 40, "a" * 40, True
    )
    monkeypatch.setattr(uv, "fetch_and_verify_remote_ref", lambda *a, **k: verified)

    def fake_run(cmd, **kwargs):
        if "merge-base" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{'b' * 40}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="one two\n", stderr="")

    monkeypatch.setattr(uv.subprocess, "run", fake_run)
    result = compare_with_upstream(["git"], tmp_path)

    assert result.verified is False
    assert "non-integer" in (result.error or "")


@pytest.mark.parametrize("failure_step", ["fetch", "rev-parse", "ls-remote"])
def test_remote_verification_redacts_url_credentials_from_git_errors(
    tmp_path, monkeypatch, failure_step
):
    from hermes_cli import update_verification as uv

    secret = "super-secret-token"
    credential_url = f"https://git-user:{secret}@github.com/org/repo.git"
    failure = subprocess.CompletedProcess(
        ["git"],
        128,
        stdout="",
        stderr=f"fatal: unable to access '{credential_url}/': HTTP 401\n",
    )
    success = subprocess.CompletedProcess(["git"], 0, stdout="", stderr="")
    resolved = subprocess.CompletedProcess(
        ["git"], 0, stdout=f"{'a' * 40}\n", stderr=""
    )

    if failure_step == "fetch":
        results = iter([failure])
    elif failure_step == "rev-parse":
        results = iter([success, failure])
    else:
        results = iter([success, resolved, failure])
    monkeypatch.setattr(uv.subprocess, "run", lambda *a, **k: next(results))

    result = fetch_and_verify_remote_ref(["git"], tmp_path, "origin", "main")

    assert result.verified is False
    assert result.error is not None
    assert secret not in result.error
    assert "git-user" not in result.error
    assert credential_url not in result.error

    path = write_update_result(
        tmp_path, {"status": "failed", "remote_error": result.error}
    )
    persisted = path.read_text()
    assert secret not in persisted
    assert "git-user" not in persisted
    assert credential_url not in persisted


@pytest.mark.parametrize(
    "credential_url",
    [
        "ftps://edge-user:edge-secret@github.com/org/repo.git",
        "sftp://edge-user:edge-secret@github.com/org/repo.git",
        "wss://edge-user:edge-secret@github.com/org/repo.git",
        "//edge-user:edge-secret@github.com/org/repo.git",
    ],
)
def test_remote_verification_strips_complete_userinfo_for_supported_url_forms(
    tmp_path, monkeypatch, credential_url
):
    from hermes_cli import update_verification as uv

    monkeypatch.setattr(
        uv.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            128,
            stdout="",
            stderr=f"fatal: unable to access '{credential_url}/': HTTP 401\n",
        ),
    )

    result = fetch_and_verify_remote_ref(["git"], tmp_path, "origin", "main")

    assert result.verified is False
    assert result.error is not None
    assert "edge-secret" not in result.error
    assert "edge-user" not in result.error
    assert credential_url not in result.error
    assert "github.com/org/repo.git" in result.error


def test_upstream_comparison_redacts_url_credentials_from_fetch_error(
    tmp_path, monkeypatch
):
    from hermes_cli import update_verification as uv

    secret = "upstream-secret-token"
    credential_url = f"https://upstream-user:{secret}@github.com/org/repo.git"
    monkeypatch.setattr(
        uv.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            128,
            stdout="",
            stderr=f"fatal: unable to access '{credential_url}/': HTTP 401\n",
        ),
    )

    result = compare_with_upstream(["git"], tmp_path)

    assert result.verified is False
    assert result.error is not None
    assert secret not in result.error
    assert "upstream-user" not in result.error
    assert credential_url not in result.error


def test_fetch_and_verify_remote_ref_rejects_malformed_object_ids(
    tmp_path, monkeypatch
):
    from hermes_cli import update_verification as uv

    results = iter(
        [
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="not-a-sha\n", stderr=""),
        ]
    )
    monkeypatch.setattr(uv.subprocess, "run", lambda *a, **k: next(results))

    result = fetch_and_verify_remote_ref(["git"], tmp_path, "origin", "main")

    assert result.verified is False
    assert "invalid object ID" in (result.error or "")


def test_compare_with_upstream_rejects_malformed_boundary_and_negative_counts(
    tmp_path, monkeypatch
):
    from hermes_cli import update_verification as uv

    verified = uv.RemoteRefVerification(
        "upstream", "main", "a" * 40, "a" * 40, True
    )
    monkeypatch.setattr(uv, "fetch_and_verify_remote_ref", lambda *a, **k: verified)
    monkeypatch.setattr(
        uv.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout="not-a-sha\n", stderr=""
        ),
    )

    malformed = compare_with_upstream(["git"], tmp_path)
    assert malformed.verified is False
    assert "merge-base returned an invalid object ID" in (malformed.error or "")

    calls = 0

    def negative_run(cmd, **kwargs):
        nonlocal calls
        calls += 1
        output = f"{'b' * 40}\n" if calls == 1 else "-1 0\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr="")

    monkeypatch.setattr(uv.subprocess, "run", negative_run)
    negative = compare_with_upstream(["git"], tmp_path)
    assert negative.verified is False
    assert "negative" in (negative.error or "")


def test_write_update_result_supports_concurrent_writers(tmp_path):
    def write(index):
        return write_update_result(tmp_path, {"status": "started", "index": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(write, range(32)))

    assert all(path == tmp_path / ".update_result.json" for path in paths)
    final = json.loads((tmp_path / ".update_result.json").read_text())
    assert final["status"] == "started"
    assert final["index"] in range(32)
    assert not list(tmp_path.glob(".update_result.json.tmp*"))
