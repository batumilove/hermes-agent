# Safe Compaction Plan — Top 3 Context-Burning Skills

**Date:** 2026-07-03
**Author:** Kanban task t_708375a8
**Parent audit:** `docs/skill-oversized-audit-2026-07-03.md`
**Scope:** Proposal only. No skill edits made. This document defines exact before/after structure, token savings, risk notes, and file paths for a later approved apply card.

---

## Executive Summary

The three largest context-burning skills consume **17.4M loaded tokens** across **948 views** in 30 days. All three follow the same disease: a monolithic SKILL.md where inline reference descriptions, per-incident playbooks, and deep-dive command catalogs have accreted into the main body. The cure in all three cases is a **router pattern** — keep trigger guidance, safety pitfalls, and decision routing in SKILL.md; move long patterns, playbooks, and deep-dive procedures to linked reference files that the agent loads only when the specific situation matches.

| Skill | Current Size | Target Size | Savings | Loaded Tok Saved (30d est.) |
|-------|-------------|-------------|---------|---------------------------|
| self-hosted-service-operations | 84K chars / 21K tok | ~12K chars / 3K tok | ~72K chars / ~18K tok | ~5.6M |
| kanban-orchestrator | 66K chars / 16.5K tok | ~14K chars / 3.5K tok | ~52K chars / ~13K tok | ~6.0M |
| hermes-agent | 64K chars / 16K tok | ~15K chars / 3.7K tok | ~49K chars / ~12.3K tok | ~4.0M |
| **Total** | **214K / 53.5K tok** | **~41K / ~10.2K tok** | **~173K / ~43.3K tok** | **~15.6M tokens** |

Each load of any of these skills would drop by **~75-80%**. At 948 combined loads/30d, that is approximately **15.6M tokens saved** — roughly 90% of the current load cost of these three skills.

---

## Skill 1: self-hosted-service-operations (84,270 chars → ~12,000 chars)

**Path (profile):** `~/.hermes/profiles/repo-pm-glm-5-1/skills/devops/self-hosted-service-operations/SKILL.md`
**Path (sync-repo):** `~/.hermes/cache/hermes-skills-sync-repo/devops/self-hosted-service-operations/SKILL.md`
**Sync status:** DIVERGED — profile has 14K chars more than sync-repo (local additions not yet published)
**Views (30d):** 516 | **Loaded tokens (30d):** 6,527,702

### Diagnosis

The SKILL.md has two mega-sections consuming 98% of its body:

1. **"Verification" section (L27-233, ~52K chars / 13K tokens):** A monolithic wall of 100+ inline reference descriptions. Each is a 200-500 word paragraph describing a reference file's contents. This is a **directory listing masquerading as skill content**. The agent does not need to read these descriptions on every load — it needs a one-line trigger hint and the filename.

