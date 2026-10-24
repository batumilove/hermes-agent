# Official upstream reconciliation, 2026-10-24

The current import reaches `5894e750c3dba980d43b25aa29e3958149ba2c61`,
including the staged daemon migration below and live security fixes from #425.
The catalog import trust transfer is described at the end of this document.

## Shared-daemon migration, 2026-10-18

This stage imports upstream through `eb75627faf532fa6ffbdae59a0d14b0b385fc780`
after the pre-daemon import and catalog update in #396/#397. The boundary brings
2,031 upstream commits since `d04056d609bb53530843a6c630493243b2ebdc24`.
Later upstream changes remain a separate batch. The version-2 receipt identifies
the immutable reconciliation source; retain that source branch after landing.

The gateway now owns local CLI, TUI, Desktop, API, ACP, bot and cron sessions.
Fork contracts follow those owners:

- `run_turn_hygiene.py` and `run_turn_persistence.py` retain compression route
  compare-and-set, cancellation ownership and post-delivery transcript actions.
- Native session delivery uses the same durable preparation/settlement path as
  platform delivery. Completion events publish plain text, retaining process-local
  handoff ownership only for the native sender.
- `session_execution.py` and `run_agent_cache.py` retain evicted-agent cleanup.
  A session boundary retains its memory/context-engine obligations across retries.
- `run_shutdown_budget.py` settles managed workers before closing shared stores.
  Bootstrap releases process identity only after runtime writers settle.
- `session_authority.py` fences both admission and queued claims during reversible
  external maintenance drain; release wakes committed queues without reopening shutdown.
- `session_kanban.py` loads the mandatory `kanban-worker` and
  `restart-window-bundling` skills in the daemon-owned worker. Missing policy
  refuses the task before model work. Dispatcher claim/run fencing remains intact.
- `hermes_state_runtime.py` carries the fork's durable side-effect evidence marker
  through the closed worker persistence protocol, including compaction handoffs.
- `hermes_state_retained.py` preserves the daemon retirement fence in optimized
  routing reconciliation, so stale mirrors cannot restore deleted session routes.
- `run_runtime.py` gives an in-flight profile database open a bounded startup
  window before parking it; durable-delivery discovery can open the same cache
  concurrently. Profile/path validation remains mandatory after the open settles.
- `run_bootstrap.py` serves live control status alongside the upstream authenticated
  session/ticket protocol. Writable runner initialization lives in `run_runtime_init.py`.
  Obsolete `run_control_socket.py` and `run_turn_response.py` copies are removed;
  tests exercise the actual bootstrap and persistence owners. The obsolete
  `run_turn_recovery.py` copy and duplicated cache lookup helpers are also removed.

Run the canonical `scripts/run_tests.sh` with an isolated home/runtime. Regression
coverage includes real Kanban daemon/worker lifecycle cases, managed shutdown,
maintenance admission, response finalization, cache boundary retries and worker
receipt persistence. Import CI compares fork changes with the recorded upstream
boundary, as authenticated by the provenance receipt; do not weaken required gates.

This is source preparation/publication only. It does not deploy or restart Hermes.
Any later activation uses the installation's included updater and fresh runtime
identity checks. Other open runtime-preservation/capacity PRs require separate
integration and are not silently included by this import.

---

# Upstream reconciliation, 2026-10-13

