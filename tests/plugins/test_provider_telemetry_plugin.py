"""Deterministic RED/GREEN tests for provider telemetry writer ownership.

The user-installed ``provider_telemetry`` plugin lives outside this repo at
``~/.hermes/plugins/provider_telemetry/__init__.py``. To keep these tests
isolated from the live install while still exercising the real plugin code,
each test copies the installed plugin into a temporary module and optionally
applies the ownership-fix patch. This gives us deterministic behavior even
before the live plugin is updated.

RED scenario (before fix): a passive dashboard context loads the plugin, the
plugin claims the exclusive writer lock, and API hooks are registered. Because
the dashboard never fires API hooks, the metrics file goes stale and real
gateways cannot claim the lock.

GREEN scenario (after fix): the plugin consults
``ctx.can_claim_provider_telemetry_writer`` and refuses to claim the lock or
register hooks on passive surfaces. Request-producing surfaces (gateway, CLI)
claim the lock, register hooks, preserve prior metrics/state, and respect
contention from another live owner.
"""
from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from hermes_cli.plugins import PluginContext, PluginManifest, PluginManager


# Prefer the installed user plugin; fall back to bundled candidate path.
_PLUGIN_CANDIDATES = [
    Path.home() / ".hermes" / "plugins" / "provider_telemetry" / "__init__.py",
    Path("/home/ubuntu/.hermes/plugins/provider_telemetry/__init__.py"),
    Path(__file__).resolve().parents[2] / "plugins" / "observability" / "provider_telemetry" / "__init__.py",
]


def _installed_plugin_path() -> Path:
    for path in _PLUGIN_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "provider_telemetry plugin not found under ~/.hermes/plugins or plugins/observability"
    )


def _load_plugin_module(tmp_path: Path, patched: bool = True, module_name: str = "provider_telemetry_under_test"):
    """Return an imported provider_telemetry module, optionally with the fix."""
    source = _installed_plugin_path().read_text(encoding="utf-8")
    if patched:
        source = source.replace(
            "def register(ctx: Any) -> None:\n    if not _claim_writer_ownership():",
            "def register(ctx: Any) -> None:\n    if not getattr(ctx, \"can_claim_provider_telemetry_writer\", True):\n"
            "        _warn(\"provider telemetry writer ownership skipped on passive surface\")\n"
            "        return\n    if not _claim_writer_ownership():",
        )
    plugin_dir = tmp_path / module_name
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "__init__.py"
    plugin_file.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, plugin_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _FakeContext:
    """Minimal PluginContext stand-in for the native plugin register() call."""

    def __init__(self, can_claim: bool = True, role: str = "gateway"):
        self.hooks: dict[str, callable] = {}
        self._can_claim = can_claim
        self._role = role

    @property
    def can_claim_provider_telemetry_writer(self) -> bool:
        return self._can_claim

    @property
    def runtime_role(self) -> str:
        return self._role

    def register_hook(self, hook_name: str, callback) -> None:
        self.hooks[hook_name] = callback


@pytest.fixture
def telemetry_env(monkeypatch, tmp_path):
    """Point provider telemetry at temp paths and clean module state."""
    metrics_file = tmp_path / "hermes_provider_telemetry.prom"
    lock_file = metrics_file.with_suffix(metrics_file.suffix + ".lock")
    monkeypatch.setenv("HERMES_PROVIDER_TELEMETRY_METRICS_FILE", str(metrics_file))
    # Ensure no stale state leaks between tests.
    for name in list(sys.modules.keys()):
        if name.startswith("provider_telemetry_"):
            del sys.modules[name]
    yield {"metrics_file": metrics_file, "lock_file": lock_file}


# ---------------------------------------------------------------------------
# Role/context gating
# ---------------------------------------------------------------------------


def test_dashboard_context_does_not_claim_lock_or_register_hooks(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=False, role="dashboard")
    module.register(ctx)
    assert not ctx.hooks
    assert not telemetry_env["metrics_file"].exists()
    assert not telemetry_env["lock_file"].exists()


