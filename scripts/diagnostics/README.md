# Reconnect diagnostic qualification (local only)

This candidate is separate from PR442. Restart impact: restart_free; processes,
activation and rollback effects: none (discard this isolated candidate only).

Metadata schema: maximum 5000 rows, pending correlations and locally assigned
labels; finite safe integer counters, strict booleans, fixed kind/method/event
allowlists only. Request/session/replay-epoch identifiers become `cN` labels;
unknown event names are omitted. Raw oracle buffers remain unchanged and are
never attached by this instrumentation. No extra requests or RPCs are issued.

**This is not whole-artifact redaction.** Existing Playwright traces,
screenshots, error-context and reports may expose fixture content, URLs or
credentials. Before any separately authorized run verify the unchanged core
fixture uses only its scripted loopback provider, disposable home and fixture
credentials; never run it with real user/provider data. The metadata promise
applies only to reconnect-rpc-metadata, not existing artifacts.

Qualification:
- Node26 built-in runner: apps/shared/src/recorder-diagnostic.test.mjs extracts
  actual recorder bytes. HARNESS_PATH points to harness.ts (default /harness.ts
  inside the disposable qualification sandbox).
- PLAYWRIGHT_MODULES=<trusted installed node_modules> python3
  scripts/diagnostics/qualify-reconnect-attachment.py streams the actual catch
  bytes into a networkless bubblewrap sandbox with real Playwright. The harmless
  assertion MUST fail (runner exit1), metadata attachment must exist and parse,
  and original assertion must remain. Qualification returns0 only on that result.
  No browser or Electron launches. Host module tree is copied, never executed
  outside the sandbox.
- deferred-owner-diagnostic.test.mjs is transport characterization with synthetic
  owner, NOT application recovery proof; its historical lossless mode fails.

Limits: full Desktop E2E and project typecheck not run. Catch qualification
covers ordinary thrown assertion errors, not hard worker kill/global timeout or
setup failure before recorder creation. REST hydration response and renderer
ownership are not instrumented; these remain unobserved and no renderer-cause
claim is justified. Do not expand into product hooks in this bounded slice.
Independent exact-final-tree review is required before publication/execution.
Wrapper is one manual invocation of unchanged reusable core workflow; no retry
or loop at wrapper level; existing dependency-download retries are unchanged.
