"""Real Git squash/rebase imports retain exact provenance and ordinary PR ratchets."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import check_fork_delta, fork_provenance
from scripts.fork_check_base import accepted_upstream, comparison_base
from scripts.fork_contributors import new_author_emails


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=30, check=True).stdout.strip()


def commit(repo, message, email="importer@example.test"):
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", f"user.email={email}", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def write_proof(repo, proof):
    (repo / fork_provenance.PROVENANCE_PATH).write_text(json.dumps(proof) + "\n")


@pytest.fixture(params=["linear", "squash", "rebase"])
def imported(tmp_path, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "importer@example.test")
    (repo / "upstream.txt").write_text("original\n")
    original = commit(repo, "original", "upstream@example.test")
    (repo / ".github").mkdir()
    (repo / fork_provenance.BASE_PATH).write_text(original + "\n")
    (repo / "fork.txt").write_text("retained fork\n")
    manifest = {
        "version": 1,
        "upstream": {"repository": "example/upstream", "branch": "main",
                     "base_file": fork_provenance.BASE_PATH, "provenance_file": fork_provenance.PROVENANCE_PATH},
        "patches": [{"id": "fixture", "kind": "core", "rationale": "fixture",
                     "retirement": "fixture", "paths": ["**"], "tests": ["fixture"]}],
    }
    (repo / ".github/batumi-patches.yaml").write_text(yaml.safe_dump(manifest))
    previous = commit(repo, "fork target", "previous@example.test")
    git(repo, "checkout", "-qb", "upstream", original)
    (repo / "upstream.txt").write_text("accepted upstream\n")
    upstream = commit(repo, "accepted upstream", "upstream@example.test")
    git(repo, "checkout", "-qb", "source", previous)
    git(repo, "merge", "--no-ff", "upstream", "-m", "reconcile source")
    (repo / fork_provenance.BASE_PATH).write_text(upstream + "\n")
    source = commit(repo, "record accepted base", "source@example.test")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    proof = dict(version=2, upstream_base=upstream, source_head=source, import_tree=tree, fetch_depth=8)
    git(repo, "checkout", "-qb", "review", previous)
    git(repo, "read-tree", "--reset", "-u", source)
    commit(repo, "linear tree import")
    write_proof(repo, proof)
    commit(repo, "metadata only")
    comparison = previous
    if request.param == "squash":
        git(repo, "checkout", "-qb", "squashed", previous)
        git(repo, "merge", "--squash", "review")
        commit(repo, "protected branch squash")
    elif request.param == "rebase":
        git(repo, "checkout", "-qb", "advanced-target", previous)
        git(repo, "commit", "--allow-empty", "-qm", "advance unchanged target tree")
        comparison = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "review")
        git(repo, "rebase", "--onto", comparison, previous)
    tip = git(repo, "rev-parse", "HEAD")
    return dict(repo=repo, upstream=upstream, source=source, tree=tree, proof=proof,
                previous=comparison, tip=tip, mode=request.param)


def test_squash_rebase_and_later_pr_keep_relationship_ratchet_and_authors(imported):
    repo = imported["repo"]
    upstream = imported["upstream"]
    previous = imported["previous"]
    tip = imported["tip"]
    assert not fork_provenance.is_ancestor(repo, imported["source"], tip)
    assert fork_provenance.resolve_v2(repo, upstream) == tip
    assert accepted_upstream(repo, official_tip="upstream") == upstream
    assert comparison_base(repo, previous, official_tip="upstream") == upstream
    assert new_author_emails(repo, comparison_ref=previous, accepted_base=upstream,
                             official_tip="upstream") == ["importer@example.test"]
    report = check_fork_delta.check_delta(repo / ".github/batumi-patches.yaml", cwd=repo, official_tip="upstream")
    assert report["provenance"] == "tree-import"
    assert report["contributor_base"] == tip
    (repo / "fork.txt").write_text("later improvement\n")
    commit(repo, "ordinary PR", "later@example.test")
    assert fork_provenance.resolve_v2(repo, upstream) == tip
    assert comparison_base(repo, tip, official_tip="upstream") == tip
    assert new_author_emails(repo, comparison_ref=tip, accepted_base=upstream,
                             official_tip="upstream") == ["later@example.test"]


def test_projection_preserves_worktree_index_and_every_other_metadata_path(imported):
    repo = imported["repo"]
    (repo / "fork.txt").write_text("unstaged work\n")
    (repo / "untracked.txt").write_text("untracked\n")
    git(repo, "add", "fork.txt")
    index = (repo / ".git/index").read_bytes()
    assert fork_provenance.projected_tree(repo, imported["tip"]) == imported["tree"]
    assert (repo / ".git/index").read_bytes() == index
    assert (repo / "fork.txt").read_text() == "unstaged work\n"
    assert (repo / "untracked.txt").read_text() == "untracked\n"


def test_wrong_source_tree_and_forged_other_metadata_import_are_rejected(imported):
    repo = imported["repo"]
    proof = imported["proof"]
    git(repo, "checkout", "-qb", "wrong-source", imported["source"])
    (repo / "fork.txt").write_text("different source\n")
    wrong = commit(repo, "wrong source tree")
    git(repo, "checkout", "--detach", imported["tip"])
    write_proof(repo, {**proof, "source_head": wrong})
    with pytest.raises(fork_provenance.ProvenanceError, match="source projected tree differs"):
        fork_provenance.resolve_v2(repo, imported["upstream"])
    git(repo, "reset", "--hard", imported["previous"])
    git(repo, "read-tree", "--reset", "-u", imported["source"])
    (repo / ".github/other-metadata.json").write_text("must remain in the projection\n")
    write_proof(repo, proof)
    commit(repo, "forged import with other metadata changed")
    with pytest.raises(fork_provenance.ProvenanceError, match="no ancestor"):
        fork_provenance.resolve_v2(repo, imported["upstream"])


def test_source_ancestry_and_recorded_base_are_required(imported):
    repo = imported["repo"]
    proof = imported["proof"]
    write_proof(repo, {**proof, "source_head": imported["tip"]})
    with pytest.raises(fork_provenance.ProvenanceError, match="not an ancestor"):
        fork_provenance.resolve_v2(repo, imported["upstream"])
    git(repo, "reset", "--hard", imported["tip"])
    git(repo, "checkout", "-qb", "wrong-base", imported["source"])
    (repo / fork_provenance.BASE_PATH).write_text("0" * 40 + "\n")
    wrong = commit(repo, "wrong recorded base")
    git(repo, "checkout", "--detach", imported["tip"])
    write_proof(repo, {**proof, "source_head": wrong})
    with pytest.raises(fork_provenance.ProvenanceError, match="does not contain"):
        fork_provenance.resolve_v2(repo, imported["upstream"])


def test_full_single_branch_clone_fetches_source_and_base_without_becoming_shallow(imported, tmp_path):
    repo = imported["repo"]
    remote = tmp_path / "remote.git"
    git(repo, "clone", "--bare", str(repo), str(remote))
    branch = git(repo, "branch", "--show-current")
    clone = tmp_path / "single-branch"
    git(repo, "clone", "--single-branch", "--branch", branch, remote.as_uri(), str(clone))
    assert git(clone, "rev-parse", "--is-shallow-repository") == "false"
    absent = subprocess.run(["git", "cat-file", "-e", f"{imported['upstream']}^{{commit}}"],
                            cwd=clone, capture_output=True, timeout=30, check=False)
    assert absent.returncode != 0
    report = check_fork_delta.check_delta(clone / ".github/batumi-patches.yaml", cwd=clone)
    assert report["contributor_base"] == imported["tip"]
    assert git(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert git(clone, "rev-parse", "HEAD") == imported["tip"]
    assert git(clone, "status", "--porcelain") == ""


def test_contributor_cli_needs_only_the_standard_library(imported):
    repo = imported["repo"]
    script = Path(__file__).resolve().parents[2] / "scripts/fork_contributors.py"
    result = subprocess.run([sys.executable, "-S", str(script), "--comparison-ref", imported["previous"],
                             "--accepted-base-file", str(repo / fork_provenance.BASE_PATH)],
                            cwd=repo, capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.splitlines() == ["importer@example.test"]


@pytest.mark.parametrize("field,value", [
    ("extra", True), ("import_tree", "HEAD"), ("source_head", "A" * 40),
    ("upstream_base", "0" * 39), ("fetch_depth", True), ("fetch_depth", 0),
    ("fetch_depth", 1025), ("version", 2.0), ("history", []),
])
def test_v2_schema_rejects_unknown_or_noncanonical_fields(field, value):
    raw = dict(version=2, upstream_base="a" * 40, source_head="b" * 40, import_tree="c" * 40, fetch_depth=8)
    raw[field] = value
    with pytest.raises(fork_provenance.ProvenanceError):
        fork_provenance.validate_v2(raw)


def test_fork_commit_cannot_be_promoted_to_official_upstream_for_ratchet(imported):
    repo = imported["repo"]
    (repo / "fork.txt").write_text("a regression\n")
    regression = commit(repo, "regression")
    (repo / fork_provenance.BASE_PATH).write_text(regression + "\n")
    commit(repo, "attempt to hide regression by promoting fork commit")
    with pytest.raises(fork_provenance.ProvenanceError, match="trusted official upstream"):
        comparison_base(repo, imported["tip"], official_tip="upstream")


def test_target_prior_import_wins_when_head_revisits_import_tree_then_regresses(imported):
    repo = imported["repo"]
    (repo / "fork.txt").write_text("locked-in improvement\n")
    target = commit(repo, "improve protected target", "prior@example.test")
    git(repo, "read-tree", "--reset", "-u", imported["tip"])
    revisit = commit(repo, "restore old import snapshot", "new@example.test")
    (repo / "fork.txt").write_text("partial regression after restoring old snapshot\n")
    commit(repo, "regression", "new@example.test")
    assert fork_provenance.resolve_v2(repo, imported["upstream"]) == revisit
    assert comparison_base(repo, target, official_tip="upstream") == target
    assert new_author_emails(repo, comparison_ref=target, accepted_base=imported["upstream"],
                             official_tip="upstream") == ["new@example.test"]


def test_stale_initial_import_cannot_pass_synthetic_merge_then_break_squash(imported):
    repo = imported["repo"]
    git(repo, "checkout", "-qb", "advanced-live", imported["previous"])
    (repo / "target-improvement.txt").write_text("concurrent target improvement\n")
    target = commit(repo, "advance target")
    git(repo, "merge", "--no-ff", imported["tip"], "-m", "GitHub synthetic PR merge")
    assert fork_provenance.resolve_v2(repo, imported["upstream"]) == imported["tip"]
    with pytest.raises(fork_provenance.ProvenanceError, match="regenerate against the advanced target"):
        comparison_base(repo, target, official_tip="upstream")
    git(repo, "checkout", "-qb", "stale-squash", target)
    git(repo, "merge", "--squash", imported["tip"])
    commit(repo, "actual stale squash landing")
    with pytest.raises(fork_provenance.ProvenanceError, match="no ancestor"):
        fork_provenance.resolve_v2(repo, imported["upstream"])


def test_v2_manifest_cannot_change_the_excluded_provenance_path(imported):
    repo = imported["repo"]
    manifest_path = repo / ".github/batumi-patches.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["upstream"]["provenance_file"] = ".github/different-proof.json"
    manifest_path.write_text(yaml.safe_dump(manifest))
    (repo / ".github/different-proof.json").write_text(json.dumps(imported["proof"]))
    with pytest.raises(check_fork_delta.DeltaError, match="canonical"):
        check_fork_delta.resolve_upstream_relationship(manifest_path, cwd=repo)


def test_trusted_fetch_head_is_frozen_before_provenance_source_fetch(imported, tmp_path):
    repo = imported["repo"]
    remote = tmp_path / "tip-freeze.git"
    git(repo, "clone", "--bare", str(repo), str(remote))
    branch = git(repo, "branch", "--show-current")
    clone = tmp_path / "tip-freeze-clone"
    git(repo, "clone", "--single-branch", "--branch", branch, remote.as_uri(), str(clone))
    git(clone, "fetch", "origin", "main")
    # main is the old target, outside the claimed new official history. A
    # source fetch replaces FETCH_HEAD with S, which DOES include that base.
    with pytest.raises(fork_provenance.ProvenanceError, match="trusted official upstream"):
        comparison_base(clone, imported["previous"], official_tip="FETCH_HEAD")
