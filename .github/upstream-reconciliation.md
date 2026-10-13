# Upstream reconciliation, 2026-10-13

This candidate reconciles `batumi/live` at
`2c31abf2bcb4a6fa0abc868f707ec09a053cb1f0` with official upstream
`6e7b83a2f1c093e992a8d838ed77ea320fbd8e51`. It is prepared on
`codex/reconcile-upstream-6e7b83` as the immutable source branch; the linear
review branch is `codex/update-fork-6e7b83`. Runtime deployment is a separate step.

## History and patch ownership

The August imported upstream base was `a08dfab30201abe562953ba3de1e182a355e2465`.
Its source commit and the fork squash have identical trees, and the source includes
that upstream base. An ancestry-only merge first records this verified import
without changing the fork tree. The source branch then merges official upstream
and preserves both histories. The review branch imports that exact source tree
as one commit whose parent is the current live head, followed by a metadata-only
provenance commit.

The verified GitHub ruleset `19142865` requires linear history and the status
context `All required checks pass`; pull requests permit squash or rebase.
Protection remains unchanged. Version 2 provenance records exactly
`version`, `upstream_base`, `source_head`, `import_tree`, and `fetch_depth`.
The projected tree removes **only** `.github/upstream-sync-provenance.json`.
The source must contain the recorded upstream base in its ancestry and base
file, and its projected tree must equal `import_tree`. At least one ancestor of
the checked head, including the head itself, must have that same projected
tree. This verifies both squash and rebase landings without depending on the
commit hash GitHub assigns. Later fork commits may change the tree; the accepted
import remains provable through their ancestor history.

Keep the immutable source branch remotely reachable permanently. CI fetches
`source_head` automatically when the object is missing. Shallow checkouts use
the recorded depth of 8; full checkouts retain their full history. The manifest
always retains its `provenance_file` setting; a missing proof
file is acceptable when the recorded upstream base is an actual ancestor.
Old version 1 metadata needs a reviewed migration if direct ancestry is absent.

Future daily batches first validate the accepted import using trusted inline
Git and jq commands. They synthesize an ancestry-only commit with the unchanged
live tree and parents live plus the accepted upstream base, then compute the
new upstream merge using Git objects. They remove old provenance from the source
tree and prepare a linear tree-import plus new provenance commit on a separate
draft PR branch. It publishes both immutable branches atomically, with
create-only ref leases so a concurrent branch creation cannot be overwritten.
If draft PR creation fails after publication, retry PR creation against those
existing branches rather than replacing them. The privileged controller
does not check out or execute incoming scripts. Conflicts or invalid proof stop
before publication. Required CI and human review remain necessary before
landing; the workflow neither merges the PR nor changes branch protection.

Before enabling daily automation, configure `UPSTREAM_SYNC_PR_TOKEN` once as a
repository-scoped App or PAT credential with contents read and pull-requests
write permissions, alongside the existing `UPSTREAM_SYNC_DEPLOY_KEY`. The plan
fails before publication if the PR token is missing. PR creation with
`GITHUB_TOKEN` suppresses the pull-request event, and the CI push trigger covers
only `main` and `batumi/live`; a separate PR credential is therefore necessary
to run the required checks normally. This change declares that requirement and
does not configure a live secret. The controller does not pass its credential
into incoming CI.

Ten patch entries are retired after verification of upstream equivalents:

- gateway E2E post-turn hook isolation;
- Desktop nanoid remediation;
- unversioned Docker config migration;
- obsolete replay-revert preservation, replaced by upstream epoch replay;
- earlier linearization and terminal lifecycle backports;
- compression transcript deduplication and host auxiliary timeout alignment;
- SQLite no-more-rows retry;
- readonly streaming reasoning configuration.

The obsolete ActiveGraph telemetry entry is also removed; its cron fixtures remain
owned by the trigger-provenance contract. The manifest retains 67 explicit owners.
Ownership follows the new upstream topical modules. Equivalent regression coverage
is retained even where production fork code has been removed.

Upstream now keeps durable sessions without automatic expiry. Explicit finalization,
request cleanup, cache eviction, maintenance admission and shutdown cleanup remain
separate operations. Tests use these current entry points.