This candidate reconciles `batumi/live` at
`2c31abf2bcb4a6fa0abc868f707ec09a053cb1f0` with official upstream
`6e7b83a2f1c093e992a8d838ed77ea320fbd8e51`. It is prepared on
`codex/reconcile-upstream-6e7b83` as the initial immutable source branch. CI
follow-ups use separate immutable sources `codex/reconcile-upstream-6e7b83-ci`,
`codex/reconcile-upstream-6e7b83-ci2`, `codex/reconcile-upstream-6e7b83-ci3`,
`codex/reconcile-upstream-6e7b83-ci4`, `codex/reconcile-upstream-6e7b83-ci5`, and
`codex/reconcile-upstream-6e7b83-ci6`; the linear review branch is
`codex/update-fork-6e7b83`. Retain all seven source branches after publication.
The current provenance record identifies the exact accepted source tree.
Runtime deployment is a separate step.

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
`GITHUB_TOKEN` can suppress workflow execution or require additional approval,
depending on GitHub's repository policy. The CI push trigger covers only
`main` and `batumi/live`; a separate PR credential allows required checks to
start through the normal pull-request authorization path. This declares the
requirement and does not configure a live secret. The controller does not pass its credential
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
owned by the trigger-provenance contract. The manifest retains those 67 explicit
owners and adds one Feishu callback authentication/quota owner after CI source review.
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

The initial retained Python regression coverage passed 4,557 tests across 234 files
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
There is no registry-advisory suppression or global release-age relaxation.

## CI follow-ups

After the maintainer resolved GitHub's fork workflow blocker, a fresh PR event
started the CI graph. Blob-size and critical-pattern checks now use the same
verified import comparison as code health. They compare this initial import
against its official upstream tree and retain ordinary target-branch gates for
later PRs. The existing blob limits and critical expressions are unchanged.

The root npm lock restores missing registry URLs and integrity values from
exact-version registry metadata without changing existing package versions.
Missing optional native bindings are restored for cross-platform SDK tests.
A clean PM-owned npm install and the pinned Nix full-lock importer pass. Generated
DCF docs match their source. JS jobs prepare desktop and website test inputs;
script permissions reflect exact reviewed versions in the locks. JS installs
enforce those decisions strictly, and installed-tree cache keys include script
policy and toolchain inputs. Standard-runner file concurrency is bounded to
four Linux and two Windows workers; all test selectors and deadlines remain.

The next Nix build exposed an offline manifest defect: dropping root overrides
made npm try to resolve the older transitive KaTeX range from the registry.
The offline manifest now preserves the security overrides and refers to each
direct dependency's imported tarball spec through npm's `$name` syntax.
An actual empty-cache npm consumer reproduces both the missing-cache failure
with dropped overrides and the direct-spec conflict with unchanged overrides;
the adapted overrides install the exact patched packages offline. The registry
manifests, dependency locks, integrity hashes and release-age policy are unchanged.
Full Nix realization and the wired offline regression passed on the repaired
predecessor; the final successor still requires its own CI acceptance.

The repaired predecessor passed Termux, the running-stack shadow and both
Windows ARM64 cold SDK jobs. The Windows jobs provisioned and executed the SDK
from an empty cache; the Azure signing service boundary remains stubbed.
This is ARM64 acceptance, without an x64 execution claim. CI and PM Bundle were
individually disabled at that point; the maintainer subsequently enabled both.

After those workflow identities were enabled, the required dependency scan
reported no issues across all seven lockfiles. Full Nix realization, all ten
JS/TypeScript workspace checks, the native Windows SDK jobs and all four PM
bundles passed on the repaired predecessor. Linux test jobs and Windows x64
reached their unchanged 30-minute limits before completing. Linux now uses
eight four-worker slices and Windows x64 four two-worker slices; canonical
partition checks prove complete, disjoint coverage with the same selectors.
macOS and Windows ARM64 keep their existing slice layout and deadlines.

Three Windows desktop-update files exceeded their 300-second file budget from
cumulative native PowerShell startup; ARM64 even reported all twelve commit
cases passing just before that file was terminated. Those portfolios now keep
the original 41 native cases, helpers, embedded programs and assertions across
sixteen topical files, with at most four cases per file. The file and job limits
remain unchanged. Shared test isolation rebinds both import-time DB defaults,
so profile-scoped tests use their actual profile while later explicit DB pins
continue to work. Lifecycle fixtures use current async deadlines and real
shutdown ordering rather than obsolete internal interfaces.

