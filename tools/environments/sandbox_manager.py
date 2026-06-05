"""Client backend for an external Agent Sandbox Manager.

This backend intentionally sends only a command/job spec to a pre-provisioned
manager over a private SSH path. It does not mount Hermes state, SSH agents,
Infisical identities, backup mounts, or other host files into the sandbox.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from typing import Any

_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SSH_USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_FORBIDDEN_ENV_PREFIXES = (
    "HERMES",
    "INFISICAL",
    "TAILSCALE",
    "TS_",
    "SSH_",
)
_FORBIDDEN_ENV_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
}


class SandboxManagerEnvironment:
    """Execute terminal jobs through an SSH/CLI Agent Sandbox Manager wrapper."""

    def __init__(
        self,
        *,
        ssh_host: str,
        ssh_user: str,
        ssh_port: int = 22,
        ssh_key: str = "",
        manager_dir: str = "/opt/agent-sandbox-manager",
        config_path: str = "config/sandbox-manager.example.json",
        runtime: str = "",
        network_profile: str = "offline",
        timeout: int = 180,
        output_bytes: int = 65536,
        env: dict[str, str] | None = None,
        trusted: bool = False,
    ) -> None:
        if not ssh_host or not ssh_user:
            raise ValueError("sandbox_manager backend requires ssh_host and ssh_user")
        if not _SSH_HOST_RE.fullmatch(ssh_host):
            raise ValueError("sandbox_manager ssh_host contains unsupported characters")
        if not _SSH_USER_RE.fullmatch(ssh_user):
            raise ValueError("sandbox_manager ssh_user contains unsupported characters")
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_port = int(ssh_port or 22)
        self.ssh_key = ssh_key
        self.manager_dir = manager_dir
        self.config_path = config_path
        self.runtime = runtime
        self.network_profile = network_profile or "offline"
        self.timeout = int(timeout or 180)
        self.output_bytes = int(output_bytes or 65536)
        self.env = env or {}
        self.trusted = trusted

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
        rewrite_compound_background: bool = True,
    ) -> dict[str, Any]:
        if stdin_data is not None:
            raise ValueError("sandbox_manager backend does not support stdin_data; pass input via the command or a pre-approved file path")
        self._validate_env()
        effective_timeout = int(timeout or self.timeout)
        job = {
            "command": command,
            "runtime": self.runtime,
            "network": self.network_profile,
            "max_runtime_seconds": effective_timeout,
            "env": self.env,
            "trusted": self.trusted,
        }
        ssh_args = self._ssh_args(self._remote_command(job))
        # Allow a small grace window for SSH setup and result transfer around
        # the sandbox-manager kill timeout.
        proc = subprocess.run(
            ssh_args,
            text=True,
            input=stdin_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=effective_timeout + 5,
        )
        if proc.returncode != 0:
            return {"output": proc.stdout + proc.stderr, "returncode": proc.returncode}
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"output": proc.stdout + proc.stderr, "returncode": 1}
        output = (result.get("stdout") or "") + (result.get("stderr") or "")
        if len(output.encode("utf-8", "replace")) > self.output_bytes:
            output = output.encode("utf-8", "replace")[: self.output_bytes].decode("utf-8", "replace")
        return {
            "output": output,
            "returncode": int(result.get("exit_code", 1)),
            "sandbox_result": result,
        }

    def cleanup(self) -> None:
        """No persistent client-side resources to clean up."""
        return None

    stop = cleanup

    def _validate_env(self) -> None:
        for key in self.env:
            upper = key.upper()
            if upper in _FORBIDDEN_ENV_NAMES or upper.startswith(_FORBIDDEN_ENV_PREFIXES):
                raise ValueError(f"env {key} is forbidden for sandbox_manager jobs")

    def _ssh_args(self, remote_command: str) -> list[str]:
        args = ["ssh", "-o", "BatchMode=yes"]
        if self.ssh_port != 22:
            args += ["-p", str(self.ssh_port)]
        if self.ssh_key:
            args += ["-i", self.ssh_key]
        args += [f"{self.ssh_user}@{self.ssh_host}", remote_command]
        return args

    def _remote_command(self, job: dict[str, Any]) -> str:
        manager = "python3 -m sandbox_manager.manager"
        job_json = json.dumps(job, separators=(",", ":"))
        parts = [
            "cd",
            shlex.quote(self.manager_dir),
            "&&",
            manager,
            "--config",
            shlex.quote(self.config_path),
            "run",
            "--job-json",
            shlex.quote(job_json),
        ]
        return " ".join(parts)
