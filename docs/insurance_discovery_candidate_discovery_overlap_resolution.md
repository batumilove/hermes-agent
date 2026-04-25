# Candidate discovery overlap resolution

Machine-readable source: `data/insurance_discovery/candidate_discovery_overlap_resolution.discovery.json`

Discovery-only companion artifact for AC 70304 sub-AC 4. This verifies that partially overlapping evidence rows were either merged into one canonical row or kept separate only when they represent distinct source items.

## Summary

- final_record_count: 55
- unique_candidate_ids: 27
- canonical_rows_with_merged_overlap: 7
- resolved_repeated_source_url_groups: 7
- resolved_repeated_source_title_groups: 6
- residual_candidate_plus_source_url_and_source_kind_groups: 0
- residual_candidate_plus_source_url_groups: 0
- residual_candidate_plus_source_title_and_source_kind_groups: 0
- residual_candidate_plus_source_title_groups: 0
- final_ledger_is_non_overlapping: true

## Resolution policy

- policy_version: 2026-04-25-canonical-source-item-v1
- canonical_selection_rule: Prefer the row with the longest evidence excerpt; tie-break on richer supporting-source metadata, then earliest record_index, then evidence_id.
- merge_behavior: Retain one canonical evidence row per overlapping source item and preserve dropped duplicate evidence IDs plus merged provenance in duplicate_resolution.

## Resolved overlap groups

| Candidate ID | Group type | Canonical evidence ID | Dropped duplicate evidence IDs | Source URL / title |
|---|---|---|---|---|
| allianz_care | source_url | allianz_care:source:2 | allianz_care:source:1 | https://www.allianzcare.com/en/personal-international-health-insurance.html |
| april_international | source_url | april_international:source:1 | april_international:source:2 | https://www.april-international.com/en/long-term-international-health-insurance |
| axa_global_healthcare | source_url | axa_global_healthcare:source:2 | axa_global_healthcare:source:1 | https://www.axaglobalhealthcare.com/en/international-health-insurance/ |
| bupa_global | source_url | bupa_global:source:1 | bupa_global:source:2 | https://www.bupaglobal.com/en/private-health-insurance |
| foyer_global_health | source_url | foyer_global_health:source:1 | foyer_global_health:source:2 | https://www.foyerglobalhealth.com/ |
| msh_international | source_url | msh_international:source:1 | msh_international:source:2 | https://www.msh-intl.com/en/individuals/ |
| william_russell | source_url | william_russell:source:2 | william_russell:source:1 | https://www.william-russell.com/international-health-insurance/ |
| allianz_care | source_title | allianz_care:source:2 | allianz_care:source:1 | International Health Insurance for Individuals | Allianz |
| april_international | source_title | april_international:source:1 | april_international:source:2 | Long-term expat international health insurance | APRIL International |
| axa_global_healthcare | source_title | axa_global_healthcare:source:2 | axa_global_healthcare:source:1 | International Health Insurance Plans: AXA Global Healthcare |
| bupa_global | source_title | bupa_global:source:1 | bupa_global:source:2 | Private Health Insurance & Medical Insurance | Bupa Global |
| msh_international | source_title | msh_international:source:1 | msh_international:source:2 | International Health Insurance for Individuals - MSH |
| william_russell | source_title | william_russell:source:2 | william_russell:source:1 | International Health Insurance | William Russell |

## Candidate rows carrying merged overlap provenance

| Candidate ID | Canonical evidence rows with merged overlap |
|---|---:|
| allianz_care | 1 |
| april_international | 1 |
| axa_global_healthcare | 1 |
| bupa_global | 1 |
| foyer_global_health | 1 |
| msh_international | 1 |
| william_russell | 1 |
