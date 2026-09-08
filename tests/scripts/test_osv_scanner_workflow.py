import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import TypeGuard

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


def _external_uses(reference: object) -> TypeGuard[str]:
    return isinstance(reference, str) and not reference.startswith("./")


def _scan_arg_lines(step: dict) -> list[str]:
    return [
        line.strip()
        for line in step["with"]["scan-args"].splitlines()
        if line.strip()
    ]


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
    expected = {
        path
        for path in tracked
        if path.endswith("package-lock.json")
        or PurePosixPath(path).name == "uv.lock"
    }

    assert configured == expected


def test_every_external_action_reference_is_pinned_to_exact_sha() -> None:
    for job_name, job in _workflow()["jobs"].items():
        reusable = job.get("uses")
        if _external_uses(reusable):
            assert SHA_PIN_RE.fullmatch(reusable), (
                f"{job_name}: mutable reusable workflow ref {reusable!r}"
            )
        for step in job.get("steps", []):
            uses = step.get("uses") if isinstance(step, dict) else None
            if _external_uses(uses):
                assert SHA_PIN_RE.fullmatch(uses), f"{job_name}: mutable uses ref {uses!r}"


def test_osv_reporter_blocks_on_vulnerabilities() -> None:
    reporter = _uses_step("google/osv-scanner-action/osv-reporter-action@")
    fail_args = [
        line for line in _scan_arg_lines(reporter) if line.startswith("--fail-on-vuln")
    ]
    assert fail_args == ["--fail-on-vuln=true"]
    assert reporter.get("continue-on-error") is not True
    assert _workflow()["jobs"]["scan"].get("continue-on-error") is not True


def test_scan_is_bounded_normal_job_not_reusable_workflow() -> None:
    scan = _workflow()["jobs"]["scan"]
    assert "uses" not in scan
    assert scan.get("runs-on")
    assert isinstance(scan.get("steps"), list)
    assert isinstance(scan.get("timeout-minutes"), int)
    assert scan["timeout-minutes"] > 0


def test_every_job_has_a_positive_timeout() -> None:
    for job_name, job in _workflow()["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), (
            f"{job_name}: missing integer timeout-minutes"
        )
        assert job["timeout-minutes"] > 0


def test_permissions_are_scoped_to_each_job() -> None:
    workflow = _workflow()
    assert workflow.get("permissions") == {}
    assert workflow["jobs"]["scan"].get("permissions") == {
        "contents": "read",
        "security-events": "write",
    }
    assert workflow["jobs"]["emit-status"].get("permissions") == {
        "actions": "read"
    }


def test_emit_status_does_not_checkout_repository_contents() -> None:
    emit_steps = _workflow()["jobs"]["emit-status"]["steps"]
    assert not any(
        isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/checkout@")
        for step in emit_steps
    )


def test_emit_status_and_its_artifact_do_not_run_after_cancellation() -> None:
    emit = _workflow()["jobs"]["emit-status"]
    assert emit.get("if") == "${{ !cancelled() }}"
    uploads = [
        step
        for step in emit["steps"]
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    assert uploads[0].get("if") == (
        "${{ !cancelled() && steps.emit.outcome != 'skipped' }}"
    )


def test_sarif_publication_runs_after_reporter_failure() -> None:
    steps = _scan_steps()
    reporter_index = steps.index(
        _uses_step("google/osv-scanner-action/osv-reporter-action@")
    )
    sarif_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and (
            step["uses"].startswith("actions/upload-artifact@")
            or step["uses"].startswith("github/codeql-action/upload-sarif@")
        )
    ]
    assert len(sarif_steps) == 2
    for index, step in sarif_steps:
        assert index > reporter_index
        assert step.get("if") == "${{ !cancelled() }}"
