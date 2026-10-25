"""Source identity follows a verified imported release without fetching at startup."""
from __future__ import annotations

import json
import subprocess

import pytest

from hermes_cli.version_info import _git_version_info
from scripts import fork_provenance as provenance


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=10).strip()


@pytest.fixture
def imported(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "tag.gpgsign", "false")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="hermes-agent"\nversion="0.0.0"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "release")
    git(tmp_path, "tag", "-am", "release", "v0.22.5")
    release = git(tmp_path, "rev-parse", "HEAD")
    tag_object = git(tmp_path, "rev-parse", "v0.22.5")
    (tmp_path / ".github").mkdir()
    (tmp_path / provenance.BASE_PATH).write_text(release + "\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "sync source")
    source = git(tmp_path, "rev-parse", "HEAD")
    tree = provenance.projected_tree(tmp_path, source)
    git(tmp_path, "checkout", "--orphan", "fork")
    proof = {"version": 2, "upstream_base": release, "source_head": source,
             "import_tree": tree, "fetch_depth": 8}
    (tmp_path / provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "tree import")
    (tmp_path / "later").write_text("fork fix")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "fork fix")
    # The pin is the trust input; repository-local tag names are unnecessary.
    git(tmp_path, "tag", "-d", "v0.22.5")
    monkeypatch.setattr("hermes_cli.version_provenance.IMPORTED_RELEASE", ("0.22.5", tag_object, release))
    return tmp_path, proof


def test_imported_release_identifies_actual_fork_commit(imported, monkeypatch):
    root, _ = imported
    info = _git_version_info(root)
    assert info.base_version == "0.22.5"
    assert info.distance == 2
    assert info.commit == git(root, "rev-parse", "HEAD")
    assert info.derived_version == f"0.22.5+2.g{info.commit[:7]}"

    from hermes_cli import version_info
    from hermes_cli.plugins_manifest import requires_hermes_error
    from hermes_cli.source_stamp import write_source_stamp

    write_source_stamp(root)
    monkeypatch.setattr(version_info, "_resolve_stamp_file", lambda: root / "install-stamp.json")
    version_info._reset_version_info_cache()
    try:
        assert requires_hermes_error({"name": "fixture", "requires_hermes": ">=0.21.7"}) is None
        assert requires_hermes_error({"name": "fixture", "requires_hermes": ">=0.99.0"}) is not None
    finally:
        version_info._reset_version_info_cache()


@pytest.mark.parametrize("damage", ["tree", "release", "missing-source"])
def test_unverified_import_cannot_claim_release(imported, monkeypatch, damage):
    root, proof = imported
    if damage == "tree":
        proof["import_tree"] = "a" * 40
        (root / provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
        git(root, "add", ".")
        git(root, "commit", "-qm", "bad proof")
    elif damage == "release":
        monkeypatch.setattr("hermes_cli.version_provenance.IMPORTED_RELEASE", ("0.99.0", "a" * 40, proof["upstream_base"]))
    else:
        proof["source_head"] = "b" * 40
        (root / provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
        git(root, "add", ".")
        git(root, "commit", "-qm", "missing source")
        # A fetch would invoke this transport and fail the assertion below.
        marker = root / "network-attempt"
        hook = root / "git-remote-probe"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
        hook.chmod(0o700)
        monkeypatch.setenv("GIT_EXEC_PATH", str(root))
        git(root, "remote", "add", "origin", "probe::fixture")
    assert _git_version_info(root).base_version == "unknown"
    if damage == "missing-source":
        with provenance.offline_git(), pytest.raises(provenance.ProvenanceError, match="not allowed"):
            provenance.git(root, "fetch", "origin")
    assert not (root / "network-attempt").exists()
