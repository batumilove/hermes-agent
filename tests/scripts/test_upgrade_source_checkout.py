"""Real Git authentication and checkout routing for release-to-source upgrade rehearsal."""
from __future__ import annotations

from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import fork_provenance as provenance
from scripts.ci import prepare_upgrade_source as upgrade


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20, check=check,
    ).stdout.strip()


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "upstream")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "app.txt").write_text("original\n", encoding="utf-8")
    original = commit(repo, "original")
    git(repo, "tag", "-am", "old release", "v2026.4.16", original)
    git(repo, "checkout", "-qb", "old-fork")
    (repo / ".github").mkdir()
    (repo / provenance.BASE_PATH).write_text(original + "\n", encoding="utf-8")
    (repo / "fork.txt").write_text("retained fork\n", encoding="utf-8")
    old_fork = commit(repo, "old fork")
    git(repo, "checkout", "upstream")
    (repo / "app.txt").write_text("release\n", encoding="utf-8")
    release = commit(repo, "release")
    tag = "v2026.9.24"
    git(repo, "tag", "-am", "release", tag)
    baseline = upgrade.Release(tag, git(repo, "rev-parse", tag), release)
    (repo / "app.txt").write_text("current upstream\n", encoding="utf-8")
    base = commit(repo, "upstream base")
    official = tmp_path / "official.git"
    git(repo, "clone", "--bare", "--single-branch", "--branch", "upstream", str(repo), str(official))
    git(repo, "checkout", "-qb", "source", old_fork)
    git(repo, "merge", "--no-ff", "-m", "reconcile", base)
    (repo / provenance.BASE_PATH).write_text(base + "\n", encoding="utf-8")
    source = commit(repo, "source binding")
    tree = provenance.projected_tree(repo, source)
    git(repo, "checkout", "-qb", "review", old_fork)
    git(repo, "checkout", source, "--", ".")
    commit(repo, "linear import")
    proof = {"version": 2, "upstream_base": base, "source_head": source,
             "import_tree": tree, "fetch_depth": 8}
    (repo / provenance.PROVENANCE_PATH).write_text(json.dumps(proof), encoding="utf-8")
    review = commit(repo, "proof")
    return {"repo": repo, "official": official, "baseline": baseline, "source": source,
            "review": review, "base": base, "old_fork": old_fork, "proof": proof}


def prepare(world):
    return upgrade.prepare_upgrade_source(
        world["repo"], upgrade.FORK, upstream=world["official"].as_uri(), release=world["baseline"],
    )


def test_verified_review_routes_to_exact_source_and_cli_publishes_release_override(world, tmp_path, monkeypatch):
    repo = world["repo"]
    expected = provenance.projected_tree(repo, world["review"])
    env_file = tmp_path / "github-env"
    monkeypatch.setattr(upgrade, "prepare_upgrade_source", partial(
        upgrade.prepare_upgrade_source, upstream=world["official"].as_uri(), release=world["baseline"],
    ))
    assert upgrade.main(["--repo", str(repo), "--repository", upgrade.FORK, "--github-env", str(env_file)]) == 0
    assert git(repo, "rev-parse", "HEAD") == world["source"]
    assert git(repo, "branch", "--show-current") == ""
    assert git(repo, "rev-parse", "review") == world["review"]
    assert provenance.projected_tree(repo, "HEAD") == expected
    assert env_file.read_text(encoding="utf-8") == f"HERMES_E2E_UPGRADE_BASE={world['baseline'].commit}\n"


@pytest.mark.parametrize("later_descendant", [False, True])
def test_review_only_clone_fetches_missing_authentic_source_and_release(world, tmp_path, later_descendant):
    if later_descendant:
        (world["repo"] / "app.txt").write_text("ordinary feature\n", encoding="utf-8")
        candidate = commit(world["repo"], "ordinary descendant")
    else:
        candidate = world["review"]
    clone = tmp_path / "review-only"
    git(world["repo"], "clone", "--no-tags", "--single-branch", "--branch", "review",
        world["repo"].as_uri(), str(clone))
    missing = subprocess.run(["git", "cat-file", "-e", f"{world['source']}^{{commit}}"],
                             cwd=clone, capture_output=True, timeout=20, check=False)
    assert missing.returncode != 0
    receipt = upgrade.prepare_upgrade_source(clone, upgrade.FORK, upstream=world["official"].as_uri(), release=world["baseline"])
    assert receipt["review_head"] == candidate and receipt["source_head"] == world["source"]
    expected = candidate if later_descendant else world["source"]
    assert receipt["checkout_head"] == git(clone, "rev-parse", "HEAD") == expected
    assert provenance.is_ancestor(clone, world["baseline"].commit, world["source"])
    assert git(clone, "branch", "--show-current") == ("review" if later_descendant else "")
    assert git(clone, "rev-parse", "--is-shallow-repository") == "false"