2. **"Common Pitfalls" section (L240-293, ~28K chars / 7K tokens):** 30 numbered pitfall entries, several running 500-2000 words each (especially #3b about Harbor proxy cache at ~2000 chars, #18b about Tailscale subnet hijack at ~2500 chars). Many are deep-dive incident postmortems, not concise safety pitfalls.

### Before/After Structure

**BEFORE (84K chars):**
```
SKILL.md (84K chars)
├── Frontmatter + Overview + When to Use + Platform Recipes (~1.5K chars) ← KEEP
├── Verification section (~52K chars) ← REWRITE: 100+ paragraph descriptions → concise index
├── k3s/Tart/Tailscale migrations (~1.4K chars) ← KEEP (brief)
├── Common Pitfalls (~28K chars) ← SPLIT: safety-critical stays, deep-dives → references
├── SQLite WAL Pitfall + Verification Checklist (~1K chars) ← KEEP
└── 110 linked reference files (848K chars total) ← UNCHANGED
```

**AFTER (~12K chars):**
```
SKILL.md (~12K chars)
├── Frontmatter + Overview + When to Use + Platform Recipes (~1.5K chars) ← UNCHANGED
├── Reference Index (NEW ~4K chars) ← One-line-per-entry indexed by category:
│   ├── k3s/Harbor/Longhorn: "For Harbor proxy cache: use references/k3s-harbor-proxy-cache-mirrors.md"
│   ├── Proxmox LXC/VM: "For LXC single-binary: references/proxmox-lxc-single-binary-service.md"
│   ├── Networking/Traefik/Tailscale: "For Traefik/Tailscale troubleshooting: references/tailscale-traefik-infisical-troubleshooting.md"
│   ├── Agent Vault/Infisical: "For Agent Vault on LXC: references/proxmox-agent-vault-lxc-operations.md"
│   ├── Backup/Recovery: "For backup audits: references/k3s-app-backup-coverage-and-repair.md"
│   └── ... (100+ entries, but ONE LINE EACH, not one PARAGRAPH each)
├── Critical Safety Pitfalls (~4K chars) ← Top 10 distilled to 2-3 sentences each:
│   ├── #0: Verify hostname+RAM on SSH connect
│   ├── #3b: Harbor proxy requires auth + registries.yaml + rolling restart
│   ├── #6: Backup audit: verify mechanism, not just file existence
│   ├── #11: Never broad-delete pods with negative label selectors
│   ├── #16: Don't confuse reachable host with healthy k8s node with healthy storage
│   ├── #18b: Tailscale accepted routes can hijack LAN subnet
│   ├── #24: Tailscale DERP relay ≠ etcd-safe latency
│   └── (3 more)
├── Full Pitfall Catalog pointer → "references/pitfall-catalog.md" for all 30 entries
└── Verification Checklist (~1K chars) ← UNCHANGED
```

**NEW reference file:** `references/pitfall-catalog.md` (~28K chars) — contains the full text of all 30 pitfalls. Loaded only when the agent encounters a situation matching a pitfall and needs the full detail.

### Estimated Savings
- **Per-load:** 21K → 3K tokens = **~18K tokens saved per load**
- **30-day total (516 views):** ~9.3M tokens saved
- **Conservative estimate (agent loads pitfall-catalog 30% of the time, avg 7K tok):** ~5.6M net savings

### Risk Notes
- **LOW RISK.** The reference files already exist and are individually complete. The only change is rewriting the Verification section from paragraphs to one-line index entries.
- The `description` frontmatter must stay unchanged — it controls trigger matching.
- **Local divergence:** The profile copy has 14K chars of local additions not in the sync-repo. These local additions should be published to the sync-repo first (via `publish-local-skill.sh`) before any compaction, to avoid losing them.
- Pitfall entries that serve as **safety guardrails** (e.g., "never broad-delete pods") must stay in SKILL.md even if shortened. Only the deep-dive explanation moves to the catalog.
- The existing 110 reference files are NOT modified, moved, or renamed — only the SKILL.md index pointing to them changes shape.

---

## Skill 2: kanban-orchestrator (66,363 chars → ~14,000 chars)

**Path (profile):** `~/.hermes/profiles/repo-pm-glm-5-1/skills/devops/kanban-orchestrator/SKILL.md`
**Path (sync-repo):** `~/.hermes/cache/hermes-skills-sync-repo/devops/kanban-orchestrator/SKILL.md`
**Sync status:** IN SYNC
**Views (30d):** 647 | **Loaded tokens (30d):** 7,731,565

### Diagnosis

Three sections consume 78% of the body:

1. **"Common patterns" section (L291-340, ~12K chars / 3K tokens):** 15 detailed pattern descriptions, each a multi-paragraph playbook (DHCP/DNS cleanup, approval-gate fan-out, infra migration audit, etc.). These are scenario-specific runbooks, not core orchestrator knowledge.

2. **"Verify service endpoints" section (L375-470, ~18.6K chars / 4.6K tokens):** The largest single section. Contains 6 sub-blocks about artifact verification paths, corrupt DB recovery/salvage (very detailed SQLite walkthrough), verification card access paths, rejected-workaround routing, controller-side fix-blocker flow, and waived-gate handling. This is a collection of **operational incident learnings** grafted into the orchestrator skill.

3. **"Controller-side patterns" (L421-605, ~12K chars / 3K tokens):** Review-required acceptance flows, blocked-parent-child dynamics, superseded paths, profile auth, monitoring work, model-routing fixes, reference loading map. Again, scenario-specific controller playbooks.

The **core decomposition playbook** (L141-289, Steps 0-6) is only ~8K chars and is the actual must-read content.

### Before/After Structure

**BEFORE (66K chars):**
```
SKILL.md (66K chars)
├── Title + When to Use + Anti-temptation rules + Roster (~3K chars) ← KEEP
├── Decomposition Playbook Steps 0-6 (~8K chars) ← KEEP (core content)
├── Common Patterns (~12K chars) ← SPLIT into reference
├── Verify Endpoints / Artifact Publishing / Corrupt DB (~18.6K chars) ← SPLIT into reference
├── Controller-side patterns (~12K chars) ← SPLIT into reference
├── Pitfalls (~4.5K chars) ← KEEP top 5, move rest to reference
├── Profile auth + Monitoring + Model routing (~8K chars) ← SPLIT into reference
└── Reference loading map (~0.5K chars) ← KEEP + EXPAND
```

**AFTER (~14K chars):**
```
SKILL.md (~14K chars)
├── Title + When to Use + Anti-temptation rules + Roster (~3K chars) ← UNCHANGED
├── Decomposition Playbook Steps 0-6 (~8K chars) ← UNCHANGED
├── Top 5 Pitfalls (~1.5K chars) ← Distilled from current 4.5K:
│   ├── "Don't do the work yourself"
│   ├── "Verify board health + spawnable profiles before dispatch"
│   ├── "Workers can't read your chat — pass all context"
│   ├── "Don't claim phantom task IDs"
│   └── "Block ≠ complete — use review-required for code changes"
├── Pattern Index (NEW ~1K chars) ← One-line pointers:
│   ├── "DHCP/DNS cleanup board → references/dhcp-dns-cleanup-board.md"
│   ├── "Approval-gate fan-out → references/approval-gate-blocker-fanout.md"
│   ├── "Corrupt DB recovery → references/kanban-db-recovery.md" (NEW)
│   ├── "Controller review flow → references/review-gated-blocked-tasks.md"
│   └── ... (15+ entries)
└── Reference loading map (~0.5K chars) ← EXPANDED with new references
```

**NEW reference files:**
- `references/kanban-common-patterns.md` (~12K chars) — all 15 common patterns, verbatim from current Common Patterns section
- `references/kanban-controller-playbook.md` (~12K chars) — corrupt DB recovery, artifact publishing, verification access paths, rejected-workaround routing, fix-blocker flow, waived-gate handling, blocked-parent-child dynamics, superseded paths
- `references/kanban-profile-auth-and-monitoring.md` (~8K chars) — profile auth for GitHub work, monitoring Kanban work, model-routing fixes

### Estimated Savings
- **Per-load:** 16.5K → 3.5K tokens = **~13K tokens saved per load**
- **30-day total (647 views):** ~8.4M tokens saved
- **Conservative estimate (agent loads 1 pattern ref 40% of the time, avg 4K tok):** ~6.0M net savings

### Risk Notes
- **LOW-MODERATE RISK.** The decomposition playbook (the core routing logic) stays untouched. Only scenario-specific patterns move.
- The 19 existing reference files remain unchanged. New reference files contain content currently in SKILL.md — pure extraction, no rewriting.
- **Corrupt DB recovery** is the riskiest extraction: it's operational safety content. Keep a 2-sentence summary + pointer in SKILL.md pitfalls: "If Kanban DB is corrupt, preserve the original, use Python sqlite3 for salvage, see references/kanban-db-recovery.md for full procedure."
- Anti-temptation rules MUST stay in SKILL.md — they are behavioral guardrails, not reference material.
- This skill is in sync with the sync-repo, so extraction can proceed cleanly via the publish pipeline.

---

## Skill 3: hermes-agent (63,854 chars → ~15,000 chars)

**Path (profile):** `~/.hermes/profiles/repo-pm-glm-5-1/skills/autonomous-ai-agents/hermes-agent/SKILL.md`
**Path (sync-repo):** `~/.hermes/cache/hermes-skills-sync-repo/autonomous-ai-agents/hermes-agent/SKILL.md`
**Bundled in repo:** `~/.hermes/hermes-agent/skills/autonomous-ai-agents/hermes-agent/SKILL.md` (51,586 chars — smaller, upstream version)
**Sync status:** Profile = sync-repo (IN SYNC). Profile ≠ bundled (local customization layer).
**Views (30d):** 332 | **Loaded tokens (30d):** 5,018,352

### Diagnosis

This skill is **protected/bundled** — it ships with the hermes-agent repo. The profile version (64K) is larger than the bundled upstream version (52K) because of local customization (deployment-specific paths, Agent Vault broker details, Telegram gateway debugging, cron hardening, etc.).

The skill has **86 headers** across 1,088 lines. It is effectively a **mini-manual** covering: CLI reference, configuration, tools, MCP, gateway, sessions, cron, webhooks, profiles, credential pools, Agent Vault, patch collection, slash commands, key paths, voice/STT/TTS, multi-agent spawning, troubleshooting, contributor reference, and a reference loading map.

The largest sections:
- **CLI Reference (L57-370, ~18K chars):** Global flags, chat, configuration, tools/skills, MCP, gateway, sessions, cron, webhooks, desktop kanban, profiles, credential pools, Agent Vault, patch workflow. This is a **command manual**.
- **Safe Update Workflow (L463-536, ~7.7K chars):** Step-by-step deployment procedure with commands. Important for this specific deployment but not needed on every load.
- **Patch Collection Workflow (L375-462, ~5.9K chars):** The live-deploy patch-stack update procedure.
- **Slash Commands (L537-619, ~2.7K chars):** In-session command reference.
- **Key Paths & Config (L620-751, ~5.7K chars):** Config file locations, provider setup, toolsets, Nous tool gateway.
- **Troubleshooting (L878-951, ~6.5K chars):** Including a large gateway issues section (~3.5K chars).

### Before/After Structure

**BEFORE (64K chars):**
```
SKILL.md (64K chars)
├── Title + Quick Start (~2.2K chars) ← KEEP
├── CLI Reference (~18K chars) ← SPLIT: keep overview, move detail to references
├── Patch Collection / Safe Update (~8.2K chars) ← MOVE to reference
├── Slash Commands (~2.7K chars) ← MOVE to reference
├── Key Paths & Config (~5.7K chars) ← KEEP condensed version (~2K)
├── Voice & Transcription (~1.6K chars) ← KEEP (short)
├── Multi-Agent Spawning (~3.5K chars) ← KEEP condensed (~1K)
├── Troubleshooting (~6.5K chars) ← MOVE to reference
├── Where to Find Things + Contributor (~3K chars) ← KEEP condensed
└── 20 linked reference files (88K chars total) ← UNCHANGED
```

**AFTER (~15K chars):**
```
SKILL.md (~15K chars)
├── Title + Quick Start (~2.2K chars) ← UNCHANGED
├── CLI Command Index (~2K chars) ← Condensed table:
│   ├── `hermes` — interactive chat
│   ├── `hermes -q "query"` — single query
│   ├── `hermes setup` — setup wizard
│   ├── `hermes config` — view/edit config
│   ├── `hermes tools` — manage toolsets
│   ├── `hermes status` — health check
│   └── "Full CLI reference → references/cli-reference.md"
├── Key Paths & Config (condensed ~2K chars) ← Config file location + provider overview only:
│   ├── ~/.hermes/config.yaml — main config
│   ├── ~/.hermes/profiles/<name>/ — per-profile data
│   ├── "Full config/provider details → references/key-paths-config.md"
├── Agent Vault (condensed ~1K chars) ← Keep the "what it is + when to use" summary:
│   └── "Full broker setup → references/agent-vault-credential-broker.md" (already exists)
├── Voice & STT/TTS (~1.6K chars) ← UNCHANGED (already compact)
├── Multi-Agent Spawning (condensed ~1K chars) ← Overview + when-to-use:
│   └── "Full examples → references/multi-agent-spawning.md" (NEW)
├── Quick Troubleshooting (~1.5K chars) ← Top 5 issues, 2-3 sentences each:
│   ├── Voice not working
│   ├── Tool not available
│   ├── Skills not showing
│   ├── Changes not taking effect
│   └── "Full troubleshooting → references/troubleshooting-guide.md" (NEW)
├── Contributor Quick Reference (condensed ~1.5K chars) ← Project layout + key rules only
└── Reference loading map (~0.5K chars) ← EXPANDED
```

**NEW reference files:**
- `references/cli-reference.md` (~18K chars) — full CLI command reference, flags, config, tools, MCP, gateway, sessions, cron, webhooks, profiles, credential pools
- `references/update-and-patch-workflow.md` (~8.2K chars) — safe update workflow + patch collection procedure
- `references/slash-commands.md` (~2.7K chars) — in-session slash command reference
- `references/troubleshooting-guide.md` (~6.5K chars) — full troubleshooting including gateway issues
- `references/multi-agent-spawning.md` (~3.5K chars) — full multi-agent examples, tmux patterns, session resume

### Estimated Savings
- **Per-load:** 16K → 3.7K tokens = **~12.3K tokens saved per load**
- **30-day total (332 views):** ~4.1M tokens saved
- **Conservative estimate (agent loads 1 ref 40% of time, avg 5K tok):** ~4.0M net savings (many troubleshooting/config lookups will still need the reference)

### Risk Notes
- **MODERATE RISK — protected/bundled skill.** This skill ships with hermes-agent and has an upstream version. The local profile copy contains deployment-specific customizations not in upstream.
- **Constraint: upstream sync.** Any compaction must account for the `hermes-skills` sync pipeline. The compacted SKILL.md + new reference files should be published to the sync-repo via `publish-local-skill.sh`, not edited directly in the hermes-agent repo.
- **Constraint: the description must not change.** The trigger "Configure, extend, or contribute to Hermes Agent" must remain — it controls when the skill loads.
- The **Quick Start** and **overview** sections are the most frequently needed content — they MUST stay in SKILL.md.
- The **Safe Update Workflow** is deployment-specific and critical for this environment — moving it to a reference is safe because the agent will load it when the task involves updating Hermes.
- **Version drift:** The bundled upstream version (52K) is already smaller. If upstream compacts independently, the sync pipeline must handle the merge. The local-vs-upstream delta (12K chars of customization) should be preserved as reference files that supplement the upstream base.

---

## Cross-Cutting Considerations

### Skill Loader Mechanics
Hermes loads SKILL.md content into context when `skill_view(name)` is called. Linked reference files are loaded **on demand** when the agent calls `skill_view(name, file_path='references/...')`. The compaction strategy relies on this two-level loading: the router (SKILL.md) is always loaded when the skill triggers; the deep-dive content loads only when the agent identifies the specific scenario and requests it.

**Key assumption:** the agent is smart enough to read the one-line index entry and decide "I need the full details for this scenario" → load the reference file. This is the designed behavior. The risk is that a model might skip loading the reference and act on the one-liner alone. Mitigation: the index entries should be phrased as **triggers** ("When X happens, load Y"), not as summaries that might be mistaken for complete guidance.

### Sync Pipeline Impact
- `kanban-orchestrator` and `hermes-agent` are in sync between profile and sync-repo.
- `self-hosted-service-operations` has 14K chars of local divergence. This should be reconciled (publish local additions to sync-repo) BEFORE compaction to avoid losing unpublished content.
- After compaction, the new reference files must be published to the sync-repo alongside the compacted SKILL.md.

### Fixed Index Impact
The `<available_skills>` catalog only includes skill name + description (one line each), not body content. Compacting SKILL.md bodies does NOT reduce fixed index cost. The audit's 4,243 tokens/turn fixed index cost for repo-ops-glm-5-1 is driven by the number of skills (274), not their body sizes. Profile isolation (removing dormant skills) is the lever for fixed index cost; body compaction is the lever for per-load cost.

### Recommended Apply Order
1. **self-hosted-service-operations** — highest absolute savings, lowest risk (reference files already exist, only the index section changes shape)
2. **kanban-orchestrator** — second highest savings, low risk (clean extraction of pattern sections)
3. **hermes-agent** — protected skill, requires controller approval and sync pipeline coordination

### Apply Card Specification (for later approved task)

Each skill compaction should be a separate Kanban card with:
- **Assignee:** `repo-pm-glm-5-1` (skill maintenance profile)
- **Allowed actions:** edit SKILL.md, create new reference files, publish via sync pipeline
- **Forbidden:** changing frontmatter description, deleting existing reference files, editing another profile's skills
- **Verification:** `hermes skill-usage --days 1` after deploy to confirm the skill still loads correctly; spot-check that `skill_view(name)` returns the compacted body and `skill_view(name, file_path='references/...')` returns the extracted content
- **Rollback:** keep the original SKILL.md as `SKILL.md.pre-compaction.bak` until verified

---

## Appendix: Measured Data Summary

| Skill | Body Chars | Body Tokens | Views (30d) | Loaded Tok (30d) | Linked Files | Linked Chars |
|-------|-----------|-------------|-------------|-----------------|-------------|-------------|
| self-hosted-service-operations | 83,985 | 20,996 | 516 | 6,527,702 | 110 | 848,021 |
| kanban-orchestrator | 66,599 | 16,650 | 647 | 7,731,565 | 19 | 51,792 |
| hermes-agent | 64,106 | 16,027 | 332 | 5,018,352 | 20 | 87,724 |
| **Total** | **214,690** | **53,673** | **948** | **17,277,619** | **149** | **987,537** |

Source: `hermes skill-usage --days 30 --json` + filesystem scan
Audit report: `docs/skill-oversized-audit-2026-07-03.md`
