"""Herdr orchestration tools for long-lived terminal-hosted agents.

This module intentionally keeps the first adapter surface small: spawn an agent,
read a pane with unwrapped output, wait for status transitions, and drive the
approval menu by keys. The command shapes are based on the Herdr 0.6.6 eval.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from tools.registry import registry


DEFAULT_TIMEOUT_SECONDS = 60


def check_herdr_requirements() -> bool:
    """Return True when the Herdr CLI is available on PATH."""
    return shutil.which("herdr") is not None


def _json_result(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _run_herdr(args: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["herdr", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
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
    return _json_result(
        success=True,
        workspace_id=agent.get("workspace_id"),
        pane_id=agent.get("pane_id"),
        tab_id=agent.get("tab_id"),
        name=agent.get("name"),
        agent_status=agent.get("agent_status"),
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
    return _json_result(success=True, pane_id=pane_id, output=proc.stdout)


def herdr_pane_send_text(
    pane_id: str,
    text: str,
    submit: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send text to a Herdr pane, optionally pressing Enter afterwards."""
    if not pane_id:
        return _json_result(success=False, error="pane_id is required")
    proc = _run_herdr(["pane", "send-text", pane_id, text or ""], timeout=timeout)
    enter_proc = None
    if proc.returncode == 0 and submit:
        enter_proc = _run_herdr(["pane", "send-keys", pane_id, "Enter"], timeout=timeout)
    success = proc.returncode == 0 and (enter_proc is None or enter_proc.returncode == 0)
    return _json_result(
        success=success,
        pane_id=pane_id,
        submitted=submit,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        enter_stdout=enter_proc.stdout if enter_proc else "",
        enter_stderr=enter_proc.stderr if enter_proc else "",
        enter_exit_code=enter_proc.returncode if enter_proc else None,
    )


def herdr_run_prompt(
    pane_id: str,
    text: str,
    wait_working_ms: int = 30000,
    wait_idle_ms: int = 60000,
    settle_seconds: float = 2.0,
    lines: int = 400,
    expect: str | None = None,
) -> str:
    """Submit a prompt, wait for Herdr status transitions, settle, then read output."""
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
        send=sent,
        working=working,
        idle=idle,
        read=read,
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
        settle_seconds=args.get("settle_seconds", 2.0),
        lines=args.get("lines", 400),
        expect=args.get("expect"),
    ),
    check_fn=check_herdr_requirements,
    description="Run prompt in Herdr pane",
    emoji="▶️",
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
