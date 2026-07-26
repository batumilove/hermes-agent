import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"


def _workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_shadow_workflow_is_small_read_only_and_non_required() -> None:
    workflow = _workflow("stack-ci-shadow.yml")

    assert workflow["on"] == {
        "pull_request": "",
        "push": {"branches": ["batumi/live"]},
        "workflow_dispatch": "",
    }
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert set(workflow["jobs"]) == {
        "detect",
        "policy",
        "full-tests",
        "lint",
        "js-tests",
        "e2e-desktop",
        "docs-site",
        "history-check",
        "contributor-check",
        "uv-lockfile",
        "docker-lint",
        "supply-chain",
        "review-labels",
        "dependency-gates",
        "runtime-tests",
        "image",
        "shadow",
    }
    assert "secrets." not in (WORKFLOWS / "stack-ci-shadow.yml").read_text(encoding="utf-8")

    shadow = workflow["jobs"]["shadow"]
    assert shadow["name"] == "Stack integration shadow (non-required)"
    assert set(shadow["needs"]) == set(workflow["jobs"]) - {"shadow"}
    assert shadow["if"] == "always()"

    policy = workflow["jobs"]["policy"]
    contract = next(step for step in policy["steps"] if step["name"] == "Check repository contracts")
    assert "uv run --locked --extra dev ruff check ." in contract["run"]
    assert "uv tool run ruff" not in contract["run"]


def test_shadow_v2_uses_full_deployed_python_and_path_gated_safety_workflows() -> None:
    workflow = _workflow("stack-ci-shadow.yml")
    jobs = workflow["jobs"]

    full_tests = jobs["full-tests"]
    assert full_tests["needs"] == "detect"
    assert full_tests["if"] == "needs.detect.outputs.python == 'true'"
    assert full_tests["uses"] == "./.github/workflows/tests.yml"
    assert full_tests["with"] == {
        "slice_count": "8",
        "python_versions_json": '["3.13"]',
        "e2e_python_version": "3.13",
    }

    assert jobs["lint"]["uses"] == "./.github/workflows/lint.yml"
    assert jobs["js-tests"]["uses"] == "./.github/workflows/js-tests.yml"
    assert jobs["e2e-desktop"]["uses"] == "./.github/workflows/e2e-desktop.yml"
    assert jobs["docs-site"]["uses"] == "./.github/workflows/docs-site-checks.yml"
    assert jobs["history-check"]["uses"] == "./.github/workflows/history-check.yml"
    assert jobs["contributor-check"]["uses"] == "./.github/workflows/contributor-check.yml"
    assert jobs["uv-lockfile"]["uses"] == "./.github/workflows/uv-lockfile-check.yml"
    assert jobs["docker-lint"]["uses"] == "./.github/workflows/docker-lint.yml"
    assert jobs["supply-chain"]["uses"] == "./.github/workflows/supply-chain-audit.yml"
    assert jobs["review-labels"]["uses"] == "./.github/workflows/review-labels.yml"

    dependency_gates = jobs["dependency-gates"]
    assert dependency_gates["runs-on"] == "ubuntu-latest"
    assert "uses" not in dependency_gates
    dependency_steps = dependency_gates["steps"]
    checkout = next(step for step in dependency_steps if step["name"] == "Checkout")
    assert checkout["with"]["persist-credentials"] == "false"
    lockfiles = next(
        step for step in dependency_steps if step["name"] == "Validate npm lockfiles"
    )
    audit = next(
        step for step in dependency_steps if step["name"] == "Block high npm advisories"
    )
    osv_jobs = _workflow("osv-scanner.yml")["jobs"]
    required_lockfiles = next(
        step
        for step in osv_jobs["npm-lockfile-integrity"]["steps"]
        if step["name"] == "Check npm lockfile resolved URLs and integrity hashes"
    )
    required_audit = next(
        step
        for step in osv_jobs["npm-audit-high"]["steps"]
        if step["name"] == "Audit npm lockfiles for high/critical advisories"
    )
    assert lockfiles["run"] == required_lockfiles["run"]
    assert audit["env"] == required_audit["env"]
    assert audit["run"] == required_audit["run"]
    assert "security-events" not in json.dumps(dependency_gates)


