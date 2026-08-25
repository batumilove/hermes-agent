import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "osv-scanner.yml"


def test_osv_scan_covers_every_repository_lockfile() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    scan_args = workflow["jobs"]["scan"]["with"]["scan-args"]
    configured = {
        line.removeprefix("--lockfile=")
        for line in scan_args.splitlines()
        if line.startswith("--lockfile=")
    }
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    package_locks = {
        path for path in tracked if path.endswith("package-lock.json")
    }
    expected = package_locks | {"uv.lock"}

    assert configured == expected
