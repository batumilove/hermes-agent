"""Tests for plugin runtime role detection and telemetry writer gating.

The dashboard is a passive supervisor: it loads plugins but never produces API
requests, so it must never own the provider telemetry writer lock. Request-
producing surfaces (CLI, gateway) may claim it.
"""
from __future__ import annotations

import pytest

from hermes_cli.plugins import PluginContext, PluginManifest, PluginManager


class _StubManager:
    _cli_ref = None
    _runtime_role = ""


def _ctx(*, runtime_role="", cli_ref=None):
    mgr = _StubManager()
    mgr._runtime_role = runtime_role
    mgr._cli_ref = cli_ref
    return PluginContext(
        manifest=PluginManifest(name="provider-telemetry", version="0.0.0", description="stub"),
        manager=mgr,  # type: ignore[arg-type]
    )


def test_runtime_role_prefers_explicit_manager_capability():
    ctx = _ctx(runtime_role="gateway")
    assert ctx.runtime_role == "gateway"
    assert ctx.can_claim_provider_telemetry_writer is True


def test_runtime_role_falls_back_to_cli_ref_when_present():
    ctx = _ctx(cli_ref=object())
    assert ctx.runtime_role == "cli"
    assert ctx.can_claim_provider_telemetry_writer is True


def test_dashboard_surface_does_not_claim_provider_writer(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "token")
    ctx = _ctx()
    assert ctx.runtime_role == "dashboard"
    assert ctx.can_claim_provider_telemetry_writer is False


def test_unknown_surface_stays_closed_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_DASHBOARD_READY", raising=False)
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)
    ctx = _ctx()
    assert ctx.runtime_role == "unknown"
    assert ctx.can_claim_provider_telemetry_writer is False


def test_profile_gateway_is_a_request_producing_surface():
    ctx = _ctx(runtime_role="gateway")
    assert ctx.runtime_role == "gateway"
    assert ctx.can_claim_provider_telemetry_writer is True


def test_cli_plugin_inspection_does_not_claim_as_gateway():
    ctx = _ctx(cli_ref=object())
    assert ctx.runtime_role == "cli"
    assert ctx.can_claim_provider_telemetry_writer is True


def test_set_runtime_role_normalizes_value():
    mgr = PluginManager()
    mgr.set_runtime_role("  GATEWAY  ")
    ctx = PluginContext(
        manifest=PluginManifest(name="provider-telemetry", version="0.0.0", description="stub"),
        manager=mgr,
    )
    assert ctx.runtime_role == "gateway"
    assert ctx.can_claim_provider_telemetry_writer is True


def test_explicit_role_overrides_dashboard_env(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "token")
    ctx = _ctx(runtime_role="gateway")
    assert ctx.runtime_role == "gateway"
    assert ctx.can_claim_provider_telemetry_writer is True
