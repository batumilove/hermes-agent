"""Exercise the privileged upstream controller using disposable real Git remotes."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/upstream-sync.yml"
PROOF = ".github/upstream-sync-provenance.json"
BASE = ".github/upstream-base"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8-sig"))


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


@dataclass
class World:
    fork: Path
    upstream: Path
    origin: Path
    temporary: Path
    environment: dict[str, str]

    def advance_upstream(self, content: str) -> str:
        (self.upstream / "upstream.txt").write_text(content, encoding="utf-8")
        return _commit(self.upstream, "upstream change")

    def run(self) -> subprocess.CompletedProcess[str]:
        job = _workflow()["jobs"]["prepare"]
        controller = next(step["run"] for step in job["steps"] if step.get("id") == "plan")
        return subprocess.run(
            ["bash", "-c", controller], cwd=self.fork, env=self.environment,
            check=False, capture_output=True, text=True, timeout=45,
        )

    def review(self) -> tuple[str, str]:
        refs = _git(self.origin, "for-each-ref", "--format=%(refname)", "refs/heads/sync/")
        branches = refs.splitlines()
        review = next(ref for ref in branches if "/upstream-batch-" in ref)
        source = next(ref for ref in branches if "/upstream-source-" in ref)
        return _git(self.origin, "rev-parse", review), _git(self.origin, "rev-parse", source)


@pytest.fixture
def world(tmp_path):
    upstream = tmp_path / "official"
    upstream.mkdir()
    _git(upstream, "init", "-q", "--initial-branch=main")
    _git(upstream, "config", "user.name", "Test")
    _git(upstream, "config", "user.email", "test@example.com")
    (upstream / "upstream.txt").write_text("v1\n", encoding="utf-8")
    base = _commit(upstream, "base")
    fork = tmp_path / "fork"
    _git(tmp_path, "clone", "-q", str(upstream), str(fork))
    _git(fork, "config", "user.name", "Test")
    _git(fork, "config", "user.email", "test@example.com")
    _git(fork, "switch", "-c", "batumi/live")
    (fork / ".github").mkdir()
    (fork / BASE).write_text(base + "\n", encoding="utf-8")
    (fork / ".github/batumi-patches.yaml").write_text(
        "version: 1\nupstream:\n  provenance_file: " + PROOF + "\n", encoding="utf-8"
    )
    (fork / "fork.txt").write_text("retained patch\n", encoding="utf-8")
    _commit(fork, "fork patch")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(origin))
    _git(fork, "remote", "set-url", "origin", str(origin))
    _git(fork, "push", "-q", "origin", "batumi/live")
    _git(fork, "config", f"url.file://{upstream}.insteadOf", "https://github.com/NousResearch/hermes-agent.git")
    temporary = tmp_path / "runner"
    temporary.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    gh = tools / "gh"
    gh.write_text(
        '#!/usr/bin/env bash\nset -eu\ncase "$1 $2" in\n'
        '"pr list") echo "${SYNC_OPEN_PRS:-0}" ;;\n'
        '"pr create") printf "%s\\n" "$@" > "$SYNC_GH_ARGS"; '
        'echo https://example.invalid/review/1 ;;\n*) exit 2 ;;\nesac\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    environment = dict(os.environ)
    environment.update({
        "PATH": str(tools) + os.pathsep + environment["PATH"],
        "RUNNER_TEMP": str(temporary), "GITHUB_OUTPUT": str(temporary / "output"),
        "GITHUB_REPOSITORY": "batumilove/hermes-agent",
        "GH_TOKEN": "fixture-token-with-no-real-permissions",
        "SYNC_GH_ARGS": str(temporary / "gh-args"),
        "SYNC_EXECUTION_SENTINEL": str(temporary / "executed"),
    })
    return World(fork, upstream, origin, temporary, environment)


def test_sync_configuration_remains_daily_serial_and_repo_scoped():
    workflow = _workflow()
    assert workflow.get("on", workflow.get(True)) == {
        "schedule": [{"cron": "17 3 * * *"}], "workflow_dispatch": None,
    }
    assert workflow["concurrency"] == {
        "group": "upstream-sync-batumi-live", "cancel-in-progress": False,
    }
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["prepare"]
    assert job["if"] == "github.repository == 'batumilove/hermes-agent'"
    checkout = job["steps"][0]["with"]
    assert checkout["ref"] == "batumi/live" and checkout["persist-credentials"] is False
    plan = next(step for step in job["steps"] if step.get("id") == "plan")
    assert plan["env"]["GH_TOKEN"] == "${{ secrets.UPSTREAM_SYNC_PR_TOKEN }}"


def test_source_merge_and_linear_draft_review_have_exact_tree_provenance(world):
    live = _git(world.fork, "rev-parse", "HEAD")
    target = world.advance_upstream("v2\n")
    hook = world.fork / ".git/hooks/pre-commit"
    hook.write_text('#!/bin/sh\ntouch "$SYNC_EXECUTION_SENTINEL"\n', encoding="utf-8")
    hook.chmod(0o755)
    result = world.run()
    assert result.returncode == 0, result.stderr
    review, source = world.review()
    proof = json.loads(_git(world.origin, "show", f"{review}:{PROOF}"))
    imported = _git(world.origin, "rev-parse", f"{review}^")
    tree = _git(world.origin, "rev-parse", f"{source}^{{tree}}")
    assert proof == {"version": 2, "upstream_base": target, "source_head": source,
                     "import_tree": tree, "fetch_depth": 8}
    assert _git(world.origin, "rev-parse", f"{imported}^") == live
    assert _git(world.origin, "rev-parse", f"{imported}^{{tree}}") == tree
    assert _git(world.origin, "diff", "--name-only", imported, review) == PROOF
    assert _git(world.origin, "rev-list", "--merges", f"{live}..{review}") == ""
    assert len(_git(world.origin, "rev-list", "--parents", "-n", "1", source).split()) == 3
    _git(world.origin, "merge-base", "--is-ancestor", target, source)
    assert _git(world.origin, "show", f"{source}:{BASE}") == target
    assert _git(world.origin, "ls-tree", source, PROOF) == ""
    assert _git(world.origin, "show", f"{source}:fork.txt") == "retained patch"
    assert _git(world.fork, "rev-parse", "HEAD") == live
    assert _git(world.fork, "status", "--porcelain") == ""
    assert not (world.temporary / "executed").exists()
    assert "--draft" in (world.temporary / "gh-args").read_text(encoding="utf-8-sig").splitlines()


def _land_review(world: World, mode: str) -> str:
    review, _ = world.review()
    _git(world.fork, "fetch", "-q", "origin")
    if mode == "squash":
        _git(world.fork, "read-tree", "--reset", "-u", review)
        return _commit(world.fork, "GitHub squash")
    commits = _git(world.fork, "rev-list", "--reverse", f"HEAD..{review}").splitlines()
    for commit in commits:
        _git(world.fork, "cherry-pick", commit)
    return _git(world.fork, "rev-parse", "HEAD")


@pytest.mark.parametrize("mode", ["squash", "rebase"])
def test_next_batch_accepts_linear_landing_and_later_fork_commit(world, mode):
    target = world.advance_upstream("v2\n")
    assert world.run().returncode == 0
    _land_review(world, mode)
    (world.fork / "later.txt").write_text("later fork change\n", encoding="utf-8")
    live = _commit(world.fork, "later patch")
    _git(world.fork, "push", "-q", "origin", "batumi/live")
    _git(world.fork, "remote", "remove", "upstream")
    next_target = world.advance_upstream("v3\n")
    result = world.run()
    assert result.returncode == 0, result.stderr
    branch = f"refs/heads/sync/upstream-batch-{next_target[:12]}-{live[:12]}"
    head = _git(world.origin, "rev-parse", branch)
    proof = json.loads(_git(world.origin, "show", f"{head}:{PROOF}"))
    assert proof["upstream_base"] == next_target
    assert _git(world.origin, "show", f"{head}:later.txt") == "later fork change"
    assert _git(world.origin, "show", f"{head}:upstream.txt") == "v3"
    _git(world.origin, "merge-base", "--is-ancestor", target, proof["source_head"])


@pytest.mark.parametrize("forgery", ["source-tree", "import-tree", "extra-field"])
def test_forged_projected_tree_proof_cannot_authorize_a_batch(world, forgery):
    world.advance_upstream("v2\n")
    assert world.run().returncode == 0
    _land_review(world, "squash")
    proof_path = world.fork / PROOF
    proof = json.loads(proof_path.read_bytes())
    if forgery == "source-tree":
        payload = world.fork / "unreviewed.txt"
        payload.write_text("unreviewed source bytes\n", encoding="utf-8")
        _git(world.fork, "add", ".")
        wrong_tree = _git(world.fork, "write-tree")
        proof["source_head"] = _git(world.fork, "commit-tree", wrong_tree,
                                    "-p", proof["source_head"], "-m", "wrong source tree")
        _git(world.fork, "read-tree", "HEAD")
        payload.unlink()
    elif forgery == "import-tree":
        proof["import_tree"] = _git(world.fork, "rev-parse", "HEAD^{tree}")
    elif forgery == "extra-field":
        proof["ignored_paths"] = ["fork.txt"]
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    _commit(world.fork, "forged proof")
    _git(world.fork, "push", "-q", "origin", "batumi/live")
    _git(world.fork, "remote", "remove", "upstream")
    world.advance_upstream("v3\n")
    refs = _git(world.origin, "for-each-ref", "--format=%(refname)")
    result = world.run()
    assert result.returncode != 0
    assert _git(world.origin, "for-each-ref", "--format=%(refname)") == refs


def test_no_path_other_than_provenance_is_excluded_from_landing_proof(world):
    world.advance_upstream("v2\n")
    assert world.run().returncode == 0
    review, _ = world.review()
    _git(world.fork, "fetch", "-q", "origin")
    _git(world.fork, "read-tree", "--reset", "-u", review)
    (world.fork / "fork.txt").write_text("unreviewed landing bytes\n", encoding="utf-8")
    _commit(world.fork, "tampered squash landing")
    _git(world.fork, "push", "-q", "origin", "batumi/live")
    _git(world.fork, "remote", "remove", "upstream")
    world.advance_upstream("v3\n")
    refs = _git(world.origin, "show-ref")
    result = world.run()
    assert result.returncode != 0 and "No live ancestor" in result.stderr
    assert _git(world.origin, "show-ref") == refs


def test_conflicting_batch_never_publishes_a_partial_source_or_review(world):
    (world.fork / "upstream.txt").write_text("fork divergence\n", encoding="utf-8")
    _commit(world.fork, "fork divergence")
    _git(world.fork, "push", "-q", "origin", "batumi/live")
    world.advance_upstream("upstream divergence\n")
    result = world.run()
    assert result.returncode != 0 and "conflicts" in result.stderr
    assert _git(world.origin, "for-each-ref", "--format=%(refname)", "refs/heads/sync/") == ""


def test_no_change_and_existing_draft_do_not_publish(world):
    assert world.run().returncode == 0
    world.environment["SYNC_OPEN_PRS"] = "1"
    world.advance_upstream("v2\n")
    assert world.run().returncode == 0
    assert _git(world.origin, "for-each-ref", "--format=%(refname)", "refs/heads/sync/") == ""


def test_missing_source_object_is_fetched_from_retained_source_branch(world, tmp_path):
    for index in range(12):
        world.advance_upstream(f"upstream history {index}\n")
    world.advance_upstream("v2\n")
    assert world.run().returncode == 0
    _, source = world.review()
    _land_review(world, "squash")
    _git(world.fork, "push", "-q", "origin", "batumi/live")
    fresh = tmp_path / "fresh-checkout"
    _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "batumi/live",
         world.origin.as_uri(), str(fresh))
    absent = subprocess.run(
        ["git", "cat-file", "-e", f"{source}^{{commit}}"], cwd=fresh,
        capture_output=True, check=False, timeout=30,
    )
    assert absent.returncode != 0
    _git(fresh, "config", f"url.file://{world.upstream}.insteadOf",
         "https://github.com/NousResearch/hermes-agent.git")
    world.fork = fresh
    world.advance_upstream("v3\n")
    result = world.run()
    assert result.returncode == 0, result.stderr
    _git(fresh, "cat-file", "-e", f"{source}^{{commit}}")
    assert _git(fresh, "rev-parse", "--is-shallow-repository") == "false"


def test_immutable_branches_are_not_rewritten_on_retry(world):
    world.advance_upstream("v2\n")
    assert world.run().returncode == 0
    refs = _git(world.origin, "show-ref")
    _git(world.fork, "remote", "remove", "upstream")
    result = world.run()
    assert result.returncode != 0 and "immutable" in result.stderr
    assert _git(world.origin, "show-ref") == refs


def test_stale_checkout_never_publishes_against_advanced_live(world):
    world.advance_upstream("v2\n")
    tree = _git(world.fork, "rev-parse", "HEAD^{tree}")
    live = _git(world.fork, "rev-parse", "HEAD")
    moved = _git(world.fork, "commit-tree", tree, "-p", live, "-m", "advanced live")
    _git(world.fork, "push", "-q", "origin", f"{moved}:refs/heads/batumi/live")
    result = world.run()
    assert result.returncode != 0 and "advanced during preparation" in result.stderr
    assert _git(world.origin, "for-each-ref", "--format=%(refname)", "refs/heads/sync/") == ""


def test_remote_review_rejection_publishes_neither_ref_and_retry_can_succeed(world):
    world.advance_upstream("v2\n")
    hook = world.origin / "hooks/pre-receive"
    hook.write_text(
        '#!/usr/bin/env bash\nwhile read -r old new ref; do\n'
        'case "$ref" in refs/heads/sync/upstream-batch-*) exit 1 ;; esac\n'
        'done\n', encoding="utf-8",
    )
    hook.chmod(0o755)
    result = world.run()
    assert result.returncode != 0
    assert _git(world.origin, "for-each-ref", "--format=%(refname)", "refs/heads/sync/") == ""
    assert not (world.temporary / "gh-args").exists()
    hook.unlink()
    _git(world.fork, "remote", "remove", "upstream")
    assert world.run().returncode == 0
    world.review()


def test_create_only_lease_preserves_ref_created_after_preflight(world):
    live = _git(world.fork, "rev-parse", "HEAD")
    target = world.advance_upstream("v2\n")
    source_ref = f"refs/heads/sync/upstream-source-{target[:12]}-{live[:12]}"
    actual_git = shutil.which("git")
    assert actual_git is not None
    wrapper = Path(world.environment["PATH"].split(os.pathsep)[0]) / "git"
    wrapper.write_text(
        '#!/usr/bin/env bash\nset -eu\nif [ "$1" = push ]; then\n'
        f'  {shlex.quote(actual_git)} --git-dir={shlex.quote(str(world.origin))} '
        f'update-ref {shlex.quote(source_ref)} {shlex.quote(live)}\nfi\n'
        f'exec {shlex.quote(actual_git)} "$@"\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    result = world.run()
    assert result.returncode != 0
    assert _git(world.origin, "rev-parse", source_ref) == live
    batch_refs = _git(world.origin, "for-each-ref", "--format=%(refname)",
                      "refs/heads/sync/upstream-batch-*")
    assert batch_refs == ""
    assert not (world.temporary / "gh-args").exists()


def test_missing_pr_event_credential_fails_before_any_publication(world):
    world.advance_upstream("v2\n")
    world.environment.pop("GH_TOKEN")
    result = world.run()
    assert result.returncode != 0 and "UPSTREAM_SYNC_PR_TOKEN" in result.stderr
    assert _git(world.origin, "for-each-ref", "--format=%(refname)", "refs/heads/sync/") == ""
    assert not (world.temporary / "gh-args").exists()
