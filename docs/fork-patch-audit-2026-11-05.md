# Fork patch audit — 2026-11-05

This reconciliation advances the accepted NousResearch source from
`9f050ccc82f8240ae57f3c039a22b913ebcd2829` to
`c55af2c93b9d61f5db7943fbc5ff50553038171f` (1,277 upstream commits).
The review import is based on fork live `e23d74e43deda7fe76f2da8c3d2e1554f0ce2ec8`.
Its immutable source and exact projected tree are recorded by the version-2 provenance file.
This is a source update; it does not activate code on a running installation.

## Changes retired in this sync

- **brotlicffi-decoder-security-fix:** upstream now resolves the fixed 1.2.1.0 decoder as an ordinary
  dependency. Remove the temporary package-specific release-age exception and its patch entry.
  Packaging/security floor contracts remain. PM owns the regenerated Python lock.
- **upstream-catalog-admission-split:** all 36 formerly owned catalog paths now match the official
  upstream tree. Retire the patch entry and use the complete official catalog, verified by
  `scripts/ci/catalog_upstream_import.py`.
- Remove manifest path claims where this import is now byte-identical to upstream (including LSP
  client, plugin loader, desktop manifests, several moved tests and docs). A shared file converging
  does not establish that an entire patch's behavior has been absorbed.
- Adopt upstream's `failed_key` pool refresh argument and the new adapter-recovery sibling instead
  of retaining duplicate recovery functions. Keep fork failed-bearer and asynchronous resume contracts.

## Upstream replacements that are incomplete

| Patch | Evidence at the frozen upstream revision | Decision |
| --- | --- | --- |
| Desktop confirmation lifetime | The ordinary timer path is guarded; the `dismissOnConfirm` path awaits `onConfirm` then calls `onClose` without checking component lifetime. | Retain the stronger mounted guard and both dismissal contracts. |
| Nous OAuth refresh | Upstream has stampede handling and `failed_key`; auxiliary refresh, client propagation and leased credential reselection still have fork contracts. | Use upstream where equivalent; retain the remaining propagation/lease delta. |
| Honcho lifecycle | Official catalog pin `7dd3702933d5cc1ce1f0a44bba6525cc61ee6e16` adds process-exit closing, but reset clears cache and slot replacement drops an old client without closing it; construction lacks the retained no-idle transport. | Keep the existing lifecycle fixture/overlay until those contracts pass against the replacement. Do not substitute the new pin blindly. |
| Tornado floor | Upstream commit `4df2ce6444b` upgrades only `uv.lock` to 6.6. | Retain the manifest floor `tornado>=6.6,<7` so the next relock cannot regress. |
| Intel macOS | Upstream removes the x64 release lane and feed support. The fork admits Linux and both Mac architectures. | Retain the fork target policy and feed support while importing the new owner-write ZIP validation and notarization workaround. |

## Move capability out of core

The source sync did not repoint installed consumers. The first extraction below was subsequently
packaged and migrated separately. The November 6 reconciliation below resolves the remaining
extraction proposals; see [completion decisions](fork-delta-completion-2026-11-06.md).