The source review also restores hosted-room dispatch grant validation before
body parsing or admission, records the retained side-effect column in recovery
schema history, and preserves ordered post-response actions in normal recovery
and lost-and-found copies. Recovery regressions verify receiver identity, action
order, attempt compare-and-swap, and unchanged source data. Native Relay scopes
retain sequential request freshness while overlapping calls remain independent;
committed compaction refreshes the owning main request history. These repairs
retain the accepted todo snapshot behavior and its separate advisory disposition.
Raw-page recovery also recognizes the live fork's fresh 24-column layout,
its earlier 23-column stores with the evidence column appended, and their
31-column physical upgrades. Common fields retain their column identity.
Some anonymous legacy tail cells are indistinguishable from an official
summary flag; consensus leaves those cells unknown instead of inventing
successful side-effect evidence. Schema-aware recovery preserves named evidence
exactly. The mapper accepts SQLite's integral on-disk encoding of REAL-affinity
values while retaining its other candidate, timestamp and consensus checks.

PM locks patched multidict, OAuthlib, PyJWT, Tornado and urllib3 within bounded
constraints, retaining the three-day release-age policy. Consumer acceptance
includes real OAuth/Google PKCE flows, native multidict, Telegram and a Chronos
JWKS regression that fails with the old PyJWT and passes with the update while
preserving key rotation. The test dependency group declares aiohttp so the
mandatory Feishu HTTP regressions execute in the CI test environment.

OSV receives all seven dependency scopes. The staged root uv lock removes only
the exact first-party editable checkout entry; its placeholder version is not a
published PyPI release. Registry dependencies and nonlocal same-named packages
remain scanned. First-party findings were separately checked against source.
The published Feishu finding GHSA-pmqc-57g8-c22c is addressed by callback
authentication before delivery quota, authenticated plaintext challenges and
bounded parsing/anomaly state, with real HTTP regression coverage.

GHSA-xq8w-9jvx-gm3v (CVE-2026-10221) remains prompt-injection hardening under
`SECURITY.md` section 3.2. The accepted upstream todo-tail merge, synthetic
provenance and stale snapshot refresh are retained and their twelve contracts
pass. Raw todo text can still appear in user-role API messages; no chained
security-boundary outcome or model execution was established. This records the
remaining behavior and policy scope; it does not claim that source finding fixed.

## Remaining release acceptance

The full dispatch on review revision `7ed0775` completed every Linux partition
within the unchanged job limits. PM Bundle passed all four native targets;
macOS Python, the ten JS workspace checks, Nix, SDK, documentation and Rust
checks also passed. The complete dispatch exposed additional fixture and
end-to-end failures that the earlier partial run had not reached.

The next source corrects premature shutdown completion and stops the durable
action worker before database closure, preserving an open database when that
worker remains live. Empty child-constructor errors now remain explicit
failures. Real browser evidence also distinguishes old-document socket records
left by reload from an actual second live connection; the recorder retires
only entries from the successfully reloaded document. Assertions against
genuine duplicate connections remain intact.

Failed durable final responses retain the existing bounded delivery retry
policy, including a visible marker when an acknowledgement may have been
lost. Recovery carries the receiving adapter identity, exact database and
attempt compare-and-swap through scheduling, reconnect and restart. Permanent
dead-target rejection is recorded as abandonment rather than successful
delivery, allowing the ordered finalization barrier to finish truthfully.
Explicit recovery opens an existing database without creating a removed
profile's store. Transport errors retain only safe retry classification and
opaque digests in durable state.

The remaining eleven-case Windows Python handoff portfolio is partitioned
without changing its cases, programs, assertions or deadlines. Test-only
process birth observation uses the native limited-rights process-time API
instead of a slow WMI query. Native acceptance on the successor is still
required. Fixed-credential mocks explicitly declare no credential pool;
genuine credential leases and their fail-closed ownership checks remain.

