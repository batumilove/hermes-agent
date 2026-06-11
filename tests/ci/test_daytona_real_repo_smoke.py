import json
import os
import re
import tarfile
from pathlib import Path

import pytest

from scripts.ci import daytona_real_repo_smoke as smoke


LEAK_PATTERNS = [
    re.compile(r"DAYTONA_API_KEY\s*=\s*(?!<redacted>)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"Authorization:\s*Bearer\s+(?!<redacted>)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"Bearer\s+(?!<redacted>)[A-Za-z0-9._~+\-/=]{8,}", re.IGNORECASE),
]


def assert_no_artifact_leaks(run_dir: Path, *, raw_secrets: list[str]) -> None:
    for path in run_dir.iterdir():
        if path.suffix == ".tar":
            continue
        text = path.read_text(encoding="utf-8")
        for secret in raw_secrets:
            assert secret not in text, f"raw secret leaked in {path.name}"
        for pattern in LEAK_PATTERNS:
            assert not pattern.search(text), f"token-like value leaked in {path.name}: {pattern.pattern}"


def test_dry_run_preserves_manual_artifact_layout_and_redacts_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTONA_API_KEY", "super-secret-token")
    monkeypatch.setenv("DAYTONA_API_URL", "http://daytona.local:3000")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    (repo / "module.py").write_text("print('ok')\n", encoding="utf-8")

    run_dir = tmp_path / "artifacts"
    code = smoke.run_smoke(
        repo_root=repo,
        run_dir=run_dir,
        mode="dry-run",
        run_id="20260611T061200Z-test",
        tests=["python3 --version"],
    )

    assert code == 0
    expected = {
        "manifest.json",
        "commands.ndjson",
        "transcript.log",
        "cleanup.json",
        "stale-check.json",
        "hermes-agent-head.tar",
    }
    assert expected == {path.name for path in run_dir.iterdir()}

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "dry-run"
    assert manifest["cleanup"]["cleanup"] == "dry-run"
    assert manifest["stale_check"]["active"] == []
    assert manifest["sandbox_home"] == "/root"
    assert manifest["remote_repo"] == "/root/hermes-agent"
    assert "daytona_api_key" not in json.dumps(manifest).lower()
    assert "super-secret-token" not in (run_dir / "manifest.json").read_text(encoding="utf-8")
    assert "super-secret-token" not in (run_dir / "commands.ndjson").read_text(encoding="utf-8")
    assert "super-secret-token" not in (run_dir / "transcript.log").read_text(encoding="utf-8")
    assert_no_artifact_leaks(run_dir, raw_secrets=["super-secret-token"])

    with tarfile.open(run_dir / "hermes-agent-head.tar") as archive:
        assert "module.py" in archive.getnames()


def test_remote_plan_discovers_home_and_uses_venv_commands():
    plan = smoke.build_remote_plan(
        sandbox_home="/srv/daytona",
        archive_remote_path="/tmp/hermes-agent-head.tar",
        tests=["python3 -m pytest tests/tools/test_daytona_environment.py -q -o addopts="],
    )

    commands = [step.cmd for step in plan]
    assert commands[0] == "printf '%s\\n' \"$HOME\""
    assert commands[1] == "mkdir -p /srv/daytona/hermes-agent && tar -xf /tmp/hermes-agent-head.tar -C /srv/daytona/hermes-agent"
    assert any("python3 -m venv .venv" == cmd for cmd in commands)
    assert any(".venv/bin/python -m pip install" in cmd and "--root-user-action" not in cmd for cmd in commands)
    assert commands[-1].startswith(".venv/bin/python -m pytest")


def test_command_artifact_redacts_secret_like_values(tmp_path):
    writer = smoke.ArtifactWriter(tmp_path)
    result = smoke.CommandResult(
        phase="remote",
        cmd="echo token",
        cwd="/root/hermes-agent",
        timeout=10,
        duration_s=0.01,
        returncode=0,
        output="DAYTONA_API_KEY=abc123 and Bearer secret-token",
    )

    writer.record_command(result)
    text = (tmp_path / "commands.ndjson").read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "secret-token" not in text
    assert "DAYTONA_API_KEY=<redacted>" in text
    assert "Bearer <redacted>" in text


def test_daytona_mode_fails_closed_when_api_key_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.setenv("DAYTONA_API_URL", "http://daytona.local:3000")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("print('ok')\n", encoding="utf-8")

    run_dir = tmp_path / "artifacts"
    code = smoke.run_smoke(
        repo_root=repo,
        run_dir=run_dir,
        mode="daytona",
        run_id="20260611T070000Z-missing-key",
        tests=["python3 --version"],
    )

    assert code == 1
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "daytona"
    assert manifest["status"] == "failed"
    assert manifest["error"] == "DAYTONA_API_KEY is required for --mode daytona"


def test_dry_run_remains_available_without_daytona_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.setenv("DAYTONA_API_URL", "http://daytona.local:3000")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("print('ok')\n", encoding="utf-8")

    run_dir = tmp_path / "artifacts"
    code = smoke.run_smoke(
        repo_root=repo,
        run_dir=run_dir,
        mode="dry-run",
        run_id="20260611T070100Z-dry-run-no-key",
        tests=["python3 --version"],
    )

    assert code == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "dry-run"
    assert manifest["status"] == "passed"
