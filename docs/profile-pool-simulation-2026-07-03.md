# Specialized Profile Simulation & Recommendation

**Date:** 2026-07-03
**Task:** t_3dac2fea — Simulate candidate specialized profiles from skill co-occurrence
**Telemetry:** `hermes skill-usage --days 30 --json` across repo-ops-glm-5-1 + default profiles
**Sessions analyzed:** 1,504 skill-loading sessions out of 4,892 total (30.8%)

---

## Executive Summary

**Recommendation: Do NOT split profiles yet. Trim dormant skills + split oversized skill bodies first.**

The data shows that 60.9% of skill-loading sessions are cross-domain — they load skills
spanning infra, kanban, development, and research simultaneously. Static profile splitting
produces a 24-28% miss rate, meaning the agent would need cross-profile skill access on
roughly 1 in 4 sessions. The routing complexity and skill duplication this requires is not
justified when the same token savings can be achieved by simpler means.

Two interventions deliver better returns at lower risk:

1. **Trim dormant skills** (93 of 260 never loaded in 30d): -30.6% fixed index cost, 0% miss
2. **Split oversized skill bodies** (8 skills >10K tokens): -72% loaded-token cost on affected skills

---

## 1. Candidate Pool Definitions

| Pool | Categories | Seed Skills | Total Skills | Idx Tok/Turn |
|------|-----------|-------------|:------------:|:------------:|
| base-shared | autonomous-ai-agents, context-tools, software-development, mcp | hermes-agent, hermes-agent-setup, dogfood | 51 | ~790 |
| infra-self-hosted | devops, security, smart-home | + base-shared | 125 | ~1,938 |
| hermes-development | (specific skill list) | hermes-agent-dev-ops, TDD, debugging, github, plan | 58 | ~899 |
| kanban-orchestration | (specific skill list) | kanban-orchestrator/worker, claude-code, codex, delegation | 55 | ~852 |
| research-productivity | research, productivity, note-taking, creative, media, email, social | + base-shared | 140 | ~2,170 |

---

## 2. Single-Pool Coverage (Each pool alone)

| Pool | Skills | Idx Tok | Sessions Covered | Miss Rate | Top Missing Skills |
|------|:------:|:-------:|:----------------:|:---------:|-------------------|
| base-shared | 51 | 790 | 6.7% | 93.3% | kanban-orchestrator, self-hosted, infisical, tailscale |
| infra-self-hosted | 125 | 1,938 | 62.0% | 38.0% | web-research, github-ops, obsidian-vault, browser-automation |
| hermes-development | 58 | 899 | 7.7% | 92.3% | kanban-orchestrator, self-hosted, infisical, tailscale |
| kanban-orchestration | 55 | 852 | 11.4% | 88.6% | self-hosted, infisical, tailscale, web-research |
| research-productivity | 140 | 2,170 | 15.5% | 84.5% | kanban-orchestrator, self-hosted, infisical, tailscale |

**Key finding:** infra-self-hosted is the only pool that achieves >50% standalone coverage.
All others miss >84% of sessions. This is because core ops skills (kanban-orchestrator,
self-hosted-service-operations, infisical-vault, tailscale-ssh) appear in almost every
multi-skill session.

---

## 3. Multi-Profile Routing (Best-Fit Assignment)

Routing each session to the pool with fewest missing skills:

| Metric | Value |
|--------|-------|
| Fully covered (best-fit) | 1,081 / 1,504 (71.9%) |
| Partial (skills missing from assigned pool) | 423 / 1,504 (28.1%) |
| Most-assigned pool | infra-self-hosted: 1,289 (85.7%) |
| Second | research-productivity: 190 (12.6%) |
| Third | hermes-development: 21 (1.4%) |
| Fourth | kanban-orchestration: 4 (0.3%) |

**Cross-domain analysis:** 916 of 1,504 sessions (60.9%) load skills from 2+ pools.
The dominant pattern: infra + kanban + dev + research in a single session (584 sessions, 38.8%).

---

## 4. Strategy Comparison

| Strategy | Idx Tok/Turn | Coverage | Miss Rate | Effort | Risk |
|----------|:------------:|:--------:|:---------:|:------:|:----:|
| A: Status quo | 4,243 | 100% | 0% | none | ongoing waste |
| B: 5 specialized profiles | ~1,542 avg | 71.9% | 28.1% | high | cross-domain routing |
| C: Trim dormant skills | 2,945 | 100% | 0% | low | minimal |
| D: Split oversized skills | 4,243 | 100% | 0% | medium | refactor risk |
| **E: Hybrid (C+D)** | **2,945** | **100%** | **0%** | **medium** | **low** |
| F: 2 profiles (ops+general) | ~1,600 avg | 75.7% | 24.3% | medium | routing complexity |

