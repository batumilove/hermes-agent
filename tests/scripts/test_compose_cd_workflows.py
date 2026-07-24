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


def test_publish_job_reuses_only_verified_exact_source_artifacts() -> None:
    workflow = _workflow("deploy-compose.yml")
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    publish = workflow["jobs"]["publish-image"]
    steps = publish["steps"]
    resolve = next(step for step in steps if step.get("id") == "resolve")
    verify = next(step for step in steps if step["name"] == "Verify reused image provenance")
    build = next(step for step in steps if step.get("id") == "build")
    attest = next(step for step in steps if step["name"] == "Attest image provenance")
    login = next(
        step for step in steps if step["name"] == "Log in to the fork GHCR namespace"
    )

    assert "resolve_ghcr_digest.py" in resolve["run"]
    assert resolve["env"]["GHCR_TOKEN"] == "${{ github.token }}"
    assert steps.index(login) < steps.index(resolve)
    assert "if" not in login
    assert build["if"] == "steps.resolve.outputs.exists == 'false'"
    assert attest["if"] == "steps.resolve.outputs.exists == 'false'"
    assert verify["if"] == "steps.resolve.outputs.exists == 'true'"
    assert "gh attestation verify" in verify["run"]
    assert '--source-digest "$EXPECTED_SHA"' in verify["run"]
    assert '--source-ref "$GITHUB_REF"' in verify["run"]
    assert "--signer-workflow" in verify["run"]
    assert '--signer-digest "$EXPECTED_SHA"' in verify["run"]
    assert "--deny-self-hosted-runners" in verify["run"]
    assert "verify_image_attestation.py" in verify["run"]
    assert '--source-sha "$EXPECTED_SHA"' in verify["run"]
    assert '--source-uri "$EXPECTED_SOURCE_URI"' in verify["run"]
    assert publish["outputs"]["digest"] == (
        "${{ steps.resolve.outputs.digest || steps.build.outputs.digest }}"
    )


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
    tailnet = next(step for step in job["steps"] if step["name"] == "Join the deployment tailnet")
    assert tailnet["with"]["authkey"] == "${{ secrets.TAILSCALE_AUTHKEY }}"
    assert reusable["on"]["workflow_call"]["secrets"]["TAILSCALE_AUTHKEY"]["required"] == "false"


def test_deployment_installs_exact_running_stack_acceptance_helper() -> None:
    reusable = _workflow("_deploy-compose.yml")
    run = next(
        step["run"]
        for step in reusable["jobs"]["deploy"]["steps"]
        if step["name"] == "Install deployment tooling and apply release"
    )
    deployer = (REPO / "scripts" / "deploy" / "hermes-compose-deploy.sh").read_text(
        encoding="utf-8"
    )

    assert "scripts/deploy/verify_running_stack.py" in run
    assert 'install -m 0755' in run
    assert 'verify-running-stack.py' in run
    assert 'verify-running-stack.py' in deployer
    assert deployer.index('verify-running-stack.py') < deployer.index('record_evidence deployed')


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
    run_tmpfs = next(entry for entry in gateway["tmpfs"] if entry.startswith("/run:"))
    assert "noexec" not in run_tmpfs
    assert "exec" in run_tmpfs.split(",")
    assert gateway["stop_grace_period"] == "90s"
    assert gateway["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "s6-svstat" in gateway["healthcheck"]["test"][1]
    assert gateway["logging"]["options"] == {"max-size": "20m", "max-file": "5"}


def test_staging_diagnostic_workflow_delegates_exact_bounded_json_only() -> None:
    workflow = _workflow("staging-telegram-socket-diagnostics.yml")
    assert workflow["on"]["workflow_dispatch"]["inputs"]["observation_seconds"]["options"] == ["60", "90", "120"]
    job = workflow["jobs"]["observe"]
    assert job["if"] == (
        "vars.HERMES_STAGING_DIAGNOSTICS_ENABLED == 'true' && "
        "inputs.activation_ack == 'enabled' && "
        "github.repository == 'batumilove/hermes-agent' && "
        "github.ref == 'refs/heads/batumi/live' && "
        "github.sha == inputs.expected_source_sha"
    )
    assert workflow["on"]["workflow_dispatch"]["inputs"]["activation_ack"] == {
        "description": "Explicit activation acknowledgement (must be enabled)",
        "required": "true",
        "default": "disabled",
        "type": "choice",
        "options": ["disabled", "enabled"],
    }
    assert "inputs.activation_ack == 'enabled'" in job["if"]
    assert job["environment"]["name"] == "batumi-staging"
    assert job["timeout-minutes"] == "20"
    assert workflow["concurrency"] == {
        "group": "hermes-staging-socket-diagnostics",
        "cancel-in-progress": "false",
    }
    run_step = next(step for step in job["steps"] if step["name"] == "Run bounded diagnostics")
    script = run_step["run"]
    assert "python3 -c" in script
    assert "json.dumps" in script
    assert "GITHUB_RUN_ID" in script and "GITHUB_RUN_ATTEMPT" in script
    assert "nonce" in script
    assert "sudo -n /usr/local/libexec/hermes-staging-diagnostic" in script
    assert "ssh \"${ssh_opts[@]}\" \"$target\" 'sudo -n /usr/local/libexec/hermes-staging-diagnostic'" in script
    assert "4096" in script


def test_staging_diagnostic_workflow_has_no_remote_privileged_logic_or_raw_logs() -> None:
    raw = (WORKFLOWS / "staging-telegram-socket-diagnostics.yml").read_text()
    forbidden = [
        "docker inspect", "docker restart", "docker exec", "docker logs",
        "gateway.json", ".diagnostic-backups", "runtime_uid", "runtime_gid",
        "sudo -n /usr/local/libexec/hermes-staging-diagnostic --", "bash -s --",
        "watchdog", "readlink -f", "stat -c", "flock ",
    ]
    for value in forbidden:
        assert value not in raw
    assert "/home/hermes-staging/.hermes-staging" not in raw
    assert "DEPLOY_ROOT" not in raw
