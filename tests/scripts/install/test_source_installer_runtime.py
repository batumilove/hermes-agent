"""Historical installers use their compatible Node without changing the driver runtime."""

import os
from pathlib import Path
import subprocess

import pytest


HELPER = Path(__file__).resolve().parents[2] / "install/e2e-assets/installer-common.sh"


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("managed", [False, True])
def test_installer_runtime_is_scoped_and_preserves_failure(tmp_path, managed):
    repo = tmp_path / "repo"
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ, HOME=str(tmp_path), GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Fixture",
               GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_NAME="Fixture",
               GIT_COMMITTER_EMAIL="fixture@example.invalid")
    for name in ("driver", "legacy"):
        directory = tmp_path / name
        directory.mkdir()
        node = directory / "node"
        node.write_text(f"#!/bin/sh\nprintf '%s\\n' '{name}'\n", encoding="utf-8")
        node.chmod(0o755)
    env["PATH"] = f"{tmp_path / 'driver'}:{env['PATH']}"
    env["HERMES_E2E_LEGACY_NODE_BIN"] = str(tmp_path / "legacy")
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
    script = repo / "scripts/install.sh"
    script.parent.mkdir()
    script.write_text('case "${1:-}" in --help) exit 0;; esac\n'
                      + ("# python -m pm.cli install\n" if managed else "")
                      + "node\nexit 23\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], env=env, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "installer"], env=env, check=True)
    result = subprocess.run(["bash", "-euc", '''
set -o pipefail
source "$1"
fail() { printf '%s\n' "$*" >&2; }
ts_prefix() { cat; }
log_group() { :; }
rc=0
run_source_installer "$2" "$3" "$3" HEAD old || rc=$?
node > "$3/driver-after.log"
exit "$rc"
''', "bash", str(HELPER), str(repo), str(work)], env=env, capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 23, result.stderr
    expected = "driver" if managed else "legacy"
    assert (work / "install-old.log").read_text(encoding="utf-8").strip() == expected
    assert (work / "driver-after.log").read_text(encoding="utf-8").strip() == "driver"
