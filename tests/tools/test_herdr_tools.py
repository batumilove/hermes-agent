import json
import subprocess

import pytest


def test_spawn_uses_agent_start_and_returns_handle(monkeypatch):
    from tools import herdr_tools

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert cmd == [
            "herdr",
            "agent",
            "start",
            "worker-1",
            "--cwd",
            "/tmp/work",
            "--workspace",
            "ws1",
            "--no-focus",
            "--",
            "hermes",
            "-w",
        ]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "result": {
                        "agent": {
                            "workspace_id": "ws1",
                            "pane_id": "pane1",
                            "tab_id": "ws1:1",
                            "name": "worker-1",
                            "agent_status": "idle",
                        }
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(
        herdr_tools.herdr_agent_start(
            name="worker-1",
            cwd="/tmp/work",
            workspace_id="ws1",
            argv=["hermes", "-w"],
        )
    )

    assert result == {
        "success": True,
        "workspace_id": "ws1",
        "pane_id": "pane1",
        "tab_id": "ws1:1",
        "name": "worker-1",
        "agent_status": "idle",
        "raw": {
            "workspace_id": "ws1",
            "pane_id": "pane1",
            "tab_id": "ws1:1",
            "name": "worker-1",
            "agent_status": "idle",
        },
    }
    assert calls


def test_read_uses_recent_unwrapped_by_default(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        assert cmd == [
            "herdr",
            "pane",
            "read",
            "pane1",
            "--source",
            "recent-unwrapped",
            "--lines",
            "120",
        ]
        return subprocess.CompletedProcess(cmd, 0, stdout="hello\n", stderr="")

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_pane_read("pane1", lines=120))

    assert result == {"success": True, "pane_id": "pane1", "output": "hello\n"}


def test_approval_deny_sends_down_down_down_enter(monkeypatch):
    from tools import herdr_tools

    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_approval("pane1", action="deny"))

    assert result["success"] is True
    assert seen == [["herdr", "pane", "send-keys", "pane1", "Down", "Down", "Down", "Enter"]]


def test_unknown_status_is_recoverable_after_reboot():
    from tools.herdr_tools import classify_agent_status

    assert classify_agent_status("unknown") == "needs_resume"
    assert classify_agent_status("idle") == "ready"
    assert classify_agent_status("blocked") == "needs_approval"
    assert classify_agent_status("working") == "running"


def test_herdr_toolset_resolves_to_adapter_tools():
    from toolsets import resolve_toolset

    assert set(resolve_toolset("herdr")) == {
        "herdr_agent_start",
        "herdr_pane_read",
        "herdr_wait_status",
        "herdr_approval",
    }
