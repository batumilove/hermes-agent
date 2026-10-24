"""Real-process helpers shared by the native Windows marker cases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

from tests.scripts.desktop_update.windows_process_support import process_creation_time as _creation_time


ROOT = Path(__file__).resolve().parent.parent.parent.parent


SCRIPT = ROOT / "scripts/desktop-update/windows.ps1"


MARKER = ".hermes-update-in-progress"


CLI = """
import sys
from pathlib import Path
def main():
    if '--version' in sys.argv:
        print('Install directory: ' + str(Path(__file__).resolve().parents[1])); return 0
    if '--help' in sys.argv:
        print('--keep-stash'); return 0
    return 0
if __name__ == '__main__':
    sys.exit(main())
"""


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    yield proc
    proc.kill()
    proc.wait()




def _protected_pid() -> int:
    """A live pid that grants only limited query rights (a SYSTEM/protected service): what
    Get-Process .StartTime needs is denied, what CIM/GetProcessTimes need is granted."""
    import ctypes

    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    names = (
        "csrss.exe",
        "smss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "MsMpEng.exe",
    )
    rows = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.Name }",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.splitlines()
    for row in rows:
        pid, _, name = row.strip().partition(" ")
        if name not in names:
            continue
        full = k32.OpenProcess(0x0400, False, int(pid))  # PROCESS_QUERY_INFORMATION
        limited = k32.OpenProcess(0x1000, False, int(pid))  # ..._LIMITED_INFORMATION
        for handle in (full, limited):
            if handle:
                k32.CloseHandle(ctypes.c_void_p(handle))
        if limited and not full:
            return int(pid)
    pytest.fail(
        "no process here grants only limited query rights: " + ", ".join(rows[:40])
    )


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _run(home: Path, *args: str, install: Path | None = None, timeout: int = 120):
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-InstallRoot",
        str(install or home / "hermes-agent"),
        "-NoUi",
        *args,
    ]
    env = {
        **os.environ,
        "HERMES_HOME": str(home),
        "HERMES_RUNTIME_DIR": str(home / "empty-store"),
    }
    proc = subprocess.Popen(
        command,
        cwd=home,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        out, _ = proc.communicate()
        pytest.fail(f"hand-off did not finish within {timeout}s: {out}")
    return proc.pid, proc.returncode, out


def _result(home: Path) -> dict:
    return json.loads(
        (home / ".hermes-update-result.json").read_text(encoding="utf-8-sig")
    )


_BODIES = {  # contract A2: identical verdicts in every reader
    "crlf-bom-v2": ("\ufeff{pid}\r\n{now}\r\nct:{ct}\r\n", "live"),
    "missing-line-2": ("{pid}\n", "dead"),
    "garbled-line-2": ("{pid}\nsoon\nct:{ct}\n", "dead"),
    "fractional-started-at": ("{pid}\n{now}.5\nct:{ct}\n", "dead"),
    "garbled-ct-is-v1-fresh": ("{pid}\n{now}\nct:garbage\n", "live"),
    "garbled-ct-is-v1-past-20min": ("{pid}\n{old}\nct:garbage\n", "dead"),
    "delegate-without-ct-is-ignored": (
        "999999\n{now}\nct:1.000\ndelegate:{pid}\n",
        "dead",
    ),
}


HOLD_CLI = """
import os, sys, time
from pathlib import Path
def main():
    if '--version' in sys.argv:
        print('Install directory: ' + str(Path(__file__).resolve().parents[1])); return 0
    if '--help' in sys.argv:
        print('--keep-stash'); return 0
    if sys.argv[1:2] == ['update']:   # still starting up: report the pid, wait to be released
        hold = Path(os.environ['HANDOFF_HOLD'])
        Path(str(hold) + '.pid').write_text(str(os.getpid()), encoding='utf-8')
        while not hold.exists():
            time.sleep(0.05)
    return 0
if __name__ == '__main__':
    sys.exit(main())
"""


def _marker_lines(path: Path) -> list[str]:
    try:  # the script may be mid-replace (sharing violation): read again next poll
        return path.read_bytes().decode().splitlines()
    except OSError:
        return []


def _parent_pid(pid: int) -> int:
    out = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()
    return int(out or 0)


def _assert_marker_body(
    tmp_path: Path,
    sleeper: subprocess.Popen,
    case: str,
) -> None:
    template, verdict = _BODIES[case]
    now = int(time.time())
    body = template.format(
        pid=sleeper.pid, now=now, old=now - 1300, ct=_creation_time(sleeper.pid)
    ).encode()
    (tmp_path / MARKER).write_bytes(body)
    pid, code, out = _run(tmp_path, "-SelfTestMarker", "-NoMarkerCleanup")
    if verdict == "live":
        assert code == 2, out
        assert (tmp_path / MARKER).read_bytes() == body
    else:
        assert code == 0, out
        assert (tmp_path / MARKER).read_bytes().decode().split("\n")[0] == str(pid)
