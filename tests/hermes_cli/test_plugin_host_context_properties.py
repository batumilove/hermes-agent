"""Scalar PluginContext properties keep their values across process isolation."""
import json

import hermes_yaml as yaml
import pytest

from hermes_cli.plugins import PluginManager
from hermes_cli.plugins_manifest import parse_manifest_file


@pytest.mark.platforms("any")
@pytest.mark.parametrize("role,allowed", [("gateway", True), ("dashboard", False)])
def test_hosted_plugin_observes_role_and_boolean_writer_capability(tmp_path, monkeypatch, role, allowed):
    home = tmp_path / "profile"
    plugin = home / "plugins" / "role-probe"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: role-probe\nversion: '1.0'\n")
    (plugin / "__init__.py").write_text('''
import json
from pathlib import Path
from hermes_constants import get_hermes_home

def register(ctx):
    (get_hermes_home() / "role.json").write_text(json.dumps({
        "role": ctx.runtime_role,
        "allowed": ctx.can_claim_provider_telemetry_writer,
    }))
''')
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"isolation": "host", "enabled": ["role-probe"]}}))
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager(scope_key=str(home))
    manager.set_runtime_role(role)
    manifest = parse_manifest_file(plugin / "plugin.yaml", plugin, "user", "")
    assert manifest is not None
    try:
        manager._load_plugin(manifest)
        loaded = manager._plugins["role-probe"]
        assert loaded.enabled, loaded.error
        assert json.loads((home / "role.json").read_text()) == {"role": role, "allowed": allowed}
    finally:
        manager.unload()
        manager._plugin_host().shutdown()