def test_gateway_context_claims_lock_and_registers_hooks(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=True, role="gateway")
    module.register(ctx)
    assert "pre_api_request" in ctx.hooks
    assert "post_api_request" in ctx.hooks
    assert "api_request_error" in ctx.hooks
    assert "on_fallback_activated" in ctx.hooks
    assert "on_fallback_chain_exhausted" in ctx.hooks
    assert "on_primary_restored" in ctx.hooks
    assert telemetry_env["metrics_file"].exists()
    body = telemetry_env["metrics_file"].read_text()
    assert "hermes_provider_telemetry_up 1" in body


def test_cli_context_claims_lock(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=True, role="cli")
    module.register(ctx)
    assert "pre_api_request" in ctx.hooks
    assert telemetry_env["metrics_file"].exists()


def test_unknown_context_stays_closed(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=False, role="unknown")
    module.register(ctx)
    assert not ctx.hooks
    assert not telemetry_env["metrics_file"].exists()


# ---------------------------------------------------------------------------
# Integration with PluginContext capability
# ---------------------------------------------------------------------------


def test_real_dashboard_context_gated_by_plugin_manager(monkeypatch, telemetry_env, tmp_path):
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "token")
    manager = PluginManager()
    ctx = PluginContext(
        manifest=PluginManifest(name="provider-telemetry", version="0.0.0", description="stub"),
        manager=manager,
    )
    assert ctx.runtime_role == "dashboard"
    assert ctx.can_claim_provider_telemetry_writer is False
    module = _load_plugin_module(tmp_path, patched=True)
    fake_ctx = _FakeContext(can_claim=ctx.can_claim_provider_telemetry_writer, role=ctx.runtime_role)
    module.register(fake_ctx)
    assert not fake_ctx.hooks


def test_real_gateway_context_allowed_by_plugin_manager(telemetry_env, tmp_path):
    manager = PluginManager()
    manager.set_runtime_role("gateway")
    ctx = PluginContext(
        manifest=PluginManifest(name="provider-telemetry", version="0.0.0", description="stub"),
        manager=manager,
    )
    assert ctx.runtime_role == "gateway"
    assert ctx.can_claim_provider_telemetry_writer is True
    module = _load_plugin_module(tmp_path, patched=True)
    fake_ctx = _FakeContext(can_claim=ctx.can_claim_provider_telemetry_writer, role=ctx.runtime_role)
    module.register(fake_ctx)
    assert "pre_api_request" in fake_ctx.hooks


def test_real_cli_context_allowed_by_plugin_manager(telemetry_env, tmp_path):
    manager = PluginManager()
    manager._cli_ref = object()
    ctx = PluginContext(
        manifest=PluginManifest(name="provider-telemetry", version="0.0.0", description="stub"),
        manager=manager,
    )
    assert ctx.runtime_role == "cli"
    assert ctx.can_claim_provider_telemetry_writer is True
    module = _load_plugin_module(tmp_path, patched=True)
    fake_ctx = _FakeContext(can_claim=ctx.can_claim_provider_telemetry_writer, role=ctx.runtime_role)
    module.register(fake_ctx)
    assert "pre_api_request" in fake_ctx.hooks


# ---------------------------------------------------------------------------
# Lock contention and stale owner recovery
# ---------------------------------------------------------------------------


