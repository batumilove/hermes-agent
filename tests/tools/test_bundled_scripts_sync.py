from pathlib import Path
from unittest.mock import patch

from tools import bundled_scripts_sync as bss


class TestSyncBundledScripts:
    def test_links_bundled_script_into_hermes_bin(self, tmp_path):
        repo = tmp_path / "repo"
        src = repo / "scripts" / "telegram-healthcheck-stateful.sh"
        src.parent.mkdir(parents=True)
        src.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

        hermes_home = tmp_path / "home" / ".hermes"
        manifest = hermes_home / ".bundled_scripts_manifest"

        with patch.object(bss, "_project_root", return_value=repo), patch.object(
            bss, "HERMES_HOME", hermes_home
        ), patch.object(bss, "SCRIPTS_MANIFEST", manifest), patch.object(
            bss,
            "BUNDLED_SCRIPTS",
            [("scripts/telegram-healthcheck-stateful.sh", "bin/telegram-healthcheck.sh")],
        ), patch.object(bss, "LEGACY_MANAGED_DESTINATIONS", []):
            result = bss.sync_bundled_scripts(quiet=True)

        dest = hermes_home / "bin" / "telegram-healthcheck.sh"
        assert result["linked"] == [str(dest)]
        assert dest.is_symlink()
        assert dest.resolve() == src.resolve()
        assert f"{dest}:" in manifest.read_text(encoding="utf-8")

    def test_repoints_existing_symlink_when_source_changes(self, tmp_path):
        repo = tmp_path / "repo"
        src = repo / "scripts" / "telegram-healthcheck-stateful.sh"
        src.parent.mkdir(parents=True)
        src.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

        old_repo = tmp_path / "old-repo"
        old_src = old_repo / "scripts" / "telegram-healthcheck-stateful.sh"
        old_src.parent.mkdir(parents=True)
        old_src.write_text("#!/bin/sh\necho old\n", encoding="utf-8")

        hermes_home = tmp_path / "home" / ".hermes"
        dest = hermes_home / "bin" / "telegram-healthcheck.sh"
        dest.parent.mkdir(parents=True)
        dest.symlink_to(old_src)
        manifest = hermes_home / ".bundled_scripts_manifest"

        with patch.object(bss, "_project_root", return_value=repo), patch.object(
            bss, "HERMES_HOME", hermes_home
        ), patch.object(bss, "SCRIPTS_MANIFEST", manifest), patch.object(
            bss,
            "BUNDLED_SCRIPTS",
            [("scripts/telegram-healthcheck-stateful.sh", "bin/telegram-healthcheck.sh")],
        ), patch.object(bss, "LEGACY_MANAGED_DESTINATIONS", []):
            result = bss.sync_bundled_scripts(quiet=True)

        assert result["updated"] == [str(dest)]
        assert dest.resolve() == src.resolve()

    def test_reports_missing_bundled_source(self, tmp_path):
        repo = tmp_path / "repo"
        hermes_home = tmp_path / "home" / ".hermes"
        manifest = hermes_home / ".bundled_scripts_manifest"
        dest = hermes_home / "bin" / "telegram-healthcheck.sh"

        with patch.object(bss, "_project_root", return_value=repo), patch.object(
            bss, "HERMES_HOME", hermes_home
        ), patch.object(bss, "SCRIPTS_MANIFEST", manifest), patch.object(
            bss,
            "BUNDLED_SCRIPTS",
            [("scripts/telegram-healthcheck-stateful.sh", "bin/telegram-healthcheck.sh")],
        ), patch.object(bss, "LEGACY_MANAGED_DESTINATIONS", []):
            result = bss.sync_bundled_scripts(quiet=True)

        assert result["missing"] == [str(dest)]

    def test_cleans_legacy_local_bin_symlink(self, tmp_path):
        repo = tmp_path / "repo"
        src = repo / "scripts" / "telegram-healthcheck-stateful.sh"
        src.parent.mkdir(parents=True)
        src.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

        hermes_home = tmp_path / "home" / ".hermes"
        manifest = hermes_home / ".bundled_scripts_manifest"
        legacy = tmp_path / "home" / ".local" / "bin" / "telegram-healthcheck-stateful"
        legacy.parent.mkdir(parents=True)
        legacy.symlink_to(src)
        manifest.parent.mkdir(parents=True)
        manifest.write_text(f"{legacy}:{src}\n", encoding="utf-8")

        with patch.object(bss, "_project_root", return_value=repo), patch.object(
            bss, "HERMES_HOME", hermes_home
        ), patch.object(bss, "SCRIPTS_MANIFEST", manifest), patch.object(
            bss,
            "BUNDLED_SCRIPTS",
            [("scripts/telegram-healthcheck-stateful.sh", "bin/telegram-healthcheck.sh")],
        ), patch.object(bss, "LEGACY_MANAGED_DESTINATIONS", [str(legacy)]):
            result = bss.sync_bundled_scripts(quiet=True)

        assert result["cleaned"] == [str(legacy)]
        assert not legacy.exists()
        manifest_text = manifest.read_text(encoding="utf-8")
        assert str(legacy) not in manifest_text
        assert str(hermes_home / "bin" / "telegram-healthcheck.sh") in manifest_text
