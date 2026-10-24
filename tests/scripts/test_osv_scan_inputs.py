"""Source-aware OSV inputs must keep registry dependency identity intact."""

from pathlib import Path
import tomllib

import pytest

from scripts.ci.osv_scan_inputs import project_root_lock


def _input(tmp_path: Path, packages: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="hermes-agent"\nversion="0.0.0"\n', encoding="utf-8")
    source = tmp_path / "uv.lock"
    source.write_text('version=1\n\n' + packages, encoding="utf-8")
    return source


LOCAL_ROOT = '\n[[package]]\nname="hermes-agent"\nversion="0.0.0"\nsource={editable="."}\n'


def test_projection_keeps_registry_and_nonlocal_same_named_packages(tmp_path):
    dependencies = (
        '\n[[package]]\nname="hermes-agent"\nversion="0.0.0"\nsource={registry="https://pypi.org/simple"}\n'
        '\n[[package]]\nname="hermes-agent"\nversion="0.0.0"\nsource={editable="../dependency"}\n'
        '\n[[package]]\nname="pyjwt"\nversion="2.15.0"\nsource={registry="https://pypi.org/simple"}\n'
        '\n[[package]]\nname="urllib3"\nversion="2.8.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    source = _input(tmp_path, LOCAL_ROOT + dependencies)
    original = source.read_bytes()
    destination = tmp_path / "scan" / "uv.lock"

    project_root_lock(source, destination)

    assert source.read_bytes() == original
    assert destination.read_text(encoding="utf-8") == 'version=1\n\n' + dependencies
    assert len(tomllib.loads(destination.read_text(encoding="utf-8"))["package"]) == 4


@pytest.mark.parametrize("packages", ["", LOCAL_ROOT + LOCAL_ROOT])
def test_missing_or_ambiguous_root_fails_without_publishing(tmp_path, packages):
    source = _input(tmp_path, packages)
    destination = tmp_path / "scan" / "uv.lock"

    with pytest.raises(ValueError, match="exactly one"):
        project_root_lock(source, destination)

    assert not destination.exists()


def test_root_version_mismatch_is_not_silently_excluded(tmp_path):
    source = _input(tmp_path, LOCAL_ROOT.replace('version="0.0.0"', 'version="0.1.0"'))

    with pytest.raises(ValueError, match="exactly one"):
        project_root_lock(source, tmp_path / "scan" / "uv.lock")


def test_unrecognized_editable_source_attributes_are_preserved(tmp_path):
    other = LOCAL_ROOT.replace('{editable="."}', '{editable=".", unexpected="value"}')
    source = _input(tmp_path, LOCAL_ROOT + other)
    destination = tmp_path / "scan" / "uv.lock"

    project_root_lock(source, destination)

    assert destination.read_text(encoding="utf-8").endswith(other)


def test_last_root_package_preserves_trailing_manifest_and_metadata_bytes(tmp_path):
    dependency = '\n[[package]]\nname="urllib3"\nversion="2.8.0"\nsource={registry="https://pypi.org/simple"}\n'
    trailing = (
        '[manifest]\nconstraints=[{name="urllib3",specifier=">=2.8.0,<3"}]\n'
        '\n[metadata]\nreview="must survive exactly"\n'
        '\n[[metadata.entries]]\nname="trailing-array"\n'
    )
    source = _input(tmp_path, dependency + LOCAL_ROOT + trailing)
    destination = tmp_path / "scan" / "uv.lock"

    project_root_lock(source, destination)

    projected = destination.read_text(encoding="utf-8")
    assert projected.endswith(trailing)
    actual = tomllib.loads(projected)
    assert actual["package"] == tomllib.loads('version=1\n' + dependency)["package"]
    assert actual["manifest"]["constraints"] == [{"name": "urllib3", "specifier": ">=2.8.0,<3"}]
    assert actual["metadata"]["entries"] == [{"name": "trailing-array"}]