def test_gateway_respects_lock_held_by_another_live_gateway(telemetry_env, tmp_path):
    lock_path = telemetry_env["lock_file"]

    # Start a separate process that holds the writer lock. We cannot simulate a
    # contending owner in the same process because flock is per-process.
    holder_script = """\
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print('locked')
sys.stdout.flush()
sys.stdin.read()
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        marker = holder.stdout.readline()
        assert marker.strip() == "locked"

        module = _load_plugin_module(tmp_path, patched=True)
        ctx = _FakeContext(can_claim=True, role="gateway")
        module.register(ctx)
        assert not ctx.hooks
        assert not telemetry_env["metrics_file"].exists()
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)


def test_gateway_recovers_after_prior_owner_releases_lock(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx1 = _FakeContext(can_claim=True, role="gateway")
    module.register(ctx1)
    assert "pre_api_request" in ctx1.hooks

    # Simulate prior owner going away: reset module state but keep metrics file.
    module.reset_metrics_for_tests()

    module2 = _load_plugin_module(tmp_path, patched=True, module_name="provider_telemetry_recovery")
    ctx2 = _FakeContext(can_claim=True, role="gateway")
    module2.register(ctx2)
    assert "pre_api_request" in ctx2.hooks
    # Metrics/state should still exist (file was written by first owner and
    # reloaded by the second).
    assert telemetry_env["metrics_file"].exists()
    assert "hermes_provider_telemetry_up 1" in telemetry_env["metrics_file"].read_text()


def test_lock_file_records_owner_pid(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=True, role="gateway")
    module.register(ctx)
    lock_path = telemetry_env["lock_file"]
    assert lock_path.exists()
    pid = lock_path.read_text(encoding="utf-8").strip()
    assert pid == str(os.getpid())


# ---------------------------------------------------------------------------
# Hook behavior and metrics/state preservation
# ---------------------------------------------------------------------------


def test_success_path_records_request_and_latency(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=True, role="gateway")
    module.register(ctx)

    ctx.hooks["pre_api_request"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-1",
        approx_input_tokens=86613,
        started_at=1000.0,
    )
    ctx.hooks["post_api_request"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-1",
        api_duration=1.25,
        started_at=1000.0,
        ended_at=1001.25,
    )

    body = telemetry_env["metrics_file"].read_text()
    assert (
        'hermes_model_requests_total{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 1'
        in body
    )
    assert (
        'hermes_model_request_duration_seconds_sum{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 1.25'
        in body
    )
    assert (
        'hermes_model_approx_input_tokens{provider="openai-codex",model="gpt-5.6-sol"} 86613'
        in body
    )


def test_metrics_are_reloaded_from_disk_across_restarts(telemetry_env, tmp_path):
    module1 = _load_plugin_module(tmp_path, patched=True)
    ctx1 = _FakeContext(can_claim=True, role="gateway")
    module1.register(ctx1)
    ctx1.hooks["post_api_request"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-1",
        api_duration=2.0,
        started_at=1000.0,
        ended_at=1002.0,
    )

    # Simulate restart: fresh module, same metrics file.
    module1.reset_metrics_for_tests()
    module2 = _load_plugin_module(tmp_path, patched=True, module_name="provider_telemetry_restart")
    ctx2 = _FakeContext(can_claim=True, role="gateway")
    module2.register(ctx2)
    ctx2.hooks["post_api_request"](
        provider="openai-codex",
        model="gpt-5.6-sol",
        api_request_id="api-2",
        api_duration=3.0,
        started_at=1000.0,
        ended_at=1003.0,
    )

    body = telemetry_env["metrics_file"].read_text()
    assert (
        'hermes_model_requests_total{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 2'
        in body
    )
    assert (
        'hermes_model_request_duration_seconds_sum{provider="openai-codex",model="gpt-5.6-sol",outcome="success"} 5'
        in body
    )


def test_hook_registration_matches_manifest(telemetry_env, tmp_path):
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=True, role="gateway")
    module.register(ctx)
    expected = {
        "pre_api_request",
        "post_api_request",
        "api_request_error",
        "on_fallback_activated",
        "on_fallback_chain_exhausted",
        "on_primary_restored",
    }
    assert expected.issubset(set(ctx.hooks))


# ---------------------------------------------------------------------------
# RED/GREEN regression: passive surface must not own the writer
# ---------------------------------------------------------------------------


def test_unpatched_plugin_reproduces_dashboard_bug(telemetry_env, tmp_path):
    """RED: without the ctx.can_claim guard the dashboard claims the lock."""
    module = _load_plugin_module(tmp_path, patched=False)
    ctx = _FakeContext(can_claim=False, role="dashboard")
    module.register(ctx)
    # Without the fix the plugin ignores the capability and claims anyway.
    assert telemetry_env["lock_file"].exists()
    assert telemetry_env["metrics_file"].exists()
    assert ctx.hooks


def test_patched_plugin_fixes_dashboard_bug(telemetry_env, tmp_path):
    """GREEN: the same dashboard context is now gated out."""
    module = _load_plugin_module(tmp_path, patched=True)
    ctx = _FakeContext(can_claim=False, role="dashboard")
    module.register(ctx)
    assert not telemetry_env["lock_file"].exists()
    assert not telemetry_env["metrics_file"].exists()
    assert not ctx.hooks
