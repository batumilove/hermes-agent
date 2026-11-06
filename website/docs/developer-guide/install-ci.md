---
title: "Fork install and update CI"
description: "Runner ownership, historical installer runtimes, and failure diagnosis for fork maintainers."
---

# Fork install and update CI

This guide covers the `batumilove/hermes-agent` fork's Linux/macOS install and
update validation. Published guidance lives in this repository on GitHub.
See [Platform Support](../getting-started/platform-support.md) for supported hosts
and [Updating](../getting-started/updating.md) for the user-facing update path.

## Runners and isolation

The macOS routes in `.github/workflows/install-e2e-macos-run.yml` use
GitHub-hosted `macos-15` runners. They do not require a Blacksmith installation.
The reusable source-install workflow, `.github/workflows/install-e2e-run.yml`,
accepts the runner label from its caller and defaults to `ubuntu-latest`.

Each source-install leg uses a disposable runner, an isolated home, and a local
bare repository serving the starting and target commits. The driver redirects
installer/updater Git transport to that repository. Run these E2Es on disposable
hosts, never against a maintainer's live Hermes installation.

For a queued job, inspect its requested runner label and queue message first.
An unavailable runner is a scheduling problem. A cancelled job is not evidence
of an assertion failure: inspect cancellation, concurrency, and timeout details.

## Historical macOS Node runtime

The pre-PM installer at `v2026.4.16` invokes Playwright through `npx`. During
the macOS failure investigation, Playwright 1.59.1 reached 100% of the Chromium
download and then hung during extraction under Node 26.12.1. The same
installation completed under Node 22.23.4.

The source-install workflow provisions Node **22.23.4** on macOS and records its
bin directory before `.github/actions/setup-pm` restores locked current driver
tools. The internal CI variable `HERMES_E2E_LEGACY_NODE_BIN` carries that path.

`run_source_installer` in `tests/install/e2e-assets/installer-common.sh` prefixes
that directory only for the installer subprocess when the script lacks `pm.cli`.
The parent driver, current PM installer, and updater retain their current tooling.
Do not globally downgrade Node or modify historical installer bytes to pass an
old-release test. Users do not need to configure this test-harness variable.

The helper uses `--skip-browser` only when a pre-PM installer's help advertises
it. The `v2026.4.16` installer lacks that option, so its leg exercises the real
browser installation. Download completion alone is not a successful install.

## Validation evidence

Keep both source-upgrade routes in the evidence for installer/runtime changes:

- **Historical release → HEAD:** installs the original release, then exercises
  `hermes update` into the candidate commit.
- **HEAD → NEXT:** installs the candidate and updates to a synthetic child commit,
  exercising the candidate's own updater.

Confirm the workflow's tested SHA matches the PR head and each relevant leg
reports success. Unsupported method combinations can skip by design; a skipped
leg does not validate that route. Inspect the matching `install-e2e-logs-…`
artifact and its installer/update transcripts for the driver's explicit `PASS`.

For helper changes, also run the focused contracts with the
[prepared test environment](contributing.md#manual-development-and-test-environment):

```bash
scripts/run_tests.sh tests/scripts/install/test_source_installer_runtime.py tests/installation/test_installer_common.py
python scripts/check
```

## Diagnose failures before retrying

Start with the failing job rather than the downstream `All required checks pass`
or coverage aggregate. With GitHub CLI:

```bash
gh pr checks PR_NUMBER --repo batumilove/hermes-agent
gh run view RUN_ID --repo batumilove/hermes-agent --log-failed
gh run view RUN_ID --repo batumilove/hermes-agent --job JOB_ID --log
```

For JS lanes, inspect test output and artifacts. An inventory receipt with
`ok: false` identifies a lane but may omit the assertion; do not infer a root
cause from it. If an inspected failure appears transient, retry the failing job
after the workflow finishes:

```bash
gh run rerun RUN_ID --repo batumilove/hermes-agent --job JOB_ID
```

Then verify the lane and downstream coverage/required gates. If it fails again,
obtain the detailed test report or reproduce the failure before another retry.
Do not bypass the gate or rerun successful install E2Es to repair an unrelated
JS failure.

A CodeRabbit “Review skipped” notice means automatic reviews are disabled; it
supplies no code finding. Read the latest CI review comment alongside current
check results, since comments can briefly lag completed jobs. Before merging,
ensure the branch includes the current target branch and required checks pass
for the exact head being merged.