The Windows install/update journey now limits cold PM installation/build
pipelines to two concurrent file workers. Six independent cold stores on the
standard runner exhausted the unchanged 1,500-second installation deadline
before the update assertions ran. The ten journey files, native markers,
strict-mode input, zero retries, 3,600-second file limit and 75-minute job limit
are retained. This capacity adjustment still requires native timing acceptance.

Historical upgrade rehearsal validates the exact import before checking out
its immutable ancestry-preserving source. The authenticated pre-PM release
`v2026.9.24` at `f97608f178d1ffeca59860195ab7da295f7c8e5f` supplies the
unchanged historical manifest and lock. That release fast-forwards to the
source, while live fork `2c31abf2` fast-forwards to the linear review; the
official release cannot fast-forward directly to that linear review.
Ordinary later fork commits retain their own checkout and normal comparison
instead of being replaced by an older import. Their historical-release
baseline remains a separate validation requirement; this rehearsal does not
claim to solve all later linear-fork upgrade scenarios.

Interrupted-update coverage respects the retained lifecycle lease policy:
ordinary retry fails closed on orphaned ownership, then explicit operator
reconciliation of the exact SHA, token and purpose precedes retry. Automatic
orphan removal is not introduced. Required Kanban policy skill assets are a
separate dependency: neither required name is present in the original pinned
skills, operations or extensions sources. Both authentic skill trees were
located in `batumilove/hermes-skills` at
`8474d463f4fcd2608f5deeb0ad0a26f731ceb36c`, now pinned by the skills component.
Provisioning and full worker/handoff acceptance must use verified source
assets; the missing-skill gate is not disabled to obtain a passing result.
The external validator's full self-test has a Python 3.14 diagnostic mismatch
for a deeply nested malformed list: it still blocks the input but reports a
different error and exit code. Authentic source bytes are retained. Fixture
acceptance checks the real valid/invalid manifest CLI behavior separately;
it does not claim the external self-test passed.

The next full dispatch, on review `db2551c7` and immutable source `a1daf073`,
passed all four native PM bundles, seven Linux partitions, both macOS lanes,
Windows ARM64, native Windows gateway E2E, JS and desktop checks, and the
seven-scope dependency scan. A Linux partition failed during a bootstrap DNS
lookup before testing. The graph also exposed additional contract, Windows
custody, PM exclusion, historical handoff/Git recovery, cron contention and
plugin-parity failures. Those results require a successor and fresh full CI;
passing predecessor jobs do not accept new source bytes.

The `-ci5` source fences owning lazy update completion with the common lifecycle
lease before PM/source mutations. Exact `lifecycle-lease inspect` and `reconcile`
commands remain reachable without completing the pending update. Ordinary
mutation under an orphan remains blocked. Namespace fixture cleanup now tracks
actual PID births and descendants even without the optional namespace backend,
and collects their exits rather than accepting surviving zombies. Native
Windows finish custody uses a ready/release handshake around the unchanged
handoff phase, instead of a fixture lock that expires during cold startup.

Cron contention rendezvous occurs before exclusive maintenance admission and
retries only a witnessed admission refusal. Actual claim decisions, twelve
rounds, both real contenders, deliveries and terminal audit rows remain checked.
Rejected manual/external attempts again produce upstream's failed audit record
without mutating the winning slot or creating a scheduled occurrence.

Plugin reload eviction snapshots the process module registry before filtering
the exact plugin namespace. A genuine concurrent import reproduced the lost
plugin with `dictionary changed size during iteration`; the regression now
preserves unrelated modules while evicting the package and its submodules.
The eight-entrypoint parity matrix retains every hook and context assertion
and includes bounded isolated startup diagnostics. The original hosted one-shot
failure did not retain a traceback, so its exact cause remains unbound until
fresh successor acceptance.

