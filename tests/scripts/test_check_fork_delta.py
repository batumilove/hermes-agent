from __future__ import annotations

import importlib.util
import json
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


def _squash_sync_fixture(
    tmp_path: Path,
    *,
    source_matches_squash: bool = True,
) -> tuple[Path, Path, str]:
    repo = tmp_path / "squash-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "upstream.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "upstream base")
    upstream_base = _git(repo, "rev-parse", "HEAD")

    (repo / "src").mkdir()
    (repo / "src" / "owned.py").write_text("owned = True\n", encoding="utf-8")
    (repo / ".github").mkdir()
    (repo / ".github" / "upstream-base").write_text(
        f"{upstream_base}\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "reconciled source")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    _git(repo, "checkout", "--orphan", "live")
    _git(repo, "rm", "-qrf", ".")
    (repo / "fork-parent.txt").write_text("fork\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fork parent")
    fork_parent = _git(repo, "rev-parse", "HEAD")

    squash_tree = source_tree
    if not source_matches_squash:
        (repo / "tampered.txt").write_text("tampered\n", encoding="utf-8")
        _git(repo, "add", ".")
        squash_tree = _git(repo, "write-tree")
    squash_commit = _git(
        repo,
        "commit-tree",
        squash_tree,
        "-p",
        fork_parent,
        "-m",
        "squash sync",
    )
    _git(repo, "reset", "--hard", squash_commit)

    github = repo / ".github"
    github.mkdir(exist_ok=True)
    (github / "upstream-base").write_text(f"{upstream_base}\n", encoding="utf-8")
    (github / "upstream-sync-provenance.json").write_text(
        (
            "{\n"
            '  "version": 1,\n'
            f'  "upstream_base": "{upstream_base}",\n'
            f'  "source_head": "{source_head}",\n'
            f'  "squash_commit": "{squash_commit}",\n'
            '  "fetch_depth": 8\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    manifest = {
        "version": 1,
        "upstream": {
            "repository": "example/project",
            "branch": "main",
            "base_file": ".github/upstream-base",
            "provenance_file": ".github/upstream-sync-provenance.json",
        },
        "patches": [
            {
                "id": "owned",
                "kind": "operations",
                "rationale": "fixture",
                "paths": [".github/**", "src/**"],
                "tests": ["pytest"],
                "retirement": "when upstream is equivalent",
            }
        ],
    }
    manifest_path = github / "batumi-patches.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "record squash provenance")
    return repo, manifest_path, squash_commit


def _rewrite_provenance(repo: Path, **updates: str) -> None:
    path = repo / ".github" / "upstream-sync-provenance.json"
    provenance = json.loads(path.read_text(encoding="utf-8"))
    provenance.update(updates)
    path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


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


def test_check_delta_accepts_verified_squash_sync_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, squash_commit = _squash_sync_fixture(tmp_path)
    monkeypatch.setattr(MODULE, "ROOT", repo)

    report = MODULE.check_delta(manifest, cwd=repo)

    assert report["provenance"] == "squash-sync"
    assert report["contributor_base"] == squash_commit
    assert report["unexplained"] == []


def test_check_delta_rejects_squash_sync_tree_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, _ = _squash_sync_fixture(tmp_path, source_matches_squash=False)
    monkeypatch.setattr(MODULE, "ROOT", repo)

    with pytest.raises(MODULE.DeltaError, match="source and squash trees differ"):
        MODULE.check_delta(manifest, cwd=repo)


def test_resolve_contributor_base_uses_verified_squash_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, squash_commit = _squash_sync_fixture(tmp_path)
    monkeypatch.setattr(MODULE, "ROOT", repo)

    assert MODULE.resolve_contributor_base(manifest, cwd=repo) == squash_commit


def test_fetch_provenance_source_skips_network_when_object_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, _ = _squash_sync_fixture(tmp_path)
    monkeypatch.setattr(MODULE, "ROOT", repo)
    original_git = MODULE._git

    def no_fetch(*args: str, cwd: Path) -> str:
        assert args[0] != "fetch"
        return original_git(*args, cwd=cwd)

    monkeypatch.setattr(MODULE, "_git", no_fetch)

    assert MODULE.fetch_provenance_source(manifest, cwd=repo)


def test_fetch_provenance_source_skips_obsolete_proof_for_direct_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest = _fixture_repo(tmp_path, ["**"])
    raw_manifest = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw_manifest["upstream"]["provenance_file"] = (
        ".github/upstream-sync-provenance.json"
    )
    manifest.write_text(yaml.safe_dump(raw_manifest), encoding="utf-8")
    (repo / ".github" / "upstream-sync-provenance.json").write_text(
        json.dumps(
            {
                "version": 1,
                "upstream_base": "0" * 40,
                "source_head": "1" * 40,
                "squash_commit": "2" * 40,
                "fetch_depth": 8,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "ROOT", repo)
    original_git = MODULE._git

    def no_fetch(*args: str, cwd: Path) -> str:
        assert args[0] != "fetch"
        return original_git(*args, cwd=cwd)

    monkeypatch.setattr(MODULE, "_git", no_fetch)

    assert MODULE.fetch_provenance_source(manifest, cwd=repo) is None


def test_fetch_provenance_source_uses_bounded_filtered_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, _ = _squash_sync_fixture(tmp_path)
    monkeypatch.setattr(MODULE, "ROOT", repo)
    original_git = MODULE._git
    calls: list[tuple[str, ...]] = []
    source = json.loads(
        (repo / ".github" / "upstream-sync-provenance.json").read_text(
            encoding="utf-8"
        )
    )["source_head"]

    def missing_source(*args: str, cwd: Path) -> str:
        if args[0] == "cat-file":
            raise MODULE.DeltaError("missing")
        if args[0] == "fetch":
            calls.append(args)
            return ""
        return original_git(*args, cwd=cwd)

    monkeypatch.setattr(MODULE, "_git", missing_source)

    MODULE.fetch_provenance_source(manifest, cwd=repo)

    assert calls == [
        (
            "fetch",
            "--depth=8",
            "--filter=blob:none",
            "--no-tags",
            "origin",
            source,
        )
    ]


def test_check_delta_rejects_source_without_recorded_upstream_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, squash_commit = _squash_sync_fixture(tmp_path)
    _rewrite_provenance(repo, source_head=squash_commit)
    monkeypatch.setattr(MODULE, "ROOT", repo)

    with pytest.raises(MODULE.DeltaError, match="upstream base is not an ancestor"):
        MODULE.check_delta(manifest, cwd=repo)


def test_check_delta_rejects_squash_commit_outside_head_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manifest, _ = _squash_sync_fixture(tmp_path)
    source_head = json.loads(
        (repo / ".github" / "upstream-sync-provenance.json").read_text(
            encoding="utf-8"
        )
    )["source_head"]
    _rewrite_provenance(repo, squash_commit=source_head)
    monkeypatch.setattr(MODULE, "ROOT", repo)

    with pytest.raises(MODULE.DeltaError, match="squash commit is not an ancestor"):
        MODULE.check_delta(manifest, cwd=repo)


def test_manifest_rejects_duplicate_patch_ids(tmp_path: Path) -> None:
    manifest = yaml.safe_load((ROOT / ".github" / "batumi-patches.yaml").read_text())
    manifest["patches"].append(dict(manifest["patches"][0]))
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(MODULE.DeltaError, match="duplicate patch id"):
        MODULE.load_manifest(path)
