# Oversized Skills & Historical Load-Cost Audit

**Date:** 2026-07-03
**Profiles:** repo-ops-glm-5-1 (this profile) + default (Telegram gateway)
**Telemetry window:** 30 days
**Source:** `hermes skill-usage --days 30 --json` (both profiles)
**Scope:** All installed skills (profile, default, bundled). No raw skill bodies included.

---

## Executive Summary

| Metric | repo-ops-glm-5-1 | default | Combined |
|--------|-----------------|---------|----------|
| Sessions (30d) | 241 | 4,651 | 4,892 |
| Sessions loading ≥1 skill | 143 (59%) | 1,360 (29%) | 1,503 |
| Total skill_view calls | 317 | 5,772 | 6,089 |
| Fixed index tokens/turn (avg) | 4,243 | 1,690 | — |
| Loaded skill tokens/skill-load session | 19,006 | 26,639 | — |
| Total unique skills (active) | — | — | 255 |
| Total unique skills (archived) | — | — | 106 |
| Skills never loaded in 30d | — | — | 213 (56%) |

**Key finding:** 213 of 380 unique skills (56%) were never loaded in the
last 30 days. The top 3 skills (self-hosted-service-operations,
kanban-orchestrator, hermes-agent) alone consumed **17.4M loaded tokens**
across 948 views. Six skills exceed 50K chars each — these dominate both
fixed-index and per-load context cost.

---

## 1. Top 20 Optimization Targets (Size × Frequency Score)

Score = loaded_tokens × (body_tokens / 1000). Rewards skills that are both
large and frequently loaded — the highest-leverage refactoring candidates.

| Rank | Skill | Body Chars | Body Tok | Views | Loaded Tok (30d) | Score | Size Tier |
|------|-------|-----------|---------|-------|-----------------|-------|-----------|
| 1 | self-hosted-service-operations | 84,270 | 21,067 | 516 | 6,527,702 | 137.5M | CRITICAL >50K |
| 2 | kanban-orchestrator | 66,363 | 16,590 | 647 | 7,731,565 | 128.3M | CRITICAL >50K |
| 3 | hermes-agent | 63,854 | 15,963 | 332 | 5,018,352 | 80.1M | CRITICAL >50K |
| 4 | hermes-cron-job-operations | 50,083 | 12,520 | 242 | 1,502,839 | 18.8M | CRITICAL >50K |
| 5 | technitium-dns-operations | 62,355 | 15,588 | 147 | 1,174,021 | 18.3M | CRITICAL >50K |
| 6 | honcho-operations | 93,274 | 23,318 | 55 | 696,241 | 16.2M | CRITICAL >50K |
| 7 | tailscale-ssh-operations | 26,905 | 6,726 | 183 | 1,823,366 | 12.3M | MODERATE 20-30K |
| 8 | gorgasali-office-hikvision-recorder | 51,757 | 12,939 | 149 | 922,332 | 11.9M | CRITICAL >50K |
| 9 | batumilove-obsidian-vault | 40,888 | 10,222 | 143 | 1,084,653 | 11.1M | HIGH 30-50K |
| 10 | infrastructure-operations | 30,362 | 7,590 | 128 | 776,420 | 5.9M | HIGH 30-50K |
| 11 | grafana-dashboard-operations | 30,293 | 7,573 | 138 | 771,648 | 5.8M | HIGH 30-50K |
| 12 | backup-storage-operations | 25,972 | 6,493 | 120 | 663,161 | 4.3M | MODERATE 20-30K |
| 13 | kubernetes-storage-operations | 34,770 | 8,692 | 45 | 483,301 | 4.2M | HIGH 30-50K |
| 14 | gpu-inference-research | 34,684 | 8,671 | 86 | 417,094 | 3.6M | HIGH 30-50K |
| 15 | infra-dashboard | 21,250 | 5,312 | 141 | 584,243 | 3.1M | MODERATE 20-30K |
| 16 | cloud-vps-vpn-kuma-operations | 29,158 | 7,289 | 71 | 397,716 | 2.9M | MODERATE 20-30K |
| 17 | github-operations | 19,606 | 4,901 | 189 | 584,885 | 2.9M | ELEVATED 10-20K |
| 18 | hermes-agent-development-operations | 25,708 | 6,427 | 72 | 386,316 | 2.5M | MODERATE 20-30K |
| 19 | proxmox-config-state-management | 9,483 | 2,370 | 96 | 668,874 | 1.6M | NORMAL <10K |
| 20 | camera-dashboard-pipeline | 23,867 | 5,966 | 49 | 231,150 | 1.4M | MODERATE 20-30K |

---

## 2. Top 20 by Body Size (Active, Non-Archived)

