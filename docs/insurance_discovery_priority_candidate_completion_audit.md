# Priority candidate completion audit

Machine-readable source: `data/insurance_discovery/priority_candidate_completion_audit.discovery.json`

Completion audit for the priority shortlist only. This verifies that each targeted insurer now has exactly one consolidated record in `shared_candidate_staging.discovery.json`, exactly one matching row in `candidate_source_ledger.discovery.json`, and the core completion fields required for this discovery pass.

## Summary

- Priority candidates audited: 14
- Priority candidates with exactly one complete consolidated record: 14
- Priority candidates with issues: 0
- Missing priority candidates in staging: 0
- Missing priority candidates in candidate source ledger: 0
- Duplicate priority candidate IDs in staging: 0
- Duplicate priority candidate IDs in candidate source ledger: 0
- Audit passed: true

## Audit rule

A priority candidate counts as having one complete record only when all of the following are true:

1. Exactly one `staging_candidates[]` record exists for the mapped `candidate_id`.
2. Exactly one `candidate_source_ledger.records[]` row exists for the same `candidate_id`.
3. The staging record includes non-empty `source_refs`, `pricing_availability_status`, `medical_underwriting_preexisting_conditions`, `coverage_ambiguity_annotations`, and `family_pricing_visibility`.
4. `coverage_ambiguity_annotations` contains non-empty `georgia`, `germany`, and `worldwide_ex_us` entries, each with `coverage_state`, `coverage_summary`, `source_backed_rationale`, and `supporting_source_refs`.
5. `family_pricing_visibility` contains non-empty `status`, `summary`, `quote_journey_outcome`, `next_action_required`, and `evidence`.
6. The matching candidate-source ledger row includes non-empty `source_set`, `applicable_source_ids`, and `primary_evidence_url`.

## Canonical shortlist mapping used

| Requested shortlist label | Canonical candidate_id | Normalized insurer name |
| --- | --- | --- |
| Cigna Global | cigna_global | Cigna Global |
| Bupa Global | bupa_global | Bupa Global |
| APRIL International | april_international | APRIL International |
| Allianz Care | allianz_care | Allianz Care |
| AXA Global Healthcare | axa_global_healthcare | AXA Global Healthcare |
| William Russell | william_russell | William Russell |
| Now Health International | now_health_international | Now Health International |
| Foyer Global Health | foyer_global_health | Foyer Global Health |
| SafetyWing | safetywing_nomad_complete | SafetyWing |
| Expatriate Group | expatriate_group_global_health | Expatriate Group |
| IMG Global Medical | img_global | IMG (International Medical Group) |
| Genki | genki_native | Genki |
| WellAway | wellaway_expat | WellAway |
| PassportCard | passportcard_global | PassportCard |

## Per-candidate audit snapshot

| Candidate ID | Exactly one staging record | Exactly one source-ledger row | Complete record present | Notes |
| --- | --- | --- | --- | --- |
| cigna_global | true | true | true | none |
| bupa_global | true | true | true | none |
| april_international | true | true | true | none |
| allianz_care | true | true | true | none |
| axa_global_healthcare | true | true | true | none |
| william_russell | true | true | true | none |
| now_health_international | true | true | true | none |
| foyer_global_health | true | true | true | none |
| safetywing_nomad_complete | true | true | true | none |
| expatriate_group_global_health | true | true | true | none |
| img_global | true | true | true | none |
| genki_native | true | true | true | none |
| wellaway_expat | true | true | true | none |
| passportcard_global | true | true | true | none |

## Findings

- No remaining duplicate, missing, or incomplete consolidated records were found for the priority shortlist.
- No data fixes were required in the audited staging or candidate-source ledger artifacts during this pass.
- Uncertainty still exists inside several candidate records, but it is recorded as intended discovery uncertainty rather than a completeness failure.