The native Windows install/update dispatch reached its unchanged 75-minute job
limit after eight journey files completed with 32 passes, one known expected
failure and no failures; two files remained unfinished. Measured completed
worker time alone consumed 69 minutes with two workers. The ten whole journey
files now run as separate native jobs, at most two concurrently. Each keeps
the 75-minute job, 1,500-second stage and 3,600-second file limits, configured
two workers, zero retries and every original case/cold-store assertion. Matrix
coverage is complete and disjoint; crash manifest checks stay with the crash
journey. All ten require fresh native successor acceptance.

The contract runner applies mandatory serial-worker options after forwarded
caller options. CI's `-j 4` previously overrode the declared `serial: true`
contract and ran its update files concurrently. Caller-selected concurrency
remains effective for the parallel lane; serial files now use one worker,
with selectors and deadlines unchanged.

Paused-gateway startup recovery also takes the common lease before restarting
or retiring debt. Operator inspection/reconciliation leaves both pause and
lease records untouched until the explicit action. A finish child inherits
only its acquired kernel lease descriptor: parent death leaves that lease busy
until the last child closes it, then leaves metadata for exact operator
reconciliation. The delegated modern-update branch releases preparation
ownership first so its own CLI can acquire the lease. Existing modern fleet
recovery keeps its caller-profile lease plus checkout custody. A restart
controller using another profile's lease does not take that checkout lock;
exclusion against those other-home controllers remains a preexisting limit.
This successor does not claim an all-profile lease or change modern fleet
recovery into a foreign-home refusal.

Historical POSIX takeover pauses authenticated, captured manual Linux gateways
in the launch profile home before dependency preparation and records restart
debt before sending the old control protocol request. Selected-runtime finish
resumes them before slow UI builds. Original no-restart intent is retained.
Captured other profile homes, unsupported supervisors, platforms or changed
identity fail closed with the already-swapped update still
unfinished; normal modern supervised updates retain their existing path. This
cannot prevent the immutable old updater's initial Git-swap-to-takeover window,
and does not claim universal mixed-generation or supervised upgrade acceptance.

The full dispatch on review `6b9e4594` and source `baa37433` completed with
58 successful jobs, six failures including the aggregate, and seven skips.
All ten native Windows install/update journey jobs passed their actual test
steps, closing the predecessor's capacity gap for that head. Retained contracts
passed 1,831 parallel and 171 serial tests, including all five Docker cases
blocked locally. Core E2E passed 819 tests with zero failures, including cron
contention and all eight entrypoint-parity cases. PM bundles passed macOS ARM64
and both Windows targets; macOS x64 failed Chromium download DNS before
compilation. A retry after completion was rejected as a disabled workflow.

The `-ci6` source corrects three fixture mismatches without changing their
production contracts: the non-checkout branch removes its genuine temporary
Git directory, Chronos mocks the current execution keyword API while checking
exact force/manual provenance, and the sliced PM takeover fixture includes
actual gateway coordination modules and genuine Git provenance. Interrupted
Git-tail fixtures prove ordinary startup remains blocked by orphaned lifecycle
ownership, then reconcile only their exact killed controller before checking
the original moved-tree completion obligations.

Historical takeover accepts the release's valid zero-second chat-interruption
budget. Authentication, finite bounds, durable stop debt, actual incarnation
exit and the existing ten-second completion tail remain required. Historical
restart debt additionally requires an actual selected store Python and current
PM generation before replay, including when its baseline already names the
target Git HEAD. Missing or unknown currency keeps that debt outstanding;
generic developer, Nix and modern pre-swap recovery retain their existing
semantics. Full historical handoff and Git journeys still require fresh
acceptance on this successor; the earlier Windows and contract results do not
accept its new tree.

The local digest-pinned OSV query was blocked by this environment's network
policy. The repaired predecessor's hosted scan reported no issues across all
seven lockfile scopes. The final successor requires a fresh hosted result;
npm audit remains separate evidence.

A container build was attempted with the session CA mounted transiently and TLS
verification enabled. SQLite's primary and fallback download hosts returned HTTP
403 under this environment's network policy. The repaired predecessor's
running-stack shadow built and exercised the production image, and its macOS
Python lanes passed. The final successor requires the complete CI graph,
including native Windows acceptance for the split portfolios. Production
rollout remains a separate release action.