def test_tests_workflow_defaults_remain_broad_but_accept_deployed_python_override() -> None:
    workflow = _workflow("tests.yml")
    inputs = workflow["on"]["workflow_call"]["inputs"]

    assert inputs["python_versions_json"]["default"] == '["3.11","3.12","3.13"]'
    assert inputs["e2e_python_version"]["default"] == "3.11"

    generate = workflow["jobs"]["generate"]
    command = next(
        step["run"] for step in generate["steps"] if step["name"] == "Generate test slices"
    )
    assert "PYTHON_VERSIONS_JSON" in command
    assert 'json.loads(os.environ["PYTHON_VERSIONS_JSON"])' in command

    e2e = workflow["jobs"]["e2e"]
    setup = next(step for step in e2e["steps"] if step["name"].startswith("Set up Python"))
    sync = next(step for step in e2e["steps"] if step["name"] == "Install dependencies")
    assert setup["env"] == {"E2E_PYTHON_VERSION": "${{ inputs.e2e_python_version }}"}
    assert setup["run"] == 'uv python install "$E2E_PYTHON_VERSION"'
    assert sync["env"] == {"E2E_PYTHON_VERSION": "${{ inputs.e2e_python_version }}"}
    assert '--python "$E2E_PYTHON_VERSION"' in sync["with"]["command"]


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        ('["3.11","3.12","3.13"]', ["3.11", "3.12", "3.13"]),
        ('["3.13"]', ["3.13"]),
    ],
)
def test_tests_workflow_generates_requested_python_matrix(
    tmp_path: Path,
    versions: str,
    expected: list[str],
) -> None:
    workflow = _workflow("tests.yml")
    command = next(
        step["run"]
        for step in workflow["jobs"]["generate"]["steps"]
        if step["name"] == "Generate test slices"
    )
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "SLICE_COUNT": "8",
            "PYTHON_VERSIONS_JSON": versions,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": os.devnull,
        },
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    matrix_line = next(
        line for line in output.read_text(encoding="utf-8").splitlines() if line.startswith("matrix=")
    )
    matrix = json.loads(matrix_line.removeprefix("matrix="))
    assert matrix["python-version"] == expected
    assert len(matrix["slice"]) == 8


def test_reused_workflows_scope_concurrency_to_the_calling_workflow() -> None:
    for name in (
        "tests.yml",
        "lint.yml",
        "e2e-desktop.yml",
        "docker-lint.yml",
        "uv-lockfile-check.yml",
    ):
        group = _workflow(name)["concurrency"]["group"]
        assert "${{ github.workflow }}" in group, name
        assert "github.ref" in group, name


