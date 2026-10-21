"""Capacity-only admission; transaction architecture deliberately deferred."""
import pytest

from hermes_cli.update_source_preflight import storage_refusal


@pytest.mark.platforms("linux", "macos", "windows")
def test_storage_refuses_without_writes(tmp_path):
    marker = tmp_path / 'owner'; marker.write_text('unchanged')
    assert 'space' in storage_refusal([tmp_path], required_bytes=10**30)
    assert marker.read_text() == 'unchanged'


@pytest.mark.platforms("posix")
def test_storage_checks_inodes(tmp_path):
    assert 'inode' in storage_refusal([tmp_path], required_bytes=0, required_inodes=10**30)


@pytest.mark.platforms("linux", "macos", "windows")
def test_storage_sufficient(tmp_path):
    assert storage_refusal([tmp_path], required_bytes=0, required_inodes=0) is None
