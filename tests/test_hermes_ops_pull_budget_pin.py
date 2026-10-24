from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_surfaces_match_locked_ops_generation() -> None:
    lock = yaml.safe_load(
        (ROOT / ".github" / "batumi-components.lock.yaml").read_text(encoding="utf-8")
    )
    ops = next(
        item for item in lock["components"] if item["repository"] == "batumilove/hermes-ops"
    )
    ops_sha = ops["commit"]

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
        assert set(refs) == {ops_sha}

    for name in ("deploy-compose.yml", "promote-compose.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert f"ops_sha: {ops_sha}" in text
