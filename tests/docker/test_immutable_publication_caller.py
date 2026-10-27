"""Fail-closed contract for the temporary publication-only caller."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OPS_SHA = "c89af38733b73087883609110a0a5e69e83c5c63"


def test_publication_is_manual_only_and_has_no_deployment_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-compose.yml").read_text())
    # PyYAML's YAML 1.1 loader interprets the Actions `on` key as True.
    assert workflow[True] == {"workflow_dispatch": None}
    assert set(workflow["jobs"]) == {"publish"}
    publish = workflow["jobs"]["publish"]
    assert publish["uses"] == f"batumilove/hermes-ops/.github/workflows/build-image.yml@{OPS_SHA}"
    assert publish["with"] == {
        "source_sha": "${{ github.sha }}",
        "ops_sha": OPS_SHA,
        "immutable_only": True,
        "deployment_lane": "live",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {
        "attestations": "write", "checks": "read", "contents": "read",
        "id-token": "write", "packages": "write",
    }
