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


def test_contributor_check_excludes_accepted_upstream_history() -> None:
    text = CONTRIBUTOR_WORKFLOW.read_text(encoding="utf-8")

    assert 'BASE_REF="${GITHUB_BASE_REF:-main}"' in text
    assert 'git fetch --no-tags origin "${BASE_REF}"' in text
    assert 'COMPARE_REF="origin/${BASE_REF}"' in text
    assert ".github/upstream-base" not in text
    assert (ROOT / "contributors" / "emails" / "hermes@local").read_text(
        encoding="utf-8"
    ).splitlines()[0] == "batumilove"


def test_fork_delta_fetches_and_validates_the_canonical_upstream_ref() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "https://github.com/NousResearch/hermes-agent.git" in text
    assert "refs/remotes/canonical-upstream/main" in text
    assert "--upstream-ref refs/remotes/canonical-upstream/main" in text
