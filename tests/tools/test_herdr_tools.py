import json
import socket
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
        "ready": None,
        "raw": {
            "workspace_id": "ws1",
            "pane_id": "pane1",
            "tab_id": "ws1:1",
            "name": "worker-1",
            "agent_status": "idle",
        },
    }
    assert calls


def test_start_can_wait_for_ready_after_pane_created(monkeypatch):
    from tools import herdr_tools

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(("run", cmd))
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
                            "name": "worker-ready",
                            "agent_status": "unknown",
                        }
                    }
                }
            ),
            stderr="",
        )

    def fake_ready(pane_id, timeout_seconds=30.0, poll_seconds=0.5):
        calls.append(("ready", pane_id, timeout_seconds, poll_seconds))
        return json.dumps({"success": True, "matched_marker": "❯"})

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(herdr_tools, "herdr_wait_ready", fake_ready)

    result = json.loads(
        herdr_tools.herdr_agent_start(
            name="worker-ready",
            argv=["hermes"],
            wait_ready=True,
            ready_timeout_seconds=12,
        )
    )

    assert result["success"] is True
    assert result["ready"] == {"success": True, "matched_marker": "❯"}
    assert calls[-1] == ("ready", "pane1", 12, 0.5)


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

    assert result == {"success": True, "transport": "cli", "pane_id": "pane1", "output": "hello\n"}


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


