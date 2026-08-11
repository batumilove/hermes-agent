Provider telemetry writer ownership fix — integrated candidate evidence
=====================================================================

Base (rollback target)
----------------------
  b53e9cc10e880ef8b5904a5e3452becc4cae2feb
  Merge commit 'f564581c26d28654f44aafa009a08cc9f3427ee8' into batumi/live-deploy

Included commits
----------------
  a63bc19f0d fix(monitoring): remove mutable state labels from platform health gauges
              (exact approved OTEL commit 0d3c50a5ae18cf86b992c71e598d6a3453684a2a)
  a64b7d127c fix(plugin): expose runtime role for telemetry ownership
              (cherry-picked from prior attempt 873397641c)
  <this commit> Integrated ownership fix: gateway role + deterministic RED/GREEN tests

What changed
------------
* hermes_cli/plugins.py
  - Added PluginManager.set_runtime_role() so host surfaces (gateway) can
    declare themselves request-producing before plugin discovery.
  - PluginContext.runtime_role now reads the explicit manager role first,
    then CLI ref, then dashboard env markers.
  - PluginContext.can_claim_provider_telemetry_writer returns True only for
    "cli" and "gateway" roles, keeping passive supervisors out.

* gateway/run.py
  - GatewayRunner.start() now calls get_plugin_manager().set_runtime_role("gateway")
    before discover_plugins(), so the provider_telemetry plugin can tell it is
    allowed to own the writer.

* tests/hermes_cli/test_plugin_runtime_role.py
  - Expanded to cover explicit gateway, profile gateway, CLI, dashboard,
    unknown, and manager role normalization.

* tests/plugins/test_provider_telemetry_plugin.py (new)
  - Deterministic RED/GREEN tests for the user-installed provider_telemetry
    plugin loaded from ~/.hermes/plugins/provider_telemetry/__init__.py.
  - Dashboard context: no lock claim, no hooks, no metrics file.
  - Gateway/CLI context: claims lock, registers hooks, writes bootstrap metrics.
  - Lock contention: a second gateway refuses to claim while another process
    holds the writer lock.
  - Recovery: a new gateway claims the writer after the prior owner releases.
  - State preservation: counters are reloaded from the existing .prom file.
  - RED regression: unpatched plugin reproduces the dashboard bug.
  - GREEN regression: patched plugin gates the dashboard out.

User plugin patch
-----------------
  provider_telemetry_writer_ownership.patch
  Apply to ~/.hermes/plugins/provider_telemetry/__init__.py:
      patch -p1 -d ~ < provider_telemetry_writer_ownership.patch
  The patch adds the guard at the top of register():
      if not getattr(ctx, "can_claim_provider_telemetry_writer", True):
          _warn("provider telemetry writer ownership skipped on passive surface")
          return

  provider_telemetry_fixed.py
  Reference candidate of the fully patched plugin for inspection/diff.

Verification run
----------------
  pytest -q tests/monitoring/                                -> 32 passed
  pytest -q tests/hermes_cli/test_plugins.py \
              tests/hermes_cli/test_dashboard_auth_plugin_hook.py \
              tests/hermes_cli/test_plugin_runtime_role.py \
              tests/plugins/test_provider_telemetry_plugin.py -> 68 passed
  pytest -q tests/providers/test_plugin_discovery.py         -> 3 passed

Immutable evidence (recorded separately from this README because the tree
hash would change if this file recorded its own hash).
------------------------------------------------------------------------
  See evidence/hashes.txt in the worktree for the integrated head, tree hash,
  and exact commit range.

Deployment constraints
------------------------
  This candidate is isolated in the worktree. No push, PR, merge, live-checkout
  mutation, service control, restart, config change, cron resume, or deployment
  was performed.
