# Local Patch Stack

This checkout intentionally carries a small local patch stack on top of upstream `main`.
Do not treat a clean upstream `main` as the complete desired state for this machine.

## Current branch

Use a local integration branch for this machine's patches:

```bash
git switch local/hermes-patch-stack-20260426
```

Upstream remotes on this machine are expected to be:

- `origin` / `upstream`: `NousResearch/hermes-agent`
- `myfork`: user fork / push target when publishing local branches

## Current patches to keep across updates

The current patch stack, newest last, is:

1. `chore(gitignore): ignore local mcporter config token`
   - Keeps `config/mcporter.json` out of git status because it can contain bearer tokens.
2. `Fix GLM error 1213: handle empty prompts gracefully`
   - Local runtime robustness fix in `gateway/run.py`.
3. `docs: update tool registration and update workflow docs`
   - Local docs improvements.
4. `docs: warn that hermes update can drop local main commits`
   - Documents the dangerous update behavior.
5. `fix(gateway): resolve stale PID file blocking startup after forced kill`
   - Gateway stale PID/process identity hardening.
6. `fix(approval): block systemctl restart hermes-gateway in sessions`
   - Prevents agents from restarting their own gateway without the safer restart path.
7. `feat(update): sync bundled telegram healthcheck helper`
   - Keeps bundled helper scripts synced.
8. `refactor(update): keep telegram healthcheck path stable`
   - Stabilizes telegram healthcheck paths.
9. `fix: fall back from Browserbase quota to Browser Use`
   - Browser fallback behavior for Browserbase quota failures.

Check the actual current stack with:

```bash
git log --oneline --decorate origin/main..HEAD
```

## Safe update procedure for this machine

Avoid running `hermes update` blindly from a feature/local patch branch. The built-in updater may switch to `main` and hard-reset it to `origin/main`, dropping local-only commits from `main` history.

Recommended workflow:

```bash
cd ~/.hermes/hermes-agent

git fetch origin upstream myfork --prune

git status --short --branch
git log --oneline --decorate origin/main..HEAD

# Keep an explicit backup ref before moving anything.
git branch backup/local-patches-before-update-$(date +%Y%m%d-%H%M%S) HEAD

# Rebase the local patch branch on the new upstream main.
git switch local/hermes-patch-stack-20260426
git rebase origin/main

# If conflicts occur, resolve them, then:
git add <resolved-files>
git rebase --continue

# Verify after the rebase.
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git grep -n -E '^(<<<<<<<|>>>>>>>)' -- '*.py' '*.md' '*.yaml' '*.yml' '*.json' || true
source venv/bin/activate
python -m pytest tests/cron/test_cron_context_from.py tests/tools/test_approval.py tests/hermes_cli/test_update_bundled_scripts.py tests/tools/test_bundled_scripts_sync.py -q -o 'addopts='
```

If you do run `hermes update`, audit immediately afterward:

```bash
git status --short --branch
git reflog --date=iso -12
git log --oneline --decorate origin/main..HEAD
git diff --stat
git diff -- web/package-lock.json | sed -n '1,220p'
git check-ignore -v config/mcporter.json 2>/dev/null || true
```

Expected cleanup after `hermes update`:

- Revert accidental `web/package-lock.json` churn unless frontend deps were intentionally changed.
- Ensure `config/mcporter.json` remains ignored.
- Re-apply/rebase the local patch stack onto updated upstream before reporting the update done.
- Restart the gateway after the desired branch/patch stack is checked out.
