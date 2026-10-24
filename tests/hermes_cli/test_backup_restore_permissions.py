"""Archive mode fidelity, secret overrides and bounded restore member reads."""

import os
import stat
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from tests.hermes_cli.test_backup import _no_real_gateway_service

pytestmark = pytest.mark.usefixtures("_no_real_gateway_service")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions only")
def test_restores_archived_unix_file_modes(tmp_path, monkeypatch):
    """Non-secret files retain the permission bits stored by Hermes backup."""
    hermes_home = tmp_path / ".hermes"
    script = hermes_home / "scripts" / "worker.sh"
    script.parent.mkdir(parents=True)
    script.write_text("old\n")
    script.chmod(0o600)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, content, mode in (
            ("config.yaml", "model: test\n", 0o644),
            ("scripts/worker.sh", "#!/bin/sh\nexit 0\n", 0o755),
        ):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, content)

    from hermes_cli.backup import run_import
    run_import(Namespace(zipfile=str(zip_path), force=True))

    assert (hermes_home / "config.yaml").stat().st_mode & 0o777 == 0o644
    assert script.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions only")
def test_secret_mode_override_wins_over_archive_mode(tmp_path, monkeypatch):
    """Archive metadata cannot make a known credential file permissive."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.yaml", "model: test\n")
        info = zipfile.ZipInfo("auth.json")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o777) << 16
        zf.writestr(info, "{}")

    from hermes_cli.backup import run_import
    run_import(Namespace(zipfile=str(zip_path), force=True))

    assert (hermes_home / "auth.json").stat().st_mode & 0o777 == 0o600


def test_import_reads_archive_members_in_bounded_chunks(tmp_path, monkeypatch):
    """A large member is never materialized by an unbounded read call."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    zip_path = tmp_path / "backup.zip"
    payload = b"x" * (2 * 1024 * 1024 + 1)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.yaml", "model: test\n")
        zf.writestr("state.db", payload)

    original_read = zipfile.ZipExtFile.read
    read_sizes = []

    def bounded_read(stream, size=-1):
        read_sizes.append(size)
        assert size is not None and size >= 0, "import used an unbounded ZIP member read"
        return original_read(stream, size)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", bounded_read)

    from hermes_cli.backup import run_import
    run_import(Namespace(zipfile=str(zip_path), force=True))

    assert len(read_sizes) > 2
    assert max(read_sizes) <= 1024 * 1024
    assert (hermes_home / "state.db").read_bytes() == payload
