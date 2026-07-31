from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CONTRIBUTOR_WORKFLOW = ROOT / ".github" / "workflows" / "contributor-check.yml"


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


def test_contributor_check_excludes_accepted_upstream_history() -> None:
    text = CONTRIBUTOR_WORKFLOW.read_text(encoding="utf-8")

    assert "COMPARE_REF=origin/main" in text
    assert "COMPARE_REF=$(tr -d '[:space:]' < .github/upstream-base)" in text
    assert 'git merge-base --is-ancestor "$COMPARE_REF" HEAD' in text
    assert (ROOT / "contributors" / "emails" / "hermes@local").read_text(
        encoding="utf-8"
    ).splitlines()[0] == "batumilove"
