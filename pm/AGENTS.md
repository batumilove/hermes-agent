# pm/ — dependencies and Hermes environments

Applies on top of the root `AGENTS.md` (which carries the short form of the pinning rule).

## Dependency pinning policy

All dependencies carry upper bounds (litellm compromise #2796/#2810; Mini Shai-Hulud worm,
May 2026). PyPI: `>=floor,<next_major` (`"httpx>=0.28.1,<1"`); pre-1.0: `<0.(minor+2)`
(`>=0.29,<0.32`). Git URLs: 40-char commit SHA. GitHub Actions: SHA + `# vN` comment. CI-only
Python requirements: `==exact`. A bare `>=X.Y.Z` is rejected by CI and reviewers.
After changing `pyproject.toml`, run `hermes pm lock`, re-source `./activate`, and commit
`pyproject.toml` with `uv.lock`. Reference: #2810 (bounds), #9801 (SHA pinning + audit CI).

PM owns Hermes Python dependency changes. Use `pm.sync_venv(['extra'], explicit=True)`
for declared runtime extras, `hermes pm install` for setup/sync, and `hermes pm repair`
for damaged dependencies. Do not mutate Hermes environments with raw pip or uv.
Use `pm.build_environment` for fresh build outputs and `pm.ensure_environment` for
isolated tool environments. Callers receive an interpreter or tool path, not uv.
Nix's declarative uv2nix builds and unrelated user projects remain independently owned.

The `[tool.uv] exclude-newer = "3 days"` release-age check covers **Hermes's own dependencies
only**, exact pins included, and it runs when Hermes LOCKS
(`hermes pm lock`, CI's lock check), never on users' machines: every generated workspace drops the
cutoff (`pm/workspace.py::_without_release_age`), so a mirror without upload times cannot break an
install (#135589). Plugin resolution is unchanged by this: core's exact pins stay put, and a plugin
may move a transitive core package within core's declared ranges
(`tests/pm/test_workspace_build_inputs.py::test_plugin_can_move_compatible_transitive_but_not_exact_requirement`).
Plugin `python_dependencies` follow the plugin's own policy. Teknium's rulings: "plugins dont have to abide by our 14 day rule … Only hermes'
dependencies themselves have to"; dates are checked by us, not users; pinned deps need >3 days.

Fork exception: the user explicitly approved skipping cooldown on October 22 for the
certifi 2026.10.19 security fix. Its package-specific cutoff is fixed at
2026-10-22T00:00:00Z. On October 24 the user also approved brotlicffi 1.2.1.0
for PYSEC-2026-4323; its fixed cutoff is 2026-10-23T00:00:00Z. No other Python
dependency is exempt. Remove each exception after its release meets the normal
three-day floor.


## Temporary browser-harness source pin

The fork temporarily pins browser-use/browser-harness#931 at
`338dea2fb8cbf84204e5cd782c80d9aa71f446d4` to permit Pillow 13.0.0. The user
approved this unreleased source explicitly. Keep the full SHA in the manifest and
lock; replace it when a compatible published release contains the fix, then run
`hermes pm lock`, sync an isolated environment with PM, and validate browser/image
integration plus the dependency gate. Reassess the Nix setuptools override in
`nix/python.nix` when replacing the Git source; do not remove a still-required
build input. PM installation alone does not prove the Nix build passes.

The certifi cutoff exception above is separate from this source-pin approval.
Remove that exception once its release satisfies the normal age policy and verify
manifest/lock agreement. Source publication and passing CI are not runtime activation.
