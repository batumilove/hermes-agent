"""Fork host support and startup refusal, including environment-based Termux detection."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_platform.support import supports_host


@pytest.mark.parametrize("system,arch,android,expected", [
    ("linux", "amd64", False, True), ("linux", "arm64", False, True),
    ("darwin", "amd64", False, True), ("darwin", "arm64", False, True),
    ("win32", "amd64", False, False), ("win32", "arm64", False, False),
    ("android", "arm64", False, False), ("linux", "arm64", True, False),
    ("linux", "x86", False, False), ("freebsd", "amd64", False, False),
])
def test_host_support(system, arch, android, expected):
    assert supports_host(system=system, arch=arch, android=android) is expected


@pytest.mark.platforms("linux", "macos")
def test_console_startup_checks_support_before_running(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "HERMES_HOME": str(tmp_path / "hermes"), "PYTHONPATH": str(root)}
    env.pop("TERMUX_VERSION", None)
    env.pop("PREFIX", None)
    command = [sys.executable, "-c", "from agent.legacy_cli import main; main(['--version'])"]
    allowed = subprocess.run(command, env=env, cwd=root, text=True, capture_output=True, timeout=30, check=False)
    assert allowed.returncode == 0, allowed.stderr
    refused = subprocess.run(command, env={**env, "TERMUX_VERSION": "0.118"}, cwd=root,
                             text=True, capture_output=True, timeout=30, check=False)
    assert refused.returncode != 0
    assert "Android/Termux are unsupported" in refused.stderr
    assert not refused.stdout
