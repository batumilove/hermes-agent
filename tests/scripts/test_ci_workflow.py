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


def test_ci_emits_the_required_aggregate_check_for_live_pushes() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers["push"]["branches"]) == {"main", "batumi/live"}
    assert workflow["jobs"]["all-checks-pass"]["name"] == "All required checks pass"


def test_fork_contracts_install_the_canonical_test_environment() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["fork-contracts"]["steps"]
    names = [step.get("name") for step in steps]

    install_index = names.index("Install test dependencies")
    run_index = names.index("Run preserved behavior contracts")
    assert install_index < run_index
    assert "uv sync --locked --python 3.11" in steps[install_index]["with"]["command"]


def test_fork_delta_fetches_the_recorded_sync_source_before_validation() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["fork-delta"]["steps"]
    command = next(
        step["run"]
        for step in steps
        if step.get("name") == "Require every upstream delta path to have an owner"
    )

    assert "python3 scripts/check_fork_delta.py --fetch-provenance-source" in command
    assert command.index("--fetch-provenance-source") < command.index(
        "python3 scripts/check_fork_delta.py\n"
    )


def test_contributor_check_excludes_accepted_upstream_history() -> None:
    text = CONTRIBUTOR_WORKFLOW.read_text(encoding="utf-8")

    assert "COMPARE_REF=origin/main" in text
    assert 'if [ -n "${GITHUB_BASE_REF:-}" ]; then' in text
    assert 'git fetch --no-tags origin "$GITHUB_BASE_REF"' in text
    assert 'COMPARE_REF=$(git merge-base "origin/${GITHUB_BASE_REF}" HEAD)' in text
    assert "elif [ -f .github/upstream-base ]; then" in text
    assert "python3 scripts/check_fork_delta.py --fetch-provenance-source" in text
    assert "COMPARE_REF=$(python3 scripts/check_fork_delta.py --contributor-base)" in text
    assert 'git log "${COMPARE_REF}..HEAD"' in text
    assert (ROOT / "contributors" / "emails" / "hermes@local").read_text(
        encoding="utf-8"
    ).splitlines()[0] == "batumilove"
