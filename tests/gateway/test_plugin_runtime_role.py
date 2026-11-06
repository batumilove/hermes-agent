"""Gateway-owned scoped managers may claim telemetry; passive/unserved homes may not."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.run import _profile_runtime_scope
from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager


def _claimable(manager):
    ctx = PluginContext(manifest=PluginManifest(name="role_probe", version="0", description=""), manager=manager)
    return ctx.can_claim_provider_telemetry_writer


@pytest.mark.asyncio
async def test_secondary_config_discovery_has_gateway_capability(monkeypatch, tmp_path):
    from gateway.run import _profile_runtime_scope
    from gateway.run_adapters import GatewayAdapterLifecycleMixin

    home = tmp_path / "secondary"
    home.mkdir()
    observed = []
    with _profile_runtime_scope(home, hydrate_secrets=False):
        manager = get_plugin_manager()
        manager.set_runtime_role("dashboard")
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda *_: None)
    monkeypatch.setattr("gateway.run_startup.recover_left_core_in", lambda *a, **k: None)
    monkeypatch.setattr("gateway.run._load_gateway_config", dict)
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: object())
    monkeypatch.setattr("gateway.run._own_policy_open_startup_violation", lambda _: None)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: observed.append(_claimable(get_plugin_manager())))
    runner = MagicMock()
    await GatewayAdapterLifecycleMixin._load_secondary_profile_config(runner, "secondary", home)
    assert observed == [True]


@pytest.mark.asyncio
async def test_authority_initialization_binds_gateway_role_to_own_home(monkeypatch, tmp_path):
    from gateway import run_runtime
    from gateway.run import _profile_runtime_scope

    home = tmp_path / "authority"
    home.mkdir()
    db = SimpleNamespace(db_path=str(home / "state.db"))
    runner = SimpleNamespace(session_authorities=MagicMock(), session_runtime_descriptor={"instance_id": "test"})
    monkeypatch.setattr(run_runtime, "open_profile_store", lambda *_: db)
    observed = []
    async def initialize(*args, **kwargs):
        observed.append(_claimable(get_plugin_manager()))
        return object()
    monkeypatch.setattr("gateway.session_authority.initialize_session_authority", initialize)
    with _profile_runtime_scope(home, hydrate_secrets=False):
        get_plugin_manager().set_runtime_role("dashboard")
    await run_runtime._build_profile_authority(runner, "authority", home, register=False)
    assert observed == [True]


@pytest.mark.parametrize("served", [True, False])
def test_reload_only_grants_gateway_role_to_served_home(monkeypatch, tmp_path, served):
    from gateway.run import _profile_runtime_scope
    from gateway.run_plugin_rewire import reload_plugins_verb

    home = tmp_path / "reload"
    home.mkdir()
    with _profile_runtime_scope(home, hydrate_secrets=False):
        manager = get_plugin_manager()
        manager.set_runtime_role("dashboard")
        assert not _claimable(manager)
    observed = []
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda **_: observed.append(_claimable(get_plugin_manager())))
    monkeypatch.setattr("gateway.run_plugin_rewire._reconcile_plugin_mcp_servers", lambda *_: None)
    monkeypatch.setattr("hermes_cli.plugins_activation.activation_summaries", lambda _: [])
    monkeypatch.setattr("hermes_cli.plugins_activation.dependency_blocked_summaries", lambda _: [])
    def count(coro, loop):
        coro.close()
        return SimpleNamespace(result=lambda **_: 0)
    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", count)
    runner = SimpleNamespace(_served_profile_homes={"secondary": home} if served else {})
    result = reload_plugins_verb(runner, object())({"home": str(home)})
    assert result["reloaded"] is served
    assert observed == ([True] if served else [])
    assert _claimable(manager) is served