| Rank | Skill | Body Chars | Body Tok | Location | Category |
|------|-------|-----------|---------|----------|----------|
| 1 | research-paper-writing | 102,734 | 25,683 | profile | research |
| 2 | honcho-operations | 93,274 | 23,318 | profile | devops |
| 3 | self-hosted-service-operations | 84,270 | 21,067 | profile | devops |
| 4 | kanban-orchestrator | 66,363 | 16,590 | profile | devops |
| 5 | hermes-agent | 63,854 | 15,963 | profile | autonomous-ai-agents |
| 6 | technitium-dns-operations | 62,355 | 15,588 | default | devops |
| 7 | gorgasali-office-hikvision-recorder | 51,757 | 12,939 | default | devops |
| 8 | hermes-cron-job-operations | 50,083 | 12,520 | default | software-development |
| 9 | batumilove-obsidian-vault | 40,888 | 10,222 | profile | note-taking |
| 10 | github-pr-workflow | 35,962 | 8,990 | profile | github |
| 11 | ml-paper-writing | 35,364 | 8,841 | profile | research |
| 12 | kubernetes-storage-operations | 34,770 | 8,692 | profile | devops |
| 13 | gpu-inference-research | 34,684 | 8,671 | profile | research |
| 14 | claude-code | 34,157 | 8,539 | profile | autonomous-ai-agents |
| 15 | agent-platform-operations | 33,403 | 8,350 | profile | devops |
| 16 | tbilisi-home-network-operations | 32,566 | 8,141 | default | devops |
| 17 | infrastructure-operations | 30,362 | 7,590 | default | devops |
| 18 | grafana-dashboard-operations | 30,293 | 7,573 | profile | devops |
| 19 | humanizer | 29,949 | 7,487 | profile | creative |
| 20 | cloud-vps-vpn-kuma-operations | 29,158 | 7,289 | default | devops |

---

## 3. Top 15 Co-Occurrence Pairs (Skills Co-Loaded in Same Session)

| Sessions | Skill Pair |
|----------|-----------|
| 77 | kanban-orchestrator + self-hosted-service-operations |
| 76 | hermes-agent + kanban-orchestrator |
| 61 | github-operations + kanban-orchestrator |
| 58 | infisical-vault + self-hosted-service-operations |
| 54 | self-hosted-service-operations + tailscale-ssh-operations |
| 49 | batumilove-obsidian-vault + github-operations |
| 47 | kanban-orchestrator + web-research-operations |
| 43 | grafana-dashboard-operations + self-hosted-service-operations |
| 43 | infisical-vault + kanban-orchestrator |
| 40 | kanban-orchestrator + kanban-worker |
| 39 | infisical-vault + tailscale-ssh-operations |
| 38 | hermes-agent + self-hosted-service-operations |
| 37 | batumilove-obsidian-vault + kanban-orchestrator |
| 32 | hermes-agent + infisical-vault |
| 31 | hermes-agent + hermes-agent-development-operations |

**Co-occurrence insight:** `kanban-orchestrator` and `self-hosted-service-operations`
form the dense hub — they co-occur with 8 of the top 15 pairs. When these two
load together (77 sessions), the combined body is ~37K tokens before any other
skill loads.

---

## 4. Fixed Index Cost Analysis

The `<available_skills>` catalog ships on every turn. Measured averages:

| Profile | Tokens/turn | Sessions (30d) | Cumulative tokens |
|---------|------------|----------------|-------------------|
| repo-ops-glm-5-1 | 4,243 | 241 | ~1,022,563 |
| default | 1,690 | 4,651 | ~7,860,190 |

**Observation:** repo-ops-glm-5-1 pays 2.5× more per turn for the fixed index
(4,243 vs 1,690 tokens). This is because it has 274 profile-local skills
versus the default profile's 87 default-dir skills. The index is built from
all visible skills at session start.

**Reduction opportunity:** Removing/archiving 106 archived skills from the
active index would immediately cut fixed cost. 213 skills that were never
loaded in 30d are candidates for archive or profile isolation.

---

## 5. Dormant Skills (0 Views in 30d)

213 skills (56%) were never loaded across either profile in the 30-day window.
Notable large dormant skills:

| Skill | Body Chars | Location | Notes |
|-------|-----------|----------|-------|
| research-paper-writing | 102,734 | profile | Largest active skill, 0 views |
| ml-paper-writing | 35,364 | profile | Duplicate of research-paper-writing? |
| claude-code | 34,157 | profile | Bundled version available |
| humanizer | 29,949 | profile | Creative, not used in ops profile |
| p5js | 27,329 | profile | Creative, not used in ops profile |

**Recommendation:** Move creative/research skills to a dedicated profile or
archive. They inflate the fixed index for the ops profile without any usage.

---

## 6. Archived Skills Audit