def test_wait_ready_polls_until_prompt_marker(monkeypatch):
    from tools import herdr_tools

    outputs = iter(["booting...", "Welcome to Hermes Agent\n❯"])
    sleeps = []

    def fake_read(pane_id, lines=80, source="recent-unwrapped", timeout=60):
        return json.dumps({"success": True, "output": next(outputs)})

    monkeypatch.setattr(herdr_tools, "herdr_pane_read", fake_read)
    monkeypatch.setattr(herdr_tools.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = json.loads(herdr_tools.herdr_wait_ready("pane1", timeout_seconds=5, poll_seconds=0.25))

    assert result["success"] is True
    assert result["matched_marker"] == "❯"
    assert sleeps == [0.25]


def test_run_prompt_waits_ready_when_requested_then_working_idle(monkeypatch):
    from tools import herdr_tools

    calls = []

    def fake_ready(pane_id, timeout_seconds=30, poll_seconds=0.5):
        calls.append(("ready", pane_id, timeout_seconds, poll_seconds))
        return json.dumps({"success": True, "matched_marker": "❯"})

    def fake_send(pane_id, text, submit=True, timeout=60):
        calls.append(("send", pane_id, text, submit))
        return json.dumps({"success": True})

    def fake_wait(pane_id, status, timeout_ms=30000, timeout=None):
        calls.append(("wait", pane_id, status, timeout_ms))
        return json.dumps({"success": True, "status": status})

    def fake_read(pane_id, lines=200, source="recent-unwrapped", timeout=60):
        calls.append(("read", pane_id, lines, source))
        return json.dumps({"success": True, "output": "...FINAL_TOKEN..."})

    monkeypatch.setattr(herdr_tools, "herdr_wait_ready", fake_ready)
    monkeypatch.setattr(herdr_tools, "herdr_pane_send_text", fake_send)
    monkeypatch.setattr(herdr_tools, "herdr_wait_status", fake_wait)
    monkeypatch.setattr(herdr_tools, "herdr_pane_read", fake_read)
    monkeypatch.setattr(herdr_tools.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    result = json.loads(
        herdr_tools.herdr_run_prompt(
            "pane1",
            "do it",
            wait_ready=True,
            ready_timeout_seconds=12,
            wait_working_ms=1000,
            wait_idle_ms=2000,
            settle_seconds=1.5,
            lines=321,
            expect="FINAL_TOKEN",
        )
    )

    assert result["success"] is True
    assert result["ready"]["matched_marker"] == "❯"
    assert calls == [
        ("ready", "pane1", 12, 0.5),
        ("send", "pane1", "do it", True),
        ("wait", "pane1", "working", 1000),
        ("wait", "pane1", "idle", 2000),
        ("sleep", 1.5),
        ("read", "pane1", 321, "recent-unwrapped"),
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


def test_spawn_and_run_starts_ready_then_runs_prompt(monkeypatch):
    from tools import herdr_tools

    calls = []

    def fake_start(name, cwd=None, workspace_id=None, argv=None, no_focus=True, wait_ready=False, ready_timeout_seconds=30.0):
        calls.append(("start", name, cwd, workspace_id, argv, no_focus, wait_ready, ready_timeout_seconds))
        return json.dumps(
            {
                "success": True,
                "workspace_id": "ws1",
                "pane_id": "pane1",
                "name": name,
                "agent_status": "idle",
            }
        )

    def fake_run(pane_id, text, wait_working_ms=30000, wait_idle_ms=60000, pre_send_settle_seconds=0.0,
                 wait_ready=False, ready_timeout_seconds=30.0, settle_seconds=2.0, lines=400, expect=None):
        calls.append(("run", pane_id, text, wait_working_ms, wait_idle_ms, wait_ready, settle_seconds, lines, expect))
        return json.dumps(
            {
                "success": True,
                "stage": "complete",
                "matched_expect": True,
                "output": "prefix DONE suffix",
                "idle": {"status": "idle"},
            }
        )

    monkeypatch.setattr(herdr_tools, "herdr_agent_start", fake_start)
    monkeypatch.setattr(herdr_tools, "herdr_run_prompt", fake_run)

    result = json.loads(
        herdr_tools.herdr_spawn_and_run(
            name="worker",
            cwd="/repo",
            argv=["hermes", "-w"],
            prompt="Do task...",
            expect="DONE",
            wait_working_ms=1000,
            wait_idle_ms=2000,
            ready_timeout_seconds=9,
            settle_seconds=0.25,
            lines=50,
        )
    )

    assert result == {
        "success": True,
        "stage": "complete",
        "status": "succeeded",
        "pane_id": "pane1",
        "workspace_id": "ws1",
        "matched_expect": True,
        "expect": "DONE",
        "output_excerpt": "prefix DONE suffix",
        "start": {
            "success": True,
            "workspace_id": "ws1",
            "pane_id": "pane1",
            "name": "worker",
            "agent_status": "idle",
        },
        "run": {
            "success": True,
            "stage": "complete",
            "matched_expect": True,
            "output": "prefix DONE suffix",
            "idle": {"status": "idle"},
        },
    }
    assert calls == [
        ("start", "worker", "/repo", None, ["hermes", "-w"], True, True, 9),
        ("run", "pane1", "Do task...", 1000, 2000, False, 0.25, 50, "DONE"),
    ]


def test_spawn_and_run_requires_expect():
    from tools import herdr_tools

    result = json.loads(herdr_tools.herdr_spawn_and_run(name="worker", prompt="Do task..."))

    assert result == {"success": False, "stage": "validate", "error": "expect is required"}


def test_spawn_and_run_returns_failed_start_envelope(monkeypatch):
    from tools import herdr_tools

    monkeypatch.setattr(
        herdr_tools,
        "herdr_agent_start",
        lambda **kwargs: json.dumps({"success": False, "error": "herdr agent start failed", "stderr": "boom"}),
    )

    result = json.loads(herdr_tools.herdr_spawn_and_run(name="worker", prompt="Do task...", expect="DONE"))

    assert result["success"] is False
    assert result["stage"] == "start"
    assert result["status"] == "failed_start"
    assert result["pane_id"] is None
    assert result["workspace_id"] is None
    assert result["start"]["stderr"] == "boom"


def test_spawn_and_run_returns_failed_run_envelope(monkeypatch):
    from tools import herdr_tools

    monkeypatch.setattr(
        herdr_tools,
        "herdr_agent_start",
        lambda **kwargs: json.dumps({"success": True, "pane_id": "pane1", "workspace_id": "ws1"}),
    )
    monkeypatch.setattr(
        herdr_tools,
        "herdr_run_prompt",
        lambda *args, **kwargs: json.dumps(
            {"success": False, "stage": "wait_idle", "matched_expect": None, "idle": {"error_type": "timeout"}}
        ),
    )

    result = json.loads(herdr_tools.herdr_spawn_and_run(name="worker", prompt="Do task...", expect="DONE"))

    assert result["success"] is False
    assert result["stage"] == "wait_idle"
    assert result["status"] == "failed_run"
    assert result["pane_id"] == "pane1"
    assert result["workspace_id"] == "ws1"
    assert result["matched_expect"] is None
    assert result["run"]["idle"] == {"error_type": "timeout"}


def test_spawn_and_run_wraps_timeout_exception(monkeypatch):
    from tools import herdr_tools

    monkeypatch.setattr(
        herdr_tools,
        "herdr_agent_start",
        lambda **kwargs: json.dumps({"success": True, "pane_id": "pane1", "workspace_id": "ws1"}),
    )

    def raise_timeout(*args, **kwargs):
        raise TimeoutError("run took too long")

    monkeypatch.setattr(herdr_tools, "herdr_run_prompt", raise_timeout)

    result = json.loads(herdr_tools.herdr_spawn_and_run(name="worker", prompt="Do task...", expect="DONE"))

    assert result["success"] is False
    assert result["stage"] == "run"
    assert result["status"] == "error"
    assert result["error_type"] == "timeout"
    assert result["error"] == "run took too long"
    assert result["pane_id"] == "pane1"
    assert result["workspace_id"] == "ws1"


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
        "herdr_wait_ready",
        "herdr_run_prompt",
        "herdr_spawn_and_run",
        "herdr_wait_status",
        "herdr_approval",
        "herdr_workspace_list",
        "herdr_workspace_close",
        "herdr_pane_close",
    }


def test_workspace_list_parses_workspaces(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        assert cmd == ["herdr", "workspace", "list"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "id": "cli:workspace:list",
                    "result": {
                        "type": "workspace_list",
                        "workspaces": [
                            {"workspace_id": "ws1", "label": "alpha", "pane_count": 3},
                            {"workspace_id": "ws2", "label": "beta", "pane_count": 1},
                        ],
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_workspace_list())
    assert result["success"] is True
    assert result["workspaces"] == [
        {"workspace_id": "ws1", "label": "alpha", "pane_count": 3},
        {"workspace_id": "ws2", "label": "beta", "pane_count": 1},
    ]


def test_workspace_list_failure_returns_envelope(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="bad json", stderr="conn refused")

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_workspace_list())
    assert result["success"] is False
    assert result["workspaces"] == []
    assert result["stderr"] == "conn refused"


def test_workspace_close_success(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        assert cmd == ["herdr", "workspace", "close", "ws1"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"id": "cli:workspace:close", "result": {"type": "ok"}}),
            stderr="",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_workspace_close("ws1"))
    assert result["success"] is True
    assert result["workspace_id"] == "ws1"
    assert result["result"]["result"]["type"] == "ok"


def test_workspace_close_rejects_empty_id():
    from tools import herdr_tools

    result = json.loads(herdr_tools.herdr_workspace_close(""))
    assert result["success"] is False
    assert result["error"] == "workspace_id is required"


def test_pane_close_success(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        assert cmd == ["herdr", "pane", "close", "pane1"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"id": "cli:pane:close", "result": {"type": "ok"}}),
            stderr="",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_pane_close("pane1"))
    assert result["success"] is True
    assert result["pane_id"] == "pane1"


def test_pane_close_failure_returns_envelope(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout=json.dumps({"error": {"code": "pane_not_found", "message": "pane pane1 not found"}}),
            stderr="",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_pane_close("pane1"))
    assert result["success"] is False
    assert result["pane_id"] == "pane1"
    assert "pane_not_found" in result["stdout"]


def test_pane_close_rejects_empty_id():
    from tools import herdr_tools

    result = json.loads(herdr_tools.herdr_pane_close(""))
    assert result["success"] is False
    assert result["error"] == "pane_id is required"


class FakeUnixSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.connected_path = None
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        self.connected_path = path

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if not self.responses:
            return b""
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def test_socket_transport_sends_json_rpc_line_and_parses_response(monkeypatch):
    from tools import herdr_tools

    fake = FakeUnixSocket([
        json.dumps({"id": "req-1", "result": {"type": "pong", "protocol": 12}}).encode() + b"\n"
    ])
    monkeypatch.setattr(herdr_tools.socket, "socket", lambda family, kind: fake)

    transport = herdr_tools.HerdrSocketTransport(socket_path="/tmp/herdr.sock")
    response = transport.request("ping", {}, timeout=3, request_id="req-1")

    assert response == {"id": "req-1", "result": {"type": "pong", "protocol": 12}}
    assert fake.connected_path == "/tmp/herdr.sock"
    assert fake.timeout == 3
    assert fake.closed is True
    assert json.loads(fake.sent[0].decode()) == {"id": "req-1", "method": "ping", "params": {}}
    assert fake.sent[0].endswith(b"\n")


def test_socket_transport_timeout_returns_adapter_compatible_error(monkeypatch):
    from tools import herdr_tools

    class TimeoutSocket(FakeUnixSocket):
        def recv(self, size):
            raise socket.timeout("too slow")

    fake = TimeoutSocket([])
    monkeypatch.setattr(herdr_tools.socket, "socket", lambda family, kind: fake)

    transport = herdr_tools.HerdrSocketTransport(socket_path="/tmp/herdr.sock")
    result = transport.request_envelope("workspace.list", {}, timeout=2, request_id="req-timeout")

    assert result["success"] is False
    assert result["transport"] == "socket"
    assert result["method"] == "workspace.list"
    assert result["socket_path"] == "/tmp/herdr.sock"
    assert result["error"] == "herdr socket request timed out"
    assert result["error_type"] == "timeout"


def test_socket_path_resolves_from_profile_config(monkeypatch):
    from tools import herdr_tools

    monkeypatch.setattr(
        herdr_tools,
        "load_config_readonly",
        lambda: {"tools": {"herdr": {"socket_path": "~/custom/herdr.sock"}}},
    )
    monkeypatch.setenv("HOME", "/home/tester")

    transport = herdr_tools.HerdrSocketTransport()

    assert transport.socket_path == "/home/tester/custom/herdr.sock"


def test_timeout_stdout_bytes_is_decoded_to_string_before_json(monkeypatch):
    from tools import herdr_tools

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd,
            timeout=0.1,
            output=b"partial stdout bytes",
            stderr=b"partial stderr bytes",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_workspace_list(timeout=0.1))
    assert result["success"] is False
    assert result["exit_code"] == 124
    assert result["stdout"] == "partial stdout bytes"
    assert result["stderr"] == "partial stderr bytes"