After review and required CI, land the linear review branch into `batumi/live`
using either permitted squash or rebase method. Retain its immutable source
branch so later imports can verify their provenance. Source installs then use
`hermes update --branch batumi/live`; this
fork's default source channel also resolves to `batumi/live`. Container installs
pull and recreate from the tested immutable image digest with the existing data
mount. Use the separate staging/soak and production promotion workflow for rollout.

## Image acceptance repair after ci6

PR #387 landed the accepted ci6 tree at
`a562062c3c29354bab5cb04ac4fa583a7128173b`. The source-SHA image was uploaded,
but its fixable HIGH/CRITICAL Trivy gate rejected thirteen HIGH library findings;
candidate promotion and staging deployment did not run. All seven earlier source
refs remain immutable. `codex/reconcile-upstream-6e7b83-ci7` is an ordinary
repair descendant of the accepted merge and retains its ci6 import proof.

The application locks already resolve patched packages. The additional copies
were in Node's redundant npm bundle, the independently selected npm's Undici,
and pip's vendored libraries in the managed interpreter. PM now removes the
redundant installer payloads before recording store digests. Its selected npm
bootstraps from its own verified tarball, offline with lifecycle scripts disabled;
a third hash-pinned artifact supplies Undici 6.29.0 to the shared npm/Nix overlay.
The interpreter retains the archive's licensed distlib 0.4.2 as a standalone
launcher supplier. PM continues to create unseeded environments through its
pinned UV; managed Python no longer supplies pip or ensurepip bootstrap wheels.
Native Windows launcher and install/update acceptance is required for this tree.

The image's `hermes-agent` finding also matched placeholder metadata `0.0.0`.
The published DNS-rebinding repair is present in the accepted upstream ancestry,
and the existing WebSocket Host/Origin regressions pass. Image publication must
prepare an install stamp while Git is available: authenticate the pinned release
object and accepted import, derive a PEP 440 source version, and bind it to the
actual source SHA. The shared assembler accepts that image-only metadata override
and removes stale metadata for its own distribution. Source manifests, dependency
selection, and global release allocation retain their existing semantics.

The application publication caller pins the companion image-identity workflow
repair by immutable ops commit. Deployment and promotion callers retain their
previous tooling pins. The scanner severity, fixable filter, and candidate gate
remain enforced. Local tests and source checks do not accept a rebuilt image:
the new source needs fresh full CI and the actual image scan before deployment.

The immutable ci7 candidate `78007da94bff89f3b38cf82d6751677a001e70fa`
passed hosted running-stack image validation, Nix, and prebuilt TUI checks. Its
native Termux contract job found one remaining test prerequisite: the wheelhouse
fixture created a pip-seeded stdlib venv. The managed interpreter now deliberately
has no ensurepip wheels. `codex/reconcile-upstream-6e7b83-ci8` retains the ci7
source ref and prepares that disposable build environment through PM from a
manifest and frozen lock declaring the same mature pip 26.2.1 wheel and its
verified SHA-256. It still builds the exact Git
revision and installs the resulting wheel offline after removing its source URL.
The production Termux wheel builder continues to use the container's own native
Python/pip; only its isolated test prerequisite changes. The successor still needs
fresh hosted checks and rebuilt-image scanner acceptance.