@pytest.mark.parametrize("mode", ["upstream", "no-proof"])
def test_upstream_and_no_proof_checkout_are_unchanged(world, mode):
    repo = world["repo"]
    repository = "NousResearch/hermes-agent"
    if mode == "no-proof":
        (repo / provenance.PROVENANCE_PATH).unlink()
        commit(repo, "source without proof")
        repository = upgrade.FORK
    before = git(repo, "rev-parse", "HEAD")
    assert upgrade.prepare_upgrade_source(repo, repository, upstream="unreachable") is None
    assert git(repo, "rev-parse", "HEAD") == before
    assert not (repo / ".git" / "FETCH_HEAD").exists()


@pytest.mark.parametrize("mutation", ["extra-field", "wrong-source", "wrong-base"])
@pytest.mark.parametrize("later_descendant", [False, True])
def test_invalid_binding_fails_without_changing_checkout(world, mutation, later_descendant):
    repo, proof = world["repo"], dict(world["proof"])
    if mutation == "extra-field":
        proof["ignored"] = True
    elif mutation == "wrong-source":
        git(repo, "checkout", "--detach", world["source"])
        (repo / "fork.txt").write_text("unreviewed source\n", encoding="utf-8")
        proof["source_head"] = commit(repo, "different source")
        git(repo, "checkout", "review")
    elif mutation == "wrong-base":
        proof["upstream_base"] = world["old_fork"]
    (repo / provenance.PROVENANCE_PATH).write_text(json.dumps(proof), encoding="utf-8")
    if later_descendant:
        (repo / "app.txt").write_text("ordinary later feature\n", encoding="utf-8")
    before = commit(repo, "changed review")
    with pytest.raises(provenance.ProvenanceError):
        prepare(world)
    assert git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("detached", [False, True])
def test_valid_later_descendant_keeps_current_code_and_publishes_verified_baseline(world, tmp_path, monkeypatch, detached):
    repo = world["repo"]
    (repo / "app.txt").write_text("new ordinary fork feature\n", encoding="utf-8")
    later = commit(repo, "ordinary descendant")
    if detached:
        git(repo, "checkout", "--detach", later)
    branch = git(repo, "branch", "--show-current")
    env_file = tmp_path / "github-env"
    monkeypatch.setattr(upgrade, "prepare_upgrade_source", partial(
        upgrade.prepare_upgrade_source, upstream=world["official"].as_uri(), release=world["baseline"],
    ))
    assert upgrade.main(["--repo", str(repo), "--repository", upgrade.FORK, "--github-env", str(env_file)]) == 0
    assert git(repo, "rev-parse", "HEAD") == later
    assert git(repo, "branch", "--show-current") == branch
    assert (repo / "app.txt").read_text(encoding="utf-8") == "new ordinary fork feature\n"
    assert provenance.projected_tree(repo, later) != world["proof"]["import_tree"]
    assert env_file.read_text(encoding="utf-8") == f"HERMES_E2E_UPGRADE_BASE={world['baseline'].commit}\n"
    assert git(repo, "describe", "--tags", "--abbrev=0", "HEAD~1") == "v2026.4.16"


@pytest.mark.parametrize("later_descendant", [False, True])
def test_replaced_canonical_release_tag_is_rejected_before_checkout(world, later_descendant):
    if later_descendant:
        (world["repo"] / "app.txt").write_text("ordinary feature\n", encoding="utf-8")
        commit(world["repo"], "ordinary descendant")
    before = git(world["repo"], "rev-parse", "HEAD")
    official = world["official"]
    git(official, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "tag", "-fam", "replaced release", world["baseline"].tag, world["base"])
    with pytest.raises(provenance.ProvenanceError, match="pinned object"):
        prepare(world)
    assert git(world["repo"], "rev-parse", "HEAD") == before