106 skills sit under `.archive/curator-umbrella-20260430/`. The largest:

| Skill | Body Chars | Notes |
|-------|-----------|-------|
| pytorch-fsdp | 159,244 | Largest skill overall; should it be deleted? |
| k3s-self-hosted-app-deploy | 62,363 | Superseded by self-hosted-service-operations? |
| run | 50,529 | Ouroboros legacy |
| remote-backup-difference-audit | 34,148 | Superseded? |

**Recommendation:** Archived skills may still appear in the fixed index
depending on skill loader behavior. Verify whether `.archive/` is excluded
from skill discovery. If not, these 106 skills add ~360K chars of index
overhead for zero utility.

---

## 7. Recommendations: Router/Reference Splitting Candidates

### Tier 1 — Immediate refactor (CRITICAL >50K chars + high frequency)

1. **self-hosted-service-operations** (84K chars, 516 views, 6.5M loaded tokens)
   - Most impactful single target. Split into a router SKILL.md with
     category-specific reference files for each service type.
   - Co-occurs heavily with kanban-orchestrator, infisical-vault, tailscale.

2. **kanban-orchestrator** (66K chars, 647 views, 7.7M loaded tokens)
   - Highest view count of any skill. Router split: keep workflow steps in
     SKILL.md, move conventions/rosters/examples to references/.

3. **hermes-agent** (64K chars, 332 views, 5.0M loaded tokens)
   - Bundled skill; refactoring requires controller approval. Consider whether
     the deployment's local copy can use a reference-loaded pattern.

### Tier 2 — High-value refactors (50K+ chars, moderate frequency)

4. **technitium-dns-operations** (62K chars, 147 views) — default-profile
   DNS ops; likely large because of command catalogs. Reference split viable.

5. **hermes-cron-job-operations** (50K chars, 242 views) — cron patterns
   can be templated; move hardening/recovery docs to references/.

6. **honcho-operations** (93K chars, 55 views) — largest Tier 2 by size.
   Very large for moderate usage. Strong reference-split candidate.

7. **gorgasali-office-hikvision-recorder** (52K chars, 149 views) —
   camera/recorder specifics could move to references.

### Tier 3 — Moderate size, high frequency (20-30K chars, 100+ views)

8. **tailscale-ssh-operations** (27K, 183 views) — frequently loaded but
   moderate size. Lower priority.
9. **grafana-dashboard-operations** (30K, 138 views) — dashboards catalog
   could be reference-file driven.
10. **infrastructure-operations** (30K, 128 views) — broad infra umbrella.

### Tier 4 — Profile isolation candidates

11. **research-paper-writing** (103K chars, 0 views) — move to a research
    profile, not the ops profile.
12. **ml-paper-writing** (35K, 0 views) — same; check overlap with #11.
13. **claude-code** (34K, 0 views in 30d) — bundled version may suffice.
14. **humanizer** (30K, 0 views) — creative profile candidate.
15. **p5js** (27K, 0 views) — creative profile candidate.

### Profile strategy assessment

The repo-ops-glm-5-1 profile carries 274 skills (4,243 tokens/turn fixed
index). A specialized ops-only profile with ~80-100 skills would reduce
fixed index cost to ~1,500-2,000 tokens/turn — a 50%+ reduction. Creative,
research, and ML skills are dead weight in an ops-focused profile.

---

## 8. Specialized Profile Assessment

| Factor | Evidence | Verdict |
|--------|----------|---------|
| Fixed index cost disparity | 4,243 tok (repo-ops) vs 1,690 tok (default) | Profile separation has real cost |
| 56% skills never loaded | 213 of 380 dormant | Too many skills for current scope |
| Creative/research in ops profile | 5 dormant skills >25K chars each | Clear isolation candidates |
| DevOps skills dominate usage | Top 15 by frequency are all devops/infra | Ops-focused profile is justified |
| Co-occurrence clustering | Kanban+self-hosted+hermes-agent hub | Core ops skills load together; no need to separate them |

**Conclusion:** A specialized profile IS worth it, but the current repo-ops
profile is over-loaded. Trimming to ~100 ops-relevant skills (from 274)
would cut fixed index cost by ~50% with zero impact on workflow — the
dormant 174 skills are pure overhead.

---

## Data Sources

- `hermes skill-usage --days 30 --json` (repo-ops-glm-5-1 profile)
- `hermes skill-usage --days 30 --profile default --json` (default/Telegram profile)
- Filesystem scan of SKILL.md body sizes across:
  - `~/.hermes/profiles/repo-ops-glm-5-1/skills/` (274 skills)
  - `~/.hermes/skills/` (87 skills)
  - `~/.hermes/hermes-agent/skills/` (73 bundled)
- Raw merged data: `/tmp/skill_audit_dedup.json`
