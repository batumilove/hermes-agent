"""Native Windows python update failures acceptance within the unchanged file deadline."""
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
@pytest.mark.parametrize(
    ('code', 'no_gateway', 'gateway_code'),
    [(1, False, 0), (2, False, 0)],
)
def test_pm_handoff_reports_update_and_gateway_results(
    tmp_path: Path, code: int, no_gateway: bool, gateway_code: int,
) -> None:
    install = tmp_path / 'checkout with spaces'
    publish_fixture_launcher(install, CLI)
    (install / 'hermes_cli/desktop_update_verify.py').write_text('pass\n', encoding='utf-8')
    home = tmp_path / 'profile'; home.mkdir()
    calls = tmp_path / 'calls.jsonl'
    command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(ROOT / 'scripts/desktop-update/windows.ps1'), '-InstallRoot', str(install), '-NoUi']
    if no_gateway:
        command.append('-NoGateway')
    result = subprocess.run(
        command,
        cwd=tmp_path, env={**os.environ, 'HERMES_HOME': str(home),
                          'HERMES_RUNTIME_DIR': str(tmp_path / 'empty-store'),
                          'HANDOFF_CALLS': str(calls), 'HANDOFF_EXIT': str(code),
                          'GATEWAY_EXIT': str(gateway_code)},
        capture_output=True, text=True, timeout=120,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    expected = [{'argv': ['update', '--yes'] + ([] if no_gateway else ['--gateway'])
                 + ['--branch', 'main', '--keep-stash'], 'cwd': str(install)}]
    if code == 0 and not no_gateway:
        expected.append({'argv': ['gateway', 'start', '--all'], 'cwd': str(install)})
    assert [json.loads(line) for line in calls.read_text(encoding='utf-8-sig').splitlines()] == expected
    receipt = json.loads((home / '.hermes-update-result.json').read_text(encoding='utf-8-sig'))
    assert receipt['ok'] == (code == 0)
    assert receipt.get('manual', False) == (code == 0 and not no_gateway and gateway_code != 0)
    assert not (home / '.hermes-update-in-progress').exists()
