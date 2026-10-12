# Canonical operator recovery CLI (candidate, not activated)

Use the installed `hermes` console entrypoint from the **same immutable release
and Python/SQLite runtime as the target gateway**. Do not invoke the raw recovery
module, wrap it to evade the terminal guard, alter guard policy, or point the live
console script at a worktree using PYTHONPATH. Deployment must bind the console
entrypoint and gateway together; an old editable console installation is not proof
of release identity.

```
hermes --profile default delivery-recovery inspect EXACT_ID --expect-home /absolute/canonical/profile
hermes --profile default delivery-recovery request-retry EXACT_ID --expect-home /absolute/canonical/profile --fingerprint EXACT_SHA256 --operator OPERATOR_LABEL --acknowledge-possible-duplicate
```

The second command is a read-only dry run. Only appending the complete `--apply`
flag records one audited intent. That intent can be consumed immediately by the
owning worker and cause a possible duplicate. No CLI send, replay, ownership reset,
or barrier release occurs. Inspect afresh after deployment/startup. Never infer
permission to retry other obligations from permission to retry a specified set.
An execution controller must enforce its approved ID allowlist; this generic CLI
accepts exactly one explicit ID per invocation, not a batch or SQL selector.

`--expect-home` is mandatory and confirms the profile already selected by Hermes;
it does not reroute a command. Noncanonical/symlink profile paths, absent DBs, stale
or wrong-profile fingerprints, already-recorded decisions, ineligible receipts,
and missing acknowledgements fail closed. Output omits response/payload/routing
content. `requested` means intent recorded, not delivery confirmed. Inspection
returns the current request history. Do not repeat an uncertain send automatically.

## Security/review boundary

The existing terminal live-state guard is unchanged. Canonical CLI commands are
already its sanctioned architectural route; there is no new interpreter/module
allowlist, bypass token, env-based exemption, or broad SQL write surface. The
existing `state-db query` remains read-only. The recovery API uses the shared
tracked connection lifecycle in both read and write modes; its transaction/CAS,
eligibility, duplicate acknowledgement and audit semantics otherwise stay intact.

The new canonical write command itself requires independent exact-artifact review.
A guard 'not blocked' decision is not user authorization, runtime identity proof,
or permission to deploy. Tests use temporary databases, the actual installed
console script with isolated candidate import resolution, and the unchanged guard
with explicit live-liveness input. They do not qualify live deployment identity or
exercise live retry requests. No safeguard modification or activation is included.

Restart impact: CLI registration is restart-free for a newly launched CLI. Shared
recovery connection code is imported by the long-lived gateway, so aggregate
activation is restart-required, deferred to the reviewed PR385 maintenance bundle.
Target is the authorized default gateway only; no other profile activation.
Baseline/rollback source: PR385 3df9d0d9d82dfcb0cea797b99f9bb5d921cf3f49;
actual deployed rollback identity must be revalidated by the external controller.