def test_workspace_list_prefers_socket_and_normalizes_result(monkeypatch):
    from tools import herdr_tools

    def fake_request(self, method, params=None, timeout=60, request_id=None):
        assert method == "workspace.list"
        assert params == {}
        return {
            "id": request_id,
            "result": {
                "type": "workspace_list",
                "workspaces": [{"workspace_id": "ws1", "pane_count": 2}],
            },
        }

    monkeypatch.setattr(herdr_tools.HerdrSocketTransport, "is_available", lambda self: True)
    monkeypatch.setattr(herdr_tools.HerdrSocketTransport, "request", fake_request)

    result = json.loads(herdr_tools.herdr_workspace_list())

    assert result["success"] is True
    assert result["transport"] == "socket"
    assert result["workspaces"] == [{"workspace_id": "ws1", "pane_count": 2}]


def test_workspace_list_falls_back_to_cli_when_socket_unavailable(monkeypatch):
    from tools import herdr_tools

    monkeypatch.setattr(herdr_tools.HerdrSocketTransport, "is_available", lambda self: False)

    def fake_run(cmd, **kwargs):
        assert cmd == ["herdr", "workspace", "list"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"result": {"workspaces": [{"workspace_id": "cli-ws"}]}}),
            stderr="",
        )

    monkeypatch.setattr(herdr_tools.subprocess, "run", fake_run)

    result = json.loads(herdr_tools.herdr_workspace_list())

    assert result["success"] is True
    assert result["transport"] == "cli"
    assert result["workspaces"] == [{"workspace_id": "cli-ws"}]
