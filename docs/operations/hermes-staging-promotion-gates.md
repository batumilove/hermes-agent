# Hermes staging promotion gates

Date: 2026-07-03
Status: **STAGING CANARY EVIDENCE RECORDED; PRODUCTION BLOCKED**

This document is a corrective audit record for the `hermes-staging-daytona` Kanban board. It was created after verification found that earlier task summaries claimed this file had been updated, but the file did not exist in the live checkout, active worktrees, or git history.

## Scope

Board: `hermes-staging-daytona`

Relevant tasks:

- `t_6221bb4c` — provision/bootstrap persistent staging VM
- `t_c45deb96` — run staging gateway and cron canary smokes
- `t_d4fc15c4` — review staging VM canary evidence

This document records the promotion state from verified local artifacts and live read-only checks only. It does **not** approve production changes.

## Verified board state

As of the corrective verification:

- Board `hermes-staging-daytona` exists.
- Board status is all done: 10 `done`, 0 `running`, 0 `ready`, 0 `todo`, 0 `blocked`.
- `t_d4fc15c4` is `done`, but its summary over-claimed that this document had been updated before it actually existed.

## Verified staging evidence artifact

Smoke evidence exists at:

```text
/home/ubuntu/infra-ops/docs/operations/staging-gateway-cron-smoke-evidence-20260611.md
```

The evidence file records:

- Staging VM name: `hermes-staging-01`
- Tailscale IP observed during smoke: `100.112.103.69`
- Staging Hermes home: `/home/hermes-staging/.hermes-staging`
- Profiles used: `gateway-canary`, `skill-lab`
- Gateway smoke: **PASS**
- Cron smoke: **PASS**
- Telegram E2E/send: intentionally skipped
- No production Telegram token used
- Telegram send intentionally skipped
- Staging checkout used `batumi/live` fallback with `HERMES_STAGING_ALLOW_LIVE_BRANCH=1`

Evidence file metadata at verification time:

```text
Path: /home/ubuntu/infra-ops/docs/operations/staging-gateway-cron-smoke-evidence-20260611.md
Size: 3534 bytes
Modified: 2026-06-11 15:16:35 +0000
Mode: 0600
Owner: ubuntu:ubuntu
```

## Staging VM facts

Historical evidence proves the VM existed during the 2026-06-11 canary run:

- VMID/name from task evidence: `429` / `hermes-staging-01`
- Host from historical evidence: Proxmox Intel / node `server`
- Tailscale IP recorded during successful smoke: `100.112.103.69`
- LAN IP recorded during provisioning handoff: `192.168.10.225`
- Historical VM config was captured in `/home/ubuntu/infra-ops/docs/operations/hermes-staging-vm-logs-20260611-123020/02-create-vm.txt`, including:
  - `name: hermes-staging-01`
  - `description: Dedicated persistent Hermes staging VM; task t_6221bb4c; Ubuntu 24.04; Tailscale-only target; no production Telegram credentials.`
  - `net0: virtio=62:9A:27:D7:56:86,bridge=vmbr0`
  - `smbios1: uuid=34fed008-76f3-4719-9d03-c95bd6e6eaa6`
  - `tags: hermes;kanban-t_6221bb4c;staging;ubuntu2404`

Expanded corrective inventory on 2026-07-03 found the VM on an additional Proxmox host that was missing from the first scan:

- Current host: `proxmox-dell` / `192.168.10.10`
- Current VMID/name: `429` / `hermes-staging-01`
- Current status: `stopped`
- Current config path: `/etc/pve/qemu-server/429.conf` on `proxmox-dell`
- Current config still matches the historical staging identity:
  - `description: Dedicated persistent Hermes staging VM; task t_6221bb4c; Ubuntu 24.04; Tailscale-only target; no production Telegram credentials.`
  - `net0: virtio=62:9A:27:D7:56:86,bridge=vmbr0`
  - `smbios1: uuid=34fed008-76f3-4719-9d03-c95bd6e6eaa6`
  - `tags: hermes;kanban-t_6221bb4c;staging;ubuntu2404`
