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
        "promote-compose.yml",
        "stack-ci-shadow.yml",
        "staging-telegram-socket-diagnostics.yml",
    )
    for name in workflows:
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        jobs = yaml.safe_load(text)["jobs"]
        refs = [job["uses"].rsplit("@", 1)[1]
                for job in jobs.values()
                if job.get("uses", "").startswith("batumilove/hermes-ops/")]
        assert refs
        assert set(refs) == {ops_sha}

    for name in ("promote-compose.yml",):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert f"ops_sha: {ops_sha}" in text

    # The manual immutable-only bridge deliberately uses a separately reviewed
    # generation; all other operational surfaces remain bound to the lock.
    bridge = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-compose.yml").read_text(encoding="utf-8")
    )
    bridge_sha = "c89af38733b73087883609110a0a5e69e83c5c63"
    assert set(bridge["jobs"]) == {"publish"}
    publish = bridge["jobs"]["publish"]
    assert publish["uses"] == (
        f"batumilove/hermes-ops/.github/workflows/build-image.yml@{bridge_sha}"
    )
    assert publish["with"]["ops_sha"] == bridge_sha
    assert publish["with"]["immutable_only"] is True
