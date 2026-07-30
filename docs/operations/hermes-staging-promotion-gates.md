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

Status: **PASS on 2026-07-03 after approved VM start**

Evidence:

- Fresh evidence artifact: `/home/ubuntu/infra-ops/docs/operations/staging-gateway-cron-smoke-evidence-20260703.md`
- VM `429` / `hermes-staging-01` was started on `proxmox-dell` after explicit user approval (`start vm`).
- Proxmox config and guest identity matched:
  - host: `proxmox-dell` / `192.168.10.10`
  - VMID/name: `429` / `hermes-staging-01`
  - MAC: `62:9A:27:D7:56:86`
  - DMI/product UUID: `34fed008-76f3-4719-9d03-c95bd6e6eaa6`
  - Tailscale IP: `100.112.103.69`
  - LAN IP after start: `192.168.10.238`
  - `qemu-guest-agent` active
  - `tailscaled` active
- SSH host key was reconciled only after Proxmox config, guest DMI UUID, MAC, hostname, and Tailscale IP matched.
- Current ED25519 fingerprint for `hermes-staging-01`:
  - `SHA256:8e196QDWSEVV7lAi3VZFFzjFSMcYDZZWI4pBI1Em+Bo`

### Gate 3 — Fresh staging smokes

Status: **PASS on 2026-07-03**

Evidence:

- Fresh evidence artifact: `/home/ubuntu/infra-ops/docs/operations/staging-gateway-cron-smoke-evidence-20260703.md`
- Gateway smoke: PASS
- Cron smoke: PASS
- Cron marker verified at `/home/hermes-staging/.hermes-staging/staging-smoke/staging-cron-smoke-20260703143416.txt`
- Telegram E2E remained disabled because staging bot/chat were not provided or approved.
- No production Telegram token/chat was used.
- No production gateway/profile/live-feed action was performed.

### Gate 4 — Production proposal and approved narrow restart

Status: **APPROVED NARROW RESTART EXECUTED; RECOVERED WITH TRANSIENT TELEGRAM DEGRADATION OBSERVED**

Proposal:

```text
/home/ubuntu/.hermes/hermes-agent/docs/operations/hermes-staging-to-production-proposal-20260703.md
```

The user explicitly approved the narrow proposed scope by replying `Approved` on 2026-07-03. The executed scope was limited to restarting the default production `hermes-gateway.service` only, with no config/env/token/profile/model changes and no restart of other profile gateways.

Execution/post-restart evidence:

- Pre-restart PID: `1066434`
- Post-restart PID: `1530593`
- Post-restart service start: `Fri 2026-07-03 22:03:30 UTC`
- Post-restart state: `active/running`
- `NRestarts=0`
- Kanban `hermes-staging-daytona`: still 10 `done`, 0 `running`, 0 `ready`, 0 `todo`, 0 `blocked`
- Telegram reply path recovered, but logs showed transient `getUpdates` polling wedge recovery, `send_path_degraded`, and one failed-send-after-retries line.
- No additional restart or production mutation was performed during the post-restart audit.

## Current verdict

- Staging canary historical evidence: **PASS, evidence exists**
- Audit trail/documentation: **corrected by this file**
- Current staging VM identity/SSH trust: **PASS on 2026-07-03 after approved VM start and host-key reconciliation**
- Fresh staging canary: **PASS on 2026-07-03 for gateway and cron smokes; Telegram E2E not tested**
- Approved narrow production restart: **EXECUTED; service active/running afterward**
- Telegram after restart: **RECOVERED, but not clean-green because transient polling/send degradation was observed**
- Further production action: **BLOCKED pending separate explicit human approval**

## Staging diagnostic helper promotion boundary

The root-owned staging diagnostic transaction helper must remain dormant until
all of these independent gates pass: exact commit/tree manifest and staged
root-owned installer/helper/unit/tmpfiles hash and mode readback; external
installer-digest verification before first privileged execution; boot-recreated shared-lock proof;
explicit sudo authorization; no-mutation request rejection; crash/reboot
restore canaries for every durable state; one 60-second live diagnostic gate;
and only then workflow activation. Activation requires both the default-off
`HERMES_STAGING_DIAGNOSTICS_ENABLED=true` environment variable and a per-run
`staging_stop_start_ack=staging-stop-start-authorized`, which explicitly
authorizes controlled staging gateway stop/start transitions to enable and
restore diagnostics. The workflow supplies bounded JSON on stdin
to one exact no-argument sudo command. It cannot select paths, commands,
container/image identity, recovery mode, or an unbounded duration.

Recovery state remains under `/var/lib/hermes-staging-diagnostics` and is never
stored in runtime-owned Hermes data. `ARMED` is durable before mutation. Any
interruption or ambiguous journal state is restore-only: restore exact original
bytes/existence/uid/gid/mode, restart with bounded retries, verify health and
effective diagnostics false, and never resume collection.

This is a correctness remediation, not a least-privilege completion.
`hermes-deploy` remains in the Docker group, which is root-equivalent; therefore
privilege containment remains **FAIL** until a separate reviewed deployment
helper replaces direct Docker access and removes that membership. No group
change is part of this candidate.

Rollback order is mandatory: revoke sudoers first; keep recovery available until
all transactions are `RESTORED` or safely `ABORTED`; restore and verify; revert workflow activation;
then remove timer/unit/helper. Docker-group access must not be added back as a
rollback convenience.
