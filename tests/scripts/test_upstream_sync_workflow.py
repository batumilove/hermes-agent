from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "upstream-sync.yml"
BASELINE = ROOT / ".github" / "upstream-sync-base"
EXPECTED_BASELINE = "9b1028f2974f7b456285b23b28eac5336f71e13c"


def _workflow() -> tuple[dict, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def test_sync_baseline_is_exact_reviewed_upstream_sha():
    assert BASELINE.read_text(encoding="utf-8") == f"{EXPECTED_BASELINE}\n"


def test_workflow_is_serial_and_runs_on_schedule_manual_and_merged_sync_pr():
    workflow, _ = _workflow()
    # PyYAML 1.1 parses the unquoted GitHub Actions `on` key as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)

    assert triggers["schedule"] == [{"cron": "*/15 * * * *"}]
    assert "workflow_dispatch" in triggers
    assert triggers["pull_request"] == {
        "types": ["closed"],
        "branches": ["batumi/live"],
    }
    assert workflow["concurrency"] == {
        "group": "upstream-sync-batumi-live",
        "cancel-in-progress": False,
    }


def test_workflow_uses_scoped_deploy_key_and_never_executes_upstream_code():
    workflow, text = _workflow()
    job = workflow["jobs"]["sync-one-commit"]

    assert workflow["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert job["if"].startswith("github.repository == 'batumilove/hermes-agent'")
    assert "secrets.UPSTREAM_SYNC_DEPLOY_KEY" in text
    assert "github.token" in text
    assert "APP_CLIENT_ID" not in text
    assert "APP_PRIVATE_KEY" not in text
    assert "persist-credentials: false" in text
    assert "persist-credentials: true" not in text
    assert "git@github.com:batumilove/hermes-agent.git" in text
    assert "pytest" not in text
    assert "npm " not in text
    assert "uv " not in text


def test_workflow_advances_exactly_one_first_parent_commit_and_fails_closed():
    _, text = _workflow()

    required_fragments = [
        'git merge-base --is-ancestor "$BASELINE" upstream/main',
        'git rev-list --first-parent --reverse "$BASELINE..upstream/main"',
        'FIRST_PARENT=$(git rev-parse "$NEXT_COMMIT^1")',
        'if [ "$FIRST_PARENT" != "$BASELINE" ]',
        'git cherry-pick "$NEXT_COMMIT"',
        'git cherry-pick -m 1 "$NEXT_COMMIT"',
        'printf \'%s\\n\' "$NEXT_COMMIT" > .github/upstream-sync-base',
        'gh pr merge "$PR_NUMBER" --auto --rebase',
    ]
    for fragment in required_fragments:
        assert fragment in text

    assert "|| true" not in text
    assert "git push --force " not in text
    assert "--force-with-lease=refs/heads/$BRANCH:$INITIAL_HEAD" in text
    assert "git reset --hard" not in text


def test_pr_exists_before_final_lease_guarded_push_triggers_ci():
    _, text = _workflow()

    initial_push = text.index('git push origin "HEAD:refs/heads/$BRANCH"')
    create_pr = text.index("gh pr create")
    amend_marker = text.index("git commit --amend --no-edit")
    final_push = text.index("--force-with-lease=refs/heads/$BRANCH:$INITIAL_HEAD")
    auto_merge = text.index('gh pr merge "$PR_NUMBER" --auto --rebase')

    assert initial_push < create_pr < amend_marker < final_push < auto_merge


def test_deploy_key_cleanup_does_not_depend_on_setup_export():
    _, text = _workflow()

    assert "UPSTREAM_SYNC_KEY_FILE" not in text
    assert 'KEY_FILE="$RUNNER_TEMP/upstream-sync-deploy-key"' in text
    assert 'shred -u "$KEY_FILE"' in text


def test_workflow_blocks_legacy_or_existing_sync_prs_before_mutation():
    _, text = _workflow()

    assert "sync/hermes-update-" in text
    assert "sync/upstream-" in text
    assert "Open sync PR already exists" in text
    assert text.index("Open sync PR already exists") < text.index("git cherry-pick")
