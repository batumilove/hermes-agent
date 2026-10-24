"""Official catalog imports authenticate Git objects; ordinary mixed PRs fail closed."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import fork_provenance
from scripts.ci.catalog_upstream_import import verify_import


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True, timeout=30).stdout.strip()


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def imported(tmp_path, catalog="official\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "plugin-catalog").mkdir()
    entry = repo / "plugin-catalog/example.yaml"
    entry.write_text("original\n")
    original = commit(repo, "original official")
    (repo / ".github").mkdir()
    (repo / fork_provenance.BASE_PATH).write_text(original + "\n")
    previous = commit(repo, "fork target")
    git(repo, "checkout", "-qb", "official", original)
    entry.write_text("official\n")
    official = commit(repo, "official catalog admission")
    git(repo, "checkout", "-qb", "source", previous)
    git(repo, "merge", "--no-ff", "official", "-m", "import upstream")
    (repo / fork_provenance.BASE_PATH).write_text(official + "\n")
    (repo / "core.py").write_text("fork adaptation\n")
    entry.write_text(catalog)
    source = commit(repo, "reconcile")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    git(repo, "checkout", "-qb", "review", previous)
    git(repo, "read-tree", "--reset", "-u", source)
    commit(repo, "linear import")
    proof = dict(version=2, upstream_base=official, source_head=source, import_tree=tree, fetch_depth=8)
    (repo / fork_provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
    head = commit(repo, "receipt")
    return repo, previous, official, head


@pytest.mark.parametrize("merge_commit", [False, True])
def test_exact_official_catalog_and_source_are_accepted(tmp_path, merge_commit):
    repo, previous, official, head = imported(tmp_path)
    if merge_commit:
        git(repo, "checkout", "main")
        git(repo, "merge", "--no-ff", "review", "-m", "PR merge")
        head = "HEAD"
    # Worktree content cannot substitute for the committed catalog.
    (repo / "plugin-catalog/example.yaml").write_text("uncommitted\n")
    assert verify_import(repo, head, previous, official) == official


def test_valid_receipt_does_not_admit_a_modified_official_catalog(tmp_path):
    repo, previous, official, head = imported(tmp_path, catalog="unreviewed plugin pin\n")
    with pytest.raises(fork_provenance.ProvenanceError, match="catalog tree differs"):
        verify_import(repo, head, previous, official)


def test_self_declared_upstream_outside_trusted_history_is_rejected(tmp_path):
    repo, previous, _official, head = imported(tmp_path)
    with pytest.raises(fork_provenance.ProvenanceError, match="outside trusted"):
        verify_import(repo, head, previous, previous)


@pytest.mark.parametrize("mutation", ["receipt", "descendant", "no-advance"])
def test_receipt_cannot_authorize_a_different_import(tmp_path, mutation):
    repo, previous, official, head = imported(tmp_path)
    if mutation == "receipt":
        path = repo / fork_provenance.PROVENANCE_PATH
        proof = json.loads(path.read_text())
        proof["import_tree"] = git(repo, "rev-parse", f"{previous}^{{tree}}")
        path.write_text(json.dumps(proof))
        head = commit(repo, "forged receipt")
    elif mutation == "descendant":
        (repo / "core.py").write_text("later change\n")
        head = commit(repo, "ordinary core PR")
    else:
        previous = head
    with pytest.raises(fork_provenance.ProvenanceError):
        verify_import(repo, head, previous, official)


@pytest.mark.parametrize("kind", ["official", "catalog-only", "mixed"])
def test_workflow_routes_only_authenticated_imports_away_from_readmission(tmp_path, kind):
    repo, previous, official, _head = imported(tmp_path)
    root = Path(__file__).resolve().parents[2]
    remote = tmp_path / "official.git"
    git(repo, "clone", "--bare", "--single-branch", "--branch", "official", str(repo), str(remote))
    git(remote, "update-ref", "refs/heads/main", official)
    git(repo, "config", f"url.{remote.as_uri()}.insteadOf", "https://github.com/NousResearch/hermes-agent.git")
    if kind != "official":
        git(repo, "checkout", "-qb", "admission", previous)
        (repo / "plugin-catalog/example.yaml").write_text("new admission\n")
        if kind == "mixed":
            (repo / "core.py").write_text("unproven core change\n")
        commit(repo, "admission")
    branch = git(repo, "branch", "--show-current")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", branch, "-m", "PR merge")
    for relative in ("scripts/fork_provenance.py", "scripts/ci/catalog_upstream_import.py"):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)
    workflow = yaml.safe_load((root / ".github/workflows/plugin-catalog-ci.yml").read_text())
    steps = workflow["jobs"]["pinned-source-validate"]["steps"]
    command = next(step["run"] for step in steps if step.get("id") == "changed")
    output = tmp_path / "outputs"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", command], cwd=repo,
                            env={**os.environ, "GITHUB_OUTPUT": str(output)},
                            capture_output=True, text=True, timeout=30)
    if kind == "mixed":
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"official_import={str(kind == 'official').lower()}\n" in output.read_text()
        assert "plugin-catalog/example.yaml" in output.read_text()
