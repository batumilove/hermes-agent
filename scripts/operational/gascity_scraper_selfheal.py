#!/usr/bin/env python3
"""Conservative Gas City self-heal loop for general-real-estate-scraper.

Runs an audit, then asks Gas City/Codex to fix exactly one top-priority issue on
an isolated branch. It never merges or pushes.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

RUNNER = Path('/home/ubuntu/gascity-codex-runner')
REPO = Path('/home/ubuntu/general-real-estate-scraper')
WORKSPACE = Path('/srv/gascity-codex-runner/workspace-compose')
AUDIT = REPO / 'GASCITY_AUDIT.md'


def run(cmd: str, cwd: Path | None = None, timeout: int = 1800, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {cmd}", flush=True)
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, shell=True, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    print(p.stdout, flush=True)
    if check and p.returncode != 0:
        raise SystemExit(f"command failed ({p.returncode}): {cmd}")
    return p


def git_status() -> list[str]:
    out = run('git status --porcelain', REPO, timeout=60).stdout
    return [line for line in out.splitlines() if line.strip()]


def only_allowed_dirty(lines: list[str]) -> bool:
    # Allow only the audit report as uncommitted input/output. Everything else
    # means a human or previous agent has work in progress that we must not trample.
    return all(line[3:] == 'GASCITY_AUDIT.md' for line in lines)


def cleanup_generated_runtime_dirs() -> None:
    """Remove Gas City/Codex runtime dirs left untracked in the target repo."""
    for name in ['.codex', '.gc']:
        path = REPO / name
        if path.exists() and not run(f'git ls-files --error-unmatch {shlex.quote(name)}', REPO, timeout=30, check=False).returncode == 0:
            run(f'rm -rf -- {shlex.quote(name)}', REPO, timeout=60)


def recover_to_main_if_safe(branch: str) -> str:
    """Recover from a previous failed auto branch that made no commits."""
    if branch == 'main':
        return branch
    if not branch.startswith('gascity-auto/fix-one-'):
        raise SystemExit(f"Refusing to run from branch {branch!r}; expected main")
    head = run('git rev-parse HEAD', REPO, timeout=60).stdout.strip()
    main = run('git rev-parse main', REPO, timeout=60).stdout.strip()
    if head != main:
        raise SystemExit(f"Refusing to recover auto branch {branch!r}: HEAD differs from main")
    cleanup_generated_runtime_dirs()
    dirty = git_status()
    if dirty and not only_allowed_dirty(dirty):
        raise SystemExit('Refusing to recover auto branch: repo has unrelated local changes:\n' + '\n'.join(dirty))
    run('git switch main', REPO, timeout=60)
    print(f"Recovered from stale empty auto branch {branch!r}; switched back to main.", flush=True)
    return 'main'


def ensure_runner_image() -> None:
    """Build the local Gas City/Codex sandbox image if Docker no longer has it."""
    inspect = run('sudo -n docker image inspect gc-hermes-sandbox:codex >/dev/null 2>&1', RUNNER, timeout=60, check=False)
    if inspect.returncode == 0:
        print('Docker image gc-hermes-sandbox:codex is present.', flush=True)
        return
    print('Docker image gc-hermes-sandbox:codex is missing; rebuilding from /home/ubuntu/gascity-codex-runner.', flush=True)
    run('sudo -n docker build -t gc-hermes-sandbox:codex .', RUNNER, timeout=900)


def run_gascity(task: str, expect_file: str = '', expect_cmd: str = '', timeout_s: int = 1800) -> None:
    ensure_runner_image()
    env = os.environ.copy()
    env.update({
        'HOST_WORKSPACE_DIR': str(WORKSPACE),
        'RIG_NAME': 'general-real-estate-scraper',
        'HOST_RIG_DIR': str(REPO),
        'EXPECT_TIMEOUT_SECONDS': str(timeout_s),
    })
    if expect_file:
        env['EXPECT_FILE'] = expect_file
    if expect_cmd:
        env['EXPECT_CMD'] = expect_cmd
    cmd = ['./run-gascity-task.sh', task]
    print(f"\n$ {' '.join(shlex.quote(x) for x in cmd)}", flush=True)
    p = subprocess.run(cmd, cwd=str(RUNNER), env=env, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_s + 600)
    print(p.stdout, flush=True)
    if p.returncode != 0:
        raise SystemExit(f"Gas City task failed ({p.returncode})")


def cleanup_stack() -> None:
    run(f"sudo -n env HOST_WORKSPACE_DIR={shlex.quote(str(WORKSPACE))} docker-compose -f docker-compose.codex.yml down || true", RUNNER, timeout=180, check=False)


def audit_has_actionable_top_issue(text: str) -> bool:
    critical = re.search(r'## Critical Issues\n(.*?)(\n## |\Z)', text, re.S)
    high = re.search(r'## High Priority Issues\n(.*?)(\n## |\Z)', text, re.S)
    for block in [critical.group(1) if critical else '', high.group(1) if high else '']:
        stripped = re.sub(r'(?im)^\s*(none|no .*issues).*$', '', block).strip()
        if re.search(r'(?m)^###\s+', stripped) or re.search(r'(?m)^\d+\.\s+', stripped):
            return True
    return False


def main() -> None:
    if any(arg in {'-h', '--help'} for arg in sys.argv[1:]):
        print(__doc__.strip())
        print('\nUsage: gascity_scraper_selfheal.py')
        print('Runs immediately; intended for Hermes cron. No auto-merge or push.')
        return

    print(f"Gas City scraper self-heal loop started at {dt.datetime.now(dt.timezone.utc).isoformat()}")
    for path in [RUNNER, REPO]:
        if not path.exists():
            raise SystemExit(f"missing path: {path}")

    run('git fetch origin --prune', REPO, timeout=120, check=False)
    branch = run('git branch --show-current', REPO, timeout=60).stdout.strip()
    branch = recover_to_main_if_safe(branch)
    cleanup_generated_runtime_dirs()

    # Keep the scheduled loop auditing the current default branch. Preserve the
    # allowed untracked audit report, but refuse to update if any other local
    # work is present.
    dirty = git_status()
    if branch == 'main' and (not dirty or only_allowed_dirty(dirty)):
        run('git pull --ff-only origin main', REPO, timeout=120, check=True)
        dirty = git_status()
    if dirty and not only_allowed_dirty(dirty):
        raise SystemExit('Refusing to run: repo has unrelated local changes:\n' + '\n'.join(dirty))

    run(f'sudo -n mkdir -p {shlex.quote(str(WORKSPACE))} && sudo -n chown -R 1000:1000 /srv/gascity-codex-runner', timeout=120)

    try:
        run_gascity(
            'Audit this repository for production readiness, security issues, reliability bugs, and test/deployment gaps. '
            'Do not modify application code. Create or update GASCITY_AUDIT.md with sections: Summary, Critical Issues, High Priority Issues, Medium/Low Priority Issues, Verification Performed, Recommended Next Tasks. '
            'Run existing tests and config validation if possible, and include exact commands/results in the report.',
            expect_file='GASCITY_AUDIT.md',
            timeout_s=1200,
        )

        text = AUDIT.read_text(errors='replace') if AUDIT.exists() else ''
        if not audit_has_actionable_top_issue(text):
            print('Audit completed; no Critical/High actionable issue detected. Stopping before fix phase.')
            return

        date = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
        fix_branch = f'gascity-auto/fix-one-{date}'
        run(f'git switch -c {shlex.quote(fix_branch)}', REPO, timeout=60)

        validation = 'python3 -m unittest discover -s tests -v && python3 general_scraper.py validate-config --config config.yaml && python3 general_scraper.py validate-config --config config.json && python3 -m compileall . && git diff --check'
        run_gascity(
            'Fix exactly one top Critical/High issue from GASCITY_AUDIT.md. Commit it on the current branch. Do not push or merge.',
            expect_cmd=validation + ' && test -z "$(git status --porcelain --untracked-files=no)"',
            timeout_s=2400,
        )

        print('\nFinal status:')
        run('git status --short --branch && git log --oneline -3', REPO, timeout=60)
        print(f'Created/updated branch: {fix_branch}')
    finally:
        cleanup_stack()


if __name__ == '__main__':
    main()
