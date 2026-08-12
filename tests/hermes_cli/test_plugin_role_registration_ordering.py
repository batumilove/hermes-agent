"""Deterministic tests for plugin registration ordering vs runtime role.

Live acceptance of the provider-telemetry fix (commit b676a735) exposed a
startup-order bug: the CLI's ``_prepare_agent_startup()`` starts background
plugin discovery *before* ``GatewayRunner`` declares ``runtime_role="gateway"``.
The gateway's own ``set_runtime_role("gateway"); discover_plugins()`` ran too
late, because ``discover_plugins()`` is idempotent and the plugin had already
registered under the "unknown" passive role. This file pins that ordering in
unit tests and verifies the re-registration safety net.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    discover_plugins,
    get_plugin_manager,
)


def _make_plugin_dir(
    base: Path,
    name: str,
    *,
    register_body: str,
    manifest_extra: dict | None = None,
) -> Path:
    """Create a minimal plugin directory with plugin.yaml + __init__.py."""
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": f"Test plugin {name}",
    }
    if manifest_extra:
        manifest.update(manifest_extra)

    (plugin_dir / "plugin.yaml").write_text(yaml.dump(manifest))
    (plugin_dir / "__init__.py").write_text(register_body)

    hermes_home_str = os.environ.get("HERMES_HOME")
    if hermes_home_str:
        hermes_home = Path(hermes_home_str)
    else:
        hermes_home = base.parent
    hermes_home.mkdir(parents=True, exist_ok=True)
    cfg_path = hermes_home / "config.yaml"
    cfg: dict = {}
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            cfg = {}
    plugins_cfg = cfg.setdefault("plugins", {})
    enabled = plugins_cfg.setdefault("enabled", [])
    if isinstance(enabled, list) and name not in enabled:
        enabled.append(name)
    cfg_path.write_text(yaml.safe_dump(cfg))

    return plugin_dir


def _stub_bundled(monkeypatch, plugins_mod, tmp_path) -> None:
    """Point bundled plugin discovery at an empty directory."""
    empty = tmp_path / "bundled"
    empty.mkdir()
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: empty)


class _RecordingContext(PluginContext):
    """PluginContext that records the role observed during register()."""

    _recorded: dict[str, Any] | None = None

    def _record_role(self, role: str, can_claim: bool) -> None:
        if self._recorded is not None:
            self._recorded["role"] = role
            self._recorded["can_claim"] = can_claim


@pytest.fixture
def recording_ctx_class(monkeypatch):
    """Patch PluginContext to the recording subclass for the test."""
    from hermes_cli import plugins as plugins_mod

    class _BoundRecordingContext(_RecordingContext):
        pass

    monkeypatch.setattr(plugins_mod, "PluginContext", _BoundRecordingContext)
    return _BoundRecordingContext


# -----------------------------------------------------------------------------
# CLI gateway startup ordering
# -----------------------------------------------------------------------------


def test_gateway_role_set_after_discovery_triggers_reregistration(
    tmp_path, monkeypatch, recording_ctx_class
):
    """GREEN: gateway runner can claim the writer even when CLI discovery ran first.

    Without the role-transition safety net this would reproduce the live bug:
    the probe plugin registers during early discovery under the "unknown" role
    and is never re-invoked after ``set_runtime_role('gateway')``.
    """
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    recorded: dict[str, Any] = {}
    recording_ctx_class._recorded = recorded

    _make_plugin_dir(
        home / "plugins",
        "role-probe",
        register_body=(
            "def register(ctx):\n"
            "    ctx._record_role(ctx.runtime_role, ctx.can_claim_provider_telemetry_writer)\n"
            "    ctx.register_hook('pre_api_request', lambda **kw: None)\n"
        ),
    )

    manager = PluginManager()
    # Simulate CLI startup: background discovery runs before gateway role is set.
    manager.discover_and_load()
    assert recorded.get("role") == "unknown"
    assert recorded.get("can_claim") is False

    # Simulate GatewayRunner.start(): role is declared, then discover_plugins.
    manager.set_runtime_role("gateway")
    assert manager._role_transitioned_to_claiming is True
    manager.discover_and_load()
    assert manager._role_transitioned_to_claiming is False

    assert recorded.get("role") == "gateway"
    assert recorded.get("can_claim") is True
    assert manager.has_hook("pre_api_request") is True


def test_cli_ref_set_after_discovery_triggers_reregistration(
    tmp_path, monkeypatch, recording_ctx_class
):
    """CLI chat sets ``_cli_ref`` after early discovery; plugins must re-register."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    recorded: dict[str, Any] = {}
    recording_ctx_class._recorded = recorded

    _make_plugin_dir(
        home / "plugins",
        "role-probe",
        register_body=(
            "def register(ctx):\n"
            "    ctx._record_role(ctx.runtime_role, ctx.can_claim_provider_telemetry_writer)\n"
            "    ctx.register_hook('pre_api_request', lambda **kw: None)\n"
        ),
    )

    manager = PluginManager()
    manager.discover_and_load()
    assert recorded.get("role") == "unknown"
    assert recorded.get("can_claim") is False

    # Simulate cli.py setting the CLI reference after PluginManager exists.
    manager._cli_ref = object()
    assert manager._role_transitioned_to_claiming is True
    manager.discover_and_load()
    assert manager._role_transitioned_to_claiming is False

    assert recorded.get("role") == "cli"
    assert recorded.get("can_claim") is True