@pytest.mark.parametrize("later_descendant", [False, True])
@pytest.mark.parametrize("invalid", ["commit", "ancestry"])
def test_release_commit_and_ancestry_are_authenticated_before_env_publication(world, tmp_path, monkeypatch,
                                                                            later_descendant, invalid):
    repo = world["repo"]
    if later_descendant:
        (repo / "app.txt").write_text("ordinary feature\n", encoding="utf-8")
        commit(repo, "ordinary descendant")
    before = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")
    pinned = world["baseline"]
    if invalid == "commit":
        pinned = upgrade.Release(pinned.tag, pinned.tag_object, world["base"])
    else:
        # Authentic exact tag and commit, but on a separate canonical branch.
        official = world["official"]
        tree = git(official, "rev-parse", f"{world['base']}^{{tree}}")
        separate = git(official, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                       "commit-tree", tree, "-m", "unrelated release")
        git(official, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "tag", "-am", "unrelated release", "unrelated-release", separate)
        pinned = upgrade.Release("unrelated-release", git(official, "rev-parse", "unrelated-release"), separate)
    monkeypatch.setattr(upgrade, "prepare_upgrade_source", partial(
        upgrade.prepare_upgrade_source, upstream=world["official"].as_uri(), release=pinned,
    ))
    env_file = tmp_path / "github-env"
    env_file.write_text("EXISTING=value\n", encoding="utf-8")
    assert upgrade.main(["--repo", str(repo), "--repository", upgrade.FORK, "--github-env", str(env_file)]) == 1
    assert env_file.read_text(encoding="utf-8") == "EXISTING=value\n"
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "branch", "--show-current") == branch


def test_real_fast_forward_distinguishes_source_rehearsal_and_linear_fork_publication(world):
    repo = world["repo"]
    git(repo, "checkout", "--detach", world["baseline"].commit)
    refused = subprocess.run(["git", "merge", "--ff-only", world["review"]], cwd=repo,
                             capture_output=True, timeout=20, check=False)
    assert refused.returncode != 0
    assert git(repo, "rev-parse", "HEAD") == world["baseline"].commit
    git(repo, "merge", "--ff-only", world["source"])
    assert git(repo, "rev-parse", "HEAD") == world["source"]
    git(repo, "checkout", "--detach", world["old_fork"])
    git(repo, "merge", "--ff-only", world["review"])
    assert git(repo, "rev-parse", "HEAD") == world["review"]


def test_git_installer_and_base_share_explicit_release_and_keep_legacy_default(world, monkeypatch):
    from tests.e2e.core.upgrade.git import _git_world as git_world

    monkeypatch.setattr(git_world.H, "WORKTREE", world["repo"])
    monkeypatch.delenv("HERMES_E2E_UPGRADE_BASE", raising=False)
    monkeypatch.delenv("HERMES_E2E_N1_REF", raising=False)
    assert git_world.n1_tag() == "v2026.4.16"
    monkeypatch.setenv("HERMES_E2E_UPGRADE_BASE", world["baseline"].commit)
    assert git_world.n1_tag() == git_world.n1_base() == world["baseline"].commit
    monkeypatch.setenv("HERMES_E2E_N1_REF", world["old_fork"])
    assert git_world.n1_base() == world["old_fork"]
    assert git_world.n1_tag() == world["baseline"].commit


def test_canonical_runner_forwards_release_override_and_scrubs_credentials(tmp_path):
    child = tmp_path / "test_release_environment.py"
    child.write_text(
        "import os\ndef test_release_environment():\n"
        f"    assert os.environ['HERMES_E2E_UPGRADE_BASE'] == '{upgrade.BASELINE.commit}'\n"
        "    assert 'OPENAI_API_KEY' not in os.environ\n", encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "HERMES_PYTHON": sys.executable, "HERMES_E2E_UPGRADE_BASE": upgrade.BASELINE.commit,
           "OPENAI_API_KEY": "fixture-secret", "HERMES_TEST_FILE_RETRIES": "0"}
    result = subprocess.run([str(root / "scripts/run_tests.sh"), "-j", "1", str(child)], cwd=root,
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=25, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
