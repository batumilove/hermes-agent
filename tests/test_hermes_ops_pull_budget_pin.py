from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OPS_SHA = "e2de0980d2898b3b629119cce4c3662b5988ff0a"


def test_deployment_surfaces_pin_slow_pull_budget_generation() -> None:
    lock = yaml.safe_load(
        (ROOT / ".github" / "batumi-components.lock.yaml").read_text(encoding="utf-8")
    )
    ops = next(
        item for item in lock["components"] if item["repository"] == "batumilove/hermes-ops"
    )
    assert ops["commit"] == EXPECTED_OPS_SHA

    workflows = (
        "deploy-compose.yml",
        "promote-compose.yml",
        "stack-ci-shadow.yml",
        "staging-telegram-socket-diagnostics.yml",
    )
    for name in workflows:
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        jobs = yaml.safe_load(text)["jobs"]
        refs = [job["uses"].rsplit("@", 1)[1]
                for role, job in jobs.items()
                if job.get("uses", "").startswith("batumilove/hermes-ops/")
                and not (name == "deploy-compose.yml" and role == "publish")]
        assert refs
        assert set(refs) == {EXPECTED_OPS_SHA}

    for name in ("deploy-compose.yml", "promote-compose.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert f"ops_sha: {EXPECTED_OPS_SHA}" in text