def _run_shadow_evaluator(needs: dict[str, str], **flags: str) -> subprocess.CompletedProcess[str]:
    workflow = _workflow("stack-ci-shadow.yml")
    step = next(
        step for step in workflow["jobs"]["shadow"]["steps"] if step["name"] == "Evaluate shadow lanes"
    )
    run = step["run"]
    marker = "python3 - <<'PY'\n"
    assert marker in run
    script = run.split(marker, 1)[1].rsplit("\nPY", 1)[0]
    env = {
        **os.environ,
        "NEEDS": json.dumps({name: {"result": result} for name, result in needs.items()}),
        "EVENT_NAME": "pull_request",
        "PYTHON_CHANGED": "false",
        "FRONTEND_CHANGED": "false",
        "SITE_CHANGED": "false",
        "SCAN_CHANGED": "false",
        "DEPS_CHANGED": "false",
        "DOCKER_META_CHANGED": "false",
        "CI_REVIEW_CHANGED": "false",
        "MCP_CATALOG_CHANGED": "false",
        "SUPPLY_CHAIN_REVIEW": "false",
        **flags,
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _shadow_results(optional: str = "skipped") -> dict[str, str]:
    always = {
        "detect",
        "policy",
        "runtime-tests",
        "image",
        "uv-lockfile",
        "dependency-gates",
        "history-check",
    }
    jobs = set(_workflow("stack-ci-shadow.yml")["jobs"]) - {"shadow"}
    return {name: ("success" if name in always else optional) for name in jobs}


def test_shadow_evaluator_accepts_only_detection_proven_skips() -> None:
    result = _run_shadow_evaluator(_shadow_results())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("lane", "flags"),
    [
        ("full-tests", {"PYTHON_CHANGED": "true"}),
        ("lint", {"PYTHON_CHANGED": "true"}),
        ("e2e-desktop", {"PYTHON_CHANGED": "true"}),
        ("js-tests", {"FRONTEND_CHANGED": "true"}),
        ("docs-site", {"SITE_CHANGED": "true"}),
        ("docker-lint", {"DOCKER_META_CHANGED": "true"}),
        ("supply-chain", {"SCAN_CHANGED": "true"}),
        ("review-labels", {"CI_REVIEW_CHANGED": "true"}),
        ("review-labels", {"MCP_CATALOG_CHANGED": "true"}),
        ("review-labels", {"SUPPLY_CHAIN_REVIEW": "true"}),
    ],
)
def test_shadow_evaluator_rejects_unexpected_skips(lane: str, flags: dict[str, str]) -> None:
    needs = _shadow_results()
    result = _run_shadow_evaluator(needs, **flags)
    assert result.returncode == 1
    assert f"{lane}=skipped" in result.stdout


@pytest.mark.parametrize("result_name", ["failure", "cancelled", "skipped"])
def test_shadow_evaluator_rejects_any_non_success_for_always_required_lane(
    result_name: str,
) -> None:
    needs = _shadow_results()
    needs["image"] = result_name
    result = _run_shadow_evaluator(needs)
    assert result.returncode == 1
    assert f"image={result_name}" in result.stdout


def test_shadow_runtime_lane_matches_the_deployed_python_and_is_unsliced() -> None:
    workflow = _workflow("stack-ci-shadow.yml")
    job = workflow["jobs"]["runtime-tests"]
    steps = job["steps"]
    setup = next(step for step in steps if str(step.get("uses", "")).startswith("actions/setup-python@"))
    sync = next(step for step in steps if step.get("name") == "Install locked dependencies")
    run = next(step for step in steps if step.get("name") == "Run running-stack contract tests")

    assert setup["with"]["python-version"] == "3.13"
    command = sync["with"]["command"]
    assert "uv sync --locked --python 3.13 --extra dev" in command
    assert "--extra messaging" in command
    assert "matrix" not in job
    assert "scripts/run_tests.sh" in run["run"]
    for required_path in (
        "tests/gateway/test_telegram_connect.py",
        "tests/gateway/test_telegram_polling_progress.py",
        "tests/cron/test_scheduler.py",
        "tests/scripts/test_hermes_compose_deploy.py",
        "tests/scripts/test_stack_ci_shadow_workflow.py",
        "tests/plugins/test_provider_telemetry_plugin.py",
    ):
        assert required_path in run["run"]


def test_shadow_image_lane_builds_only_the_deployed_architecture_without_push() -> None:
    workflow = _workflow("stack-ci-shadow.yml")
    image = workflow["jobs"]["image"]
    build = next(step for step in image["steps"] if step.get("id") == "build")

    assert build["with"]["platforms"] == "linux/amd64"
    assert build["with"]["load"] == "true"
    assert build["with"]["push"] == "false"
    assert "cache-to" not in build["with"]
    assert any(step.get("name") == "Exercise image runtime contract" for step in image["steps"])


def test_existing_required_ci_and_deployment_gate_remain_unchanged_during_shadow() -> None:
    ci = _workflow("ci.yml")
    deploy = _workflow("deploy-compose.yml")

    assert ci["jobs"]["all-checks-pass"]["name"] == "All required checks pass"
    assert ci["jobs"]["tests"]["with"]["slice_count"] == (
        "${{ fromJSON(inputs.python_slice_count || '8') }}"
    )
    assert deploy["jobs"]["wait-for-required-ci"]["env"]["REQUIRED_CONTEXT"] == (
        "All required checks pass"
    )
