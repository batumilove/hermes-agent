"""Regression coverage for race-free synthetic Kanban home permissions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_secure_home_ignores_permissive_inherited_umask(tmp_path: Path) -> None:
    """Synthetic Kanban homes stay private without changing process umask."""
    script = f"""
import os
from pathlib import Path
from tests.hermes_cli.kanban_test_helpers import create_secure_home

os.umask(0)
home = create_secure_home(Path({str(tmp_path / 'home')!r}))
raise SystemExit(0 if home.stat().st_mode & 0o777 == 0o700 else 1)
"""
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        cwd=repository_root,
    )
    assert completed.returncode == 0
