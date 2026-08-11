from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_fork_delta.py"
SPEC = importlib.util.spec_from_file_location("check_fork_delta", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fixture_repo(tmp_path: Path, patterns: list[str]) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / ".github").mkdir()
    (repo / ".github" / "upstream-base").write_text(f"{base}\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "upstream": {
            "repository": "example/project",
            "branch": "main",
            "base_file": ".github/upstream-base",
        },
        "patches": [
            {
                "id": "owned",
                "kind": "core",
                "rationale": "fixture",
                "paths": patterns,
                "tests": ["pytest"],
                "retirement": "when upstream is equivalent",
            }
        ],
    }
    manifest_path = repo / ".github" / "batumi-patches.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "owned.py").write_text("owned = True\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fork delta")
    return repo, manifest_path


def _reconciled_fixture_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """Build a fork head whose trusted upstream base is not its ancestor."""
    repo = tmp_path / "reconciled-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "trusted upstream")
    upstream_base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "--orphan", "fork", "-q")
    _git(repo, "rm", "-rf", "--cached", ".")
    for path in repo.iterdir():
        if path.name != ".git" and path.is_file():
            path.unlink()
    (repo / ".github").mkdir()
    (repo / ".github" / "upstream-base").write_text(
        f"{upstream_base}\n", encoding="utf-8"
    )
    manifest = {
        "version": 1,
        "upstream": {
            "repository": "example/project",
            "branch": "main",
            "base_file": ".github/upstream-base",
        },
        "patches": [
            {
                "id": "owned",
                "kind": "core",
                "rationale": "fixture",
                "paths": [".github/**", "fork.txt", "upstream.txt"],
                "tests": ["pytest"],
                "retirement": "when upstream is equivalent",
            }
        ],
    }
    manifest_path = repo / ".github" / "batumi-patches.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (repo / "fork.txt").write_text("fork\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "reconciled fork")
    return repo, manifest_path, upstream_base


def test_classify_paths_reports_every_matching_owner() -> None:
    owners = [
        MODULE.PatchOwner("broad", "core", ("src/**",)),
        MODULE.PatchOwner("exact", "core", ("src/example.py",)),
    ]
    report = MODULE.classify_paths(["src/example.py", "other.txt"], owners)
    assert report["broad"] == ["src/example.py"]
    assert report["exact"] == ["src/example.py"]
    assert report["unexplained"] == ["other.txt"]


def test_check_delta_reports_owned_and_unexplained_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest = _fixture_repo(tmp_path, ["src/**", ".github/**"])
    monkeypatch.setattr(MODULE, "ROOT", repo)
    report = MODULE.check_delta(manifest, cwd=repo)
    assert "src/owned.py" in report["patches"]["owned"]
    assert report["unexplained"] == ["outside.txt"]


def test_check_delta_passes_when_all_paths_are_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest = _fixture_repo(tmp_path, ["src/**", ".github/**", "outside.txt"])
    monkeypatch.setattr(MODULE, "ROOT", repo)
    report = MODULE.check_delta(manifest, cwd=repo)
    assert report["unexplained"] == []


def test_check_delta_accepts_trusted_upstream_base_outside_fork_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, upstream_base = _reconciled_fixture_repo(tmp_path)
    monkeypatch.setattr(MODULE, "ROOT", repo)

    report = MODULE.check_delta(
        manifest,
        cwd=repo,
        upstream_ref=upstream_base,
    )

    assert report["base"] == upstream_base
    assert report["unexplained"] == []


def test_manifest_rejects_duplicate_patch_ids(tmp_path: Path) -> None:
    manifest = yaml.safe_load((ROOT / ".github" / "batumi-patches.yaml").read_text())
    manifest["patches"].append(dict(manifest["patches"][0]))
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(MODULE.DeltaError, match="duplicate patch id"):
        MODULE.load_manifest(path)
