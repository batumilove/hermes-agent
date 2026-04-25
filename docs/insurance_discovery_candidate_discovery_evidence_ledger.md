# Candidate discovery evidence ledger

Machine-readable sources:
- `data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.jsonl`
- `data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.json`
Normalization audit: `data/insurance_discovery/candidate_discovery_evidence_ledger_normalization_audit.discovery.json`
Final overlap-resolution audit: `data/insurance_discovery/candidate_discovery_overlap_resolution.discovery.json`

Discovery-only ledger with one evidence-item row per distinct collected claim-bearing source item tied to a normalized insurer/plan discovery candidate. Each row corresponds to exactly one collected public page, quote path, broker page, or plan document retained in `candidate_source_ledger.source_set` / normalized `source_traceability`.

## Summary counts

- total_records: 55
- unique_candidate_ids: 27
- records_with_citations: 55
- records_with_supporting_source_refs: 55
- records_with_candidate_linkage: 55
- records_with_exactly_one_citation: 55
- records_with_exactly_one_supporting_source_ref: 55
- rows_flagged_for_follow_up_after_normalization: 0
- row_granularity: one row per collected claim-bearing source item applicable to the candidate evidence entry
- source_kind_breakdown: {'broker page': 2, 'official insurer page': 24, 'official insurer page via search result': 1, 'official quote page': 16, 'policy wording or brochure PDF': 12}
- source_directness_breakdown: {'high': 27, 'low': 3, 'medium': 25}
- repeated_candidate_url_groups_reviewed: 7
- repeated_candidate_title_groups_reviewed: 6

## Normalization notes

- The builder now emits one evidence-entry row for each source-traceability item rather than one candidate-level row with multiple citations.
- Source attribution is preserved per row through `source_id`, `source_origin`, `source_dataset_path`, `source_dataset_section`, `source_url`, `candidate_source_ledger_ref`, `citations`, and `supporting_source_refs`.
- Repeated candidate/source URL/source kind rows are now deduplicated to one canonical row per repeated item. Canonical selection prefers the longest evidence excerpt, then richer supporting-source metadata, then the earliest record index, with duplicate provenance preserved in `duplicate_resolution`.

## Record inventory by candidate

| Candidate ID | Insurer | Plan | Evidence rows | Source kinds |
|---|---|---|---:|---|
| acs_expat | ACS | ACS Expat | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| aetna_international | Aetna International | Aetna International global healthcare entrypoint | 2 | official insurer page, official quote page |
| alc_health | ALC Health | Global Prima Medical Insurance / Flying Colours legacy entrypoints | 1 | official insurer page |
| allianz_care | Allianz Care | Care International Health Insurance Plans | 2 | official insurer page, official quote page |
| april_international | APRIL International | Long-term Expat International Health Insurance | 2 | official insurer page, official quote page |
| axa_global_healthcare | AXA Global Healthcare | International Health Insurance Plans | 2 | official insurer page, official quote page |
| bupa_global | Bupa Global | Private Health Insurance & Medical Insurance for Individuals and Families | 1 | official insurer page |
| cigna_global | Cigna Global | International Health Insurance / Global Medical Cover | 2 | official insurer page, official quote page |
| expatriate_group_global_health | Expatriate Group | International Health Insurance / Select / Primary+ / Primary+ Lite / Primary | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| foyer_global_health | Foyer Global Health | Expat Health Insurance / International Health Insurance comparison | 3 | official insurer page via search result, official quote page, policy wording or brochure PDF |
| genki_native | Genki | Native / international health insurance | 2 | official insurer page, official quote page |
| geoblue_xplorer_bcbs_global_solutions | Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) | Worldwide Premier / Outside U.S. / Outside U.S. Select | 2 | broker page, policy wording or brochure PDF |
| globality_health | Globality Health | YouGenio individual international health insurance plans | 2 | official insurer page, official quote page |
| hci_group_health_protect | HCI Group | Health Protect / Protector Plans / Nimbl Health | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| henner | Henner | Individuals & Families international health solutions | 1 | official insurer page |
| img_global | IMG (International Medical Group) | Global Medical international health insurance | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| integra_global | Integra Global | yourLife / PremierLife / yourFamily / PremierFamily | 2 | broker page, policy wording or brochure PDF |
| medihelp_international | MediHelp International | My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal | 1 | official insurer page |
| morgan_price | Morgan Price International Healthcare | Evolution Health / Flexible Choices individual international healthcare plans | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| msh_international | MSH International | First'Expat+ / International Health Insurance for Individuals | 2 | official insurer page, official quote page |
| now_health_international | Now Health International | SimpleCare / WorldCare International Health Insurance Plans | 1 | official insurer page |
| passportcard_global | PassportCard | Expat Basic / Expat Comprehensive / Executive | 2 | official insurer page, policy wording or brochure PDF |
| safetywing_nomad_complete | SafetyWing | Nomad Insurance Complete | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| securus_global_health_cover | Securus International | Global Health Cover | 2 | official insurer page, policy wording or brochure PDF |
| vumi | VUMI | Absolute VIP / Universal VIP / Special VIP / Access VIP product family | 1 | official insurer page |
| wellaway_expat | WellAway | EXPAT / Gold / Diamond / Expat Plus | 3 | official insurer page, official quote page, policy wording or brochure PDF |
| william_russell | William Russell | International Health Insurance | 1 | official insurer page |

