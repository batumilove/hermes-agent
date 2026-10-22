"""Image package identities come from release objects, not placeholder versions."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import subprocess

import pytest

from scripts.build.agent import write_metadata
from scripts.build.image_identity import package_version, validate_metadata_version
from scripts.ci.prepare_upgrade_source import Release


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=30).strip()


@pytest.fixture
def history(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname="hermes-agent"\nversion="0.21.5"\n'
                       'dependencies=["example>=1"]\n[project.scripts]\nhermes="example:main"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "release")
    commit = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "-c", "tag.gpgsign=false", "tag", "-am", "release", "v2026.9.24")
    release = Release("v2026.9.24", git(tmp_path, "rev-parse", "v2026.9.24"), commit)
    project.write_text(project.read_text().replace('0.21.5', '0.0.0'))
    git(tmp_path, "commit", "-qam", "source")
    return tmp_path, release, {"commit": git(tmp_path, "rev-parse", "HEAD")}


def test_source_identity_and_repeated_metadata_preserve_dependencies(history):
    root, release, stamp = history
    version = package_version(root, stamp, release)
    assert version == f"0.21.5+1.g{stamp['commit']}"
    write_metadata(root / "pyproject.toml", root)
    old = (root / "hermes_agent-0.0.0.dist-info/METADATA").read_text()
    write_metadata(root / "pyproject.toml", root, version=version)
    distributions = list(importlib.metadata.distributions(path=[str(root)]))
    assert len(distributions) == 1 and distributions[0].version == version
    assert (root / f"hermes_agent-{version}.dist-info/METADATA").read_text() == old.replace('Version: 0.0.0', f'Version: {version}')
    assert distributions[0].entry_points["hermes"].value == "example:main"


def test_explicit_release_stamp_wins(history):
    root, _release, stamp = history
    stamp.update(baseVersion="0.22.0", displayVersion="0.22.0", distance=0)
    assert package_version(root, stamp, Release("missing", "a" * 40, "b" * 40)) == "0.22.0"



def _unrelated(root, release, stamp):
    git(root, "checkout", "--orphan", "unrelated")
    git(root, "commit", "-qm", "unrelated")
    stamp["commit"] = git(root, "rev-parse", "HEAD")
    return release


@pytest.mark.parametrize("damage", [
    pytest.param(lambda root, release, stamp: stamp.update(commit="a" * 40) or release, id="commit"),
    pytest.param(lambda root, release, stamp: ((root / "pyproject.toml").write_text("broken"), release)[1], id="dirty"),
    pytest.param(lambda root, release, stamp: Release(release.tag, "a" * 40, release.commit), id="object"),
    pytest.param(lambda root, release, stamp: Release(release.tag, release.tag_object, stamp["commit"]), id="peeled"),
    pytest.param(_unrelated, id="nonancestor"),
])
def test_unproven_identity_is_rejected(history, damage):
    root, release, stamp = history
    release = damage(root, release, stamp)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        package_version(root, stamp, release)


@pytest.mark.parametrize("value", ["../bad", "0.21.5\nX: injected", "git.abc", None, "0.21.5+1.gabc"])
def test_metadata_version_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_metadata_version(value)


def test_import_proof_authenticates_linear_source_and_later_changes(history):
    from scripts import fork_provenance as provenance

    root, release, _stamp = history
    (root / ".github").mkdir()
    base = git(root, "rev-parse", "HEAD")
    (root / provenance.BASE_PATH).write_text(base + "\n")
    git(root, "add", ".github")
    git(root, "commit", "-qm", "source baseline")
    source = git(root, "rev-parse", "HEAD")
    tree = provenance.projected_tree(root, source)
    git(root, "checkout", "--orphan", "review")
    proof = {"version": 2, "upstream_base": base, "source_head": source,
             "import_tree": tree, "fetch_depth": 9}
    (root / provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
    git(root, "add", ".")
    git(root, "commit", "-qm", "linear import")
    head = git(root, "rev-parse", "HEAD")
    assert package_version(root, {"commit": head}, release) == f"0.21.5+2.g{head}"
    (root / "later").write_text("application change")
    git(root, "add", ".")
    git(root, "commit", "-qm", "later")
    head = git(root, "rev-parse", "HEAD")
    assert package_version(root, {"commit": head}, release) == f"0.21.5+3.g{head}"
    proof["import_tree"] = "a" * 40
    (root / provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
    git(root, "add", ".")
    git(root, "commit", "-qm", "tamper")
    with pytest.raises(ValueError):
        package_version(root, {"commit": git(root, "rev-parse", "HEAD")}, release)


def test_cli_creates_stamp_without_changing_source(history, monkeypatch):
    from scripts.build import image_identity

    root, release, _stamp = history
    original = image_identity.package_version
    monkeypatch.setattr(image_identity, "package_version", lambda repo, stamp: original(repo, stamp, release))
    stamp_file = root / "install-stamp.json"
    monkeypatch.setattr("sys.argv", ["image_identity", "--repo", str(root), "--stamp", str(stamp_file)])
    image_identity.main()
    stamp = json.loads(stamp_file.read_text())
    assert stamp["commit"] == git(root, "rev-parse", "HEAD")
    assert stamp["distribution"] == "docker" and stamp["updateMechanism"] == "external"
    assert stamp["packageVersion"] == f"0.21.5+1.g{stamp['commit']}"
    assert not git(root, "status", "--porcelain", "--untracked-files=no")
    image_identity.main()
    assert json.loads(stamp_file.read_text()) == stamp
