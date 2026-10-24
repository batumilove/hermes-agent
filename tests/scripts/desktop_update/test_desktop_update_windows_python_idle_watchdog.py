"""Native Windows python idle watchdog acceptance within the unchanged file deadline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import pytest
from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.windows_python_handoff_support import ROOT
from tests.scripts.desktop_update.windows_python_handoff_support import CLI

@pytest.mark.platforms('windows')
@pytest.mark.parametrize(('output', 'code'), [
    ('✓ Update complete! (v1.0.0)', 0),
    ('✓ Update complete! (v1.0.0)|  ✗ hermes-gateway failed to come back after restart.', 124),
    ('✓ Update complete! (v1.0.0)|Update incomplete — some units were not restarted', 124),
    ('Cloning into the checkout...', 124),
])
def test_update_killed_by_idle_watchdog_after_completing_is_a_success(
    tmp_path: Path, output: str, code: int,
) -> None:
    """#96205: a finished update parked silently in its restart phase is still finished,
    unless anything after its banner already reported a failure."""
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, CLI)
    (install / 'hermes_cli/desktop_update_verify.py').write_text('pass\n', encoding='utf-8')
    home = tmp_path / 'profile'; home.mkdir()
    calls = tmp_path / 'calls.jsonl'
    result = subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(ROOT / 'scripts/desktop-update/windows.ps1'), '-InstallRoot', str(install), '-NoUi'],
        cwd=tmp_path, env={**os.environ, 'HERMES_HOME': str(home),
                          'HERMES_RUNTIME_DIR': str(tmp_path / 'empty-store'),
                          'HANDOFF_CALLS': str(calls), 'HANDOFF_EXIT': '0', 'HANDOFF_HANG': output,
                          'HERMES_UPDATE_STEP_IDLE_SECONDS': '3', 'PYTHONIOENCODING': 'utf-8'},
        capture_output=True, text=True, timeout=180,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    argv = [json.loads(line)['argv'][:2] for line in calls.read_text(encoding='utf-8-sig').splitlines()]
    assert argv == ([['update', '--yes'], ['gateway', 'start']] if code == 0 else [['update', '--yes']])
    receipt = json.loads((home / '.hermes-update-result.json').read_text(encoding='utf-8-sig'))
    assert receipt['ok'] == (code == 0)
