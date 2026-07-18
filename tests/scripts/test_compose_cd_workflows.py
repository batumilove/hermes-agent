from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_automatic_deployment_is_fork_and_live_branch_scoped() -> None:
    workflow = _workflow("deploy-compose.yml")

    assert workflow["on"] == {"push": {"branches": ["batumi/live"]}}
    wait = workflow["jobs"]["wait-for-required-ci"]
    assert wait["if"] == "github.repository == 'batumilove/hermes-agent'"
    assert wait["permissions"] == {"contents": "read", "checks": "read"}
    script = wait["steps"][0]["run"]
    assert "All required checks pass" not in script  # supplied through env
    assert "15368" in script
    assert "Refusing stale deployment" in script


def test_publish_job_has_only_required_write_permissions_and_attests() -> None:
    workflow = _workflow("deploy-compose.yml")
    publish = workflow["jobs"]["publish-image"]

    assert publish["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    build = next(step for step in publish["steps"] if step.get("id") == "build")
    assert build["with"]["push"] == "true"
    assert build["with"]["platforms"] == "linux/amd64"
    assert build["with"]["provenance"] == "mode=max"
    assert build["with"]["sbom"] == "true"
    assert "HERMES_GIT_SHA=" in build["with"]["build-args"]
    attest = next(
        step for step in publish["steps"] if step["name"] == "Attest image provenance"
    )
    assert attest["with"]["push-to-registry"] == "true"


def test_deployment_uses_environment_scoped_reusable_workflow() -> None:
    automatic = _workflow("deploy-compose.yml")["jobs"]["deploy-staging"]
    manual = _workflow("promote-compose.yml")["jobs"]["deploy"]
    reusable = _workflow("_deploy-compose.yml")

    assert automatic["uses"] == "./.github/workflows/_deploy-compose.yml"
    assert automatic["if"] == "vars.HERMES_STAGING_DEPLOY_ENABLED == 'true'"
    assert automatic["with"]["environment"] == "batumi-staging"
    assert automatic["secrets"] == "inherit"
    assert manual["uses"] == "./.github/workflows/_deploy-compose.yml"
    assert manual["secrets"] == "inherit"
    job = reusable["jobs"]["deploy"]
    assert job["environment"]["name"] == "${{ inputs.environment }}"
    assert reusable["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] == "20"


def test_manual_promotion_verifies_digest_without_rebuilding() -> None:
    workflow = _workflow("promote-compose.yml")
    verify = workflow["jobs"]["verify-candidate"]
    scripts = "\n".join(
        step.get("run", "") for step in verify["steps"] if isinstance(step, dict)
    )

    assert "docker buildx imagetools inspect" in scripts
    assert "git merge-base --is-ancestor" in scripts
    assert not any(
        "docker/build-push-action" in step.get("uses", "")
        for step in verify["steps"]
    )
    options = workflow["on"]["workflow_dispatch"]["inputs"]["operation"]["options"]
    assert options == ["deploy", "rollback"]


def test_all_new_external_actions_are_commit_pinned() -> None:
    for name in ("_deploy-compose.yml", "deploy-compose.yml", "promote-compose.yml"):
        workflow = _workflow(name)
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses and not uses.startswith("./"):
                    assert PINNED_ACTION.fullmatch(uses), uses


def test_compose_runtime_is_digest_driven_and_health_checked() -> None:
    compose = yaml.load((REPO / "deploy" / "compose.yml").read_text(), Loader=yaml.BaseLoader)
    gateway = compose["services"]["gateway"]

    assert "HERMES_IMAGE" in gateway["image"]
    assert gateway["network_mode"] == "host"
    assert gateway["security_opt"] == ["no-new-privileges:true"]
    assert gateway["stop_grace_period"] == "90s"
    assert gateway["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "s6-svstat" in gateway["healthcheck"]["test"][1]
    assert gateway["logging"]["options"] == {"max-size": "20m", "max-file": "5"}
