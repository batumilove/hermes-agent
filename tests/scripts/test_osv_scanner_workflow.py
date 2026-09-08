import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "osv-scanner.yml"
SHA_PIN_RE = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _scan_steps() -> list[dict]:
    scan = _workflow()["jobs"]["scan"]
    steps = scan.get("steps")
    assert isinstance(steps, list), "scan must be a normal job with local steps"
    return steps


def _uses_step(prefix: str) -> dict:
    matches = [
        step
        for step in _scan_steps()
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith(prefix)
    ]
    assert len(matches) == 1, f"expected exactly one {prefix} step"
    return matches[0]


def test_osv_scan_covers_every_repository_lockfile() -> None:
    scanner = _uses_step("google/osv-scanner-action/osv-scanner-action@")
    scan_args = scanner["with"]["scan-args"]
    configured = {
        line.removeprefix("--lockfile=")
        for line in scan_args.splitlines()
        if line.startswith("--lockfile=")
    }
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    package_locks = {path for path in tracked if path.endswith("package-lock.json")}
    expected = package_locks | {"uv.lock"}

    assert configured == expected


def test_every_external_action_step_is_pinned_to_exact_sha() -> None:
    for job_name, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses") if isinstance(step, dict) else None
            if isinstance(uses, str) and not uses.startswith("./"):
                assert SHA_PIN_RE.fullmatch(uses), f"{job_name}: mutable uses ref {uses!r}"


def test_osv_reporter_blocks_on_vulnerabilities() -> None:
    reporter = _uses_step("google/osv-scanner-action/osv-reporter-action@")
    reporter_args = reporter["with"]["scan-args"]
    assert "--fail-on-vuln=true" in reporter_args.splitlines()
    assert "fail-on-vuln: false" not in WORKFLOW.read_text(encoding="utf-8")


def test_scan_is_bounded_normal_job_not_reusable_workflow() -> None:
    scan = _workflow()["jobs"]["scan"]
    assert "uses" not in scan
    assert scan.get("runs-on")
    assert isinstance(scan.get("steps"), list)
    assert isinstance(scan.get("timeout-minutes"), int)
    assert scan["timeout-minutes"] > 0


def test_sarif_publication_runs_after_reporter_failure() -> None:
    steps = _scan_steps()
    sarif_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and (
            step["uses"].startswith("actions/upload-artifact@")
            or step["uses"].startswith("github/codeql-action/upload-sarif@")
        )
    ]
    assert len(sarif_steps) == 2
    for step in sarif_steps:
        assert step.get("if") == "${{ !cancelled() }}"
