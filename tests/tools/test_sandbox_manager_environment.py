import json

import pytest

from tools.environments.sandbox_manager import SandboxManagerEnvironment
import tools.terminal_tool as terminal_tool


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_sandbox_manager_execute_invokes_ssh_cli_with_safe_defaults(monkeypatch):
    calls = []
    result = {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 12,
        "image_digest": "sha256:abc",
        "network_profile": "offline",
        "timed_out": False,
        "resource_status": {},
        "cleanup": {"ok": True, "detail": "removed"},
    }

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeCompleted(stdout=json.dumps(result), returncode=0)

    monkeypatch.setattr("tools.environments.sandbox_manager.subprocess.run", fake_run)

    env = SandboxManagerEnvironment(
        ssh_host="192.168.10.141",
        ssh_user="sandbox",
        manager_dir="/opt/agent-sandbox-manager",
        config_path="config/sandbox-manager.example.json",
        runtime="alpine",
        network_profile="offline",
        timeout=30,
        output_bytes=4096,
    )

    executed = env.execute("printf hello", timeout=7)

    assert executed["returncode"] == 0
    assert executed["output"] == "hello\n"
    assert executed["sandbox_result"]["image_digest"] == "sha256:abc"
    assert executed["sandbox_result"]["network_profile"] == "offline"
    args, kwargs = calls[0]
    assert args[:4] == ["ssh", "-o", "BatchMode=yes", "sandbox@192.168.10.141"]
    remote = args[4]
    assert remote.startswith("cd /opt/agent-sandbox-manager &&")
    assert "python3 -m sandbox_manager.manager" in remote
    assert "--job-json" in remote
    job = json.loads(remote.split("--job-json ", 1)[1].split("'", 2)[1])
    assert job == {
        "command": "printf hello",
        "runtime": "alpine",
        "network": "offline",
        "max_runtime_seconds": 7,
        "env": {},
        "trusted": False,
    }
    assert kwargs["timeout"] == 12


def test_sandbox_manager_rejects_secret_environment_names():
    env = SandboxManagerEnvironment(
        ssh_host="192.168.10.141",
        ssh_user="sandbox",
        env={"HERMES_HOME": "/home/ubuntu/.hermes"},
    )

    with pytest.raises(ValueError, match="forbidden"):
        env.execute("true")


def test_sandbox_manager_rejects_stdin_data():
    env = SandboxManagerEnvironment(
        ssh_host="192.168.10.141",
        ssh_user="sandbox",
    )

    with pytest.raises(ValueError, match="does not support stdin_data"):
        env.execute("cat", stdin_data="secret-ish input")


def test_sandbox_manager_rejects_unsafe_ssh_identity():
    with pytest.raises(ValueError, match="ssh_user contains unsupported"):
        SandboxManagerEnvironment(
            ssh_host="192.168.10.141",
            ssh_user="sandbox -oProxyCommand=evil",
        )

    with pytest.raises(ValueError, match="ssh_host contains unsupported"):
        SandboxManagerEnvironment(
            ssh_host="192.168.10.141 -oProxyCommand=evil",
            ssh_user="sandbox",
        )


def test_sandbox_manager_enforces_client_output_cap(monkeypatch):
    result = {"stdout": "abcdef", "stderr": "gh", "exit_code": 0}

    monkeypatch.setattr(
        "tools.environments.sandbox_manager.subprocess.run",
        lambda *args, **kwargs: FakeCompleted(stdout=json.dumps(result), returncode=0),
    )

    env = SandboxManagerEnvironment(
        ssh_host="192.168.10.141",
        ssh_user="sandbox",
        output_bytes=4,
    )

    executed = env.execute("printf long")

    assert executed["output"] == "abcd"


def test_terminal_tool_config_and_factory_support_sandbox_manager(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "sandbox_manager")
    monkeypatch.setenv("TERMINAL_SANDBOX_SSH_HOST", "192.168.10.141")
    monkeypatch.setenv("TERMINAL_SANDBOX_SSH_USER", "sandbox")
    monkeypatch.setenv("TERMINAL_SANDBOX_MANAGER_DIR", "/opt/agent-sandbox-manager")
    monkeypatch.setenv("TERMINAL_SANDBOX_RUNTIME", "python")
    monkeypatch.setenv("TERMINAL_SANDBOX_NETWORK", "offline")
    monkeypatch.setenv("TERMINAL_SANDBOX_OUTPUT_BYTES", "1234")

    config = terminal_tool._get_env_config()
    assert config["env_type"] == "sandbox_manager"
    assert config["sandbox_manager"]["ssh_host"] == "192.168.10.141"
    assert config["sandbox_manager"]["runtime"] == "python"
    assert config["sandbox_manager"]["network_profile"] == "offline"
    assert config["sandbox_manager"]["output_bytes"] == 1234

    env = terminal_tool._create_environment(
        "sandbox_manager",
        image="ignored",
        cwd="/root",
        timeout=30,
        container_config=config,
    )
    assert isinstance(env, SandboxManagerEnvironment)
    assert env.network_profile == "offline"
    assert env.runtime == "python"


def test_execute_code_reports_sandbox_manager_unsupported(monkeypatch):
    import tools.code_execution_tool as code_execution_tool

    monkeypatch.setattr(code_execution_tool, "SANDBOX_AVAILABLE", True)
    monkeypatch.setattr(
        terminal_tool,
        "_get_env_config",
        lambda: {"env_type": "sandbox_manager"},
    )
    monkeypatch.setattr(
        "tools.approval.check_execute_code_guard",
        lambda code, env_type: {"approved": True},
    )

    result = json.loads(code_execution_tool.execute_code("print('hello')"))

    assert result["status"] == "error"
    assert "not supported with terminal.backend=sandbox_manager" in result["error"]
    assert result["tool_calls_made"] == 0
