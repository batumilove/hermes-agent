# Candidate source ledger integrity audit

Machine-readable source: `data/insurance_discovery/candidate_source_ledger_integrity_audit.discovery.json`

Discovery-only validation artifact for the German citizen family of four residing in Georgia.

## Audit scope

- Source-backed staging candidates: entries in `shared_candidate_staging.discovery.json` with non-empty `source_refs`.
- Ledger under test: `candidate_source_ledger.discovery.json` `records[]`.
- Integrity rule: every identified source-backed candidate has exactly one ledger row, and every ledger row remains cited by its own source metadata.

## Summary

- Source-backed candidates: 27
- Ledger rows: 27
- Source-backed candidates with exactly one ledger row: 27
- Ledger rows with exactly one source-backed candidate match: 27
- Duplicate source-backed candidate IDs: 0
- Duplicate ledger candidate IDs: 0
- Missing ledger rows for source-backed candidates: 0
- Extra ledger rows without source-backed candidate: 0
- Uncited ledger entries: 0
- Row integrity failures: 0
- Audit passed: true

## Cited-entry rule used in this audit

A ledger row counts as cited only when all of the following are true:

1. `source_set` is non-empty.
2. At least one `source_set` item has `applicable_to_candidate_evidence_entry = true`.
3. `applicable_source_ids` is present and each referenced ID exists in `source_set`.
4. `primary_evidence_url` is present.

## Exceptions

- None. No duplicate, missing, extra, or uncited rows were found.

## Per-row integrity snapshot

| candidate_id | one staging match | one ledger row | cited entry | issues |
| --- | --- | --- | --- | --- |
| cigna_global | true | true | true | none |
| allianz_care | true | true | true | none |
| bupa_global | true | true | true | none |
| axa_global_healthcare | true | true | true | none |
| april_international | true | true | true | none |
| william_russell | true | true | true | none |
| msh_international | true | true | true | none |
| now_health_international | true | true | true | none |
| img_global | true | true | true | none |
| aetna_international | true | true | true | none |
| foyer_global_health | true | true | true | none |
| henner | true | true | true | none |
| globality_health | true | true | true | none |
| morgan_price | true | true | true | none |
| passportcard_global | true | true | true | none |
| vumi | true | true | true | none |
| integra_global | true | true | true | none |
| medihelp_international | true | true | true | none |
| alc_health | true | true | true | none |
| geoblue_xplorer_bcbs_global_solutions | true | true | true | none |
| safetywing_nomad_complete | true | true | true | none |
| genki_native | true | true | true | none |
| acs_expat | true | true | true | none |
| hci_group_health_protect | true | true | true | none |
| expatriate_group_global_health | true | true | true | none |
| wellaway_expat | true | true | true | none |
| securus_global_health_cover | true | true | true | none |
