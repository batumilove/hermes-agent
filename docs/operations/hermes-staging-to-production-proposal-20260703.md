# Hermes staging to production proposal

Date: 2026-07-03T14:44:58Z
Status: **APPROVED SCOPE EXECUTED — RECOVERED WITH TRANSIENT TELEGRAM DEGRADATION OBSERVED**

This document is the required separate production proposal after the `hermes-staging-daytona` staging canary passed fresh gateway and cron smokes on 2026-07-03.

This document began as the required separate production proposal after the `hermes-staging-daytona` staging canary passed fresh gateway and cron smokes on 2026-07-03. The user later approved the narrow restart scope. That approved scope has now been executed and audited below.

## Source evidence

Fresh staging evidence:

```text
/home/ubuntu/infra-ops/docs/operations/staging-gateway-cron-smoke-evidence-20260703.md
```

Promotion gate document:

```text
/home/ubuntu/.hermes/hermes-agent/docs/operations/hermes-staging-promotion-gates.md
```

Staging verification summary:

- VM `429` / `hermes-staging-01` started on `proxmox-dell` after explicit approval.
- VM identity verified by Proxmox config, hostname, DMI/product UUID, MAC, Tailscale IP, QGA, and Tailscale daemon.
- SSH host key reconciled after identity proof.
- Gateway smoke: PASS.
- Cron smoke: PASS.
- Telegram E2E/send: not attempted.
- Production gateway/profile/token/chat/live-feed: not touched.

## Current production state inspected for this proposal

Production Hermes checkout:

```text
/home/ubuntu/.hermes/hermes-agent
branch: batumi/live
current observed head at proposal preflight: c41867cb8 docs: record fresh staging smoke pass
proposal commit after writing this document: 2c0627ab2 docs: add Hermes staging production proposal
```

Production gateway service:

```text
systemd user unit: hermes-gateway.service
state: active/running
main PID observed: 1066434
ExecStart: /home/ubuntu/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run
HERMES_HOME: /home/ubuntu/.hermes
Telegram configured: yes
Allowed Telegram user: 407304892
Home channel: 407304892
```

Relevant drop-ins observed:

```text
10-stale-pid-cleanup.conf
20-infisical-env.conf
30-oom-policy.conf
40-telegram-env.conf
override.conf
```

Other gateway profiles observed running:

```text
pa
research-news-nemotron-3-ultra-fr
wg-ops-glm-5-2
```

## Proposed production scope

The safest production action is a **production gateway refresh/restart only** after confirming the live checkout contains the staged documentation and no unexpected dirty production code is involved.

Proposed in-scope actions:

1. Re-check production gateway status and current PID.
2. Re-check git status for `/home/ubuntu/.hermes/hermes-agent`.
3. If the only relevant production checkout changes are the committed staging documentation commits, leave code unchanged.
4. Restart only the default production `hermes-gateway.service` to prove the current checkout and environment come back cleanly.
5. Verify Telegram gateway responds after restart.
6. Verify gateway logs have no startup traceback or Telegram polling failure.
7. Verify Kanban board `hermes-staging-daytona` and production session continuity remain accessible.

Explicitly out of scope unless separately approved:

- Editing production `.env`, auth files, profile configs, memory, skills, or Telegram token/chat settings.
- Changing model/provider settings.
- Updating Hermes code from upstream/fork.
- Restarting non-default profile gateways (`pa`, `research-news-nemotron-3-ultra-fr`, `wg-ops-glm-5-2`).
- Stopping/deleting staging VM 429.
- Enabling Telegram E2E on staging.
- Any Cloudflare/tunnel/network exposure changes.

## Exact proposed commands

Preflight:

```bash
cd /home/ubuntu/.hermes/hermes-agent
git status --short --branch
git log -3 --oneline -- docs/operations/hermes-staging-promotion-gates.md docs/operations/hermes-staging-to-production-proposal-20260703.md
hermes gateway status
systemctl --user status hermes-gateway --no-pager
```

