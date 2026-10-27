"""Synthetic offline Git release/import proofs and custody failures."""
import copy
import json
import subprocess

import pytest

from hermes_cli import upstream_compatibility as compat


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()




def test_two_distinct_releases_use_same_validator(release_repo):
    repo, receipt = release_repo
    assert compat.validate_receipt(repo, receipt) == receipt["release_tag"][1:]


@pytest.mark.parametrize("fault", ["missing", "wrongtype", "wrongcommit", "mismatch", "tagname"])
def test_local_annotated_tag_required(release_repo, monkeypatch, fault):
    repo, receipt = release_repo
    receipt = copy.deepcopy(receipt)
    anchor = {k: receipt[k] for k in compat.REVIEWED_RELEASES[0]}
    changes = {
        "missing": {"tag_object": "0" * 40},
        "wrongtype": {"tag_object": receipt["release_commit"]},
        "wrongcommit": {"release_commit": receipt["squash_commit"]},
        "tagname": {"release_tag": "v9.0.0"},
    }
    if fault in changes:
        anchor.update(changes[fault])
    else:
        git(repo, "tag", "-fa", receipt["release_tag"], "-m", "replacement")
        anchor["tag_object"] = git(repo, "rev-parse", receipt["release_tag"])
        # A changed trusted identity cannot validate the original receipt.
        monkeypatch.setattr(compat, "REVIEWED_RELEASES", (anchor,), raising=False)
        assert compat.validate_receipt(repo, receipt) is None
        return
    receipt.update(anchor)
    monkeypatch.setattr(compat, "REVIEWED_RELEASES", (anchor,), raising=False)
    (repo / compat.RECEIPT_PATH).write_text(json.dumps(receipt), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "bad receipt")
    assert compat.validate_receipt(repo, receipt) is None


@pytest.mark.parametrize("fault", ["receipt", "committed_receipt", "provenance", "base", "malformed", "untrusted", "unrelated"])
def test_evidence_cannot_elevate(release_repo, fault):
    repo, receipt = release_repo
    if fault == "committed_receipt":
        git(repo, "rm", compat.RECEIPT_PATH)
        git(repo, "commit", "-qm", "remove custody")
        (repo / compat.RECEIPT_PATH).write_text(json.dumps(receipt), encoding="utf-8")
        assert compat.validate_receipt(repo, receipt) is None
        return
    mutations = {
        "receipt": (compat.RECEIPT_PATH, json.dumps(dict(receipt, squash_commit="0" * 40))),
        "provenance": (compat.PROVENANCE_PATH, json.dumps(dict(receipt["proof"], fetch_depth=9))),
        "base": (".github/upstream-base", "0" * 40),
        "malformed": (compat.RECEIPT_PATH, "{"),
    }
    if fault in mutations:
        name, content = mutations[fault]
        (repo / name).write_text(content, encoding="utf-8")
    elif fault == "untrusted":
        receipt = dict(receipt, release_id=99)
    else:
        unrelated = git(repo, "commit-tree", receipt["proof"]["import_tree"], "-m", "unrelated root")
        receipt = copy.deepcopy(receipt)
        receipt["proof"]["source_head"] = unrelated
        (repo / compat.PROVENANCE_PATH).write_text(json.dumps(receipt["proof"]), encoding="utf-8")
        (repo / compat.RECEIPT_PATH).write_text(json.dumps(receipt), encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "unrelated proof")
    assert compat.validate_receipt(repo, receipt) is None


def test_validation_processes_are_read_only(release_repo, monkeypatch):
    repo, receipt = release_repo
    original = subprocess.run
    calls = []
    before = sorted(str(p.relative_to(repo)) for p in (repo / ".git/objects").rglob("*") if p.is_file())
    def guarded(argv, **kwargs):
        assert argv[0] == "git"
        assert argv[1] in {"show", "cat-file", "rev-parse", "merge-base", "ls-tree"}
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == ""
        calls.append(argv)
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", guarded)
    assert compat.validate_receipt(repo, receipt)
    assert calls
    assert before == sorted(str(p.relative_to(repo)) for p in (repo / ".git/objects").rglob("*") if p.is_file())


@pytest.mark.parametrize("spec,accepted", [(">=0.19,<0.24", True), ("<0.20", False), (">=0.19,<0.22", False)])
def test_imported_boundary_evaluates_composite_once(release_repo, monkeypatch, spec, accepted):
    from hermes_cli import plugins_manifest as manifests
    repo, receipt = release_repo
    monkeypatch.setattr(compat, "_executing_repo", lambda: repo)
    monkeypatch.setattr("hermes_cli.version_info._resolve_stamp_file", lambda: None)
    monkeypatch.setattr(manifests, "running_hermes_version", lambda: "0.14.0")
    original = manifests.version_satisfies
    calls = []
    def evaluate(spec, current):
        calls.append(current)
        return original(spec, current)
    monkeypatch.setattr(manifests, "version_satisfies", evaluate)
    assert (manifests.requires_hermes_error({"requires_hermes": spec}) is None) == accepted
    assert calls == [receipt["release_tag"][1:]]


def test_real_stamp_prevents_valid_receipt_elevation(release_repo, monkeypatch):
    from hermes_cli import version_info, plugins_manifest as manifests
    repo, receipt = release_repo
    assert compat.validate_receipt(repo, receipt)
    stamp = repo / "install-stamp.json"
    stamp.write_text(json.dumps(dict(commit="a" * 40, baseVersion="0.14.0", version="0.14.0", updateMechanism="docker")), encoding="utf-8")
    monkeypatch.setattr(version_info, "_resolve_stamp_file", lambda: stamp)
    monkeypatch.setattr(compat, "_executing_repo", lambda: repo)
    assert compat.verified_upstream_version() is None
    monkeypatch.setattr(manifests, "running_hermes_version", lambda: "0.14.0")
    assert manifests.requires_hermes_error({"requires_hermes": ">=0.19"})


def test_receipt_byte_equality(release_repo):
    repo, receipt = release_repo
    path = repo / compat.RECEIPT_PATH
    path.write_bytes(path.read_bytes() + b"\n")
    assert compat.validate_receipt(repo, receipt) is None
