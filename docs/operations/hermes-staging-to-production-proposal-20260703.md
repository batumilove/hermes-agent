# Hermes staging to production proposal

Date: 2026-07-03T14:44:58Z
Status: **PROPOSAL ONLY — NOT APPROVED**

This document is the required separate production proposal after the `hermes-staging-daytona` staging canary passed fresh gateway and cron smokes on 2026-07-03.

No production action is approved by this document. Production action requires an explicit human approval that names the approved scope.

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

Production action is blocked until the user explicitly approves this exact scope.

Suggested approval phrase:

```text
Approved: restart default production hermes-gateway.service only, using the 2026-07-03 staging proposal scope. No config/env/token/profile changes; verify Telegram reply and gateway logs afterward.
```

Anything broader requires a new proposal or a revised approval scope.