## Claim-bearing evidence rows

| Record | Candidate ID | Evidence ID | Source kind | Source title | Applicability roles |
|---:|---|---|---|---|---|
| 1 | acs_expat | acs_expat:source:1 | official insurer page | ACS Expat - Customisable Expatriate Health Insurance Solution | candidate_discovery_entry, official_plan_positioning, quote_or_application_path_reference, geography_relevance_evidence |
| 2 | acs_expat | acs_expat:source:quote_path | official quote page | ACS quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 3 | acs_expat | acs_expat:source:plan_document | policy wording or brochure PDF | ACS plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 4 | aetna_international | aetna_international:source:1 | official insurer page | Home | Aetna | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 5 | aetna_international | aetna_international:source:quote_path | official quote page | Aetna International quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 6 | alc_health | alc_health:source:1 | official insurer page | Global Health and International Medical Insurance For Expatriates | candidate_discovery_entry, candidate_identification, official_plan_positioning, geography_relevance_evidence, pricing_or_quote_path_evidence |
| 7 | allianz_care | allianz_care:source:2 | official insurer page | International Health Insurance for Individuals | Allianz | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 8 | allianz_care | allianz_care:source:quote_path | official quote page | Allianz Care quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 9 | april_international | april_international:source:1 | official insurer page | Long-term expat international health insurance | APRIL International | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference, geography_relevance_evidence |
| 10 | april_international | april_international:source:quote_path | official quote page | APRIL International quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 11 | axa_global_healthcare | axa_global_healthcare:source:2 | official insurer page | International Health Insurance Plans: AXA Global Healthcare | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 12 | axa_global_healthcare | axa_global_healthcare:source:quote_path | official quote page | AXA Global Healthcare quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 13 | bupa_global | bupa_global:source:1 | official insurer page | Private Health Insurance & Medical Insurance | Bupa Global | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference, geography_relevance_evidence, pricing_or_quote_path_evidence, access_path_evidence |
| 14 | cigna_global | cigna_global:source:1 | official insurer page | International Health Insurance & Global Medical Cover | Cigna | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 15 | cigna_global | cigna_global:source:quote_path | official quote page | Cigna Global quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 16 | expatriate_group_global_health | expatriate_group_global_health:source:1 | official insurer page | International Health Insurance | Global Private Medical Cover | candidate_discovery_entry, official_plan_positioning, quote_or_application_path_reference, geography_relevance_evidence |
| 17 | expatriate_group_global_health | expatriate_group_global_health:source:quote_path | official quote page | Expatriate Group quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 18 | expatriate_group_global_health | expatriate_group_global_health:source:plan_document | policy wording or brochure PDF | Expatriate Group plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 19 | foyer_global_health | foyer_global_health:source:1 | official insurer page via search result | Foyer Global Health: Expat Health Insurance | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 20 | foyer_global_health | foyer_global_health:source:quote_path | official quote page | Foyer Global Health quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 21 | foyer_global_health | foyer_global_health:source:plan_document | policy wording or brochure PDF | Foyer Global Health plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 22 | genki_native | genki_native:source:1 | official insurer page | Genki • Health Insurance for Digital Nomads | candidate_discovery_entry, official_plan_positioning, quote_or_application_path_reference, geography_relevance_evidence, pricing_or_quote_path_evidence |
| 23 | genki_native | genki_native:source:quote_path | official quote page | Genki quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 24 | geoblue_xplorer_bcbs_global_solutions | geoblue_xplorer_bcbs_global_solutions:source:1 | broker page | GeoBlue Health Insurance Plan - GeoBlue Xplorer | candidate_discovery_entry, candidate_identification, official_plan_positioning, broker_substitute_evidence, geography_relevance_evidence, access_path_evidence |
| 25 | geoblue_xplorer_bcbs_global_solutions | geoblue_xplorer_bcbs_global_solutions:source:plan_document | policy wording or brochure PDF | Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 26 | globality_health | globality_health:source:1 | official insurer page | International Health Insurance for Expats - Globality Health | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 27 | globality_health | globality_health:source:quote_path | official quote page | Globality Health quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 28 | hci_group_health_protect | hci_group_health_protect:source:1 | official insurer page | International Healthcare | Healthcare International | HCI Global | candidate_discovery_entry, official_plan_positioning, quote_or_application_path_reference |
| 29 | hci_group_health_protect | hci_group_health_protect:source:quote_path | official quote page | HCI Group quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 30 | hci_group_health_protect | hci_group_health_protect:source:plan_document | policy wording or brochure PDF | HCI Group plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 31 | henner | henner:source:1 | official insurer page | Individuals & Families - Henner | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 32 | img_global | img_global:source:1 | official insurer page | International Health & Travel Medical Insurance - IMG | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 33 | img_global | img_global:source:quote_path | official quote page | IMG (International Medical Group) quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 34 | img_global | img_global:source:plan_document | policy wording or brochure PDF | IMG (International Medical Group) plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 35 | integra_global | integra_global:source:1 | broker page | Integra Health Insurance | Integra Expat Medical Plan | candidate_discovery_entry, candidate_identification, official_plan_positioning, broker_substitute_evidence, geography_relevance_evidence, access_path_evidence |
| 36 | integra_global | integra_global:source:plan_document | policy wording or brochure PDF | Integra Global plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 37 | medihelp_international | medihelp_international:source:1 | official insurer page | Individual plans | MediHelp International | candidate_discovery_entry, candidate_identification, official_plan_positioning, geography_relevance_evidence |
| 38 | morgan_price | morgan_price:source:1 | official insurer page | International Healthcare Insurance Plans for Expats | Morgan Price International Healthcare | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference, pricing_or_quote_path_evidence |
| 39 | morgan_price | morgan_price:source:quote_path | official quote page | Morgan Price International Healthcare quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 40 | morgan_price | morgan_price:source:plan_document | policy wording or brochure PDF | Morgan Price International Healthcare plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 41 | msh_international | msh_international:source:1 | official insurer page | International Health Insurance for Individuals - MSH | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference |
| 42 | msh_international | msh_international:source:quote_path | official quote page | MSH International quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 43 | now_health_international | now_health_international:source:1 | official insurer page | Compare International Health Insurance Plans from Now Health | candidate_discovery_entry, candidate_identification, official_plan_positioning, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 44 | passportcard_global | passportcard_global:source:1 | official insurer page | International health insurance for expats & nomads | PassportCard | candidate_discovery_entry, candidate_identification, official_plan_positioning, geography_relevance_evidence, pricing_or_quote_path_evidence |
| 45 | passportcard_global | passportcard_global:source:plan_document | policy wording or brochure PDF | PassportCard plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 46 | safetywing_nomad_complete | safetywing_nomad_complete:source:1 | official insurer page | SafetyWing | Complete Health And Travel Insurance For Nomads | candidate_discovery_entry, official_plan_positioning, quote_or_application_path_reference |
| 47 | safetywing_nomad_complete | safetywing_nomad_complete:source:quote_path | official quote page | SafetyWing quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 48 | safetywing_nomad_complete | safetywing_nomad_complete:source:plan_document | policy wording or brochure PDF | SafetyWing plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 49 | securus_global_health_cover | securus_global_health_cover:source:1 | official insurer page | Securus - Global Health Cover | candidate_discovery_entry, official_plan_positioning |
| 50 | securus_global_health_cover | securus_global_health_cover:source:plan_document | policy wording or brochure PDF | Securus International plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 51 | vumi | vumi:source:1 | official insurer page | Home - VUMI | candidate_discovery_entry, candidate_identification, official_plan_positioning, geography_relevance_evidence |
| 52 | wellaway_expat | wellaway_expat:source:1 | official insurer page | Expat health insurance plans for living abroad | WellAway | candidate_discovery_entry, official_plan_positioning, quote_or_application_path_reference |
| 53 | wellaway_expat | wellaway_expat:source:quote_path | official quote page | WellAway quote or application path | candidate_discovery_entry, quote_or_application_path_reference, pricing_or_quote_path_evidence, access_path_evidence |
| 54 | wellaway_expat | wellaway_expat:source:plan_document | policy wording or brochure PDF | WellAway plan document or brochure | candidate_discovery_entry, plan_document_reference, benefit_detail_evidence, geography_relevance_evidence |
| 55 | william_russell | william_russell:source:2 | official insurer page | International Health Insurance | William Russell | candidate_discovery_entry, candidate_identification, official_plan_positioning, geography_relevance_evidence, pricing_or_quote_path_evidence, access_path_evidence |
