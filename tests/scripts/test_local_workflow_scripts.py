import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(REPO / "scripts" / args[0]), *args[1:]],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )


def test_local_update_with_patches_dry_run_documents_patch_branch():
    result = run_script("local-update-with-patches.sh", "--dry-run")

    assert result.returncode == 0
    assert "local/hermes-patch-stack-20260426" in result.stdout
    assert "backup/local-patches-before-update-" in result.stdout
    assert "git rebase origin/main" in result.stdout
    assert "tests/cron/test_cron_context_from.py" in result.stdout
    assert "systemctl --user restart hermes-gateway" in result.stdout


def test_cron_context_from_smoke_help_documents_live_safe_cleanup():
    result = run_script("smoke-cron-context-from.py", "--help")

    assert result.returncode == 0
    assert "context_from" in result.stdout
    assert "creates temporary cron jobs" in result.stdout
    assert "removes temporary jobs" in result.stdout
    assert "--keep-output" in result.stdout
