"""Native Windows ui profile behavior, isolated within the existing per-file deadline."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import pytest
from tests.scripts.desktop_update.windows_marker_support import SCRIPT
from tests.scripts.desktop_update.windows_marker_support import _dead_pid

pytest_plugins = ["tests.scripts.desktop_update.windows_marker_support"]


@pytest.mark.platforms("windows")
def test_ui_profile_sweep_keeps_dirs_of_live_handoffs(
    tmp_path: Path, sleeper: subprocess.Popen
) -> None:
    live = tmp_path / f"hermes-update-ui-{sleeper.pid}"
    dead = tmp_path / f"hermes-update-ui-{_dead_pid()}"
    live.mkdir()
    dead.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-SelfTestUi",
            "-NoUi",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "HERMES_HOME": str(home),
            "TEMP": str(tmp_path),
            "HERMES_SELFTEST_HOLD_SECONDS": "0",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert live.exists(), "another live hand-off lost its browser profile"
    assert not dead.exists()
