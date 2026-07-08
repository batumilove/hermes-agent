"""Systemd user-manager environment secret source."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Set

from agent.secret_sources.base import FetchResult, SecretSource


def _parse_allowlist(value) -> Set[str]:
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = []
    return {str(p).strip() for p in parts if str(p).strip()}


class SystemdSource(SecretSource):
    name = "systemd"
    label = "systemd user manager"
    shape = "bulk"

    def fetch(self, cfg: dict, home_path: Path) -> FetchResult:
        result = FetchResult()
        allow = _parse_allowlist((cfg or {}).get("allowlist"))
        if not allow:
            return result
        uid = os.getuid() if hasattr(os, "getuid") else 0
        xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "XDG_RUNTIME_DIR": xdg,
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get(
                "DBUS_SESSION_BUS_ADDRESS", f"unix:path={xdg}/bus"
            ),
        }
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "show-environment"],
                env=env,
                capture_output=True,
                text=True,
                timeout=float((cfg or {}).get("timeout_seconds", 10) or 10),
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            result.error = f"systemctl --user show-environment failed: {exc}"
            return result
        if proc.returncode != 0:
            result.error = (proc.stderr or "systemctl --user show-environment failed").strip()
            return result
        for line in (proc.stdout or "").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in allow:
                result.secrets[key] = value
        return result
