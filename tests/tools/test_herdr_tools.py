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


def test_send_text_can_submit_enter(monkeypatch):
    from tools import herdr_tools

    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_pane_send_text("pane1", "hello", submit=True))

    assert result["success"] is True
    assert seen == [
        ["herdr", "pane", "send-text", "pane1", "hello"],
        ["herdr", "pane", "send-keys", "pane1", "Enter"],
    ]


def test_run_prompt_waits_working_idle_then_settles_and_reads(monkeypatch):
    from tools import herdr_tools

    calls = []

    def fake_send(pane_id, text, submit=True, timeout=60):
        calls.append(("send", pane_id, text, submit))
        return json.dumps({"success": True})

    def fake_wait(pane_id, status, timeout_ms=30000, timeout=None):
        calls.append(("wait", pane_id, status, timeout_ms))
        return json.dumps({"success": True, "status": status})

    def fake_read(pane_id, lines=200, source="recent-unwrapped", timeout=60):
        calls.append(("read", pane_id, lines, source))
        return json.dumps({"success": True, "output": "...FINAL_TOKEN..."})

    monkeypatch.setattr(herdr_tools, "herdr_pane_send_text", fake_send)
    monkeypatch.setattr(herdr_tools, "herdr_wait_status", fake_wait)
    monkeypatch.setattr(herdr_tools, "herdr_pane_read", fake_read)
    monkeypatch.setattr(herdr_tools.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    result = json.loads(
        herdr_tools.herdr_run_prompt(
            "pane1",
            "do it",
            pre_send_settle_seconds=0.5,
            wait_working_ms=1000,
            wait_idle_ms=2000,
            settle_seconds=1.5,
            lines=321,
            expect="FINAL_TOKEN",
        )
    )

    assert result["success"] is True
    assert result["matched_expect"] is True
    assert result["output"] == "...FINAL_TOKEN..."
    assert calls == [
        ("sleep", 0.5),
        ("send", "pane1", "do it", True),
        ("wait", "pane1", "working", 1000),
        ("wait", "pane1", "idle", 2000),
        ("sleep", 1.5),
        ("read", "pane1", 321, "recent-unwrapped"),
    ]


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
        "herdr_pane_send_text",
        "herdr_run_prompt",
        "herdr_wait_status",
        "herdr_approval",
    }