## Runtime and external plugins

Python 3.14 and the upstream PM own application and test environments. The fork's
DCF extra remains declared and locked. Toolchain authority is `pm/lock.json`: npm
12.1.0 is published only after applying its hash-pinned brace-expansion 5.0.12
overlay; Nix consumes the same verified artifacts. uv 0.12.5 is hash-pinned
across supported targets; the already newer Termux artifact remains intact.

Honcho moved from core to its exact catalog source commit
`plastic-labs/honcho@e2d2aba182862deeb846e5cd4a30a41bce0ed82a`. The component installer
checks source and overlay hashes, applies the reviewed HTTP lifecycle overlay in a
scratch tree, publishes atomically, records provenance and enables the plugin only
after publication. It preserves rollback recovery on publication failure.

Langfuse uses the exact official plugin at
`NousResearch/hermes-official-plugins@c2a95963c442f3f23e4bc6fe0da898e1dd1fb63d`
and PM-selected SDK 4.15.2. Missing SDK imports remain fail-open; PM repair restores
the dependency. The old special pre-import installer is removed. Plugin source
fixtures are immutable provenance evidence, checked by behavioral harnesses rather
than reformatted by Ruff.

Provider telemetry reads its metrics path from the owning profile's
`provider_telemetry.metrics_file` configuration. Queue and series cardinality are
bounded. No new process-wide behavioral environment setting is introduced.

## Validation and release path

Required CI includes fork-delta ownership, fork contracts and fail-closed advisory
scanning alongside upstream checks. For this import, code health compares fork
ports with the recorded upstream tree after verifying ancestry. Once the target
branch contains that import, later PRs use their normal target-branch comparison,
so improvements remain enforced. Contributor checks exclude the accepted upstream
history while checking new fork authors. The import-tree proof establishes the
accepted upstream baseline; subsequent ordinary PRs retain the normal target
branch health ratchet.

Validation uses PM-managed Python 3.14, the repository test runner, strict code
health, portability guards, frontend checks and a production web build. External
plugin acceptance uses disposable profile homes. Langfuse is installed and repaired
through PM and exports an actual OTLP trace to a loopback test service.

Final retained Python regression coverage passed 4,557 tests across 234 files
with zero failures and 22 genuine platform skips. The stable runtime lane ran
230 files and the provenance lane ran four files. Historical updater, real PM
worker/TLS bootstrap, and fresh-source installer acceptance passed another
188 tests across nine files using exact PM tools and disposable verified caches.
The repository check passed all 12 gates with zero unallowed code-health findings;
26 narrow, reviewed boundary allowances remain visible. Frontend typechecks,
desktop/TUI behavioral tests, and production renderer, web and documentation
bundles passed. Native desktop packaging requires an environment that can download
Electron headers.

Root, website, Photon, WhatsApp and the production Nix node-gyp npm scopes have
zero vulnerabilities at every npm-audit severity. The unpatched public braces
nesting advisory uses a separately named, licensed derivative with reviewed
depth/cycle guards, original and patched hashes, and installed consumer tests.
Its one frozen tarball is the only hash-checked archive-boundary exception.
There is no advisory suppression or global release-age relaxation.

The digest-pinned OSV scanner extracted all seven lockfile scopes, but its
query to api.osv.dev received Forbidden under this environment network policy.
A fresh full OSV result remains required in CI; npm audit is separate evidence.

A container build was attempted with the session CA mounted transiently and TLS
verification enabled. SQLite's primary and fallback download hosts returned HTTP
403 under this environment's network policy. A complete production image, fresh
advisory scan, Windows/macOS execution and production rollout still require CI or
the appropriate release host; this candidate does not claim those outcomes.

After review and required CI, land the linear review branch into `batumi/live`
using either permitted squash or rebase method. Retain its immutable source
branch so later imports can verify their provenance. Source installs then use
`hermes update --branch batumi/live`; this
fork's default source channel also resolves to `batumi/live`. Container installs
pull and recreate from the tested immutable image digest with the existing data
mount. Use the separate staging/soak and production promotion workflow for rollout.
