"""Offline imported-release admission without changing fork identity."""
import subprocess

import pytest

from hermes_cli import plugins_manifest as manifests


def test_imported_release_admits_without_relabeling(monkeypatch, tmp_path):
    from hermes_cli import upstream_compatibility as compat
    from hermes_cli.version_info import VersionInfo

    identity = VersionInfo("0.14.0", "0.14.0+1", 1, "a" * 40, None, "git")
    monkeypatch.setattr("hermes_cli.version_info.get_version_info", lambda: identity)
    monkeypatch.setattr(compat, "verified_upstream_version", lambda: "0.22.0")
    assert manifests.requires_hermes_error({"requires_hermes": ">=0.19.0"}) is None
    assert manifests.running_hermes_version() == "0.14.0"
    assert identity.base_version == "0.14.0"


def test_unknown_identity_is_not_admitted(monkeypatch):
    monkeypatch.setattr(manifests, "running_hermes_version", lambda: "unknown")
    assert manifests.requires_hermes_error({"requires_hermes": ">=0.19.0"})


@pytest.mark.parametrize("current,spec,accepted", [
    ("0.22.0", ">=0.19", True), ("0.14.0", ">=0.19", False),
    ("0.22.0", "<0.20", False), ("0.14.0", "<0.20", True),
])
def test_ordinary_admission(monkeypatch, current, spec, accepted):
    from hermes_cli import upstream_compatibility as compat
    monkeypatch.setattr(manifests, "running_hermes_version", lambda: current)
    monkeypatch.setattr(compat, "verified_upstream_version", lambda: None)
    assert (manifests.requires_hermes_error({"requires_hermes": spec}) is None) == accepted


def test_receipt_requires_immutable_tag_object(release_repo, tmp_path_factory):
    from hermes_cli import upstream_compatibility as compat

    repo, receipt = release_repo
    assert compat.validate_receipt(repo, receipt) == receipt["release_tag"][1:]
    # Explicit revision packing retains all committed proof but not tag objects.
    missing = tmp_path_factory.mktemp("missing-tag")
    subprocess.run(["git", "init", "-q", str(missing)], check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).strip()
    pack = subprocess.check_output(
        ["git", "pack-objects", "--stdout", "--revs"], cwd=repo, input=head + b"\n")
    subprocess.run(["git", "index-pack", "--stdin"], cwd=missing, input=pack,
                   check=True, capture_output=True)
    subprocess.run(["git", "checkout", "--detach", head.decode()], cwd=missing,
                   check=True, capture_output=True)
    absent = subprocess.run(["git", "cat-file", "-t", receipt["tag_object"]],
                            cwd=missing, capture_output=True, check=False)
    assert absent.returncode != 0
    assert (missing / compat.RECEIPT_PATH).read_bytes() == (repo / compat.RECEIPT_PATH).read_bytes()
    assert compat.validate_receipt(missing, receipt) is None
    assert compat.validate_receipt(repo, receipt) == receipt["release_tag"][1:]


def test_missing_and_malformed_receipt_are_denied(tmp_path, monkeypatch):
    from hermes_cli import upstream_compatibility as compat

    monkeypatch.setattr(compat, "_executing_repo", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.version_info._resolve_stamp_file", lambda: None)
    assert compat.verified_upstream_version() is None
    (tmp_path / ".github").mkdir()
    (tmp_path / compat.RECEIPT_PATH).write_text("{broken", encoding="utf-8")
    assert compat.verified_upstream_version() is None


def test_git_is_offline_and_read_only(monkeypatch, tmp_path):
    from hermes_cli import upstream_compatibility as compat
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "abc\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert compat._git(tmp_path, "rev-parse", "HEAD") == "abc"
    argv, kwargs = calls[0]
    assert "fetch" not in argv
    assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
    assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == ""
