"""Pinned source dependencies build online and install from wheelhouse offline."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.termux import build_wheels as builder
from pm import build_environment


_PIP_URL = (
    "https://files.pythonhosted.org/packages/f3/6e/"
    "1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/"
    "pip-26.2.1-py3-none-any.whl"
)
_PIP_SHA256 = "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e"


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_requirement_readers_preserve_pins_and_reject_malformed_rows(tmp_path, bom):
    lock, export, resolved, reqs = (tmp_path / name for name in
                                    ("uv.lock", "export.txt", "resolved.txt", "reqs.txt"))
    lock.write_bytes(bom + 'package = []\n# café\n'.encode())
    export.write_bytes(bom + 'example==1.0\n# café\n'.encode())
    builder.normalize_reqs(export, lock, resolved)
    assert resolved.read_bytes() == b"example\t==1.0\t\t\n"
    resolved.write_bytes(bom + resolved.read_bytes())
    assert builder.load_entries(resolved) == {"example": "==1.0"}
    builder.write_reqs_file(resolved, reqs)
    assert reqs.read_bytes() == b"example==1.0\n"
    resolved.write_bytes(bom + b"example\t==1.0\n")
    with pytest.raises(ValueError, match="fields"):
        builder.load_entries(resolved)


def test_source_pin_survives_resolution_build_and_offline_install(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=75.3.4"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "hermes-source-fixture"\nversion = "8.0.0"\n', encoding="utf-8")
    (repo / "source_fixture.py").write_text('VALUE = "from pinned source"\n', encoding="utf-8")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-m", "fixture"],
                   check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "source_fixture.py").write_text('VALUE = "wrong moving head"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-am", "advance head"],
                   check=True, capture_output=True)
    url = repo.as_uri()
    source = f"git+{url}@{commit}"
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "hermes-source-fixture"\nversion = "8.0.0"\n'
        f'source = {{ git = "{url}?rev={commit}#{commit}" }}\n', encoding="utf-8")
    export = tmp_path / "requirements.txt"
    export.write_text(
        'hermes-source-fixture==7.2.2 ; python_version < "3.11"\n'
        f'hermes-source-fixture @ {source} ; python_version >= "3.11"\n', encoding="utf-8")
    resolved = tmp_path / "resolved.txt"
    builder.normalize_reqs(export, lock, resolved)
    specs = builder.load_entries(resolved)
    assert specs == {"hermes-source-fixture": f" @ {source}"}
    reqs = tmp_path / "offline.txt"
    builder.write_reqs_file(resolved, reqs)
    assert "@" not in reqs.read_text(encoding="utf-8")
    assert 'hermes-source-fixture==8.0.0 ; python_version >= "3.11"' in reqs.read_text(encoding="utf-8")

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    build_env = tmp_path / "build-env"
    tooling = tmp_path / "build-tooling"
    tooling.mkdir()
    (tooling / "pyproject.toml").write_text(
        '[project]\nname = "termux-wheel-builder"\nversion = "0"\n'
        f'requires-python = ">=3.11"\ndependencies = ["pip @ {_PIP_URL}"]\n'
        '[tool.uv]\npackage = false\n',
        encoding="utf-8")
    (tooling / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n\n'
        '[[package]]\nname = "pip"\nversion = "26.2.1"\n'
        f'source = {{ url = "{_PIP_URL}" }}\n'
        'wheels = [\n'
        f'    {{ url = "{_PIP_URL}", hash = "sha256:{_PIP_SHA256}" }},\n'
        ']\n\n'
        '[[package]]\nname = "termux-wheel-builder"\nversion = "0"\n'
        'source = { virtual = "." }\ndependencies = [\n    { name = "pip" },\n]\n\n'
        '[package.metadata]\n'
        f'requires-dist = [{{ name = "pip", url = "{_PIP_URL}" }}]\n',
        encoding="utf-8")
    python = build_environment(source=tooling, out=build_env, python=Path(sys.executable),
                               no_install_project=True, frozen=True, explicit=True, timeout=180)
    builder.build_wheels(list(specs), specs, wheels, python=str(python))
    assert len(list(wheels.glob("hermes_source_fixture-8.0.0-*.whl"))) == 1
    # Remove the original source URL to rule out a Git fallback.
    repo.rename(tmp_path / "unavailable-source")
    target = tmp_path / "installed"
    subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
                    "--find-links", str(wheels), "--target", str(target), "-r", str(reqs)],
                   check=True, capture_output=True)
    result = subprocess.check_output(
        [sys.executable, "-c", "import source_fixture; print(source_fixture.VALUE)"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(target)}, text=True)
    assert result.strip() == "from pinned source"


def test_source_pin_rejects_moved_revision_and_missing_locked_metadata(tmp_path):
    commit = "a" * 40
    source = "https://example.invalid/package.git"
    export, lock, resolved = (tmp_path / name for name in ("requirements.txt", "uv.lock", "resolved.txt"))
    lock.write_text('[[package]]\nname = "example"\nversion = "8.0.0"\n'
                    f'source = {{ git = "{source}?rev={commit}#{commit}" }}\n', encoding="utf-8")
    for revision in ("main", "b" * 40):
        export.write_text(f"example @ git+{source}@{revision}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="commit|lock"):
            builder.normalize_reqs(export, lock, resolved)
    export.write_text(f"example @ git+{source}@{commit}\n", encoding="utf-8")
    lock.write_text("package = []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lock"):
        builder.normalize_reqs(export, lock, resolved)
