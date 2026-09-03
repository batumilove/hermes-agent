from pathlib import Path

import tomllib
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict:
    with (ROOT / ".github" / "workflows" / name).open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=yaml.BaseLoader)


def test_osv_reusable_workflow_is_pinned_to_fully_immutable_v2_5_1() -> None:
    workflow = _workflow("osv-scanner.yml")
    assert workflow["jobs"]["scan"]["uses"] == (
        "google/osv-scanner-action/.github/workflows/"
        "osv-scanner-reusable.yml@6e4298ebc4db23e847df9b2e2de2939d6f066c67"
    )


def test_ci_elevates_permissions_only_for_jobs_that_need_them() -> None:
    workflow = _workflow("ci.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["supply-chain"]["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
    }
    assert workflow["jobs"]["review-labels"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert workflow["jobs"]["osv-scanner"]["permissions"] == {
        "actions": "read",
        "contents": "read",
        "security-events": "write",
    }


def test_deploy_elevates_publish_permissions_only() -> None:
    workflow = _workflow("deploy-compose.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["publish"]["permissions"] == {
        "contents": "read",
        "checks": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert workflow["jobs"]["deploy-staging"]["permissions"] == {
        "contents": "read"
    }


def test_dcf_uses_the_uv_managed_optional_extra() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["optional-dependencies"]["dcf"] == ["openpyxl==3.1.5"]
    assert not (ROOT / "optional-skills/finance/dcf-model/requirements.txt").exists()
