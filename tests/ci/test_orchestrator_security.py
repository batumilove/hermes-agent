"""Security invariants for the top-level CI orchestrator."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DOCKER_PR_PATH = REPO_ROOT / ".github" / "workflows" / "docker-pr.yml"
SUPPLY_CHAIN_PATH = REPO_ROOT / ".github" / "workflows" / "supply-chain-audit.yml"


def _jobs() -> dict:
    data = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    return data["jobs"]


def test_pull_request_orchestrator_never_references_persistent_app_private_key():
    text = CI_PATH.read_text(encoding="utf-8")

    assert "secrets.APP_PRIVATE_KEY" not in text
    assert "secrets.APP_CLIENT_ID" not in text
    assert "secrets: inherit" not in text
    assert "github-token: ${{ github.token }}" in text

    data = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert data["permissions"]["pull-requests"] == "read"
    assert jobs["comment-live"]["permissions"]["pull-requests"] == "write"
    assert jobs["ci-timings"]["permissions"] == {"contents": "read", "actions": "read"}


def test_pr_jobs_do_not_expose_privileged_credentials_to_pr_code():
    jobs = _jobs()
    docker = jobs["docker"]
    docker_publish = jobs["docker-publish"]
    lockfile = jobs["lockfile-diff"]
    comment = jobs["comment-live"]

    assert "secrets" not in docker
    assert docker["permissions"] == {"contents": "read", "actions": "read"}
    assert docker["uses"] == "./.github/workflows/docker-pr.yml"
    assert "event_name == 'pull_request'" in docker["if"]
    assert "event_name != 'pull_request'" in docker_publish["if"]
    assert docker_publish["uses"] == "./.github/workflows/docker.yml"
    assert set(docker_publish["secrets"]) == {"DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"}
    assert docker_publish["permissions"]["id-token"] == "write"
    assert docker_publish["permissions"]["attestations"] == "write"
    assert lockfile["permissions"] == {"contents": "read"}

    checkout = next(
        step for step in comment["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert checkout["with"]["persist-credentials"] is False

    docker_pr = yaml.safe_load(DOCKER_PR_PATH.read_text(encoding="utf-8"))
    assert docker_pr["permissions"] == {"contents": "read", "actions": "read"}
    docker_pr_text = DOCKER_PR_PATH.read_text(encoding="utf-8")
    assert "secrets." not in docker_pr_text
    assert "id-token:" not in docker_pr_text
    assert "attestations:" not in docker_pr_text
    assert "security-events:" not in docker_pr_text

    aggregate = jobs["all-checks-pass"]
    assert {"docker", "docker-publish"}.issubset(set(aggregate["needs"]))
    aggregate_script = aggregate["steps"][0]["run"]
    assert "'docker' if event_name == 'pull_request' else 'docker-publish'" in aggregate_script


def test_mcp_only_change_uses_review_label_gate_without_requiring_supply_chain():
    jobs = _jobs()
    condition = jobs["supply-chain"]["if"]
    aggregate_script = jobs["all-checks-pass"]["steps"][0]["run"]

    assert "needs.detect.outputs.mcp_catalog == 'true'" not in condition
    assert "MCP_CATALOG_CHANGED" not in aggregate_script


def test_critical_supply_chain_findings_fail_without_label_override():
    jobs = yaml.safe_load(SUPPLY_CHAIN_PATH.read_text(encoding="utf-8"))["jobs"]
    steps = {step.get("name"): step for step in jobs["scan"]["steps"]}
    fail_step = steps["Fail on critical supply-chain findings"]

    assert fail_step["if"] == "steps.scan.outputs.found == 'true'"
    assert "exit 1" in fail_step["run"]
