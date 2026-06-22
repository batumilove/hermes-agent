"""Herdr orchestration tools for long-lived terminal-hosted agents.

This module intentionally keeps the first adapter surface small: spawn an agent,
read a pane with unwrapped output, wait for status transitions, and drive the
approval menu by keys. The command shapes are based on the Herdr 0.6.6 eval.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_cli.config import load_config_readonly
from tools.registry import registry


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_SOCKET_PATH = "~/.config/herdr/herdr.sock"


def check_herdr_requirements() -> bool:
    """Return True when either direct Herdr socket or CLI fallback is available."""
    return HerdrSocketTransport().is_available() or shutil.which("herdr") is not None


def _json_result(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _resolve_socket_path(socket_path: str | None = None) -> str:
    """Resolve the Herdr Unix socket path from explicit arg, config, or default.

    User-facing non-secret configuration belongs in ``config.yaml``. Supported
    keys are ``tools.herdr.socket_path`` (preferred) and ``herdr.socket_path``
    (short form for early adopters). The default mirrors Herdr 0.6.6 on Linux.
    """
    if socket_path:
        return os.path.expanduser(os.path.expandvars(socket_path))
    try:
        config = load_config_readonly()
    except Exception:
        config = {}
    configured = (
        ((config.get("tools") or {}).get("herdr") or {}).get("socket_path")
        or (config.get("herdr") or {}).get("socket_path")
    )
    return os.path.expanduser(os.path.expandvars(configured or DEFAULT_SOCKET_PATH))


class HerdrTransportError(Exception):
    """Base exception for normalized Herdr transport failures."""

    error_type = "transport_error"
    message = "herdr socket request failed"


class HerdrTransportTimeout(HerdrTransportError):
    error_type = "timeout"
    message = "herdr socket request timed out"


class HerdrTransportProtocolError(HerdrTransportError):
    error_type = "protocol_error"
    message = "herdr socket protocol error"


class HerdrSocketTransport:
    """Direct JSON-over-Unix-socket transport for Herdr daemon requests.

    Herdr 0.6.6 protocol 12 accepts newline-delimited JSON request objects:
    ``{"id": str, "method": str, "params": object}``, and replies with a
    matching JSON object containing either ``result`` or ``error``.
    """

    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = _resolve_socket_path(socket_path)

    def is_available(self) -> bool:
        return Path(self.socket_path).is_socket()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not method:
            raise HerdrTransportProtocolError("method is required")
        request_id = request_id or f"hermes:{uuid.uuid4().hex}"
        request = {"id": request_id, "method": method, "params": params or {}}
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(timeout)
            client.connect(self.socket_path)
            client.sendall(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        except socket.timeout as exc:
            raise HerdrTransportTimeout(str(exc)) from exc
        except OSError as exc:
            raise HerdrTransportError(str(exc)) from exc
        finally:
            client.close()

        raw = b"".join(chunks).split(b"\n", 1)[0]
        if not raw:
            raise HerdrTransportProtocolError("empty response")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HerdrTransportProtocolError(str(exc)) from exc
        if not isinstance(response, dict):
            raise HerdrTransportProtocolError("response is not a JSON object")
        return response

    def request_envelope(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.request(method, params=params, timeout=timeout, request_id=request_id)
        except HerdrTransportError as exc:
            return {
                "success": False,
                "transport": "socket",
                "method": method,
                "socket_path": self.socket_path,
                "error": exc.message,
                "error_type": exc.error_type,
                "detail": str(exc),
            }
        return {
            "success": "error" not in response,
            "transport": "socket",
            "method": method,
            "socket_path": self.socket_path,
            "response": response,
            "error": response.get("error"),
        }


def _socket_transport_if_available() -> HerdrSocketTransport | None:
    transport = HerdrSocketTransport()
    return transport if transport.is_available() else None


def _parse_socket_result(response: dict[str, Any]) -> Any:
    return response.get("result") if isinstance(response, dict) else None


def _run_herdr(args: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    command = ["herdr", *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout
        stderr = exc.stderr
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=stdout or "",
            stderr=stderr or f"timed out after {timeout} seconds",
        )


def _parse_json_stdout(proc: subprocess.CompletedProcess[str]) -> Any:
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def classify_agent_status(status: str | None) -> str:
    """Map Herdr statuses to adapter recovery classes."""
    mapping = {
        "idle": "ready",
        "working": "running",
        "blocked": "needs_approval",
        "unknown": "needs_resume",
        None: "unknown",
        "": "unknown",
    }
    return mapping.get(status, "unknown")


def herdr_agent_start(
    name: str,
    cwd: str | None = None,
    workspace_id: str | None = None,
    argv: list[str] | None = None,
    no_focus: bool = True,
    wait_ready: bool = False,
    ready_timeout_seconds: float = 30.0,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Start an agent in Herdr and return its structured handle."""
    if not name:
        return _json_result(success=False, error="name is required")
    command = ["agent", "start", name]
    if cwd:
        command.extend(["--cwd", cwd])
    if workspace_id:
        command.extend(["--workspace", workspace_id])
    if no_focus:
        command.append("--no-focus")
    command.append("--")
    command.extend(argv or ["hermes"])

    proc = _run_herdr(command, timeout=timeout)
    parsed = _parse_json_stdout(proc)
    if proc.returncode != 0 or not parsed:
        return _json_result(
            success=False,
            error="herdr agent start failed",
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            command=["herdr", *command],
        )

    agent = (parsed.get("result") or {}).get("agent") or {}
    pane_id = agent.get("pane_id")
    ready = None
    if wait_ready and pane_id:
        ready = json.loads(herdr_wait_ready(pane_id, timeout_seconds=ready_timeout_seconds))
    return _json_result(
        success=True,
        workspace_id=agent.get("workspace_id"),
        pane_id=pane_id,
        tab_id=agent.get("tab_id"),
        name=agent.get("name"),
        agent_status=agent.get("agent_status"),
        ready=ready,
        raw=agent,
    )


