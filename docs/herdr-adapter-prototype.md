# Hermes → Herdr Adapter Prototype Spec

## Goal

Use Herdr as the durable PTY/session substrate for spawned Hermes agents. Hermes keeps orchestration logic; Herdr owns panes, TUI, SSH-disconnect persistence, remote thin-client rendering, and approval-menu interaction.

## Tested Herdr baseline

- Herdr: `0.6.6`, protocol `12`
- Server host: `herdr-test`, Tailscale `100.96.90.117`
- Remote client host: `herdr-client`, Tailscale `100.74.176.127`
- Socket path on eval VM: `/home/ubuntu/.config/herdr/herdr.sock`
- Socket API verified: `ping`, `workspace.list`, `pane.list`, `pane.read`, `pane.send_input`
- Socket protocol verified: newline-delimited JSON request `{"id":"req-1","method":"ping","params":{}}` → response `{"id":"req-1","result":{"type":"pong","protocol":12}}`
- CLI verified: `agent start`, `pane read`, `wait agent-status`, `pane send-keys`
- Remote TUI verified: `herdr --remote ubuntu@100.96.90.117`

## Adapter transport

`tools/herdr_tools.py` now uses a small transport abstraction instead of scattering socket logic through handlers:

- `HerdrSocketTransport` for direct Unix-socket calls.
- subprocess CLI fallback for operations without proven socket coverage, or when the socket is absent/fails.
- Tool handlers return the same JSON success/error envelopes and include `transport: "socket"` or `transport: "cli"` for diagnostics.

Socket path resolution is profile-safe/configurable:

1. explicit constructor argument for tests/internal callers
2. `tools.herdr.socket_path` in the active profile's `config.yaml`
3. legacy short form `herdr.socket_path`
4. default `~/.config/herdr/herdr.sock`

The socket wire format is one newline-delimited JSON object per request:

```json
{"id":"req-1","method":"workspace.list","params":{}}
```

and one JSON object response, usually:

```json
{"id":"req-1","result":{"type":"workspace_list","workspaces":[]}}
```

Timeouts, connection errors, malformed responses, and Herdr `error` responses are normalized into JSON envelopes with `success: false`, `transport: "socket"`, `method`, `socket_path`, `error`, and `error_type` where available.

## Adapter primitives

### Spawn

```bash
herdr agent start <name> --cwd <cwd> --workspace <workspace_id> --no-focus -- hermes -w
```

Return handle fields:

- `workspace_id`
- `tab_id`
- `pane_id`
- `name`
- `agent_status`
- optional `ready` when `wait_ready=true`
- optional `agent_session` when Hermes has emitted it

### Read

```bash
herdr pane read <pane_id> --source recent-unwrapped --lines <N>
```

Use `recent-unwrapped` by default. Plain `recent` wraps output and produced false negatives during 12-pane stress tests.

### Send text

```bash
herdr pane send-text <pane_id> <text>
herdr pane send-keys <pane_id> Enter   # optional submit
```

Use this to feed prompts/follow-ups into spawned interactive agents.

### Run prompt helper

`herdr_run_prompt` composes send/wait/read safely:

1. optional pre-send settle for freshly spawned panes
2. optionally poll for readiness (`herdr_wait_ready`) until the Hermes prompt/banner is visible
3. `herdr_pane_send_text(..., submit=True)`
4. wait for `working`
5. wait for `idle`
6. sleep briefly to let the final render flush
7. read with `recent-unwrapped`

This avoids both observed races: sending before a fresh child is ready, and reading before the final answer is visible in `pane read`.

### Spawn and run helper

`herdr_spawn_and_run` is the high-level orchestration helper for one-shot child work. It composes:

1. `herdr_agent_start(..., wait_ready=True)`
2. `herdr_run_prompt(pane_id=..., text=prompt, expect=expect)`
3. a bounded result envelope with `success`, `stage`, `status`, `pane_id`, `workspace_id`, `matched_expect`, `expect`, `output_excerpt`, `start`, and `run`

Schema:

