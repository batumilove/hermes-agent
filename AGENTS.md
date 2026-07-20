# Hermes Agent - Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.

**Never give up on the right solution.**

This file is intentionally concise so repo/workdir context injection does not exceed gateway prompt limits. The expanded historical/reference material lives in [`docs/AGENTS_REFERENCE.md`](docs/AGENTS_REFERENCE.md). Load that reference when a task needs detailed contributor policy, architecture notes, or edge-case pitfalls.

## Non-negotiables

- **Prompt caching is sacred.** Do not mutate past context, swap toolsets, reload memories, or rebuild the system prompt mid-conversation. Slash commands that change prompt state should defer to the next session unless they explicitly offer a cache-invalidating `--now` path.
- **Keep the core narrow.** Prefer existing code, CLI command + skill, service-gated tool, plugin, or MCP before adding a new core model tool. Every core tool schema is paid for on every call.
- **Config belongs in `config.yaml`; secrets belong in `.env`.** Do not add user-facing `HERMES_*` env vars for non-secret behavior.
- **Use profile-safe paths.** Use `get_hermes_home()` for state paths and `display_hermes_home()` for user-facing paths. Never hardcode `~/.hermes` in profile-aware code.
- **Tests must be hermetic.** Do not write tests that touch the real `~/.hermes/`. Use `scripts/run_tests.sh`, not direct `pytest`, for CI-parity. A retry-pass is still a flaky-test defect.
- **Verify real behavior.** Reproduce reported bugs against current code, identify the exact line/path where behavior manifests, and add behavior/invariant tests rather than snapshots.
- **Plugins stay at the edge.** Plugins must not modify core files for plugin-specific behavior; widen generic hooks/ABCs if needed.
- **No silent broad rewrites.** Inspect git state before editing, preserve user/local changes, and secret-scan changed/untracked files before committing or pushing.

## What Hermes Is

Hermes is a personal AI agent that runs the same core across CLI, messaging gateway, TUI, Electron desktop, cron, webhooks, plugins, skills, MCP, memory, browser, and terminal tooling. It is intentionally expansive at the product edges and conservative at the core model/tool waist.

Design lens:

1. Long-lived conversations rely on stable cached prompt prefixes.
2. Capabilities should usually arrive as plugins, skills, CLI commands, MCP servers, or gated tools rather than permanent core schemas.

## Contribution Rubric

Wanted:

- Fix real bugs completely, including sibling call paths.
- Expand platforms/providers/channels/UI at the edges using existing setup/config UX.
- Refactor god-files into focused modules when the refactor is explicit and mechanical.
- Preserve contributor authorship when salvaging external work.
- Test contracts and invariants, not mutable snapshots.
- E2E-validate config propagation, backends, security boundaries, and file/network paths with real imports against temp `HERMES_HOME`.

Rejected patterns:

- Speculative hooks or managers with no concrete consumer.
- Non-secret behavior configured only by env var.
- New core tools where terminal/file/CLI/skill/plugin/MCP is enough.
- Lazy/paginated loading on instructional tools whose content must be read fully.
- Mitigations that destroy the feature they are securing.
- Opt-out telemetry, attribution tags, or third-party analytics.
- Third-party product integrations landed as in-tree plugins instead of standalone plugin repos.
- Stale-branch squash merges that revert unrelated recent fixes.

Before calling something a bug, verify the premise and intent against the code/history (`git log -p -S`). Some omissions are load-bearing.

## Project Structure

Filesystem is canonical; counts change constantly. Key entry points:

```text
run_agent.py          # AIAgent conversation loop
model_tools.py        # tool discovery and dispatch
toolsets.py           # toolset definitions
cli.py                # classic CLI orchestration
hermes_state.py       # SQLite sessions/search
hermes_constants.py   # profile-aware paths
agent/                # prompt/context/provider/memory internals
hermes_cli/           # subcommands, config, setup, commands, skin engine
tools/                # tool implementations and environments
gateway/              # messaging gateway and platform adapters
plugins/              # plugin systems: memory, model providers, kanban, etc.
cron/                 # scheduled jobs
skills/               # bundled active skills
optional-skills/      # installed explicitly from skills hub
ui-tui/               # Ink/React terminal UI
tui_gateway/          # Python JSON-RPC backend for TUI/desktop/web surfaces
apps/desktop/         # Electron desktop app
web/                  # dashboard frontend
website/              # Docusaurus docs
tests/                # pytest suite
```

User paths:

- Config: `~/.hermes/config.yaml`
- Secrets: `~/.hermes/.env`
- Logs: `~/.hermes/logs/`
- Sessions: `~/.hermes/sessions/`

## Development Environment

```bash
source .venv/bin/activate   # or: source venv/bin/activate
scripts/run_tests.sh tests/path_or_file.py -q
```

`scripts/run_tests.sh` probes `.venv`, `venv`, then `$HOME/.hermes/hermes-agent/venv` and enforces CI-like isolation.

## Architecture Quick Notes

### Agent loop

`AIAgent` lives in `run_agent.py`; `run_conversation()` builds OpenAI-format messages, calls the model, dispatches tool calls through `handle_function_call()`, appends tool results, and returns final text when no tool calls remain. Preserve strict role alternation.

### Tool wiring