- Other checked Proxmox hosts (`server` / `100.111.166.31`, `proxmox02` / `100.84.169.101`, and `proxmox01` / `100.90.255.19`) did not have VMID/CTID `429` or `hermes-staging-01`.
- SSH to `hermes-staging-01` currently times out because the VM is stopped.

Therefore the current staging VM placement/state is **verified as stopped on proxmox-dell**. The earlier "missing from reachable Proxmox inventory" conclusion was incomplete because `proxmox-dell` had not been included in the first focused scan.

## Production isolation state

The smoke evidence and task record support these limited claims:

- No production gateway restart/start/stop was recorded as part of the staging canary.
- No production profile write was recorded as part of the staging canary.
- No production Telegram token/chat use was recorded.
- Telegram E2E was not attempted because staging bot/chat credentials were not provided/approved.

These are evidence-scope claims only. They are not a blanket proof that production is unaffected by all possible paths.

## Exposure posture

The staging evidence claims Tailscale-only access and no public ingress, but this could not be freshly re-verified during the corrective pass because the staging VM was not live/accessible for full inspection.

Before production promotion, verify on the live staging VM:

```bash
ss -ltnup
systemctl list-units --type=service --state=running
systemctl list-unit-files | grep -Ei 'cloudflared|nginx|caddy|traefik|apache|tailscale'
tailscale ip -4
tailscale status
```

Expected promotion condition:

- Tailscale identity matches the staging VM.
- No Cloudflare tunnel service is installed/running unless explicitly approved.
- No public HTTP(S) ingress listener is present unless explicitly approved.
- Any listener is either loopback, LAN-only by design, or Tailscale-only by design.

## SSH host key blocker

Corrective verification found a host-key mismatch for `hermes-staging-01` in the controller's `known_hosts`.

Observed new ED25519 fingerprint from SSH warning:

```text
SHA256:8e196QDWSEVV7lAi3VZFFzjFSMcYDZZWI4pBI1Em+Bo
```

This must be reconciled from a trusted source before any further staging smokes or production proposal. Do **not** blindly remove and replace the known_hosts entry.

Acceptable reconciliation sources include:

- Proxmox console/QGA evidence from VMID 429 after confirming the VM host and identity.
- Tailscale device identity plus host SSH fingerprint collected from inside the verified VM.
- A documented reprovision event that explains the host key change.

## Promotion gates

### Gate 1 — Audit trail repaired

Status: **PASS after this corrective document**

Required evidence:

- Missing promotion-gates document exists.
- It explicitly records that earlier task summaries over-claimed this file.
- It records the host-key blocker and no-production-action boundary.

### Gate 2 — Staging VM identity re-verified

Status: **BLOCKED**

Required before continuing:

- Confirm current VMID/name/host for `hermes-staging-01`.
- Confirm VM is intentionally started or stopped.
- Reconcile SSH host key from trusted source.
- Confirm Tailscale identity/IP.

### Gate 3 — Fresh staging smokes

Status: **BLOCKED**

Required after Gate 2:

- Rerun gateway smoke.
- Rerun cron smoke.
- Keep Telegram E2E disabled unless staging bot/chat are explicitly provided and approved.
- Record fresh evidence path and timestamps.

### Gate 4 — Production proposal

Status: **BLOCKED**

A production proposal may be drafted only after Gates 2 and 3 pass. It must include:

- Exact production actions.
- Rollback plan.
- Telegram impact statement.
- Expected restart/downtime behavior.
- Explicit human approval gate.

No production action is approved by this document.

## Current verdict

- Staging canary historical evidence: **PASS, evidence exists**
- Audit trail/documentation: **corrected by this file**
- Current staging VM identity/SSH trust: **BLOCKED pending live re-verification**
- Fresh staging canary: **BLOCKED pending VM identity/host-key reconciliation**
- Production action: **BLOCKED pending separate proposal and explicit human approval**
