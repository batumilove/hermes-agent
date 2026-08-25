Provider telemetry role registration ordering fix — evidence
=============================================================

Base
----
  b676a73543ec35902a2996478898b8bf8f8e4938
  fix(provider-telemetry): gateway runtime role gates writer ownership (reconciled onto live SessionDB)

This commit
-----------
  See the final response / evidence/hashes.txt for the exact commit and tree
  hash of the fix.  The hash cannot be embedded inside the file it is part of
  without changing the hash.

What changed
------------
* hermes_cli/plugins.py
  - Added `_discovered_role` and `_role_transitioned_to_claiming` state to
    `PluginManager`.
  - Made `_cli_ref` a property whose setter detects transitions from a
    passive/unknown discovered role to a request-producing effective role.
  - `set_runtime_role()` now also detects the same transition.
  - `discover_and_load()` consumes the transition flag and performs a single
    force re-discovery, clearing stale hooks and re-invoking plugins with the
    new claiming role.
  - This fixes the live bug where CLI background discovery runs before
    `GatewayRunner.set_runtime_role("gateway")`, causing provider_telemetry
    to register under "unknown" and never claim the writer.

* tests/hermes_cli/test_plugin_role_registration_ordering.py (new)
  - Deterministic GREEN coverage of the real CLI/gateway startup ordering:
    early discovery, then `set_runtime_role("gateway")`, then re-discovery.
  - CLI `_cli_ref` set after early discovery.
  - Dashboard stays passive and does not trigger re-registration.
  - Re-registration happens exactly once (no duplicate hooks).
  - `discover_plugins(force=True)` clears and re-registers correctly.
  - Global `discover_plugins()` path.
  - Early role set avoids transition.

* tests/plugins/test_provider_telemetry_plugin.py
  - `test_unpatched_plugin_reproduces_dashboard_bug` now xfails when the
    installed `~/.hermes/plugins/provider_telemetry/__init__.py` already
    contains the `can_claim_provider_telemetry_writer` guard, which is the
    current live state.

Verification run
----------------
  pytest -q tests/monitoring/                                -> 32 passed
  pytest -q tests/hermes_cli/test_plugins.py \
              tests/hermes_cli/test_dashboard_auth_plugin_hook.py \
              tests/hermes_cli/test_plugin_runtime_role.py \
              tests/plugins/test_provider_telemetry_plugin.py -> 71 passed, 1 xfailed
  pytest -q tests/providers/test_plugin_discovery.py         -> 3 passed
  pytest -q tests/hermes_cli/test_plugin_role_registration_ordering.py -> 8 passed

  Combined prior 103 + new 8 tests:
  pytest -q tests/monitoring/ tests/hermes_cli/test_plugins.py \
              tests/hermes_cli/test_dashboard_auth_plugin_hook.py \
              tests/hermes_cli/test_plugin_runtime_role.py \
              tests/plugins/test_provider_telemetry_plugin.py \
              tests/providers/test_plugin_discovery.py \
              tests/hermes_cli/test_plugin_role_registration_ordering.py
  -> 110 passed, 1 xfailed

Additional targeted checks:
  pytest -q tests/gateway/test_discord_double_dispatch.py \
              tests/gateway/relay/test_auth.py \
              tests/gateway/test_discord_component_auth.py -> 28 passed
  pytest -q tests/gateway/test_pre_gateway_dispatch.py \
              tests/gateway/test_platform_registry.py \
              tests/gateway/test_session_boundary_hooks.py \
              tests/gateway/test_startup_no_eager_platform_install.py -> 39 passed
  pytest -q tests/hermes_cli/test_mcp_startup.py \
              tests/hermes_cli/test_codex_runtime_plugin_migration.py \
              tests/hermes_cli/test_dashboard_basic_auth_plugin_enable.py -> 26 passed

Deployment constraints
----------------------
  This candidate is isolated in the worktree. No push, PR, merge, live-checkout
  mutation, service control, restart, config change, cron resume, or deployment
  was performed.
