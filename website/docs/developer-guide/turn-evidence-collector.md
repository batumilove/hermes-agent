---
title: "Turn Evidence Collector"
description: "Bounded post-quiescence collection and authenticated transfer of per-turn delivery receipts"
---

# Turn Evidence Collector

The turn evidence collector is the owning side of the per-turn delivery-evidence
contract. One `TurnEvidenceCollector` instance owns exactly one terminal turn: it
creates original delivery `Receipt` objects bound to the owning producer identity,
retains each Receipt's terminal-proof reader through the
[turn evidence custody](observer-hooks.md) boundary, and — only after that boundary
has closed and every admitted callback has quiesced — collects a fresh terminal
proof in the owning process and issues a single-use authenticated transfer
envelope for offline backend validation.

The collector never queries a backend, holds credentials, exports data, or
changes dispatcher timeout/failure behavior. Observer callbacks must be
registered before `begin_turn()`; registering hooks during the owned turn changes
the callback-registration generation and invalidates the evidence.

## Lifecycle

```python
from hermes_cli.plugins_turn_evidence_collector import (
    TurnEvidenceCollector, TransferVerifier, validate_backend)

collector = TurnEvidenceCollector(
    manager, context, receipts, turn_id=turn_id,
    receipt_directory=str(receipt_dir), key=transfer_key)

with collector.begin_turn():
    receipt = collector.open_trace(trace_id, root_id)
    ... record observations, receipt.finish("normal") ...
    collector.retain(trace_id)   # custody holds the terminal-proof reader

proofs = collector.collect()    # bounded wait for quiescence; fresh proofs only
envelope = collector.issue_transfer(trace_id)  # HMAC-signed, single-use

verifier = TransferVerifier(backend_key)
payload = verifier.verify(envelope, expected_binding=dict(collector.binding(), trace_id=trace_id))
verdict = validate_backend(payload, backend_envelope, receipts=receipts)
# "captured_inventory_delivered" | "mismatch" | "unverified"
```

## Outcomes

| Status | Meaning |
| --- | --- |
| `collected` | Fresh terminal proof obtained in the owning process after quiescence |
| `transferred` | Single-use authenticated envelope already issued for the trace |
| `unknown` | Quiescence deadline, eviction or retention expiry — no verdict possible |
| `unverified` | Boundary failure, abandonment, registration change or invalid receipt |

Evicted or expired retention always returns `unknown`; it is never success. A
missing or partial receipt file is `unknown`, never `unverified`-as-pass.

## Bounds

- At most 16 retained traces per collector (the custody per-turn reader bound);
  the oldest active trace is evicted and invalidated beyond that.
- Receipts and proofs are retained for a bounded window (default 300 s), then
  invalidated and dropped; expiry returns `unknown`. Failed retention and refused
  custody reads drop the Receipt reference immediately, so live Receipt objects
  never exceed the active bound.
- A disposed (evicted/expired/replaced) reader's custody slot is released at the
  next collector call once the boundary is no longer pending; the closure holds
  only a small entry cell (never the collector or a dropped Receipt) and the
  disposed reader can never yield a valid authority. The slot itself is memory the
  reviewed core reclaims lazily on its next custody operation; there is no
  wall-clock timer.
- The trace ledger holds at most 16 entries in total (active plus terminal);
  pruning a terminal entry queues its custody handle so release still happens.
  Reopening a trace id disposes the prior entry immediately.
- `collect()` waits a bounded deadline (default 2 s, at most 10 s) for quiescence;
  it never joins abandoned callbacks indefinitely.
- Transfer envelopes are HMAC-SHA256 keyed, carry exact producer/profile/turn/
  receipt-digest bindings, and each nonce is consumed once per verifier. Verifiers
  may additionally pin the exact receipt digest and registration-generation digest
  via `expected_binding(trace_id)` (captured before transfer) or their own record.

## Transfer authority

The signed proof is never a cached or caller-mutable value. `issue_transfer`
re-checks quiescence and abandonment at issue time and re-reads a fresh
terminal proof from the retained Receipt in the owning process; any
post-collection invalidation (registration change, late dispatch on a copied
context, abandonment, a late observation on the sealed Receipt) refuses the
transfer and marks the trace `unverified`. Every authority predicate is
re-verified again after the signature exists and before the envelope is
released. Proofs returned by `collect()` are copies and cannot alter what is
signed, and observed abandonment or boundary failure latches `unverified`
permanently.

Trust boundary: these are observed states of in-process objects. The collector
refuses on every observation, but a mutator landing in the final instruction
window, or a context flag toggled without ever being observed, is outside this
contract — the same in-process consumer trust the reviewed turn-evidence core
relies on.

## Not a delivery verdict

A collected proof or a verified envelope is local lifecycle evidence. Only
`validate_backend` with a complete, authenticated backend observation envelope can
return `captured_inventory_delivered`, and that verdict covers the captured
inventory only — hooks lost before instrumentation are excluded.
