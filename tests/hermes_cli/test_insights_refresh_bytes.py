"""Artifact bytes independently bound SQLite serialization."""
import pytest

from tests.hermes_cli.test_insights_refresh_stage import refresh, valid_source

pytest_plugins = ['tests.hermes_cli.test_insights_metadata_read']


@pytest.mark.platforms('posix')
def test_artifact_size_failure_removes_empty_stage(databases, tmp_path):
    valid_source(databases)
    # Two tiny projection rows fit 4096 bytes, but schema/index pages do not.
    stage = tmp_path / 'artifact-too-large'
    assert refresh(databases, stage, max_bytes=4096) == 1
    assert not stage.exists()
