# Honcho Memory-Quality + Observability Roadmap

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Stop stale or contradictory Honcho memory from polluting injections, make every injected memory explain why it was selected, and expose enough queue/state diagnostics to debug failures without guessing.

**Architecture:** Keep Honcho as the external memory source of truth, but make Hermes responsible for selection, labeling, and diagnostics. The first pass is a read/label/filter layer in the Hermes plugin; the second pass is observability and failure-proofing around config, embeddings, and queue-state paths. Prefer additive metadata and graceful fallback when the Honcho SDK or server does not expose a capability.

**Tech Stack:** Python, pytest, Honcho SDK integration, Hermes CLI/plugin layer, existing Honcho session manager, local diagnostics/doctor commands.

---

## Task 1: Filter stale and contradicted memory before injection

**Objective:** Prevent task-progress notes, corrected facts, and obsolete snippets from being injected into the prompt unless they still satisfy the current selection rules.

**Likely files:**
- Modify: `plugins/memory/honcho/__init__.py`
- Modify: `plugins/memory/honcho/session.py`
- Modify: `tests/honcho_plugin/test_session.py`
- Possibly modify: `tests/agent/test_memory_provider.py`

**What changes:**
- Add a pre-injection filter that can drop memories tagged as stale, corrected, task-progress-only, or contradicted by newer evidence.
- Preserve stable user preferences and active-project context even if nearby context is noisy.
- Keep the current “don’t inject trivial prompts” short-circuit, but add a second pass that ranks/filter-limits already-retrieved content.
- Treat “stale” as a selection decision, not a deletion policy: nothing is removed from Honcho, only excluded from Hermes injection.

**Test plan:**
- Add regression tests that show a stale task-progress snippet is not injected when a newer correction exists.
- Add tests that stable preference facts still survive filtering.
- Add tests that contradictory snippets are excluded when a better-supported newer fact is present.

**Acceptance criteria:**
- Injection output no longer includes stale task-progress memory when a correction exists.
- Stable preference/context memories still inject when relevant.
- Filtering is deterministic and covered by tests.

---

## Task 2: Attach reason metadata to every injected memory chunk

**Objective:** Make the origin of injected memories auditable so Hermes can tell the model why each item was selected.

**Likely files:**
- Modify: `plugins/memory/honcho/__init__.py`
- Modify: `plugins/memory/honcho/session.py`
- Modify: `tests/honcho_plugin/test_session.py`
- Possibly modify: `plugins/memory/honcho/README.md`

**What changes:**
- Add a structured reason field for each memory chunk, such as:
  - `exact_entity_match`
  - `semantic_match`
  - `recent_correction`
  - `stable_user_preference`
  - `active_project_match`
  - `session_continuity`
  - `queue_signal`
- Carry the reason metadata through the existing injection envelope, not as freeform prose.
- If Honcho returns its own metadata, preserve it and augment it rather than replacing it.
- Surface reason metadata in debug/doctor output when available.

**Test plan:**
- Add unit tests for reason assignment on exact matches, semantic matches, and correction-based selections.
- Add tests proving the metadata survives truncation/budgeting.
- Add tests that unknown reasons fall back to a safe generic label instead of crashing.

**Acceptance criteria:**
- Every injected memory item has a machine-readable reason label.
- The reason label is visible in diagnostics and testable in unit tests.
- No new secrets or raw internal traces leak into user-facing output.

---

## Task 3: Consume queue details when Honcho exposes them; otherwise provide local diagnostics

**Objective:** Reduce blind spots around per-work-unit queue state so we can see what happened when a session fails, stalls, or injects nothing useful.

**Likely files:**
- Modify: `plugins/memory/honcho/session.py`
- Modify: `plugins/memory/honcho/client.py`
- Modify: `plugins/memory/honcho/cli.py`
- Possibly add: `plugins/memory/honcho/diagnostics.py`
- Possibly modify: `hermes_cli/doctor.py`
- Possibly add: `plugins/memory/honcho/templates/...` if an exporter script is needed
- Modify/add tests under `tests/honcho_plugin/` and `tests/hermes_cli/`

**What changes:**
- If the Honcho SDK/server exposes queue detail per work unit, thread it through the session manager and surface it in Hermes.
- If not available, add a local diagnostic path that records:
  - last fetch/query used
  - session key and peer mapping
  - empty-result streaks
  - current cadence and liveness snapshot
  - config resolution path and effective config values
- Expose the diagnostics through `hermes honcho status` and/or `hermes doctor` so operators can confirm the loaded config, not just that the endpoint responds.
- Prefer a read-only diagnostic/export path over remote mutation.

**Test plan:**
- Add a happy-path test for queue detail propagation when the SDK exposes it.
- Add a fallback test proving the local diagnostic path still works when queue detail is absent.
- Add smoke tests that config resolution, not just service liveness, is reported correctly.

**Acceptance criteria:**
- We can inspect per-work-unit queue state when available.
- When unavailable, Hermes still emits useful local diagnostics.
- `doctor`/`status` verify loaded config values, not just network reachability.

---

## Task 4: Add failure-mode tests and smoke checks for config, embeddings, and queue behavior

**Objective:** Catch the ugly cases early: bad config, missing embedding support, queue stalls, and partial Honcho outages.

**Likely files:**
- Modify: `tests/honcho_plugin/test_session.py`
- Modify: `tests/test_honcho_client_config.py`
- Modify: `tests/hermes_cli/test_doctor.py`
- Modify: `tests/hermes_cli/test_model_validation.py` if embedding selection/validation is shared
- Modify: `tests/agent/test_memory_provider.py` if provider-level propagation needs coverage

**What changes:**
- Add tests for:
  - config file present but malformed values
  - missing API key vs self-hosted base URL
  - embedding/model validation failures
  - queue stall or empty-result backoff behavior
  - stale-thread recovery / liveness snapshot reporting
- Add smoke checks that verify the effective Honcho config was loaded, not merely that the process can connect.
- Keep failure output actionable and non-secretive.

**Acceptance criteria:**
- The critical failure modes have explicit regression tests.
- The smoke checks distinguish “service reachable” from “config actually loaded.”
- Queue/embedding failures degrade gracefully instead of taking down the agent.

---

## Risk gates / rollout constraints

- Do not remove or rewrite existing Honcho memories; only change Hermes selection/injection behavior.
- Preserve stable user preferences and active-project signals unless a newer correction clearly supersedes them.
- Keep all new diagnostics secret-safe: no raw keys, tokens, or private memory dumps.
- Treat queue-detail integration as optional capability detection, not a hard dependency.
- Verify config resolution and effective values in tests before considering the rollout safe.
- If the Honcho SDK/server behavior is ambiguous, stop at the diagnostic layer and do not guess.

## Suggested implementation order

1. Filtering + reason metadata in the injection path.
2. Diagnostics / queue-detail plumbing.
3. Failure-mode tests and smoke checks.
4. Docs update for the new labels and debug output.

## Done when

- Hermes no longer injects stale/contradicted task-progress memory by default.
- Every injected memory chunk includes a reason label.
- Queue or export diagnostics explain what Honcho is doing when memory looks wrong.
- Tests cover config, embeddings, queue failures, and the loaded-config smoke path.
