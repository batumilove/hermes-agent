import sys
import types
import builtins

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig


def test_local_gateway_preloads_run_agent_module(monkeypatch):
    class FakeAgent:
        pass

    fake_run_agent = types.SimpleNamespace(AIAgent=FakeAgent)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    imported: list[str] = []
    real_import = builtins.__import__

    def recording_import(name, *args, **kwargs):
        if name == "run_agent":
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", recording_import)
    runner = types.SimpleNamespace(_get_proxy_url=lambda: None)

    assert gateway_run._preload_gateway_agent_runtime(runner) is True
    assert imported == ["run_agent"]


def test_local_gateway_preload_resolves_lazy_openai_sdk(monkeypatch):
    class FakeAgent:
        pass

    fake_run_agent = types.SimpleNamespace(AIAgent=FakeAgent)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    import agent.process_bootstrap as process_bootstrap

    loaded: list[str] = []
    monkeypatch.setattr(
        process_bootstrap,
        "_load_openai_cls",
        lambda: loaded.append("openai.OpenAI") or object,
    )

    assert gateway_run._load_gateway_agent_class() is FakeAgent
    assert loaded == ["openai.OpenAI"]


def test_proxy_gateway_skips_local_agent_runtime_preload(monkeypatch):
    runner = types.SimpleNamespace(_get_proxy_url=lambda: "https://proxy.invalid")
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_agent_class",
        lambda: (_ for _ in ()).throw(AssertionError("must not preload in proxy mode")),
        raising=False,
    )

    assert gateway_run._preload_gateway_agent_runtime(runner) is False


def test_local_agent_preload_failure_preserves_degraded_gateway_startup(monkeypatch):
    runner = types.SimpleNamespace(_get_proxy_url=lambda: None)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_agent_class",
        lambda: (_ for _ in ()).throw(ImportError("broken optional runtime")),
        raising=False,
    )

    assert gateway_run._preload_gateway_agent_runtime(runner) is False


def test_startup_proxy_probe_caches_local_mode_for_message_hot_path(monkeypatch):
    calls = 0

    def load_config():
        nonlocal calls
        calls += 1
        return {"gateway": {}}

    runner = gateway_run.GatewayRunner.__new__(gateway_run.GatewayRunner)
    monkeypatch.delenv("GATEWAY_PROXY_URL", raising=False)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", load_config)
    monkeypatch.setattr(gateway_run, "_load_gateway_agent_class", lambda: object)

    assert gateway_run._preload_gateway_agent_runtime(runner) is True
    assert runner._get_proxy_url() is None
    assert calls == 1


@pytest.mark.asyncio
async def test_start_gateway_preloads_agent_before_platform_start(
    monkeypatch, tmp_path
):
    events: list[str] = []

    class CleanExitRunner:
        def __init__(self, config):
            self.config = config
            self.adapters = {}
            self.should_exit_cleanly = True
            self.should_exit_with_failure = False
            self.exit_reason = None
            self.exit_code = None
            self._running = False

        async def start(self):
            events.append("runner.start")
            return True

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gateway_run, "GatewayRunner", CleanExitRunner)
    monkeypatch.setattr(
        gateway_run,
        "_preload_gateway_agent_runtime",
        lambda runner: events.append("agent.preload") or True,
        raising=False,
    )
    monkeypatch.setattr(
        gateway_run, "_ensure_windows_gateway_venv_imports", lambda: None
    )
    monkeypatch.setattr("gateway.code_skew.record_boot_fingerprint", lambda: None)
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("gateway.status.acquire_gateway_runtime_lock", lambda: True)
    monkeypatch.setattr("gateway.status.release_gateway_runtime_lock", lambda: None)
    monkeypatch.setattr("gateway.status.write_pid_file", lambda: None)
    monkeypatch.setattr("gateway.status.remove_pid_file", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr(
        "hermes_logging.setup_logging", lambda hermes_home, mode: tmp_path / "logs"
    )
    monkeypatch.setattr(
        "hermes_cli.security_audit_startup.log_startup_security_warnings",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "hermes_cli.nous_auth_keepalive.start_nous_auth_keepalive", lambda: None
    )
    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", lambda: None)

    ok = await gateway_run.start_gateway(
        config=GatewayConfig(), replace=False, verbosity=None
    )

    assert ok is True
    assert events == ["agent.preload", "runner.start"]