1. **Honcho monitor and queue diagnostic → `batumilove/hermes-ops` (first).**
   [Operations PR #12](https://github.com/batumilove/hermes-ops/pull/12) packages the monitor and
   queue diagnostic through `ctx.register_cli_command`, without tools, hooks, workers or new
   dependencies. Its merged revision is `7ec58a359fd5ba22ee2f411d71a800769462a61b`.
   The seven behavior suites moved with the implementation; installed discovery, profile-state
   isolation and recoverable wrapper migration contracts passed on native Linux and macOS.
   The selected installation now uses the pinned plugin for its cron wrapper and three legacy
   CLI entry points. The wrapper's exact original bytes were preserved, the directory's immutable
   flag restored, and the cron definition retained. A natural scheduled monitor run completed
   successfully with a fresh profile-state write; the queue command also completed successfully.
   These are execution receipts, not a claim of Telegram delivery. This follow-up removes the
   application copies and tests without internal import shims. The independent `.mailmap` alias
   remains under `fork-automation-attribution`. No new core hook is necessary.
2. **Provider telemetry → its existing external observer repository.**
   [Telemetry PR #2](https://github.com/batumilove/hermes-provider-telemetry/pull/2)
   reconciles bounded state, owning-profile configuration and callback quiescence into
   `batumilove/hermes-provider-telemetry`. The selected VM's installed source was verified
   byte-identical to that repository's base, so it has no runtime dependency on the core fixture.
   Remove the older core collector, consumer tests and historical integration artifacts. Keep
   generic runtime-role/discovery contracts and stable platform-health gauges. Preserve scalar
   runtime-role/capability properties across process isolation, rather than exposing them as
   truthy callable proxies. Settings and
   legacy key registration are declared by the external plugin. This source cleanup does not
   install its new version or activate either telemetry receipt PR #475 / official-plugins #1.
3. **Side-effect policy → retain its mandatory final core guard.**
   The installed `batumi-policy` already supplies optional evidence/workspace wording. It cannot
   replace `agent/side_effect_evidence.py` through today's `transform_llm_output`: that hook
   selects the first nonempty rewrite, and another plugin can introduce a success claim. The
   core check runs after all rewrites, using trusted pre-decoration current-turn evidence;
   its delivery-only warning stays out of durable model context. Existing finalizer tests
   prove that boundary. Extraction stays conditional on equivalent ordered final validation,
   replay isolation, overlapping turns and owning-profile behavior; no speculative hook is added.

Environment backend products, Langfuse, and restart-policy skills already have external homes.
Retain only their generic registries, ownership/admission mechanisms and compatibility contracts.
Delivery acknowledgment/ledger, cancellation custody, shutdown fencing, SessionDB admission,
prompt-cache stability and profile scope are cross-product correctness invariants: moving them
behind an optional plugin would weaken the base rather than reduce product coupling.

## Scope and evidence limits

All 124 pre-sync manifest entries were inventoried against both frozen revisions. The appendix is
conservative: `retain` means there is still an owned delta and an unmet retirement gate; it does
not claim a fresh semantic proof of every line. Detailed semantic reconciliation focused on
upstream overlap, merge conflicts, dependency floors and the extraction candidates above.
The patch manifest remains the source for each retained rationale, contract command and retirement
condition. Historical missing paths and now-identical path claims are pruned from ownership.

Upstream advanced by 22 redaction fixes during reconciliation; those commits are included in
the final frozen target above and their redaction/browser/file-read contracts were run locally.

Local validation uses `scripts/run_tests.sh` and the fork contract runner. Full CI must also cover
Linux, native release/build lanes and Docker; local macOS checks do not prove those environments.
Plugin extraction is reviewed separately from this source reconciliation.

### Honcho consumer migration

Select the owning installation's CLI and profile explicitly. Install the reviewed package with
an explicit `--ref`: selecting a commit in a tree URL alone does not pin future plugin updates.

```bash
hermes plugins install https://github.com/batumilove/hermes-ops/tree/7ec58a359fd5ba22ee2f411d71a800769462a61b/plugins/honcho-ops --ref 7ec58a359fd5ba22ee2f411d71a800769462a61b --enable
hermes honcho-ops monitor --help
hermes honcho-ops queue --help
```

Review native scan findings before consenting; retain operator isolation policy. CLI registration
requires in-process loading. Run the reviewed package's `scripts/honcho/migrate_monitor_entrypoint.py`
with the owning `--hermes-home`, `--hermes-cli` and freshly inspected `--expected-sha256`. It refuses
drift, backs up the original and atomically replaces only `scripts/honcho_monitor.py`. If a directory
is protected, obtain scoped authorization before unlocking it, and restore its protection immediately.
Keep job id, schedule, delivery destination and no-agent mode unchanged. Back up legacy installed
`bin/honcho-monitor`, `bin/honcho_monitor.py` and `bin/honcho_queue_diagnostic.py` before forwarding
them to the matching plugin command; stop reinstalling the obsolete hermes-extensions copies.
Verify fresh command execution and the scheduled consumer before removing application internals.

Monitor state remains `cache/honcho_monitor.json` in the owning profile, and ingestion reads that
profile's `state.db`. Existing overrides and silent-healthy/alert-output contracts are unchanged.
A monitor run can issue its configured chat health probe; `--help` performs no monitoring probes.
Restoring the saved cron wrapper is sufficient while the old core modules remain installed. After
updating to a revision that removes them, use that installation's included updater to restore the
previous application revision before restoring the wrapper. Installation and consumer migration do
not require an application update or restart. The operations deployment-component pin is unchanged;
plugin provenance lives in the native installer metadata.

## Validation for the reconciliation

- `scripts/run_batumi_contracts.py -j 4`: 2,081 parallel and 151 serial tests passed;
  79 host/environment-specific skips on macOS. Linux/Docker validation remains in CI.
- Focused latest-upstream redaction/browser/file reads and real-Git updater identity:
  436 passed, 4 native Windows skips. Focused PM publication/admission/survival/build-input
  contracts: 78 passed. The newly enabled incompatible-core requirement was proven red
  before integrating upstream's check with the retained manifest sitout path.
- `npm run --workspace tests-js check`: typecheck and lint passed; 243 tests passed,
  3 native/host skips. Lint reports one existing nonblocking spacing warning.
- Desktop confirmation and canonical workspace contracts: 22 passed.
- Native PM lock check passed. Root and standalone website `npm ci --ignore-scripts` passed
  with Node 26.12.1 / npm 12.1.0. Reconcile from the retained fork lock's valid consumer graph;
  seeding from the upstream lock can preserve incompatible matcher resolutions despite overrides.
  The duplicate website KaTeX override is removed so the maintained 0.19.0 floor wins.
- `python scripts/check --base upstream/main`: all 14 checks passed before the final PM
  reconciliation; the final committed import is checked again before publication.

The Python tests use the existing isolated PM-built Python 3.14.9 test environment; this is not
proof of a complete fresh install of every optional dependency. Full PR CI remains the merge gate.

## Disposition of the pre-sync patch inventory

The count is of declared paths with a remaining file delta before ownership pruning, not unique
behavior or a measure of code complexity. Shared ownership can count a file more than once.

| Patch | Disposition | Remaining paths | Notes |
| --- | --- | ---: | --- |
| `watchdog-early-thread-attribution` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `bootstrap-final-identity-custody` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `bounded-config-explanation` | retain | 13 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `desktop-confirmation-async-lifetime` | retain | 2 | Retain: upstream still lacks the mounted guard after awaited confirmation in immediate-dismiss mode. |
| `mattermost-delivery-failure-semantics` | retain | 10 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `trusted-default-branch-ci-baselines` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `desktop-ui-isolated-ci-shards` | retain | 8 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `local-npm-source-cache-identity` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `desktop-gateway-notification-test-cleanup` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `upstream-release-plugin-admission` | retain | 12 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `channel-smoke-cli-test-budget` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `legacy-matcher-segment-and-google-repair` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `cron-bounded-worker-adoption-ownership` | retain | 8 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `tornado-66-security-floor` | retain | 3 | Retain: upstream fixes the lock to 6.6 but lacks a manifest floor for future relocks. |
| `native-windows-pinned-launcher-publication` | retain | 20 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `isolated-upgrade-empty-service-discovery` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `qs-supported-site-dependency-removal` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `upgrade-service-manager-shim-contract` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `high-fd-pty-stream-framing-regression` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `hosted-image-history-and-undici-floor` | retain | 9 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `s6-waited-cleanup-immutable-publication` | retain | 15 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `mattermost-bounded-thread-context` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `optional-migration-pm-receipts` | retain | 8 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `live-restore-durable-state-assertion` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `startup-scheduler-live-status` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `mattermost-exact-fleet-mention-routing` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `browse-schema-begin-budget-refusal` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `desktop-update-process-diagnostics` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `october-security-dependency-remediation` | retain | 14 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `post-turn-owning-context-engine` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `ci-compare-response-bound` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `native-updater-scope-custody-enforcement` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `native-updater-source-admission` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `native-updater-mandatory-pm-failure` | retain | 9 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `lcm-native-snapshot-provenance` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `cron-live-worker-monitor-recovery` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `stage2-install-state-ownership` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `telegram-private-reply-anchor-retry` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `included-updater-operational-policy` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `upstream-catalog-admission-split` | retire | 0 | Retire: official catalog matches the accepted upstream catalog exactly. |
| `producer-generation-pythonpath-isolation` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `delegation-capacity-rejection-accounting` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `feishu-webhook-authentication-quota` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `supply-chain-build-input-pinning` | retain | 42 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `browserslist-ghsa-73wf-remediation` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `high-severity-dependency-remediation` | retain | 35 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `dcf-locked-optional-extra` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `fork-free-runner-labels` | retain | 71 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `install-e2e-sandbox-node-ca` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `fork-patch-governance` | retain | 57 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `import-restore-fidelity` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `safe-update-eol-normalization` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `lifecycle-controller-serialization` | retain | 49 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `upstream-sync-review-remediation` | retain | 43 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `codex-attribution-opt-in-policy` | retain | 11 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `upstream-sync-whitespace-cleanup` | retain | 16 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `cron-no-agent-timeout-rendering` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `cron-result-context-extraction` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `api-adapter-turn-admission` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `post-tool-none-compressor-guard` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `context-engine-midturn-detach-guard` | retain | 13 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `background-review-teardown-ownership` | retain | 7 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `delegation-timeout-sessiondb-close-ownership` | retain | 18 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `test-dns-isolation` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `test-hermes-home-import-isolation` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-session-request-resource-lifecycle` | retain | 8 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `sessiondb-wal-checkpoint-off-writer-lock` | retain | 7 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-context-engine-cache-lifecycle` | retain | 17 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-lifecycle-diagnostics` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `sessiondb-write-latency-diagnostics` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `doctor-bounded-state-health` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `sessiondb-routing-delta-reconciliation` | retain | 13 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-expiry-routing-log-precision` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-channel-directory-routing-projection` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `relay-scope-concurrency-lifecycle` | retain | 22 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `provider-telemetry-runtime-ownership` | consumer extracted | 10 | Collector, settings and consumer contracts live in the existing external telemetry repository; retain generic role/discovery and health contracts. |
| `cron-trigger-provenance` | retain | 25 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `langfuse-lazy-sdk-retention` | retain | 13 | Keep remaining compatibility fixtures/governance; the product implementation is already external. |
| `plugin-environment-backends` | retain | 8 | Retain generic registry seam; keep product backends external. |
| `final-output-policy-context` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `side-effect-evidence-regulator` | retain | 21 | Mandatory validation follows every optional output rewrite; first-wins transform hooks cannot replace that contract. See the November 6 completion decision. |
| `provider-fallback-lifecycle` | retain | 12 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `kimi-billing-classification` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `cron-verbatim-delivery` | retain | 5 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `honcho-connection-lifecycle` | retain | 5 | Retain: current official plugin does not close clients on reset/slot replacement or provide the retained no-idle transport. |
| `honcho-operational-monitoring` | extracted | 0 | Implementation and contracts moved to the pinned external Honcho Ops plugin; installed cron/CLI consumers migrated before core removal. The independent mailmap delta remains owned by `fork-automation-attribution`. |
| `cron-ephemeral-telegram-topics` | retain | 9 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `telegram-cancellation-socket-ownership` | retain | 7 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-delivery-shutdown-correctness` | retain | 37 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `telegram-general-pool-timeout-recovery` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-drain-control-ownership` | retain | 16 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `buzz-allowlisted-dm-metadata-fallback` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `web-search-budget-convergence` | retain | 10 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-full-reply-context` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-startup-readiness` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-event-loop-status-io` | retain | 36 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-shutdown-work-attribution` | retain | 28 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `sessiondb-p0-write-admission-handle-leaks` | retain | 17 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-lifecycle-drain-incident-contracts` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `gateway-reliability-revival-heartbeat-stopsec` | retain | 7 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `desktop-messaging-config-load-once` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `desktop-profile-session-lightweight-target-contract` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `nous-oauth-refresh-stampede-backport` | retain | 13 | Partial replacement: adopt upstream failed_key refresh contract and auth/proxy code; retain failed-bearer propagation and lease reselection gaps. |
| `live-state-db-access-guard` | retain | 6 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `acknowledged-final-delivery-recovery` | retain | 15 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `durable-post-response-barrier` | retain | 39 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `kanban-worker-pid-race-guard` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `kanban-dispatch-test-hardening` | retain | 4 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `kanban-admission-kernel` | retain | 16 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `kanban-mandatory-restart-policy-skills` | retain | 17 | Keep generic enforcement; restart policy skills already live in hermes-skills. |
| `ci-load-flake-tolerant-timeouts` | retain | 3 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `shared-daemon-fork-contract-port` | retain | 33 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `browser-vault-authorized-otp-instructions` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `ci-aggregate-bounded-context` | retain | 2 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `nix-managed-guard-pipefail` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `brotlicffi-decoder-security-fix` | retire | 4 | Retire: fixed decoder is now an ordinary upstream dependency; temporary age exemption removed. |
| `october-lockfile-advisory-remediation` | retain | 14 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `vm-update-pause-and-plugin-admission` | retain | 13 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `imported-source-release-identity` | retain | 20 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `cron-profile-offset-at-scheduled-instant` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `installed-npm-consumer-compatibility` | retain | 7 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `linux-macos-host-support` | retain | 67 | Retain deliberate fork policy, including Intel Mac release support removed upstream. |
| `turn-liveness-watchdog-diagnostics` | retain | 22 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
| `recovery-missing-cli-fixture` | retain | 1 | Retain remaining owned delta; retirement requires the behavior contracts recorded in the patch manifest. |
