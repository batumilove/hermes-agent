# Recovering one failed final reply

Use `hermes delivery-recovery` when a saved final reply is stranded and you have
reviewed the risk of sending it twice. The command never replays the model turn.
Use the installed CLI belonging to the gateway's source and runtime.

First inspect the exact obligation in its runtime profile:

```sh
hermes --profile default delivery-recovery inspect EXACT_ID --expect-home /absolute/canonical/profile
```

Use the returned fingerprint to review a retry request:

```sh
hermes --profile default delivery-recovery request-retry EXACT_ID --expect-home /absolute/canonical/profile --fingerprint EXACT_SHA256 --operator OPERATOR_LABEL --acknowledge-possible-duplicate
```

This is a read-only dry run. Append `--apply` to record one audited decision. The
running gateway can consume that decision immediately and send a reply labelled
“Operator-requested retry — possible duplicate”. `requested` confirms the decision
was recorded; it does not confirm delivery.

`--expect-home` confirms the profile already selected by Hermes. It cannot select
another database. Each invocation accepts one exact obligation ID. Missing
acknowledgement, noncanonical paths, stale fingerprints, ineligible finalizers,
and receipts without a durable delivery-bot identity are refused. Inspection
omits saved response text, routing details and action payloads.

The gateway uses the receipt's recorded bot, which may differ from the database's
runtime profile for a shared bot. If that bot is unavailable, the decision stays
pending. Another profile's bot cannot substitute for it. Registered databases
progress independently, so one suspended send cannot block the other profiles.

A decision authorizes one attempt. A cancelled, crashed or ambiguous send never
retries automatically. Inspect again and acknowledge a fresh fingerprint before
requesting another attempt. An execution still owned by a live process prevents
another decision until its transport has finished and its outcome is reconciled.
Only confirmed delivery lets the existing ordered worker finalize the old turn;
a newer turn's token remains protected.

CLI inspection and requests use tracked SQLite connections. Gateway reads,
claims and settlement use its existing executor and closing fence. Shutdown
cancels consumers and retains their writer lifetime until transports unwind;
it does not declare them stopped merely because cancellation was requested.
The terminal live-state guard and `hermes state-db query` remain unchanged.

Installing this feature requires a gateway restart through the included updater.
Installation does not authorize a retry: each obligation needs its own explicit
duplicate acknowledgement and `--apply` request.
