"""PM ownership of the exact external Langfuse SDK and missing-SDK fail-open behavior."""
import hashlib
import json
import shutil

import hermes_yaml as yaml

from tests.plugins.external.langfuse_fixture import PLUGIN_DIR, REPO_ROOT, _load_plugin

class TestManagedDependencySelection:
    def test_exact_catalog_source_is_pm_dependency_member(self):
        from pm.plugin_declarations import read_python_declaration

        source = json.loads((PLUGIN_DIR / "SOURCE.json").read_text())
        catalog = yaml.safe_load((REPO_ROOT / "plugin-catalog/langfuse.yaml").read_text())
        assert (catalog["repo"], catalog["sha"], catalog["subdir"]) == (
            source["repository"], source["commit"], source["subdir"],
        )
        for name, expected in source["files"].items():
            assert hashlib.sha256((PLUGIN_DIR / name).read_bytes()).hexdigest() == expected
        declaration = read_python_declaration(PLUGIN_DIR)
        assert declaration.is_member
        assert declaration.install_requirements == ("langfuse==4.15.2",)

    def test_enabled_plugin_keeps_sdk_member_after_selection_refresh(self, tmp_path, monkeypatch):
        from pm.workspace import enabled_member_dirs
        import hermes_yaml

        home = tmp_path / "hermes"
        plugin = home / "plugins" / "langfuse"
        shutil.copytree(PLUGIN_DIR, plugin)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: home)
        monkeypatch.setattr("hermes_cli.plugins_manifest.running_hermes_version", lambda: "0.21.7")
        # Durable plugin selection stays present when the base image's environment changes.
        (home / "config.yaml").write_text(hermes_yaml.safe_dump({"plugins": {"enabled": ["langfuse"]}}))
        assert enabled_member_dirs() == [plugin]
        assert enabled_member_dirs(proposed_home=home, enabled=[]) == []
        assert enabled_member_dirs() == [plugin]


def test_missing_sdk_does_not_install_or_raise_on_import(monkeypatch):
    import sys
    from tools import lazy_deps

    monkeypatch.setitem(sys.modules, "langfuse", None)
    monkeypatch.setattr(lazy_deps, "ensure", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plugin tried legacy dependency installation")))
    plugin = _load_plugin()
    assert plugin.Langfuse is None
    assert plugin._get_langfuse() is None
    plugin.on_pre_llm_call(session_id="missing-sdk", task_id="task")