```json
{
  "name": "herdr_spawn_and_run",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {"type": "string"},
      "cwd": {"type": "string"},
      "workspace_id": {"type": "string"},
      "argv": {"type": "array", "items": {"type": "string"}},
      "prompt": {"type": "string"},
      "expect": {"type": "string"},
      "ready_timeout_seconds": {"type": "number", "default": 30.0},
      "wait_working_ms": {"type": "integer", "default": 30000},
      "wait_idle_ms": {"type": "integer", "default": 60000},
      "settle_seconds": {"type": "number", "default": 2.0},
      "lines": {"type": "integer", "default": 400}
    },
    "required": ["name", "prompt", "expect"]
  }
}
```

Example call:

```json
{
  "name": "worker",
  "cwd": "/repo",
  "argv": ["hermes", "-w"],
  "prompt": "Do task...",
  "expect": "DONE"
}
```

Example result:

```json
{
  "success": true,
  "stage": "complete",
  "status": "succeeded",
  "pane_id": "pane1",
  "workspace_id": "ws1",
  "matched_expect": true,
  "expect": "DONE",
  "output_excerpt": "...DONE",
  "start": {"success": true, "pane_id": "pane1", "workspace_id": "ws1"},
  "run": {"success": true, "stage": "complete", "matched_expect": true}
}
```

### Wait

```bash
herdr wait agent-status <pane_id> --status blocked --timeout 30000
herdr wait agent-status <pane_id> --status idle --timeout 30000
```

Status classification:

- `idle` → ready/completed/currently not working
- `working` → running
- `blocked` → needs approval input
- `unknown` → needs resume/restart; common after full reboot because layout/session metadata restores but live processes do not

### Approval

Hermes approval in a Herdr pane is a menu, not a yes/no prompt.

- allow once: `Enter`
- allow session: `Down Enter`
- allow always: `Down Down Enter`
- deny: `Down Down Down Enter`

Do not send `n`, `x`, or textual yes/no values.

## Prototype module

The first in-repo prototype is `tools/herdr_tools.py`, exposed as an opt-in `herdr` toolset:

- `herdr_agent_start`
- `herdr_pane_read`
- `herdr_pane_send_text`
- `herdr_wait_ready`
- `herdr_run_prompt`
- `herdr_spawn_and_run`
- `herdr_wait_status`
- `herdr_approval`
- `herdr_workspace_list`
- `herdr_workspace_close`
- `herdr_pane_close`

### List workspaces

```bash
herdr workspace list
```

Returns workspace metadata including `workspace_id`, `label`, `pane_count`, `tab_count`, and `active_tab_id`. The adapter extracts the `workspaces` array from the JSON envelope.

### Close workspace

```bash
herdr workspace close <workspace_id>
```

Closes the workspace and all contained panes/tabs. Returns `success: true` with the raw CLI response, or an error envelope on failure (e.g. workspace not found).

### Close pane

```bash
herdr pane close <pane_id>
```

Closes a single pane. Returns `success: true` with the raw CLI response, or an error envelope on failure (e.g. pane not found).

Socket-backed operations are preferred when `/home/ubuntu/.config/herdr/herdr.sock` (or the configured socket path) is present. CLI fallback remains in place for spawn/wait/approval and for lifecycle calls without verified socket coverage.

## Recovery policy

- If pane status is `blocked`, adapter should surface the blocked handle and wait for explicit `herdr_approval` action.
- If pane status is `unknown`, adapter should not pretend the agent is live. It should either:
  - restart a new agent in the same workspace, or
  - report `needs_resume` and preserve the old pane as scrollback/history.
- After full reboot, restored panes/layout are useful context but not proof that child agent processes survived.

## Remote usage

From a client with Herdr installed and Tailscale access:

```bash
herdr --remote ubuntu@100.96.90.117
```

Fallback:

```bash
ssh ubuntu@100.96.90.117
export PATH="$HOME/.local/bin:$PATH"
herdr
```

## Verification commands

```bash
python -m pytest tests/tools/test_herdr_tools.py -q
python -m py_compile tools/herdr_tools.py tests/tools/test_herdr_tools.py toolsets.py
```

Runtime remote smoke:

```bash
ssh ubuntu@100.96.90.117 'export PATH=$HOME/.local/bin:$PATH; herdr status server'
ssh ubuntu@100.96.90.117 'export PATH=$HOME/.local/bin:$PATH; herdr pane list | jq ".result.panes|length"'
```