---

## 5. Token Savings Breakdown

### Fixed Index Savings (per-turn cost, paid on every API call)

| Intervention | Skills Removed | Idx Tok/Turn | Savings | 30d Impact |
|-------------|:--------------:|:------------:|:-------:|:----------:|
| Current state | 0 | 4,243 | — | ~20.8M tok |
| Trim 93 dormant | 93 | 2,945 | -30.6% | ~14.4M tok (saves 6.3M) |

### Loaded Skill Savings (per-load cost, paid when skill_view fires)

| Skill | Body Tok | Views | After Split | Savings/Load | 30d Savings |
|-------|:--------:|:-----:|:-----------:|:------------:|:-----------:|
| self-hosted-service-operations | 21,067 | 516 | 5,000 | 16,067 | 8.3M tok |
| kanban-orchestrator | 16,590 | 647 | 4,000 | 12,590 | 8.1M tok |
| hermes-agent | 15,963 | 332 | 4,000 | 11,963 | 4.0M tok |
| technitium-dns-operations | 15,588 | 147 | 3,000 | 12,588 | 1.9M tok |
| hermes-cron-job-operations | 12,520 | 242 | 3,000 | 9,520 | 2.3M tok |
| gorgasali-office-hikvision-recorder | 12,939 | 149 | 3,000 | 9,939 | 1.5M tok |
| honcho-operations | 23,318 | 55 | 5,000 | 18,318 | 1.0M tok |
| batumilove-obsidian-vault | 10,222 | 143 | 2,500 | 7,722 | 1.1M tok |
| **Total** | | | | | **~28.2M tok** |

---

## 6. Why Profile Splitting Fails Here

The co-occurrence data reveals that the deployment's skill usage is **hub-and-spoke, not
siloed**:

```
                    kanban-orchestrator (337 sessions)
                           |
         ┌─────────────────┼──────────────────┐
         |                 |                  |
  self-hosted-svc   infisical-vault    hermes-agent
    (316 sess)        (214 sess)         (277 sess)
         |                 |                  |
    tailscale-ssh     github-ops        hermes-cron
     (148 sess)        (143 sess)        (200 sess)
```

These 7 skills appear in 60-80% of all multi-skill sessions. They span what would be
3 separate profile pools (infra, kanban, development). Splitting them into separate profiles
means either:

- **Duplicating** 7 core skills across all profiles (no index savings), or
- **Routing** sessions to the right profile (28% miss rate = broken workflows), or
- **Cross-profile skill loading** (negates the purpose of splitting)

The profile split only helps for genuinely siloed skills (research-paper-writing, p5js,
humanizer — creative/research skills in an ops context). Those are better handled by
archiving (Strategy C) than by creating a whole new profile.

---

## 7. Recommended Action Plan

### Phase 1: Trim (Low risk, immediate impact)
- Archive 93 skills with 0 views in 30d
- Verify none are recently installed (< 7 days old) or seasonally needed
- Expected: -1,298 tok/turn fixed index, saves ~6.3M tok/30d

### Phase 2: Split oversized skills (Medium effort, high impact)
Priority order by 30d token waste:
1. self-hosted-service-operations (8.3M tok waste) — router + per-service refs
2. kanban-orchestrator (8.1M) — router + conventions/rosters in refs
3. hermes-agent (4.0M) — needs controller approval (bundled skill)
4. hermes-cron-job-operations (2.3M) — router + pattern templates
5. technitium-dns-operations (1.9M) — command catalog to refs
6. gorgasali-office-hikvision-recorder (1.5M) — recorder specifics to refs
7. batumilove-obsidian-vault (1.1M) — vault structure to refs
8. honcho-operations (1.0M) — API/config to refs

Expected: -72% loaded-token cost on split skills, saves ~28.2M tok/30d

### Phase 3: Re-evaluate profiles (Deferred)
After Phases 1-2, re-measure co-occurrence with `hermes skill-usage --days 30`.
If cross-domain sessions drop below 40%, a 2-profile split may become viable.
The decision point: if the trimmed+split index is already <2,000 tok/turn, the
marginal benefit of profiles is <500 tok/turn — likely not worth the complexity.

---

## Data Sources

- `hermes skill-usage --days 30 --profile repo-ops-glm-5-1 --json` (241 sessions, 317 views)
- `hermes skill-usage --days 30 --profile default --json` (4,651 sessions, 5,772 views)
- Parent audit: `docs/skill-oversized-audit-2026-07-03.md`
- Raw skill body sizes: `docs/skill-oversized-audit-2026-07-03-data.json`
- Pool simulation data: `docs/profile-pool-simulation-2026-07-03-data.json`