Full CI on immutable ci8 commit
`2f026dce70e39cf3bab7aa4ad7c25814d1a7eeb3` passed the hosted Termux
contracts but exposed a native macOS setup failure. On a case-insensitive
filesystem, `Lib` aliases the parent `lib` directory; the bootstrap verifier
counted its nonexistent distlib path alongside the real versioned library's
supplier. `codex/reconcile-upstream-6e7b83-ci9` counts only present suppliers
while retaining exact-one, containment, version, license, and bootstrap-payload
checks. The retained ci8 branch remains unchanged. Fresh successor native
macOS and full CI acceptance are required before rebuilt-image acceptance.
The same run found that the fresh-install fixture's copied stdlib omitted
standalone distlib, and that the ops workflow contract still required publication
and deployment to share one revision. The successor carries the authentic host
launcher supplier in the fixture archive and verifies immutable publication inputs
separately from the locked deployment revision. The original installer fault and
warm-path assertions remain enforced; deployment pins are unchanged.
Custom cold-runtime, activation, startup-recovery, takeover, and pin-update
fixtures now carry complete runnable Python libraries and the authentic launcher
supplier before recording facts or archive digests. Their installed-fact checks,
GC selection, recovery, dependency isolation, and lifecycle oracles stay intact.
The handoff lane also exposed a baseline routing gap: ordinary descendants kept
their candidate checkout but stopped publishing the authenticated N-1 release,
so the fixture selected an older fork tag whose lock could not be installed.
The successor authenticates and publishes the pinned release baseline on both
import and descendant routes, retaining the descendant's actual HEAD and branch.
The historical baseline lock remains enforced with `--locked`.
The lock-probe concurrency fixture now ends after its required acquisition
sample completes rather than imposing a four-second throughput floor. It still
requires more than 50 real acquire/release attempts, zero refusals, and a live
concurrent prober, with bounded failure and shutdown guards.
The staged-Git desktop integration case receives an explicit 30-second test
timeout for its real Git and Python subprocesses. Its source SHA, isolated Git
configuration, staged-main, foreign-checkout, and launcher assertions are unchanged.

Full manual CI on immutable ci9 commit
`4fc72862e582e26ab9a7bd89bc8d516e920356e1` passed the upgrade and
handoff lanes and the previously interrupted TUI check. Desktop UI reported
10,816 passing cases but failed on a delayed Radix close-focus event after
component cleanup. The ci10 fixture waits for its actual close-focus callback
before allowing JSDOM teardown, retaining the model-selection and search
assertions. Event errors remain fatal; no test skip, global event substitution,
or timeout increase is used. Fresh successor CI and image scanner acceptance
remain required.

Full manual ci10 CI on `405445ebcfad1792df90bde680cfb4ad7d0d9d5c`
passed JS/TS, all ten Windows install/update E2E lanes and all six upgrade
shards. Two Windows Git-trampoline unit cases failed; the owner waived further
Windows investigation for this update. This does not make the CI aggregate green.
Image publication uses the existing exact-source `shadow / shadow` gate, not
that aggregate. Candidate builds still enforce provenance and the fixable
HIGH/CRITICAL Trivy scan. Staging deployment now additionally requires the
`batumi/live` ref so a manual `rebuild/batumi-live` candidate build cannot deploy
to staging even when the staging enable variable is set.
The candidate ref retains its historical security-layer refresh commit
`f5333d3d2eb0ca8fd0198cb097ba03fe99dd848d` through a merge; its Dockerfile
and regression changes are already present in the reconciled source, so their
current implementations are retained when resolving the historical conflicts.

## Official catalog imports

A bulk official import changes runtime code and catalog entries together. Catalog
CI accepts that combination only when `scripts/ci/catalog_upstream_import.py`
verifies a version-2 immutable source receipt, an exact projected import head,
an advancing upstream base in separately fetched official history, and complete
`plugin-catalog` tree identity with that official commit. Git tree identity covers
all filenames, bytes, symlinks and modes, including the removal list and policy.
No PR label or branch name grants this path. Missing or invalid proof fails closed.

These pins have already passed official upstream admission, so this path verifies
that trust transfer instead of executing every third-party plugin again. The
structural catalog check still runs. Data-only submissions continue to clone,
scan and validate each changed pin under the ordinary admission gate. A later
mixed code/catalog PR cannot reuse an old receipt: its head must still be the
exact initial import and its upstream base must advance.