def herdr_pane_read(
    pane_id: str,
    lines: int = 200,
    source: str = "recent-unwrapped",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Read pane output; defaults to recent-unwrapped to avoid false negatives."""
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    transport = _socket_transport_if_available()
    if transport is not None:
        response = transport.request_envelope(
            "pane.read",
            {"pane_id": pane_id, "source": source, "lines": lines},
            timeout=timeout,
        )
        if response.get("success"):
            result = _parse_socket_result(response["response"]) or {}
            output = result.get("output", result.get("text", "")) if isinstance(result, dict) else ""
            return _json_result(
                success=True,
                transport="socket",
                pane_id=pane_id,
                output=output,
                raw=result,
            )
    proc = _run_herdr(
        ["pane", "read", pane_id, "--source", source, "--lines", str(lines)],
        timeout=timeout,
    )
    if proc.returncode != 0:
        return _json_result(
            success=False,
            pane_id=pane_id,
            error="herdr pane read failed",
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return _json_result(success=True, transport="cli", pane_id=pane_id, output=proc.stdout)


def herdr_pane_send_text(
    pane_id: str,
    text: str,
    submit: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send text to a Herdr pane, optionally pressing Enter afterwards."""
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    transport = _socket_transport_if_available()
    if transport is not None:
        response = transport.request_envelope(
            "pane.send_input",
            {"pane_id": pane_id, "text": (text or "") + ("\n" if submit else "")},
            timeout=timeout,
        )
        if response.get("success"):
            return _json_result(
                success=True,
                transport="socket",
                pane_id=pane_id,
                submitted=submit,
                result=_parse_socket_result(response["response"]),
            )
    proc = _run_herdr(["pane", "send-text", pane_id, text or ""], timeout=timeout)
    enter_proc = None
    if proc.returncode == 0 and submit:
        enter_proc = _run_herdr(["pane", "send-keys", pane_id, "Enter"], timeout=timeout)
    success = proc.returncode == 0 and (enter_proc is None or enter_proc.returncode == 0)
    return _json_result(
        success=success,
        transport="cli",
        pane_id=pane_id,
        submitted=submit,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        enter_stdout=enter_proc.stdout if enter_proc else "",
        enter_stderr=enter_proc.stderr if enter_proc else "",
        enter_exit_code=enter_proc.returncode if enter_proc else None,
    )


def herdr_wait_ready(
    pane_id: str,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.5,
    lines: int = 80,
    markers: list[str] | None = None,
) -> str:
    """Poll pane output until the interactive prompt/banner is visible."""
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    markers = markers or ["❯", "Welcome to Hermes Agent", "Type your message"]
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_read: dict[str, Any] | None = None
    while True:
        attempts += 1
        last_read = json.loads(herdr_pane_read(pane_id, lines=lines))
        output = last_read.get("output", "") if last_read.get("success") else ""
        for marker in markers:
            if marker in output:
                return _json_result(
                    success=True,
                    pane_id=pane_id,
                    matched_marker=marker,
                    attempts=attempts,
                    output=output,
                )
        if time.monotonic() >= deadline:
            return _json_result(
                success=False,
                pane_id=pane_id,
                error="timed out waiting for pane readiness marker",
                attempts=attempts,
                markers=markers,
                last_read=last_read,
            )
        time.sleep(poll_seconds)


def herdr_run_prompt(
    pane_id: str,
    text: str,
    wait_working_ms: int = 30000,
    wait_idle_ms: int = 60000,
    pre_send_settle_seconds: float = 0.0,
    wait_ready: bool = False,
    ready_timeout_seconds: float = 30.0,
    settle_seconds: float = 2.0,
    lines: int = 400,
    expect: str | None = None,
) -> str:
    """Submit a prompt, wait for Herdr status transitions, settle, then read output."""
    if pre_send_settle_seconds > 0:
        time.sleep(pre_send_settle_seconds)

    ready: dict[str, Any] | None = None
    if wait_ready:
        ready = json.loads(herdr_wait_ready(pane_id, timeout_seconds=ready_timeout_seconds))
        if not ready.get("success"):
            return _json_result(success=False, stage="wait_ready", pane_id=pane_id, ready=ready)

    sent = json.loads(herdr_pane_send_text(pane_id, text, submit=True))
    if not sent.get("success"):
        return _json_result(success=False, stage="send", pane_id=pane_id, send=sent)

    working = json.loads(herdr_wait_status(pane_id, "working", timeout_ms=wait_working_ms))
    if not working.get("success"):
        return _json_result(success=False, stage="wait_working", pane_id=pane_id, send=sent, working=working)

    idle = json.loads(herdr_wait_status(pane_id, "idle", timeout_ms=wait_idle_ms))
    if not idle.get("success"):
        return _json_result(success=False, stage="wait_idle", pane_id=pane_id, send=sent, working=working, idle=idle)

    if settle_seconds > 0:
        time.sleep(settle_seconds)

    read = json.loads(herdr_pane_read(pane_id, lines=lines))
    output = read.get("output", "") if read.get("success") else ""
    matched = expect in output if expect else None
    return _json_result(
        success=read.get("success", False) and (matched is not False),
        stage="complete" if read.get("success") else "read",
        pane_id=pane_id,
        matched_expect=matched,
        output=output,
        ready=ready,
        send=sent,
        working=working,
        idle=idle,
        read=read,
    )


def _parse_tool_result(payload: str, stage: str) -> dict[str, Any]:
    try:
        result = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        return {
            "success": False,
            "stage": stage,
            "status": "error",
            "error": f"invalid JSON result from {stage}: {exc}",
            "error_type": "protocol_error",
            "raw": payload,
        }
    if not isinstance(result, dict):
        return {
            "success": False,
            "stage": stage,
            "status": "error",
            "error": f"non-object result from {stage}",
            "error_type": "protocol_error",
            "raw": result,
        }
    return result


def _output_excerpt(output: Any, limit: int = 4000) -> str:
    text = output if isinstance(output, str) else ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def herdr_spawn_and_run(
    name: str,
    prompt: str,
    expect: str | None = None,
    cwd: str | None = None,
    workspace_id: str | None = None,
    argv: list[str] | None = None,
    no_focus: bool = True,
    ready_timeout_seconds: float = 30.0,
    wait_working_ms: int = 30000,
    wait_idle_ms: int = 60000,
    settle_seconds: float = 2.0,
    lines: int = 400,
) -> str:
    """Start a Herdr agent, wait for readiness, run one prompt, and summarize the result."""
    if not name:
        return _json_result(success=False, stage="validate", error="name is required")
    if not prompt:
        return _json_result(success=False, stage="validate", error="prompt is required")
    if not expect:
        return _json_result(success=False, stage="validate", error="expect is required")

    try:
        start = _parse_tool_result(
            herdr_agent_start(
                name=name,
                cwd=cwd,
                workspace_id=workspace_id,
                argv=argv,
                no_focus=no_focus,
                wait_ready=True,
                ready_timeout_seconds=ready_timeout_seconds,
            ),
            "start",
        )
    except TimeoutError as exc:
        return _json_result(success=False, stage="start", status="error", error=str(exc), error_type="timeout")
    except Exception as exc:
        return _json_result(success=False, stage="start", status="error", error=str(exc), error_type=type(exc).__name__)

    pane_id = start.get("pane_id")
    workspace_id = start.get("workspace_id")
    if not start.get("success") or not pane_id:
        return _json_result(
            success=False,
            stage="start",
            status="failed_start",
            pane_id=pane_id,
            workspace_id=workspace_id,
            start=start,
        )

    try:
        run = _parse_tool_result(
            herdr_run_prompt(
                pane_id,
                prompt,
                wait_working_ms=wait_working_ms,
                wait_idle_ms=wait_idle_ms,
                wait_ready=False,
                settle_seconds=settle_seconds,
                lines=lines,
                expect=expect,
            ),
            "run",
        )
    except TimeoutError as exc:
        return _json_result(
            success=False,
            stage="run",
            status="error",
            pane_id=pane_id,
            workspace_id=workspace_id,
            error=str(exc),
            error_type="timeout",
            start=start,
        )
    except Exception as exc:
        return _json_result(
            success=False,
            stage="run",
            status="error",
            pane_id=pane_id,
            workspace_id=workspace_id,
            error=str(exc),
            error_type=type(exc).__name__,
            start=start,
        )

    success = bool(run.get("success"))
    return _json_result(
        success=success,
        stage=run.get("stage", "complete" if success else "run"),
        status="succeeded" if success else "failed_run",
        pane_id=pane_id,
        workspace_id=workspace_id,
        matched_expect=run.get("matched_expect"),
        expect=expect,
        output_excerpt=_output_excerpt(run.get("output")),
        start=start,
        run=run,
    )


def herdr_wait_status(
    pane_id: str,
    status: str,
    timeout_ms: int = 30000,
    timeout: int | None = None,
) -> str:
    """Wait for a Herdr agent-status transition."""
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    if not status:
        return _json_result(success=False, error="status is required")
    proc = _run_herdr(
        ["wait", "agent-status", pane_id, "--status", status, "--timeout", str(timeout_ms)],
        timeout=timeout or max(5, int(timeout_ms / 1000) + 5),
    )
    parsed = _parse_json_stdout(proc)
    return _json_result(
        success=proc.returncode == 0,
        pane_id=pane_id,
        status=status,
        class_=classify_agent_status(status),
        event=parsed,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
    )


def herdr_workspace_list(timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """List Herdr workspaces."""
    transport = _socket_transport_if_available()
    if transport is not None:
        response = transport.request_envelope("workspace.list", {}, timeout=timeout)
        if response.get("success"):
            result = _parse_socket_result(response["response"]) or {}
            workspaces = result.get("workspaces") if isinstance(result, dict) else []
            return _json_result(
                success=True,
                transport="socket",
                workspaces=workspaces or [],
                raw=result,
            )
    proc = _run_herdr(["workspace", "list"], timeout=timeout)
    parsed = _parse_json_stdout(proc)
    workspaces = []
    if parsed and isinstance(parsed, dict):
        result = parsed.get("result") or {}
        workspaces = result.get("workspaces") or []
    return _json_result(
        success=proc.returncode == 0,
        transport="cli",
        workspaces=workspaces,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
    )


def herdr_workspace_close(workspace_id: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Close a Herdr workspace."""
    if not workspace_id:
        return _json_result(success=False, error="workspace_id is required")
    proc = _run_herdr(["workspace", "close", workspace_id], timeout=timeout)
    parsed = _parse_json_stdout(proc)
    return _json_result(
        success=proc.returncode == 0,
        workspace_id=workspace_id,
        result=parsed,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
    )


def herdr_pane_close(pane_id: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Close a Herdr pane."""
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    proc = _run_herdr(["pane", "close", pane_id], timeout=timeout)
    parsed = _parse_json_stdout(proc)
    return _json_result(
        success=proc.returncode == 0,
        pane_id=pane_id,
        result=parsed,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
    )


def herdr_approval(pane_id: str, action: str = "allow_once", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Drive Herdr/Hermes approval menu by keys.

    The prompt is not a yes/no prompt. Deny must be Down Down Down Enter.
    """
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    key_map = {
        "allow_once": ["Enter"],
        "once": ["Enter"],
        "allow_session": ["Down", "Enter"],
        "session": ["Down", "Enter"],
        "allow_always": ["Down", "Down", "Enter"],
        "always": ["Down", "Down", "Enter"],
        "deny": ["Down", "Down", "Down", "Enter"],
    }
    keys = key_map.get(action)
    if keys is None:
        return _json_result(
            success=False,
            error="invalid action",
            valid_actions=sorted(key_map),
        )
    proc = _run_herdr(["pane", "send-keys", pane_id, *keys], timeout=timeout)
    return _json_result(
        success=proc.returncode == 0,
        pane_id=pane_id,
        action=action,
        keys=keys,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
    )


registry.register(
    name="herdr_agent_start",
    toolset="herdr",
    schema={
        "name": "herdr_agent_start",
        "description": "Start a Hermes or other agent process in a Herdr pane and return workspace/pane handles.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human label for the Herdr agent pane."},
                "cwd": {"type": "string", "description": "Working directory for the agent."},
                "workspace_id": {"type": "string", "description": "Optional Herdr workspace id to attach to."},
                "argv": {"type": "array", "items": {"type": "string"}, "description": "Command argv after --, default ['hermes']."},
                "no_focus": {"type": "boolean", "default": True},
                "wait_ready": {"type": "boolean", "default": False},
                "ready_timeout_seconds": {"type": "number", "default": 30.0},
            },
            "required": ["name"],
        },
    },
    handler=lambda args, **kw: herdr_agent_start(
        name=args.get("name", ""),
        cwd=args.get("cwd"),
        workspace_id=args.get("workspace_id"),
        argv=args.get("argv"),
        no_focus=args.get("no_focus", True),
        wait_ready=args.get("wait_ready", False),
        ready_timeout_seconds=args.get("ready_timeout_seconds", 30.0),
    ),
    check_fn=check_herdr_requirements,
    description="Start Herdr agent",
    emoji="🐑",
)

registry.register(
    name="herdr_pane_read",
    toolset="herdr",
    schema={
        "name": "herdr_pane_read",
        "description": "Read output from a Herdr pane. Uses recent-unwrapped by default for reliable token matching.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string"},
                "lines": {"type": "integer", "default": 200},
                "source": {"type": "string", "default": "recent-unwrapped"},
            },
            "required": ["pane_id"],
        },
    },
    handler=lambda args, **kw: herdr_pane_read(
        pane_id=args.get("pane_id", ""),
        lines=args.get("lines", 200),
        source=args.get("source", "recent-unwrapped"),
    ),
    check_fn=check_herdr_requirements,
    description="Read Herdr pane",
    emoji="📖",
)

registry.register(
    name="herdr_pane_send_text",
    toolset="herdr",
    schema={
        "name": "herdr_pane_send_text",
        "description": "Send text to a Herdr pane and optionally press Enter to submit it.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string"},
                "text": {"type": "string"},
                "submit": {"type": "boolean", "default": False},
            },
            "required": ["pane_id", "text"],
        },
    },
    handler=lambda args, **kw: herdr_pane_send_text(
        pane_id=args.get("pane_id", ""),
        text=args.get("text", ""),
        submit=args.get("submit", False),
    ),
    check_fn=check_herdr_requirements,
    description="Send text to Herdr pane",
    emoji="⌨️",
)

registry.register(
    name="herdr_wait_ready",
    toolset="herdr",
    schema={
        "name": "herdr_wait_ready",
        "description": "Poll a Herdr pane until the Hermes prompt/banner is visible and ready for input.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 30.0},
                "poll_seconds": {"type": "number", "default": 0.5},
                "lines": {"type": "integer", "default": 80},
                "markers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["pane_id"],
        },
    },
    handler=lambda args, **kw: herdr_wait_ready(
        pane_id=args.get("pane_id", ""),
        timeout_seconds=args.get("timeout_seconds", 30.0),
        poll_seconds=args.get("poll_seconds", 0.5),
        lines=args.get("lines", 80),
        markers=args.get("markers"),
    ),
    check_fn=check_herdr_requirements,
    description="Wait for Herdr pane readiness",
    emoji="👀",
)

registry.register(
    name="herdr_run_prompt",
    toolset="herdr",
    schema={
        "name": "herdr_run_prompt",
        "description": "Submit a prompt to a Herdr pane, wait working→idle, settle briefly, then read output.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string"},
                "text": {"type": "string"},
                "wait_working_ms": {"type": "integer", "default": 30000},
                "wait_idle_ms": {"type": "integer", "default": 60000},
                "pre_send_settle_seconds": {"type": "number", "default": 0.0},
                "wait_ready": {"type": "boolean", "default": False},
                "ready_timeout_seconds": {"type": "number", "default": 30.0},
                "settle_seconds": {"type": "number", "default": 2.0},
                "lines": {"type": "integer", "default": 400},
                "expect": {"type": "string", "description": "Optional token expected in final output."},
            },
            "required": ["pane_id", "text"],
        },
    },
    handler=lambda args, **kw: herdr_run_prompt(
        pane_id=args.get("pane_id", ""),
        text=args.get("text", ""),
        wait_working_ms=args.get("wait_working_ms", 30000),
        wait_idle_ms=args.get("wait_idle_ms", 60000),
        pre_send_settle_seconds=args.get("pre_send_settle_seconds", 0.0),
        wait_ready=args.get("wait_ready", False),
        ready_timeout_seconds=args.get("ready_timeout_seconds", 30.0),
        settle_seconds=args.get("settle_seconds", 2.0),
        lines=args.get("lines", 400),
        expect=args.get("expect"),
    ),
    check_fn=check_herdr_requirements,
    description="Run prompt in Herdr pane",
    emoji="▶️",
)

registry.register(
    name="herdr_spawn_and_run",
    toolset="herdr",
    schema={
        "name": "herdr_spawn_and_run",
        "description": "Start a Herdr agent, wait for readiness, run one prompt, and return a bounded orchestration result.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human label for the Herdr agent pane."},
                "cwd": {"type": "string", "description": "Working directory for the agent."},
                "workspace_id": {"type": "string", "description": "Optional Herdr workspace id to attach to."},
                "argv": {"type": "array", "items": {"type": "string"}, "description": "Command argv after --, default ['hermes']."},
                "prompt": {"type": "string", "description": "Prompt to submit once the pane is ready."},
                "expect": {"type": "string", "description": "Token expected in final output; required for success."},
                "ready_timeout_seconds": {"type": "number", "default": 30.0},
                "wait_working_ms": {"type": "integer", "default": 30000},
                "wait_idle_ms": {"type": "integer", "default": 60000},
                "settle_seconds": {"type": "number", "default": 2.0},
                "lines": {"type": "integer", "default": 400},
            },
            "required": ["name", "prompt", "expect"],
        },
    },
    handler=lambda args, **kw: herdr_spawn_and_run(
        name=args.get("name", ""),
        cwd=args.get("cwd"),
        workspace_id=args.get("workspace_id"),
        argv=args.get("argv"),
        prompt=args.get("prompt", ""),
        expect=args.get("expect"),
        ready_timeout_seconds=args.get("ready_timeout_seconds", 30.0),
        wait_working_ms=args.get("wait_working_ms", 30000),
        wait_idle_ms=args.get("wait_idle_ms", 60000),
        settle_seconds=args.get("settle_seconds", 2.0),
        lines=args.get("lines", 400),
    ),
    check_fn=check_herdr_requirements,
    description="Spawn Herdr agent and run prompt",
    emoji="🚀",
)

registry.register(
    name="herdr_wait_status",
    toolset="herdr",
    schema={
        "name": "herdr_wait_status",
        "description": "Wait for a Herdr pane agent_status such as idle, working, or blocked.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string"},
                "status": {"type": "string", "enum": ["idle", "working", "blocked", "unknown"]},
                "timeout_ms": {"type": "integer", "default": 30000},
            },
            "required": ["pane_id", "status"],
        },
    },
    handler=lambda args, **kw: herdr_wait_status(
        pane_id=args.get("pane_id", ""),
        status=args.get("status", ""),
        timeout_ms=args.get("timeout_ms", 30000),
    ),
    check_fn=check_herdr_requirements,
    description="Wait Herdr status",
    emoji="⏳",
)

registry.register(
    name="herdr_workspace_list",
    toolset="herdr",
    schema={
        "name": "herdr_workspace_list",
        "description": "List Herdr workspaces with metadata (pane counts, labels, active tabs).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=lambda args, **kw: herdr_workspace_list(),
    check_fn=check_herdr_requirements,
    description="List Herdr workspaces",
    emoji="📋",
)

registry.register(
    name="herdr_workspace_close",
    toolset="herdr",
    schema={
        "name": "herdr_workspace_close",
        "description": "Close a Herdr workspace by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Herdr workspace ID to close."},
            },
            "required": ["workspace_id"],
        },
    },
    handler=lambda args, **kw: herdr_workspace_close(
        workspace_id=args.get("workspace_id", ""),
    ),
    check_fn=check_herdr_requirements,
    description="Close Herdr workspace",
    emoji="🗑️",
)

registry.register(
    name="herdr_pane_close",
    toolset="herdr",
    schema={
        "name": "herdr_pane_close",
        "description": "Close a Herdr pane by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string", "description": "Herdr pane ID to close."},
            },
            "required": ["pane_id"],
        },
    },
    handler=lambda args, **kw: herdr_pane_close(
        pane_id=args.get("pane_id", ""),
    ),
    check_fn=check_herdr_requirements,
    description="Close Herdr pane",
    emoji="🗑️",
)

registry.register(
    name="herdr_approval",
    toolset="herdr",
    schema={
        "name": "herdr_approval",
        "description": "Drive the Hermes approval menu inside a Herdr pane. Deny sends Down Down Down Enter.",
        "parameters": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["allow_once", "allow_session", "allow_always", "deny"],
                    "default": "allow_once",
                },
            },
            "required": ["pane_id", "action"],
        },
    },
    handler=lambda args, **kw: herdr_approval(
        pane_id=args.get("pane_id", ""),
        action=args.get("action", "allow_once"),
    ),
    check_fn=check_herdr_requirements,
    description="Respond to Herdr approval menu",
    emoji="✅",
)
