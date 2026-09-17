from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PATCH_ID = "fork-patch-governance"
PIN_TEST = "tests/test_hermes_ops_pull_budget_pin.py"


def test_fork_patch_governance_executes_pull_budget_pin_regression() -> None:
    manifest = yaml.safe_load(
        (ROOT / ".github" / "batumi-patches.yaml").read_text(encoding="utf-8")
    )
    patch = next(item for item in manifest["patches"] if item["id"] == PATCH_ID)

    assert PIN_TEST in patch["paths"]
    assert any(PIN_TEST in command for command in patch["tests"])
