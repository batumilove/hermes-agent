from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_can_run_against_an_exact_candidate_ref() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))

    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers


def test_fork_contracts_install_the_canonical_test_environment() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["fork-contracts"]["steps"]
    names = [step.get("name") for step in steps]

    install_index = names.index("Install test dependencies")
    run_index = names.index("Run preserved behavior contracts")
    assert install_index < run_index
    assert "uv sync --locked --python 3.11" in steps[install_index]["with"]["command"]