def test_dashboard_stays_passive_after_discovery(tmp_path, monkeypatch):
    """A passive dashboard surface does not trigger re-registration."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "token")
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    _make_plugin_dir(
        home / "plugins",
        "role-probe",
        register_body=(
            "def register(ctx):\n"
            "    ctx.register_hook('pre_api_request', lambda **kw: None)\n"
        ),
    )

    manager = PluginManager()
    manager.discover_and_load()
    assert manager.has_hook("pre_api_request") is True

    # Calling discover_plugins again with no role change is a no-op.
    manager.discover_and_load()
    assert manager._role_transitioned_to_claiming is False


def test_role_transition_re_registration_happens_exactly_once(
    tmp_path, monkeypatch
):
    """Multiple set_runtime_role calls after discovery only re-register once."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    _make_plugin_dir(
        home / "plugins",
        "counter",
        register_body=(
            "def register(ctx):\n"
            "    ctx.register_hook('pre_api_request', lambda **kw: None)\n"
        ),
    )

    manager = PluginManager()
    manager.discover_and_load()

    manager.set_runtime_role("gateway")
    manager.discover_and_load()
    manager.set_runtime_role("gateway")
    manager.discover_and_load()
    manager.set_runtime_role("cli")
    manager.discover_and_load()

    # Force re-discovery clears the registry before re-registering, so there
    # must never be duplicate callbacks.
    hooks = manager._hooks.get("pre_api_request", [])
    assert len(hooks) == 1


def test_force_rediscovery_clears_and_re_registers_hooks(
    tmp_path, monkeypatch
):
    """discover_plugins(force=True) clears stale hooks and re-registers."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    _make_plugin_dir(
        home / "plugins",
        "hooker",
        register_body=(
            "def register(ctx):\n"
            "    ctx.register_hook('pre_api_request', lambda **kw: None)\n"
        ),
    )

    manager = PluginManager()
    manager.discover_and_load()
    assert len(manager._hooks.get("pre_api_request", [])) == 1

    manager.discover_and_load(force=True)
    assert len(manager._hooks.get("pre_api_request", [])) == 1


def test_unknown_to_gateway_role_transition_with_global_manager(
    tmp_path, monkeypatch, recording_ctx_class
):
    """Use the module-level discover_plugins() to match real CLI/gateway wiring."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    recorded: dict[str, Any] = {}
    recording_ctx_class._recorded = recorded

    _make_plugin_dir(
        home / "plugins",
        "role-probe",
        register_body=(
            "def register(ctx):\n"
            "    ctx._record_role(ctx.runtime_role, ctx.can_claim_provider_telemetry_writer)\n"
            "    ctx.register_hook('pre_api_request', lambda **kw: None)\n"
        ),
    )

    # Module singleton starts undiscovered.
    discover_plugins()
    assert recorded.get("role") == "unknown"
    assert recorded.get("can_claim") is False

    get_plugin_manager().set_runtime_role("gateway")
    discover_plugins()

    assert recorded.get("role") == "gateway"
    assert recorded.get("can_claim") is True


def test_gateway_role_set_after_discovery_without_safety_net_reproduces_bug(
    tmp_path, monkeypatch, recording_ctx_class
):
    """RED: without the role-transition safety net the bug reproduces.

    Simulates the old code where ``set_runtime_role('gateway')`` did not
    trigger re-registration, so the plugin remains stuck with the "unknown"
    role observed during early discovery.
    """
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    recorded: dict[str, Any] = {}
    recording_ctx_class._recorded = recorded

    _make_plugin_dir(
        home / "plugins",
        "role-probe",
        register_body=(
            "def register(ctx):\n"
            "    ctx._record_role(ctx.runtime_role, ctx.can_claim_provider_telemetry_writer)\n"
        ),
    )

    manager = PluginManager()
    manager.discover_and_load()
    assert recorded.get("role") == "unknown"

    # Disable the safety net to reproduce the original behavior.
    monkeypatch.setattr(manager, "_maybe_mark_role_transition_to_claiming", lambda: None)
    manager.set_runtime_role("gateway")
    manager.discover_and_load()

    assert recorded.get("role") == "unknown"
    assert recorded.get("can_claim") is False


def test_runtime_role_set_before_discovery_avoids_transition(
    tmp_path, monkeypatch, recording_ctx_class
):
    """When the role is declared early, no re-registration is needed."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_bundled(monkeypatch, plugins_mod, tmp_path)

    recorded: dict[str, Any] = {}
    recording_ctx_class._recorded = recorded

    _make_plugin_dir(
        home / "plugins",
        "role-probe",
        register_body=(
            "def register(ctx):\n"
            "    ctx._record_role(ctx.runtime_role, ctx.can_claim_provider_telemetry_writer)\n"
        ),
    )

    manager = PluginManager()
    manager.set_runtime_role("gateway")
    manager.discover_and_load()

    assert recorded.get("role") == "gateway"
    assert recorded.get("can_claim") is True
    assert manager._role_transitioned_to_claiming is False
