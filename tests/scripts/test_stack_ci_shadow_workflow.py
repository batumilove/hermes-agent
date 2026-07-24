from pathlib import Path

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
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"policy", "runtime-tests", "image", "shadow"}
    assert "secrets." not in (WORKFLOWS / "stack-ci-shadow.yml").read_text(encoding="utf-8")

    shadow = workflow["jobs"]["shadow"]
    assert shadow["name"] == "Stack integration shadow (non-required)"
    assert set(shadow["needs"]) == {"policy", "runtime-tests", "image"}
    assert shadow["if"] == "always()"


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
