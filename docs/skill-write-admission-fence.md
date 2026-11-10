# Scoped skill write admission fence — private implementation proposal

This candidate is not deployed. It adds an operator CLI and generation checks
to the existing `skill_manage` tool, without changing model tool schemas or
curator lock policy. The concrete consumer is the preserving shared-skills
reconciliation procedure.

## Contract

Each flat or batch call captures the active profile and package admission
epochs before staging or preflight. The call revalidates that binding inside
every package mutex before capture, mutation or rollback. A batch retains one
binding through its recursive operations. A repair fence advances the selected
epochs while holding the runtime-tree, helper-state and sorted package mutexes.
This drains active critical sections and invalidates calls still waiting to
enter them. Ending a fence advances the epochs again: an old call cannot wait
for release and then overwrite the repaired result.

New calls to a fenced package refuse; unrelated packages continue. Staged
payloads retain their admission binding. A legacy staged payload is compatible
only while every affected epoch is still zero. Do not rewrite its binding to
make an old full-file payload current; retain it for owner disposition and a
fresh preserving proposal.

## Operator interface

Use the interpreter and source supplied by the owning updater. Bind the intended
profile through its existing profile mechanism; these examples use the existing
`HERMES_HOME` convention. No gateway is started by this module.

```text
python -m tools.skill_write_fence status
python -m tools.skill_write_fence begin \
  --name category/selected-skill \
  --expected-state-sha256 <status-state-digest> \
  --plan-sha256 <exact-approved-plan-digest> \
  --owner <existing-repair-owner> \
  --expires-at <bounded-UTC-expiry> \
  --receipt <existing-private-directory>/fence-capability.json
python -m tools.skill_write_fence end \
  --receipt <same-private-capability-file> \
  --expected-state-sha256 <fresh-status-state-digest> \
  --outcome <private-exact-outcome-receipt> \
  --outcome-sha256 <outcome-file-digest>
```

`begin` requires an owned private profile and the existing runtime-tree and
shared-skills helper-state locks. It takes nonblocking exclusive locks in the
reviewed order, then the selected basename mutexes and private control lock.
Busy or stale observations refuse without publishing a fence. Scope aliases
share the existing basename mutex, including root/canonical camera paths.

The capability file must be in an existing private directory outside the profile
and selected package trees. Canonical and lexical aliases into those trees are
refused before any receipt or fence publication. The file is mode0600 and is
durably written, including its directory entry, before fence publication.
Keep it private; CLI stdout does not contain its token. The outcome is also a
private regular file and must match the supplied hash, lease, exact selected
basenames and plan digest, with status `COMPLETE`, `ABORTED_NO_MUTATION` or
`RECOVERED`. An operator may not use another lease's scope or restore over a
changed control-state digest. A crash after publication retains a recoverable
capability. A crash before publication can leave an unused receipt; inspect
status and preserve that evidence rather than automatically retrying it.

Expiry never releases a fence or replays a payload. Ending one's own lease does
not change user curator locks or restore unrelated flags. Epoch history remains
in profile state; never delete, reset or roll it back to simulate release.

## Read-only validation inside an existing transaction envelope

`validate_owner(receipt, *, names, expected_state_sha256, expected_epochs,
plan_sha256, owner)` authenticates the private capability against the exact
current lease and epoch vector, including profile, physical package names,
plan, operator and future expiry. It changes no state and acquires no locks.
It returns nonsecret evidence only, never the capability token.

The consumer MUST already hold the same runtime-tree, helper-state and all
selected canonical package mutex descriptors, in that order. Keep that
verified envelope throughout the protected phase; revalidate at each later
mutation or recovery boundary. Cooperating begin/end operations require the
same locks and cannot change the lease while the envelope is held. The function
does not verify that the caller holds those locks, instrument an old gateway,
authorize a user-locked mutation or exclude independent file writers.

Capture status immediately after begin and require its digest to equal the
begin receipt before retaining its exact selected epoch vector. Do not invent
or recapture epochs for an old transaction. A released, expired, foreign or
changed lease must refuse; expiry retains recovery custody.

A cross-process consumer can invoke the same read-only check while retaining
its own descriptor envelope:

```text
python -m tools.skill_write_fence validate-owner \
  --receipt <private-capability-file> \
  --name category/selected-skill \
  --expected-state-sha256 <bound-state-digest> \
  --expected-epochs-json '{"selected-skill": 1}' \
  --plan-sha256 <exact-approved-plan-digest> --owner <existing-repair-owner>
```

The epoch value above is illustrative, never a live default. The unchanged
executor must retain all original descriptor, source, metadata, pending-store,
custody, permission and recovery gates. No private admission context injection
or ordinary-writer bypass is supported.

## Activation and acceptance boundary

Passing isolated tests is not activation. Use the included owning updater and
an explicitly established shared-runtime activation window. Retire every old
uninstrumented writer through the supported lifecycle boundary; unchanged
on-disk source or a newly invoked operator CLI does not prove an already
running gateway enforces this protocol. Bind deployed source, interpreter,
profile, process identity and actual enforcement evidence before beginning any
real lease. Existing user curator locks remain in force, including their
user-only unlock boundary.

This is not an OS sandbox, a universal process census or a source-cache lock.
It covers the participating `skill_manage` flat, batch and staged-approval paths.
Terminal/file writers, direct helper calls, deterministic curator operations,
desktop user edits, other processes and source/reset/install entrants require
their own actual ownership/exclusion evidence. Keep the executor's verified
descriptor envelope, pending-store checks, source and metadata bindings,
custody, recovery and readback gates. This proposal does not amend the frozen
V11 transaction or grant installation authority.
