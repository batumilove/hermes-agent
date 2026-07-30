from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_workflow_is_manual_staging_only_and_approval_gated() -> None:
    workflow = _workflow("staging-provider-telemetry-deploy.yml")
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch) == {
        "infra_ops_sha",
        "infra_ops_tree",
        "expected_hermes_source_sha",
        "activation_ack",
    }
    assert dispatch["activation_ack"]["options"] == ["disabled", "enabled"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "hermes-staging-provider-telemetry-deploy",
        "cancel-in-progress": "false",
    }

    job = workflow["jobs"]["deploy"]
    assert job["environment"]["name"] == "batumi-staging"
    assert job["timeout-minutes"] == "30"
    condition = job["if"]
    for gate in (
        "vars.HERMES_STAGING_TELEMETRY_DEPLOY_ENABLED == 'true'",
        "inputs.activation_ack == 'enabled'",
        "github.repository == 'batumilove/hermes-agent'",
        "github.ref == 'refs/heads/batumi/live'",
        "inputs.infra_ops_sha == vars.HERMES_STAGING_TELEMETRY_APPROVED_SHA",
        "inputs.infra_ops_tree == vars.HERMES_STAGING_TELEMETRY_APPROVED_TREE",
    ):
        assert gate in condition
    assert "production" not in (WORKFLOWS / "staging-provider-telemetry-deploy.yml").read_text().lower()


def test_workflow_checks_out_exact_private_source_with_repo_scoped_deploy_key() -> None:
    workflow = _workflow("staging-provider-telemetry-deploy.yml")
    job = workflow["jobs"]["deploy"]
    checkout = next(step for step in job["steps"] if step["name"] == "Check out exact infra-ops source")
    assert PINNED_ACTION.fullmatch(checkout["uses"])
    assert checkout["with"] == {
        "repository": "batumilove/infra-ops",
        "ref": "${{ inputs.infra_ops_sha }}",
        "path": "infra-ops",
        "ssh-key": "${{ secrets.INFRA_OPS_READ_KEY }}",
        "ssh-strict": "true",
        "persist-credentials": "false",
    }

    raw = (WORKFLOWS / "staging-provider-telemetry-deploy.yml").read_text()
    assert "INFRA_OPS_READ_TOKEN" not in raw

    verify = next(step for step in job["steps"] if step["name"] == "Verify immutable source and candidate manifest")
    script = verify["run"]
    assert "git -C infra-ops rev-parse 'HEAD^{commit}'" in script
    assert "git -C infra-ops rev-parse 'HEAD^{tree}'" in script
    assert "sha256sum" in script
    assert "integrations/hermes-provider-telemetry/plugin/__init__.py" in script
    assert "integrations/hermes-provider-telemetry/plugin/plugin.yaml" in script
    assert "integrations/hermes-provider-telemetry/prometheus/hermes_provider_telemetry.rules.yml" in script


def test_workflow_uses_separate_pinned_ssh_identities_and_strict_options() -> None:
    raw = (WORKFLOWS / "staging-provider-telemetry-deploy.yml").read_text()
    for secret in (
        "DEPLOY_SSH_KEY",
        "DEPLOY_KNOWN_HOSTS",
        "MONITORING_DEPLOY_SSH_KEY",
        "MONITORING_KNOWN_HOSTS",
    ):
        assert f"secrets.{secret}" in raw
    for option in (
        "-F /dev/null",
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "PreferredAuthentications=publickey",
        "NumberOfPasswordPrompts=0",
        "ConnectTimeout=15",
    ):
        assert option in raw
    assert "StrictHostKeyChecking=no" not in raw
    assert "accept-new" not in raw


def test_workflow_stages_only_fixed_artifacts_and_invokes_reviewed_scripts() -> None:
    workflow = _workflow("staging-provider-telemetry-deploy.yml")
    job = workflow["jobs"]["deploy"]
    apply_step = next(step for step in job["steps"] if step["name"] == "Apply staging telemetry transaction")
    script = apply_step["run"]
    for required in (
        "scripts/deploy/hermes-staging-provider-telemetry.sh",
        "scripts/deploy/prometheus-staging-provider-telemetry.sh",
        "plugin/__init__.py",
        "plugin/plugin.yaml",
        "prometheus/hermes_provider_telemetry.rules.yml",
        "manifest.sha256",
    ):
        assert required in script
    assert "scp" in script
    assert "bash -s -- deploy" in script
    assert "bash -s -- verify" in script
    assert "bash -s -- rollback" in script
    assert "trap rollback ERR" in script
    assert "rollback_failed=0" in script
    assert "exit 70" in script


def test_hermes_deployer_is_fixed_path_atomic_and_counter_fail_closed() -> None:
    raw = (REPO / "scripts/deploy/hermes-staging-provider-telemetry.sh").read_text()
    for fixed in (
        "hermes-batumi-staging-gateway",
        "/home/hermes-staging/.hermes-staging",
        "/opt/hermes-compose/staging/telemetry-backups",
        "/run/lock/hermes-staging-diagnostic.lock",
        "hermes_provider_telemetry.prom",
        "hermes_provider_telemetry.prom.lock",
    ):
        assert fixed in raw
    for safety in (
        "set -euo pipefail",
        "flock -n",
        "sha256sum -c",
        "HERMES_SOURCE_SHA",
        "s6-svc -d",
        "s6-svc -u",
        "os.replace",
        "fcntl.LOCK_EX | fcntl.LOCK_NB",
        "counter decreased",
        "rollback",
        "ROLLBACK_FAILED",
    ):
        assert safety in raw
    assert "eval " not in raw
    assert "StrictHostKeyChecking=no" not in raw


def test_prometheus_deployer_validates_before_atomic_install_and_readback() -> None:
    raw = (REPO / "scripts/deploy/prometheus-staging-provider-telemetry.sh").read_text()
    for fixed in (
        "/opt/monitoring/prometheus/rules/hermes_provider_telemetry.rules.yml",
        "/var/backups/hermes-provider-telemetry",
        "prometheus",
    ):
        assert fixed in raw
    for safety in (
        "set -euo pipefail",
        "flock -n",
        "sha256sum -c",
        "promtool check rules",
        "os.replace",
        "/-/reload",
        "/api/v1/rules",
        "HermesProviderTelemetryCounterRegression",
        "rollback",
        "ROLLBACK_FAILED",
    ):
        assert safety in raw
    assert "/opt/monitoring/prometheus/rules/backup" not in raw
    assert "eval " not in raw
