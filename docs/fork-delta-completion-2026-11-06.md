# Fork reduction completion decisions — November 6, 2026

The end goal is a maintained fork with upstream behavior reused where equivalent, optional
product implementations owned outside core, and explicit retirement conditions for necessary
core differences. A zero-delta fork would currently discard supported behavior.

## Frozen scope

The completed source sync imported NousResearch
`c55af2c93b9d61f5db7943fbc5ff50553038171f`, retiring the decoder age exception,
36 catalog-path claims and duplicate adapter recovery. Its projected-tree provenance remains
in `.github/upstream-sync-provenance.json`; the detailed reconciliation is in
[fork-patch-audit-2026-11-05.md](fork-patch-audit-2026-11-05.md).

This follow-up starts from fork `86da5d6470178f7253d1a049a75a748b3d063dd5`.
It does not silently broaden that reviewed import. The separately fetched upstream snapshot
`90d6b98fd86d20981ee06ddd5b7d93e4320f26a4` is 584 commits and 899 changed paths
beyond the accepted import. It is comparison evidence, not an imported or tested release.
The confirmation-dialog file, credential-pool facade, Honcho catalog entry and Python manifest
have no changes in that newer range; this narrow comparison does not establish equivalence
of every related sibling or every remaining fork patch. Further upstream advancement is a
separate sync batch, rather than an unbounded condition on completing this reduction pass.

## Final extraction decisions

| Component | Owner / decision | Remaining core boundary |
| --- | --- | --- |
| Honcho operational monitor and queue diagnostics | Extracted to `batumilove/hermes-ops`; source removal and installed cron/CLI migration completed. | Generic CLI registration and provider lifecycle correctness. |
| Provider telemetry collector, textfile export and consumer tests | Consolidated in the existing `batumilove/hermes-provider-telemetry`, [PR #2](https://github.com/batumilove/hermes-provider-telemetry/pull/2). Remove the older core fixture and its tests. | Runtime role, passive-to-active discovery, stable health series, and the existing writer-capability compatibility property. |
| Langfuse and terminal backend products | Already external; leave their separately owned active work alone. | Generic registries, profile scope, lifetime/admission and compatibility contracts. |
| Restart-policy skills | Already owned by `hermes-skills`. | Generic task-policy enforcement. |
| Side-effect response guard | Retain the mandatory final core guard; optional policy wording is already external in `batumi-policy`. | Trusted current-turn tool verdicts, durable identity, replay isolation and validation after all output rewrites. |

The final guard cannot safely become an ordinary output transform. The first nonempty
`transform_llm_output` return wins; another transform can introduce an unsupported action claim.
The existing `test_transformed_deployment_claim_is_checked_after_plugin_hook` contract proves
the core guard still qualifies such text. Its warning is delivery-only and does not rewrite
persisted context. Moving it requires equivalent ordered final validation, including overlapping
turns and owning-profile tests. No new hook is justified merely to complete a checklist.

The remaining Desktop confirmation, OAuth propagation/credential lease, Honcho client-lifetime,
Tornado manifest-floor and Intel Mac support differences retain the behavior-specific gates
recorded in the audit. Delivery acknowledgment, shutdown/cancellation custody, SessionDB admission,
profile scope and prompt caching remain core correctness responsibilities.

## Telemetry consolidation evidence

The installed provider plugin was read-only verified byte-identical to external base
`8e2e4649cccb00634bba6de0b34ce85019d0ec1b`:
`__init__.py` SHA-256 `9e9f4599bf5750a0303856bd6509ee0adf5a0caa9e4f0065071f85b13569078e`.
Core did not import its evidence fixture at runtime. Removing that duplicate therefore does
not require replacing installed files or restarting a service.

External candidate `2c6263e855a5664076fc1818ee9ad99c6580a628` preserves process writer leases, serialized publication and counter
reload while adding bounded per-profile collectors and closing callback generations before
lease release. Profile-overlap and stale-callback regressions failed against the external base.
Fifteen standalone tests and seven real-host tests passed locally on native macOS; the host
suite uses the canonical Hermes runner, actual plugin loading/configuration and hook dispatch.
Plugin validation and dashboard JSON validation passed. The new CI lane exercises Linux and
macOS against pinned host source; full rule verification also requires the standalone CI's
checksum-pinned promtool gate. Local tests do not prove those hosted results.

Core retains its role, discovery-order and health behavior contracts. The relevant output-policy
and real-host suites passed together: 107 tests across seven isolated test files. Existing tests
were reused where they already prove the required invariant; no source-shape tests were added.

Two real isolated-process regressions also failed on the core base: public context
properties arrived as callable proxies, so a false writer capability was truthy. The narrow
host fix snapshots existing public scalar properties under the owning profile at registration;
methods and live facades retain their RPC behavior. General and category loading share this
serialization. The isolated telemetry role checks now pass, along with 21 tests across the
host-property, general host, return-type, mixed-profile and external interoperability suites.

## Ownership, delivery and recovery

`.github/batumi-patches.yaml` remains the authoritative inventory: every retained entry names
its owned paths, rationale, contract command and nonempty retirement gate. The earlier appendix
is a conservative inventory, not a fresh semantic proof of every line in all retained patches.
Run `python scripts/check` and `python scripts/check_fork_delta.py` for each source candidate.

Promote the core scalar-property compatibility fix and duplicate removal after exact-head
checks/review, then the external consolidation against that verified host source. These are
source promotions. The current installed collector is already external; deleting the fixture
has no live consumer dependency. The generic scalar-property fix takes effect only when a
running installation consumes the new application source. A later installation of telemetry 0.2 must select the reviewed full commit pin, owning
profile, current metrics path and recovery copy before using the native plugin installer.
Do not reset cumulative metrics or rewrite live configuration to manufacture acceptance.

Any later application activation must use the installation's included `hermes update`, with
fresh installation/profile/source/supervisor, feed-range, backup and recovery verification.
Revert the cleanup PR to restore the old source fixture if needed; it is not a live data backup.
The previously completed application rollout, Honcho migration and approved storage retirement
have separate receipts. This follow-up neither repeats them nor removes more retained storage.