- Tool implementations live in `tools/*.py` and register with `tools.registry`.
- Auto-discovery imports tool files, but exposure still requires inclusion in `toolsets.py`.
- Handlers return JSON strings.
- Use `display_hermes_home()` in schema descriptions that mention paths.
- Use `get_hermes_home()` for persistent state.

### CLI/slash commands

Slash commands are centralized in `hermes_cli/commands.py` as `CommandDef` entries. CLI, gateway known-command dispatch, help text, Telegram commands, Slack mapping, and autocomplete derive from that registry. Add handlers in `cli.py` and, if gateway-visible, `gateway/run.py`.

### Gateway/platforms

Keep platform-specific API conversion and exceptions inside the adapter (`gateway/platforms/<platform>.py`). Preserve chat/thread/source semantics. Commands that must interrupt or approve an active agent must bypass both adapter pending-message guards and runner guards.

### TUI/dashboard/desktop

- `hermes --tui`: Ink frontend over stdio JSON-RPC to `tui_gateway`.
- Dashboard `/chat`: embeds the real TUI through PTY/WebSocket; do not rebuild the primary chat transcript in React.
- Desktop app: separate Electron/React chat surface over JSON-RPC; it does not embed the TUI. Desktop slash-command curation must allow skill/quick-command extensions through.

### Plugins

General plugins are discovered by `hermes_cli/plugins.py` and can register hooks, tools, and CLI commands. Memory and model-provider plugins have separate discovery systems. New third-party product integrations should ship as standalone plugin repos, not directories in this tree.

### Cron and Kanban

Cron: `cron/jobs.py` + `cron/scheduler.py`; supports schedule strings, model/provider overrides, scripts/no-agent mode, context chaining, workdir context, and delivery targets. Cron sessions skip memory by default and have hard interrupt/catchup windows.

Kanban: SQLite-backed boards with dispatcher/worker toolsets. Board is the hard isolation boundary; tenant is a soft namespace. Workers are pinned by `HERMES_KANBAN_BOARD`.

## Configuration Rules

- Add config defaults in `hermes_cli/config.py::DEFAULT_CONFIG`.
- Bump `_config_version` only for active migrations/transforms, not ordinary new keys.
- `.env` additions are for credentials only and must be listed in `OPTIONAL_ENV_VARS` with metadata.
- Know the loader path: `load_cli_config()` for CLI, `load_config()` for most CLI subcommands, direct YAML loading in gateway runtime.
- Messaging cwd is `terminal.cwd` in config; legacy `MESSAGING_CWD` is removed.

## Dependency Policy

All dependencies need upper bounds. PyPI packages use `>=floor,<next_major` (or bounded pre-1.0 range), Git URLs use commit SHAs, GitHub Actions use pinned SHAs with comments, CI-only pip pins exact versions. Run `uv lock` after dependency changes.

## TypeScript Style

Prefer small nanostores for shared state, feature-owned atoms, thin route roots, colocated action modules, table-driven mappings, interface props for public object shapes, and explicit async UI intent (`onClick={() => void save()}`). Avoid monolithic hooks and deep prop drilling.

## Skills Standards

New/modernized skills should have short descriptions, modern section order, explicit prerequisites, scripts under `scripts/`, references under `references/`, templates under `templates/`, and tests under `tests/skills/`. Tool names in prose should be Hermes tools/MCPs, not shell utilities that wrappers already replace.

## Testing Rules

Always use:

```bash
scripts/run_tests.sh                                  # full suite
scripts/run_tests.sh tests/gateway/                   # directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # focused
scripts/run_tests.sh -v --tb=long                     # pass-through flags
```

Do not write change-detector tests for mutable catalogs/counts/version literals. Prefer behavior and invariants: catalog plumbing works, migrations reach latest config version, no plan-only models leak into legacy lists, every model has context lengths, etc.

Never test behavior by regex-reading implementation source. Extract logic and execute it. Tests for JS/TS artifacts and configuration belong in the JS test suite so path classification runs them when those artifacts change.

## Known Pitfalls

- Do not hardcode `~/.hermes`; use profile-aware helpers.
- Do not introduce new `simple_term_menu`; use curses UI.
- Do not use ANSI erase-to-EOL (`\033[K`) in spinner/display code; use space padding.
- `_last_resolved_tool_names` is process-global and is saved/restored around subagents.
- Tool schema descriptions should not mention tools from other toolsets unless added dynamically in `get_tool_definitions()`.
- Gateway has two message guards; approval/control commands must bypass both.
- Absence of `__init__.py` in some trees may be intentional/load-bearing.
- Unused code was often dead for a reason; E2E-test before wiring it in.
- Tests must redirect `HERMES_HOME`; profile tests should also mock `Path.home()`.

## Context-Size Contract

Keep this root file at or below 20,000 characters so it fits Hermes' smallest automatic context-file budget without truncation. Put detailed rationale and subsystem notes in the expanded reference. `scripts/check_agents_context.py` enforces the size, canonical reference-line, and reference-presence contract in CI.

## Expanded Reference

Read [`docs/AGENTS_REFERENCE.md`](docs/AGENTS_REFERENCE.md) for the detailed version of this guide, including:

- Full contribution rubric and PR close/review rationale.
- Detailed project layout and architecture tables.
- TUI, dashboard, desktop, plugin, memory-provider, and model-provider internals.
- Cron/Kanban details and hardening invariants.
- Skill authoring standards and testing rationale.
- Full known-pitfalls list with examples.