# Normalized candidate discovery items

Machine-readable source: `data/insurance_discovery/normalized_candidate_discovery_items.discovery.json`

Discovery-only normalization pass for the German family of four residing in Georgia. This artifact collapses raw candidate mentions into unique insurer/plan discovery items keyed by `candidate_id` and preserves source traceability for every retained candidate.

## Resolution summary

- Raw candidate mentions: 34
- Unique normalized candidates: 27
- Duplicate candidate groups resolved: 7
- Deduplicated mentions removed from the unique-item layer: 7
- Mention-count distribution by candidate: {'1': 20, '2': 7}

Duplicate-resolution rule: mentions sharing the same `candidate_id` were collapsed into one normalized item while preserving every raw mention row and source reference in traceability arrays.

## Duplicate groups resolved

- `allianz_care` — Allianz Care — Care International Health Insurance Plans — 2 raw mentions across european_providers, global_providers
- `april_international` — APRIL International — Long-term Expat International Health Insurance — 2 raw mentions across european_providers, global_providers
- `axa_global_healthcare` — AXA Global Healthcare — International Health Insurance Plans — 2 raw mentions across european_providers, global_providers
- `bupa_global` — Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families — 2 raw mentions across european_providers, global_providers
- `foyer_global_health` — Foyer Global Health — Expat Health Insurance / International Health Insurance comparison — 2 raw mentions across european_providers, global_providers
- `msh_international` — MSH International — First'Expat+ / International Health Insurance for Individuals — 2 raw mentions across european_providers, global_providers
- `william_russell` — William Russell — International Health Insurance — 2 raw mentions across european_providers, global_providers

## Record fields

- `candidate_id`, `normalized_insurer_name`, `normalized_plan_name`, `candidate_type`: stable normalized identity
- `duplicate_resolution`: whether multiple raw mentions were collapsed, how many, and from which upstream datasets
- `mention_traceability`: all raw mention rows retained for auditability
- `source_traceability`: all supporting source refs retained from shared staging and the candidate source ledger
- `linked_artifacts`: machine-readable pointers back to the upstream JSON artifacts used in this run

## Notes

- This is discovery-only. It deliberately avoids scoring, ranking, or recommendation output.
- If a candidate has one mention only, it still appears here once as the normalized discovery item with its traceability intact.
- Duplicate resolution is intentionally conservative: only shared `candidate_id` values are collapsed in this run.
