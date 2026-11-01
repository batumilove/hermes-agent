"""Exact compatibility archives are build inputs; renamed or altered bytes are not."""

from pathlib import Path
import shutil

import pytest

from scripts.ci.check_profile_archive_boundary import find_forbidden_profile_archives


ARCHIVES = [
    Path("vendor/npm/glob-7.2.3-hermes.1.tgz"),
    Path("website/vendor/serve-handler-6.1.7-hermes.1.tgz"),
]


def copy_archive(root, relative):
    source = Path(__file__).resolve().parents[2] / relative
    destination = root / relative
    destination.parent.mkdir(parents=True)
    shutil.copyfile(source, destination)
    return destination


@pytest.mark.parametrize("relative", ARCHIVES)
def test_reviewed_archive_is_admitted_but_altered_or_renamed_payload_is_rejected(
    tmp_path, relative
):
    archive = copy_archive(tmp_path, relative)
    assert find_forbidden_profile_archives(tmp_path) == []
    extra = archive.with_name("profile.tgz")
    shutil.copyfile(archive, extra)
    assert find_forbidden_profile_archives(tmp_path) == [extra.relative_to(tmp_path)]
    extra.unlink()
    archive.write_bytes(b"private profile payload")
    assert find_forbidden_profile_archives(tmp_path) == [relative]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("relative", ARCHIVES)
def test_symlink_cannot_supply_reviewed_archive(tmp_path, relative):
    archive = copy_archive(tmp_path, relative)
    target = archive.with_suffix(".bin")
    archive.rename(target)
    archive.symlink_to(target.name)
    assert find_forbidden_profile_archives(tmp_path) == [relative]
