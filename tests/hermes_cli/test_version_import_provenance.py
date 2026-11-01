"""Source identity follows a verified imported release without fetching at startup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_cli.version_info import _git_version_info
from hermes_cli import fork_provenance as provenance


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


def test_projected_runtime_verifies_separate_source_in_fresh_interpreter(imported, tmp_path):
    from pm.workspace import _copy_core_inputs
    from hermes_cli.version_provenance import IMPORTED_RELEASE

    source, _ = imported
    checkout = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _copy_core_inputs(checkout, runtime)
    assert not (runtime / "scripts").exists()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    # -I drops inherited Python paths; only the actual PM snapshot is admitted.
    code = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import hermes_cli.version_info as version_info
import hermes_cli.version_provenance as version_provenance
assert Path(version_info.__file__).is_relative_to(Path(sys.argv[1]))
assert not any(Path(p).resolve() == Path(sys.argv[2]).resolve() for p in sys.path if p)
version_provenance.IMPORTED_RELEASE = tuple(json.loads(sys.argv[3]))
info = version_info._git_version_info(Path(sys.argv[2]))
print(json.dumps({'base': info.base_version, 'distance': info.distance,
                  'commit': info.commit, 'derived': info.derived_version}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(runtime), str(source),
         json.dumps(IMPORTED_RELEASE)], cwd=unrelated,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commit = git(source, "rev-parse", "HEAD")
    assert json.loads(result.stdout) == {
        "base": "0.22.5", "distance": 2, "commit": commit,
        "derived": f"0.22.5+2.g{commit[:7]}",
    }


def test_build_and_contributor_tools_share_runtime_provenance(monkeypatch):
    from scripts import check_fork_delta, fork_check_base, fork_contributors
    from scripts.build import image_identity
    from scripts.ci import catalog_upstream_import, prepare_upgrade_source

    for shared in (check_fork_delta.fork_provenance, fork_check_base.fork_provenance,
                   fork_contributors.fork_provenance, catalog_upstream_import.provenance,
                   prepare_upgrade_source.provenance):
        assert shared is provenance
        assert shared.ProvenanceError is provenance.ProvenanceError
    assert not provenance._OFFLINE.get()
    with provenance.offline_git():
        assert prepare_upgrade_source.provenance._OFFLINE.get()
        with provenance.offline_git():
            assert catalog_upstream_import.provenance._OFFLINE.get()
        assert provenance._OFFLINE.get()
    assert not provenance._OFFLINE.get()
    sentinel = object()
    monkeypatch.setattr(provenance, "source_distance", lambda *args: sentinel)
    assert image_identity._source_distance(Path("unused"), "release", "head") is sentinel


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


def test_git_utf8_text_and_binary_roundtrip_under_ascii_locale(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "ascii")
    with provenance.offline_git():
        oid = provenance.git(tmp_path, "hash-object", "-w", "--stdin", input_data="ქართული\n")
        assert len(oid) == 40 and oid.isascii()
        assert provenance.git(tmp_path, "cat-file", "blob", oid) == "ქართული"
        raw = b"\xff\x00\x80\n"
        binary_oid = provenance.git(tmp_path, "hash-object", "-w", "--stdin", binary=True, input_data=raw)
        assert isinstance(binary_oid, bytes)
        assert provenance.git(tmp_path, "cat-file", "blob", binary_oid.strip().decode("ascii"), binary=True) == raw
