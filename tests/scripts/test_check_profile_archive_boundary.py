"""Behavioral tests for the profile archive CI guard."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
import tarfile

import pytest

from scripts.ci import reviewed_npm_archive as reviewed


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check_profile_archive_boundary.py"
)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_root_passes(tmp_path):
    result = _run(tmp_path)

    assert result.returncode == 0
    assert "No profile export archives" in result.stdout


def test_root_profile_archive_fails_without_printing_contents(tmp_path):
    default_archive = tmp_path / "default.tar.gz"
    alternate_archive = tmp_path / "backup.TGZ"
    default_archive.write_bytes(b"profile webhook secret must never be printed")
    alternate_archive.write_bytes(b"another archive")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "default.tar.gz" in result.stdout
    assert "backup.TGZ" in result.stdout
    assert "profile webhook secret" not in result.stdout
    assert "another archive" not in result.stdout


def test_nested_profile_archive_is_also_rejected(tmp_path):
    nested = tmp_path / "fixtures"
    nested.mkdir()
    (nested / "fixture.tar.gz").write_bytes(b"test fixture")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "fixtures/fixture.tar.gz" in result.stdout


def _copy_reviewed_input(root: Path) -> Path:
    repo = SCRIPT.parents[2]
    source = repo / "vendor/npm"
    destination = root / "vendor/npm"
    destination.mkdir(parents=True)
    for name in (reviewed.ARCHIVE_PATH.name, "braces-3.0.3-hermes.1.sha256"):
        shutil.copyfile(source / name, destination / name)
    shutil.copytree(source / "braces", destination / "braces")
    return root / reviewed.ARCHIVE_PATH


def test_reviewed_build_input_passes_but_profile_exports_still_fail(tmp_path):
    _copy_reviewed_input(tmp_path)
    assert _run(tmp_path).returncode == 0

    (tmp_path / "vendor/npm/default.tar.gz").write_bytes(b"profile secret")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "vendor/npm/default.tar.gz" in result.stdout
    assert reviewed.ARCHIVE_PATH.as_posix() not in result.stdout
    assert "profile secret" not in result.stdout


@pytest.mark.parametrize("proof", ["archive", "sidecar", "manifest", "missing-sidecar"])
def test_altered_or_missing_proof_fails_closed(tmp_path, proof):
    archive = _copy_reviewed_input(tmp_path)
    sidecar = archive.with_suffix(".sha256")
    manifest = tmp_path / "vendor/npm/braces/SOURCE.json"
    if proof == "archive":
        archive.write_bytes(archive.read_bytes()[:-1] + b"x")
    elif proof == "sidecar":
        sidecar.write_text("0" * 64 + "  " + archive.name + "\n", encoding="utf-8")
    elif proof == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    else:
        sidecar.unlink()
    assert _run(tmp_path).returncode == 1


def test_profile_payload_with_forged_sidecar_and_manifest_is_rejected(tmp_path):
    archive = _copy_reviewed_input(tmp_path)
    secret = b"profile webhook secret must never be printed"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("package/config.yaml")
        info.size = len(secret)
        output.addfile(info, io.BytesIO(secret))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    manifest = tmp_path / "vendor/npm/braces/SOURCE.json"
    metadata = json.loads(manifest.read_bytes())
    metadata["patched_sha256"] = {"config.yaml": hashlib.sha256(secret).hexdigest()}
    manifest.write_text(json.dumps(metadata), encoding="utf-8")

    result = _run(tmp_path)
    assert result.returncode == 1
    assert "profile webhook secret" not in result.stdout


def test_reviewed_bytes_at_another_path_are_not_exempt(tmp_path):
    archive = _copy_reviewed_input(tmp_path)
    shutil.copyfile(archive, archive.with_name("another.tgz"))
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "vendor/npm/another.tgz" in result.stdout


@pytest.mark.platforms("posix")
def test_symlink_to_reviewed_artifact_is_not_exempt(tmp_path):
    archive = _copy_reviewed_input(tmp_path)
    target = archive.with_name("braces-input.bin")
    archive.rename(target)
    archive.symlink_to(target.name)
    assert _run(tmp_path).returncode == 1


@pytest.mark.parametrize(
    "mutation", ["extra", "duplicate", "missing", "payload", "link", "directory", "size"]
)
def test_complete_regular_member_inventory_is_required(mutation):
    repo = SCRIPT.parents[2]
    source = (repo / "vendor/npm/braces/SOURCE.json").read_bytes()
    inventory = reviewed._expected_inventory(source)
    archive_data = (repo / reviewed.ARCHIVE_PATH).read_bytes()
    assert reviewed._matches_inventory(archive_data, inventory)

    result = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as original:
        with tarfile.open(fileobj=result, mode="w:gz") as output:
            for index, member in enumerate(original):
                if index == 0 and mutation == "missing":
                    continue
                payload = original.extractfile(member)
                assert payload is not None
                data = payload.read()
                if index == 0:
                    if mutation == "payload":
                        data = b"profile secret"
                        member.size = len(data)
                    elif mutation in {"link", "directory"}:
                        member.type = tarfile.SYMTYPE if mutation == "link" else tarfile.DIRTYPE
                        member.linkname = "../../config.yaml"
                        member.size = 0
                    elif mutation == "size":
                        data = b"x" * 65537
                        member.size = len(data)
                output.addfile(member, io.BytesIO(data))
                if index == 0 and mutation == "duplicate":
                    output.addfile(member, io.BytesIO(data))
            if mutation == "extra":
                member = tarfile.TarInfo("package/config.yaml")
                member.size = 6
                output.addfile(member, io.BytesIO(b"secret"))
    assert not reviewed._matches_inventory(result.getvalue(), inventory)
