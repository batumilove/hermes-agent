"""Native Windows python launcher probe acceptance within the unchanged file deadline."""
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
def test_earlier_pm_userbin_launcher_is_identity_checked(tmp_path: Path) -> None:
    home = tmp_path / 'profile'
    userbin = home / 'bin'; userbin.mkdir(parents=True)
    root = tmp_path / 'source'
    launcher = publish_fixture_launcher(root, CLI)
    external = userbin / launcher.name
    launcher.rename(external)
    wrong = tmp_path / 'other'
    (wrong / 'pm').mkdir(parents=True)
    (wrong / 'hermes_cli').mkdir()
    (wrong / 'hermes_cli/_launchers.py').touch()
    helper = str(ROOT / 'scripts/desktop-update/runtime.ps1').replace("'", "''")
    for target, expected_code in [(root, 0), (wrong, 1)]:
        script = f". '{helper}'; try {{ @(Get-HermesRuntimeCommand -InstallRoot '{target}') | ConvertTo-Json -Compress }} catch {{ exit 1 }}"
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                                env={**os.environ, 'HERMES_HOME': str(home)},
                                capture_output=True, text=True, timeout=45, check=False)
        assert result.returncode == expected_code, result.stdout + result.stderr
        if expected_code == 0:
            assert json.loads(result.stdout) == str(external)


@pytest.mark.platforms('windows')
def test_command_file_launcher_probe_runs_through_cmd_exe_explicitly(tmp_path: Path) -> None:
    """A .cmd launcher probe goes through `cmd.exe /d /s /c` exactly like the update step,
    never through CreateProcess's implicit batch handling; unquotable arguments are refused."""
    bin_dir = tmp_path / 'launcher dir'; bin_dir.mkdir()
    launcher = bin_dir / 'hermes.cmd'
    launcher.write_text('@echo off\r\necho CMDLINE=%CMDCMDLINE%\r\necho ARGS=%*\r\n', encoding='ascii')
    helper = str(ROOT / 'scripts/desktop-update/runtime.ps1').replace("'", "''")
    target = str(launcher).replace("'", "''")

    def probe(argument: str) -> subprocess.CompletedProcess:
        script = (f". '{helper}'; try {{ $r = Invoke-HermesProbe '{target}' @('--version', '{argument}'); "
                  "[Console]::Out.Write(\"code=$($r.Code)`n$($r.Output)\") } catch { [Console]::Out.Write('refused'); exit 3 }")
        return subprocess.run(['powershell', '-NoProfile', '-Command', script],
                              capture_output=True, text=True, timeout=60, check=False)

    ok = probe('two words')
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert 'code=0' in ok.stdout, ok.stdout
    cmdline = next(line for line in ok.stdout.splitlines() if line.startswith('CMDLINE='))
    assert ' /d /s /c ' in cmdline.lower(), cmdline
    assert 'ARGS="--version" "two words"' in ok.stdout, ok.stdout
    bad = probe('100%PATH%')
    assert (bad.returncode, bad.stdout) == (3, 'refused'), bad.stdout + bad.stderr