Restart:

```bash
systemctl --user restart hermes-gateway.service
```

Post-restart verification:

```bash
systemctl --user status hermes-gateway --no-pager
hermes gateway status
journalctl --user -u hermes-gateway.service --since '5 minutes ago' --no-pager | tail -200
hermes kanban --board hermes-staging-daytona stats
```

Telegram impact verification:

- Send a normal user message from Telegram after restart.
- Confirm the default gateway replies in the same DM/topic.
- Confirm no stale approval/thread errors in the last 5 minutes of gateway logs.

## Expected user-visible impact

- The default Telegram gateway will be unavailable briefly during restart.
- Expected interruption: seconds to about one minute.
- Any in-flight default-gateway agent turn may be interrupted or resume depending on current gateway drain/session state.
- Other profile gateways are not intended to be restarted by this proposal.

## Rollback plan

If restart fails or Telegram becomes silent:

1. Check service status:

```bash
systemctl --user status hermes-gateway --no-pager
journalctl --user -u hermes-gateway.service --since '10 minutes ago' --no-pager | tail -300
```

2. Try a clean start if stopped:

```bash
systemctl --user start hermes-gateway.service
```

3. If the gateway fails because of the current checkout state, revert only the documentation commits if they are implicated. These commits are doc-only and should not affect runtime, so this is expected to be unnecessary:

```bash
cd /home/ubuntu/.hermes/hermes-agent
git revert --no-edit c41867cb8 79ea22f53 3537754c3 19653c820
systemctl --user restart hermes-gateway.service
```

4. If session continuity is wedged but the service is running, restart the gateway once more and verify Telegram with a fresh user-originated message.

Rollback boundaries:

- Do not rotate Telegram tokens.
- Do not delete sessions or state DBs.
- Do not reset profiles.
- Do not stop unrelated profile gateways unless explicitly approved.

## Approval gate

Production action was blocked until the user explicitly approved this exact scope.

Suggested approval phrase:

```text
Approved: restart default production hermes-gateway.service only, using the 2026-07-03 staging proposal scope. No config/env/token/profile changes; verify Telegram reply and gateway logs afterward.
```

Anything broader requires a new proposal or a revised approval scope.

## Approved production restart outcome

Approval:

```text
User approved the proposed narrow production action on 2026-07-03 by replying: "Approved".
Approved interpreted scope: restart default production `hermes-gateway.service` only; no config/env/token/profile/model changes; no restart of other profile gateways; verify status/logs/Kanban/Telegram afterward.
```

Execution evidence:

```text
Restart runner: /home/ubuntu/.hermes/tmp/approved_gateway_restart_20260703.sh
Pre-restart PID: 1066434
Post-restart PID: 1530593
Post-restart service start: Fri 2026-07-03 22:03:30 UTC
Post-restart state: active/running
NRestarts: 0
```

Post-restart read-only audit:

```text
systemctl --user show hermes-gateway.service:
  MainPID=1530593
  ExecMainStartTimestamp=Fri 2026-07-03 22:03:30 UTC
  ActiveState=active
  SubState=running
  NRestarts=0

hermes-staging-daytona Kanban board:
  done=10
  running=0
  ready=0
  todo=0
  blocked=0
```

Log verdict:

- The default gateway did restart and recover under systemd.
- Telegram delivery path recovered enough to continue this DM/topic after restart.
- The restart was **not clean-green**: post-restart logs showed transient Telegram polling/send degradation, including `getUpdates consumer appears wedged`, automatic polling restart, `send_path_degraded`, and one `Failed to deliver response after 2 retries: send_path_degraded` line.
- No additional restart or production mutation was performed during the post-restart audit.

Final production verdict for this proposal:

- Approved narrow restart scope: **EXECUTED**
- Gateway service health after audit: **ACTIVE/RUNNING**
- Telegram user-visible continuity: **RECOVERED**
- Log quality: **WARN — transient Telegram degradation observed**
- Further production action: **BLOCKED pending separate explicit approval**
