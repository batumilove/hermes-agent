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

Use the local wrapper instead:

```bash
cd ~/.hermes/hermes-agent
scripts/local-update-with-patches.sh
```

Dry-run what it will do:

```bash
scripts/local-update-with-patches.sh --dry-run
```

The wrapper automates the workflow that previously had to be remembered manually:

1. Fetch `origin`, `upstream`, and `myfork` with prune.
2. Switch to `local/hermes-patch-stack-20260426`.
3. Create `backup/local-patches-before-update-<timestamp>`.
4. Rebase on `origin/main`.
5. Check for conflict markers.
6. Run targeted patch-stack tests, including `tests/cron/test_cron_context_from.py`.
7. Restart `hermes-gateway` unless `--no-restart` is passed.

If conflicts occur, resolve them, then:

```bash
git add <resolved-files>
git rebase --continue
scripts/local-update-with-patches.sh --no-restart
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

## Cron `context_from` smoke test

Use the live smoke helper after cron scheduler/tooling changes:

```bash
cd ~/.hermes/hermes-agent
scripts/smoke-cron-context-from.py
```

It creates temporary cron jobs, runs a source job, runs a dependent job with `context_from`, verifies that the dependent prompt includes the source output, then removes the temporary jobs. Use `--help` for options.
