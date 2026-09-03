from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "upstream-sync.yml"


def _workflow() -> tuple[dict, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def test_sync_is_one_daily_serial_batch() -> None:
    workflow, _ = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {
        "schedule": [{"cron": "17 3 * * *"}],
        "workflow_dispatch": None,
    }
    assert workflow["concurrency"] == {
        "group": "upstream-sync-batumi-live",
        "cancel-in-progress": False,
    }


def test_privileged_job_merges_without_executing_upstream() -> None:
    workflow, text = _workflow()
    job = workflow["jobs"]["prepare"]
    assert workflow["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert "secrets.UPSTREAM_SYNC_DEPLOY_KEY" in text
    assert "persist-credentials: false" in text
    assert "git merge --no-ff --no-commit upstream/main" in text
    assert "python" not in text
    assert "pytest" not in text
    assert "npm " not in text
    assert "force-with-lease" not in text
    assert job["if"] == "github.repository == 'batumilove/hermes-agent'"


def test_sync_advances_base_and_requires_a_reviewed_pr() -> None:
    _, text = _workflow()
    assert "printf '%s\\n' \"$upstream_sha\" > .github/upstream-base" in text
    assert "gh pr create" in text
    assert "gh pr merge" not in text
    assert "git reset --hard" not in text
