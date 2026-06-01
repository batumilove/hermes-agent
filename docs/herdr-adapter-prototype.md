# Hermes → Herdr Adapter Prototype Spec

## Goal

Use Herdr as the durable PTY/session substrate for spawned Hermes agents. Hermes keeps orchestration logic; Herdr owns panes, TUI, SSH-disconnect persistence, remote thin-client rendering, and approval-menu interaction.

## Tested Herdr baseline

- Herdr: `0.6.6`, protocol `12`
- Server host: `herdr-test`, Tailscale `100.96.90.117`
- Remote client host: `herdr-client`, Tailscale `100.74.176.127`
- Socket API verified: `ping`, `workspace.list`, `pane.list`, `pane.read`, `pane.send_input`
- CLI verified: `agent start`, `pane read`, `wait agent-status`, `pane send-keys`
- Remote TUI verified: `herdr --remote ubuntu@100.96.90.117`

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
2. `herdr_pane_send_text(..., submit=True)`
3. wait for `working`
4. wait for `idle`
5. sleep briefly to let the final render flush
6. read with `recent-unwrapped`

This avoids the observed race where `idle` is emitted before the final answer is visible in `pane read`.

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
- `herdr_run_prompt`
- `herdr_wait_status`
- `herdr_approval`

This is intentionally CLI-backed for the first pass. The next hardening step is to add a socket transport, which avoids parsing CLI JSON and can support remote socket forwarding more directly.

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
