"""Execute the scanner workflow's shell boundary without a Docker daemon."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.scripts.test_osv_scanner_workflow import _named_step


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("step", ["Scan lockfiles", "Report vulnerabilities"])
def test_scan_containers_only_mount_the_output_directory_writable(tmp_path: Path, step: str):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    capture = tmp_path / "docker-argv.json"
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    docker.chmod(0o700)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _named_step(step)["run"]],
        cwd=checkout,
        env={"PATH": f"{binaries}{os.pathsep}{os.defpath}", "RUNNER_TEMP": str(runtime),
             "CAPTURE": str(capture), "GITHUB_TOKEN": "fixture-only"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    argv = json.loads(capture.read_text())
    assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "-v"]
    assert f"{checkout}:/src:ro" in mounts
    assert f"{runtime}/osv-tools:/tools:ro" in mounts
    writable = [mount for mount in mounts if not mount.endswith(":ro")]
    assert writable == [f"{runtime}/osv-output:/output"]
    assert "HOME=/output" in argv
