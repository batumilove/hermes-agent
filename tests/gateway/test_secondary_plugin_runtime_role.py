"""Served profiles activate role-gated plugins before their first model request."""
from pathlib import Path

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_role", ["unknown", "dashboard"])
async def test_secondary_startup_promotes_passive_plugins_in_own_home(tmp_path, monkeypatch, prior_role):
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner, _profile_runtime_scope
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    home = tmp_path / ".hermes"
    secondary = home / "profiles" / "alpha"
    plugin = secondary / "plugins" / "role_probe"
    plugin.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("{}\n")
    (secondary / "config.yaml").write_text("plugins:\n  enabled: [role_probe]\n")
    (plugin / "plugin.yaml").write_text("name: role_probe\nversion: '1.0'\ndescription: Role probe\n")
    (plugin / "__init__.py").write_text(
        "from hermes_constants import get_hermes_home\n"
        "def register(ctx):\n"
        "    if ctx.can_claim_provider_telemetry_writer:\n"
        "        (get_hermes_home() / 'writer-ready').write_text(ctx.runtime_role)\n"
    )
    with _profile_runtime_scope(secondary, {}):
        manager = get_plugin_manager()
        manager.set_runtime_role(prior_role)
        discover_plugins()
    assert not (secondary / "writer-ready").exists()

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner._subscribe_plugin_rewire = lambda *args: None
    runner._register_config_hooks = lambda *args, **kwargs: None
    runner._snapshot_profile_busy_modes = lambda *args: None
    await runner._load_secondary_profile_config("alpha", secondary)

    assert (secondary / "writer-ready").read_text() == "gateway"
    assert not (home / "writer-ready").exists()
    with _profile_runtime_scope(secondary, {}):
        assert get_plugin_manager() is manager
