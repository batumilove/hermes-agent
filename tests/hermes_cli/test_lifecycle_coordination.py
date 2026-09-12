from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone

import pytest

from gateway.lifecycle_lease import inspect_lifecycle_lease
from hermes_cli import lifecycle_coordination as coordination


def _git(repo, *args):
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True
    ).strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Lifecycle Test"], cwd=repo, check=True
    )
    (repo / "source.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    return repo


def test_acquire_cli_lifecycle_binds_exact_source_and_manifest(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    captured = {}
    sentinel = object()

    def fake_acquire(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(coordination, "acquire_lifecycle_lease", fake_acquire)
    monkeypatch.setattr(coordination.uuid, "uuid4", lambda: type("U", (), {"hex": "1" * 32})())
    now = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)

    result = coordination.acquire_cli_lifecycle_transaction(
        home=tmp_path / "home",
        repo_root=repo,
        purpose="deployment",
        operation="hermes-update",
        now=now,
    )

    assert result is sentinel
    assert captured["home"] == tmp_path / "home"
    assert captured["owner_token"] == "deployment-" + "1" * 32
    assert captured["purpose"] == "deployment"
    assert captured["provenance"]["source_head"] == _git(repo, "rev-parse", "HEAD")
    assert captured["provenance"]["source_tree"] == _git(
        repo, "rev-parse", "HEAD^{tree}"
    )
    manifest = {
        "evidence_id": captured["owner_token"],
        "operation": "hermes-update",
        "purpose": "deployment",
        "schema": "hermes-lifecycle-cli-transaction/v1",
        "source_head": captured["provenance"]["source_head"],
        "source_tree": captured["provenance"]["source_tree"],
    }
    encoded = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    assert captured["provenance"]["artifact_sha256"] == hashlib.sha256(
        encoded
    ).hexdigest()
    assert captured["provenance"]["evidence_id"] == captured["owner_token"]
    assert captured["now"] == now
    assert captured["expires_at"] > now


def test_acquire_cli_lifecycle_fails_closed_before_lease_for_unknown_source(
    tmp_path, monkeypatch
):
    called = False

    def fake_acquire(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(coordination, "acquire_lifecycle_lease", fake_acquire)

    with pytest.raises(coordination.LifecycleCoordinationBlocked, match="source identity"):
        coordination.acquire_cli_lifecycle_transaction(
            home=tmp_path / "home",
            repo_root=tmp_path / "not-a-repo",
            purpose="deployment",
            operation="hermes-update",
        )

    assert called is False


def test_acquire_cli_lifecycle_translates_common_lease_block(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    def blocked(**kwargs):
        raise coordination.LifecycleLeaseBlocked("owned by soak")

    monkeypatch.setattr(coordination, "acquire_lifecycle_lease", blocked)

    with pytest.raises(coordination.LifecycleCoordinationBlocked, match="owned by soak"):
        coordination.acquire_cli_lifecycle_transaction(
            home=tmp_path / "home",
            repo_root=repo,
            purpose="deployment",
            operation="hermes-update",
        )


def test_acquire_cli_lifecycle_publishes_and_releases_real_common_lease(tmp_path):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    lease = coordination.acquire_cli_lifecycle_transaction(
        home=home,
        repo_root=repo,
        purpose="deployment",
        operation="hermes-update",
    )
    try:
        state = inspect_lifecycle_lease(home=home)
        assert state["status"] == "active"
        metadata = state["metadata"]
        assert metadata is not None
        assert metadata["purpose"] == "deployment"
        assert metadata["owner_token"].startswith("deployment-")
        assert metadata["provenance"]["source_head"] == _git(
            repo, "rev-parse", "HEAD"
        )
    finally:
        lease.release()

    assert inspect_lifecycle_lease(home=home)["status"] == "absent"
