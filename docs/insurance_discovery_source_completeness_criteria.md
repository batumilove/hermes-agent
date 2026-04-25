# Insurance discovery source completeness criteria

Discovery-only companion rubric for AC 40201. It defines what a source-backed candidate evidence entry must contain, which coverage fields are mandatory, and which partial/unknown states are explicitly allowed instead of being treated as silent schema failure.

Machine-readable source: `data/insurance_discovery/source_completeness_criteria.discovery.json`

Summary counts:
- total_candidates_audited: 27
- candidates_missing_required_entry_fields: 0
- candidates_missing_required_coverage_fields: 0
- source_backed_complete_for_discovery: 0
- source_backed_partial_but_usable: 27
- insufficient_for_discovery: 0
- candidates_with_all_required_coverage_details_fully_sourced: 0
- candidates_with_at_least_one_required_coverage_detail_missing_or_partial: 27
- required_coverage_detail_fully_sourced_counts:
  - georgia_country_coverage_assessment: 6
  - germany_country_coverage_assessment: 8
  - worldwide_ex_us_status: 3
- required_coverage_detail_missing_or_partial_counts:
  - georgia_country_coverage_assessment: 21
  - germany_country_coverage_assessment: 19
  - worldwide_ex_us_status: 24
- all_candidates_meet_minimum_discovery_schema: yes

Notes:
- Required means the field/state must be represented in the discovery entry or its linked ledger, not that the answer must always be fully confirmed.
- Allowed partial/unknown states are deliberate and source-backed: they preserve uncertainty from quote gates, broker handoffs, login walls, missing PDFs, or generic marketing pages.
- Silent omission is worse than an explicit unknown. This rubric prefers `unknown`, `inferred`, or `not_verified_from_reviewed_sources` over a missing field.
- For this sub-AC, “fully sourced” is intentionally strict: Georgia and Germany must be `confirmed`, and worldwide excluding the US must be `explicitly_indicated`. Anything weaker is recorded as missing_or_partial rather than guessed upward.

## Required candidate-entry fields

| Field | Presence rule | Why it matters |
|---|---|---|
| `candidate_id` | must_be_present | Stable machine-readable join key across all discovery ledgers. |
| `normalized_insurer_name` | must_be_present | Identifies the insurer responsible for the candidate plan family. |
| `normalized_plan_name` | must_be_present | Retains the discovered plan or product-family label even at discovery stage. |
| `source_refs` | must_be_present_and_non_empty | Every candidate evidence entry must remain source-backed rather than being a memory-only stub. |
| `relevance_status` | must_be_present | Preserves whether the candidate still looks plausibly relevant to this family profile. |
| `relevance_status_reason` | must_be_present | Keeps the relevance classification explainable and auditable. |
| `uncertainty_notes` | must_be_present | Discovery is allowed to be incomplete, but the uncertainty must be explicit. |
| `pricing_availability_status` | must_be_present | Quote/pricing visibility is a recurring gating factor in this market. |
| `pricing_availability_status_reason` | must_be_present | Explains whether public pricing was visible, gated, partial, or absent. |
| `access_and_intake_evidence_refs` | must_be_present | Links the candidate entry to access and gating evidence rather than hiding friction. |
| `eligibility_ambiguity_evidence_ref` | must_be_present | Links the candidate entry to the ambiguity ledger for unresolved residence/eligibility questions. |

## Required coverage fields

| Coverage field | Artifact | Allowed states | Partial/unknown allowed? | Why it matters |
|---|---|---|---|---|
| `georgia_country_coverage_assessment` | `data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json` | `confirmed`, `inferred`, `unknown` | yes | The family resides in Georgia, so Georgia coverage is a non-optional screening dimension. |
| `germany_country_coverage_assessment` | `data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json` | `confirmed`, `inferred`, `unknown` | yes | The family is German, so Germany relevance and home-country treatment matter even when living abroad. |
| `worldwide_ex_us_status` | `data/insurance_discovery/global_coverage_evidence_ledger.discovery.json` | `explicitly_indicated`, `indirectly_indicated`, `worldwide_positioned_but_ex_us_not_verified`, `not_verified_from_reviewed_sources` | yes | The target preference is worldwide excluding the US, but discovery must still preserve weaker evidence states. |

## Allowed partial and unknown states

Country coverage assessments:
- `confirmed`: Captured public source language directly names the country or equivalent selector evidence.
- `inferred`: Captured public source language is source-backed but indirect, such as region/area-of-cover wording, home-country mechanics, or destination references.
- `unknown`: Reviewed sources were too generic, gated, or incomplete to support a responsible country-level inference.

Worldwide excluding US status states:
- `explicitly_indicated`: A reviewed public source explicitly described a worldwide excluding U.S. (or equivalent outside-U.S.) coverage area/plan, or used materially equivalent wording such as Area 2 worldwide excluding the USA.
- `indirectly_indicated`: A reviewed public source gave strong source-backed signals of a non-U.S. global configuration, such as an include-USA toggle, U.S. add-on, or outside-U.S. benefit wording, but did not explicitly state a worldwide-excluding-U.S. area of cover for the exact plan wording captured.
- `worldwide_positioned_but_ex_us_not_verified`: A reviewed public source positioned the plan as worldwide/global/international, but the captured evidence did not explicitly confirm a worldwide-excluding-U.S. configuration.
- `not_verified_from_reviewed_sources`: The reviewed public sources in this run did not verify a worldwide/global geography detail sufficient to infer a worldwide-excluding-U.S. configuration.

URL capture states:
- `verified_public_url`: A public URL was verified and stored in the candidate source ledger.
- `not_verified_publicly_in_this_run`: Represented as null for official_url, broker_url, quote_or_application_url, or policy_wording_or_pdf_url; null does not mean impossible or unavailable, only unverified in this run.

Source directness states:
- `high`: Official insurer-controlled quote/application paths and official policy wording, brochure, benefit-table, or similar plan documents.
- `medium`: Official insurer-controlled landing or marketing pages that are direct but less authoritative for exact eligibility or benefit details than quote or policy documents.
- `low`: Broker/intermediary pages or other indirect public sources that materially help discovery but are not primary insurer-controlled evidence.

Evidence-entry completeness levels:
- `source_backed_complete_for_discovery`: Entry has all required join/explanation fields and all three required coverage fields are present with source-backed states. Some states may still be inferred rather than direct.
- `source_backed_partial_but_usable`: Entry is still usable for discovery because required fields are present, but one or more coverage dimensions remain inferred or unknown, or important source URLs/documents remain unverified.
- `insufficient_for_discovery`: Entry is missing required structural or source-backed fields and should not be treated as a valid discovery candidate entry.

## Audit of current discovery candidates

| Candidate | Georgia | Germany | Worldwide ex-US | Fully sourced details | Missing/partial details | Completeness level | Missing required fields |
|---|---|---|---|---|---|---|---|
| `cigna_global` | `confirmed` | `confirmed` | `worldwide_positioned_but_ex_us_not_verified` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `allianz_care` | `unknown` | `unknown` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `bupa_global` | `confirmed` | `confirmed` | `not_verified_from_reviewed_sources` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `axa_global_healthcare` | `inferred` | `inferred` | `indirectly_indicated` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `april_international` | `confirmed` | `confirmed` | `worldwide_positioned_but_ex_us_not_verified` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `william_russell` | `inferred` | `inferred` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `msh_international` | `unknown` | `unknown` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `now_health_international` | `unknown` | `unknown` | `indirectly_indicated` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `img_global` | `confirmed` | `confirmed` | `worldwide_positioned_but_ex_us_not_verified` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `aetna_international` | `unknown` | `unknown` | `indirectly_indicated` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `foyer_global_health` | `inferred` | `inferred` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `henner` | `unknown` | `unknown` | `not_verified_from_reviewed_sources` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `globality_health` | `unknown` | `unknown` | `not_verified_from_reviewed_sources` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `morgan_price` | `unknown` | `inferred` | `indirectly_indicated` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `passportcard_global` | `inferred` | `confirmed` | `worldwide_positioned_but_ex_us_not_verified` | `germany_country_coverage_assessment` | `georgia_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `vumi` | `inferred` | `inferred` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `integra_global` | `inferred` | `inferred` | `explicitly_indicated` | `worldwide_ex_us_status` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `source_backed_partial_but_usable` | none |
| `medihelp_international` | `inferred` | `inferred` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `alc_health` | `unknown` | `confirmed` | `worldwide_positioned_but_ex_us_not_verified` | `germany_country_coverage_assessment` | `georgia_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `geoblue_xplorer_bcbs_global_solutions` | `inferred` | `inferred` | `explicitly_indicated` | `worldwide_ex_us_status` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `source_backed_partial_but_usable` | none |
| `safetywing_nomad_complete` | `inferred` | `inferred` | `indirectly_indicated` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `genki_native` | `confirmed` | `confirmed` | `worldwide_positioned_but_ex_us_not_verified` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `acs_expat` | `inferred` | `inferred` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `hci_group_health_protect` | `unknown` | `unknown` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `expatriate_group_global_health` | `inferred` | `inferred` | `explicitly_indicated` | `worldwide_ex_us_status` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `source_backed_partial_but_usable` | none |
| `wellaway_expat` | `confirmed` | `confirmed` | `not_verified_from_reviewed_sources` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` | `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |
| `securus_global_health_cover` | `inferred` | `inferred` | `worldwide_positioned_but_ex_us_not_verified` | none | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` | `source_backed_partial_but_usable` | none |

## Cigna Global — International Health Insurance / Global Medical Cover

- Candidate ID: `cigna_global`
- Fully sourced required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Missing or partial required coverage details: `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: georgia_explicitly_listed
    - Primary supporting source: Cigna Global quote country selector — https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html
    - Evidence excerpt: The HTML country list includes “Georgia” and “Germany” as selectable options.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_explicitly_listed
    - Primary supporting source: International Health Insurance & Global Medical Cover | Cigna — https://www.cignaglobal.com/
    - Evidence excerpt: Cigna says its international health insurance serves individuals and families, offers access to medical support in over 200 markets and territories, and provides flexible plans with global medical care essentials.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International Health Insurance & Global Medical Cover | Cigna — https://www.cignaglobal.com/
    - Evidence excerpt: Cigna says its international health insurance serves individuals and families, offers access to medical support in over 200 markets and territories, and provides flexible plans with global medical care essentials.

## Allianz Care — Care International Health Insurance Plans

- Candidate ID: `allianz_care`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_global_positioning_only
    - Why not fully sourced: Official public page confirms long-stay international cover for people living abroad, but this run did not verify Georgia in the quote flow because the quote page was challenge-protected.
    - Primary supporting source: International Health Insurance for Individuals | Allianz — https://www.allianzcare.com/en/personal-international-health-insurance.html
    - Evidence excerpt: Allianz says its Care plans are designed for people who spend long periods overseas and offers cover for people working, studying, or living abroad.
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_not_named_global_positioning_only
    - Why not fully sourced: Allianz publicly markets long-term international cover for people living abroad, but no captured source in this run named Germany directly.
    - Primary supporting source: International Health Insurance for Individuals | Allianz — https://www.allianzcare.com/en/personal-international-health-insurance.html
    - Evidence excerpt: Allianz states its Care international health insurance plans are designed for people who spend long periods overseas and offers dedicated paths for professionals, families, students, and nomads.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International Health Insurance for Individuals | Allianz — https://www.allianzcare.com/en/personal-international-health-insurance.html
    - Evidence excerpt: Allianz states its Care international health insurance plans are designed for people who spend long periods overseas and offers dedicated paths for professionals, families, students, and nomads.

## Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families

- Candidate ID: `bupa_global`
- Fully sourced required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Missing or partial required coverage details: `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: georgia_explicitly_listed
    - Primary supporting source: Private Health Insurance & Medical Insurance | Bupa Global — https://www.bupaglobal.com/en/private-health-insurance
    - Evidence excerpt: The official individuals-and-families selector asks where the customer will be living and publicly lists both Georgia and Germany in the residence-country dropdown.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_explicitly_listed
    - Primary supporting source: Private Health Insurance & Medical Insurance | Bupa Global — https://www.bupaglobal.com/en/private-health-insurance
    - Evidence excerpt: Bupa Global's official plan selector for individuals and families includes both Georgia and Germany in the residence-country dropdown, indicating public-facing availability exploration for those markets.
  - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources` → `missing_or_partial`
    - Basis: not_verified
    - Why not fully sourced: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Primary supporting source: Private Health Insurance & Medical Insurance | Bupa Global — https://www.bupaglobal.com/en/private-health-insurance
    - Evidence excerpt: Bupa Global's official plan selector for individuals and families includes both Georgia and Germany in the residence-country dropdown, indicating public-facing availability exploration for those markets.

## AXA Global Healthcare — International Health Insurance Plans

- Candidate ID: `axa_global_healthcare`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_area_of_cover_structure
    - Why not fully sourced: AXA publicly documents area-of-cover logic and optional exclusion of the USA, but Georgia itself was not named in the captured public text.
    - Primary supporting source: International Health Insurance Plans: AXA Global Healthcare — https://www.axaglobalhealthcare.com/en/international-health-insurance/
    - Evidence excerpt: AXA says it will give customers the option to exclude the USA from their area of cover, while treatment outside the chosen area of cover is not covered.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_area_of_cover_structure
    - Why not fully sourced: AXA exposes area-of-cover mechanics and USA exclusion options, but Germany was not named directly in the captured public text.
    - Primary supporting source: International Health Insurance Plans: AXA Global Healthcare — https://www.axaglobalhealthcare.com/en/international-health-insurance/
    - Evidence excerpt: AXA describes its product as international private health insurance for people living abroad or travelling overseas frequently, with treatment anywhere within the selected region of cover.
  - `worldwide_ex_us_status` → `indirectly_indicated` → `missing_or_partial`
    - Basis: strong_but_indirect
    - Why not fully sourced: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Primary supporting source: International Health Insurance Plans: AXA Global Healthcare — https://www.axaglobalhealthcare.com/en/international-health-insurance/
    - Evidence excerpt: AXA describes its product as international private health insurance for people living abroad or travelling overseas frequently, with treatment anywhere within the selected region of cover.

## APRIL International — Long-term Expat International Health Insurance

- Candidate ID: `april_international`
- Fully sourced required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Missing or partial required coverage details: `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: georgia_explicitly_listed
    - Primary supporting source: Long-term expat international health insurance | APRIL International — https://www.april-international.com/en/long-term-international-health-insurance
    - Evidence excerpt: The public country-of-coverage list includes Georgia and Germany, and APRIL says worldwide cover depends on the chosen area of cover.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_explicitly_listed
    - Primary supporting source: Long-term expat international health insurance | APRIL International — https://www.april-international.com/en/long-term-international-health-insurance
    - Evidence excerpt: APRIL positions this as long-term international health insurance for expatriates and globally mobile individuals staying abroad longer than 12 months, with worldwide cover depending on area of cover. The country selector excerpt includes Germany.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Long-term expat international health insurance | APRIL International — https://www.april-international.com/en/long-term-international-health-insurance
    - Evidence excerpt: APRIL positions this as long-term international health insurance for expatriates and globally mobile individuals staying abroad longer than 12 months, with worldwide cover depending on area of cover. The country selector excerpt includes Germany.

## William Russell — International Health Insurance

- Candidate ID: `william_russell`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_zone_based_cover
    - Why not fully sourced: William Russell publicly shows region-based and worldwide cover examples, but Georgia was not named directly in captured text.
    - Primary supporting source: International Health Insurance | William Russell — https://www.william-russell.com/international-health-insurance/
    - Evidence excerpt: The page says customers have different coverage options ranging from worldwide cover to cover in just one region, and examples are priced by country of residence and coverage area.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_zone_based_cover
    - Why not fully sourced: William Russell shows region-based and worldwide cover structures, but Germany was not named directly in the captured sources.
    - Primary supporting source: International Health Insurance | William Russell — https://www.william-russell.com/international-health-insurance/
    - Evidence excerpt: William Russell describes its product as international health insurance for expats and people living, working, or studying abroad, with members able to choose doctors or hospitals within their coverage zone.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International Health Insurance | William Russell — https://www.william-russell.com/international-health-insurance/
    - Evidence excerpt: William Russell describes its product as international health insurance for expats and people living, working, or studying abroad, with members able to choose doctors or hospitals within their coverage zone.

## MSH International — First'Expat+ / International Health Insurance for Individuals

- Candidate ID: `msh_international`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_mobility_positioning_only
    - Why not fully sourced: MSH positions First’Expat+ as international cover for mobility needs, but no Georgia-specific country evidence was visible in this run.
    - Primary supporting source: International Health Insurance for Individuals - MSH — https://www.msh-intl.com/en/individuals/
    - Evidence excerpt: MSH describes First’Expat+ as a comprehensive, flexible international health insurance plan and frames itself as the healthcare partner for mobility needs.
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_not_named_mobility_positioning_only
    - Why not fully sourced: MSH positions the plan for international mobility, but the reviewed public evidence did not place Germany inside a named territory or selector.
    - Primary supporting source: International Health Insurance for Individuals - MSH — https://www.msh-intl.com/en/individuals/
    - Evidence excerpt: MSH presents First'Expat+ as a comprehensive, flexible international health insurance plan for individuals and frames itself as a healthcare partner for mobility needs with 24/7 support.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International Health Insurance for Individuals - MSH — https://www.msh-intl.com/en/individuals/
    - Evidence excerpt: MSH presents First'Expat+ as a comprehensive, flexible international health insurance plan for individuals and frames itself as a healthcare partner for mobility needs with 24/7 support.

## Now Health International — SimpleCare / WorldCare International Health Insurance Plans

- Candidate ID: `now_health_international`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_country_of_residence_mechanics
    - Why not fully sourced: Now Health exposes international-plan geography mechanics tied to country of nationality or residence, but Georgia was not named directly in the captured page text.
    - Primary supporting source: Compare International Health Insurance Plans from Now Health — https://www.now-health.com/en/insurance-plans/
    - Evidence excerpt: The public comparison table references repatriation to the country of nationality or residence and includes residence-specific issuance notes for some markets.
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_not_named_country_of_residence_mechanics
    - Why not fully sourced: Now Health exposes country-of-residence mechanics relevant to Germany screening, but Germany was not named directly.
    - Primary supporting source: Compare International Health Insurance Plans from Now Health — https://www.now-health.com/en/insurance-plans/
    - Evidence excerpt: Now Health compares seven international plan options across SimpleCare and WorldCare product lines and includes evacuation/repatriation and optional USA treatment mechanics in the public comparison table.
  - `worldwide_ex_us_status` → `indirectly_indicated` → `missing_or_partial`
    - Basis: strong_but_indirect
    - Why not fully sourced: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Primary supporting source: Compare International Health Insurance Plans from Now Health — https://www.now-health.com/en/insurance-plans/
    - Evidence excerpt: Now Health compares seven international plan options across SimpleCare and WorldCare product lines and includes evacuation/repatriation and optional USA treatment mechanics in the public comparison table.

## IMG (International Medical Group) — Global Medical international health insurance

- Candidate ID: `img_global`
- Fully sourced required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Missing or partial required coverage details: `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: georgia_explicitly_listed
    - Primary supporting source: IMG international health insurance — https://www.imglobal.com/international-health-insurance
    - Evidence excerpt: The page HTML contains country options including Georgia and Germany and says IMG international health plans provide medical coverage worldwide.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_explicitly_listed
    - Primary supporting source: International Health & Travel Medical Insurance - IMG — https://www.imglobal.com/
    - Evidence excerpt: IMG states that its international health insurance offers annually renewable private medical coverage for expats and global citizens living or working internationally, with Global Medical Silver, Gold, and Platinum plans.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International Health & Travel Medical Insurance - IMG — https://www.imglobal.com/
    - Evidence excerpt: IMG states that its international health insurance offers annually renewable private medical coverage for expats and global citizens living or working internationally, with Global Medical Silver, Gold, and Platinum plans.

## Aetna International — Aetna International global healthcare entrypoint

- Candidate ID: `aetna_international`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_global_network_only
    - Why not fully sourced: Aetna International publicly shows worldwide telehealth or network reach, but no Georgia-specific country selector evidence was captured.
    - Primary supporting source: Home | Aetna International — https://www.aetnainternational.com/
    - Evidence excerpt: Aetna says members can access care anywhere, anytime through the member website and worldwide telehealth services.
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_not_named_global_network_only
    - Why not fully sourced: Aetna International shows worldwide service reach, but no Germany-specific plan or selector evidence was captured.
    - Primary supporting source: Home | Aetna — https://www.aetnainternational.com/
    - Evidence excerpt: Aetna International says it serves globally mobile members with access to quality care and support wherever they are, with provider network access in 200+ countries and territories.
  - `worldwide_ex_us_status` → `indirectly_indicated` → `missing_or_partial`
    - Basis: strong_but_indirect
    - Why not fully sourced: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Primary supporting source: Home | Aetna — https://www.aetnainternational.com/
    - Evidence excerpt: Aetna International says it serves globally mobile members with access to quality care and support wherever they are, with provider network access in 200+ countries and territories.

## Foyer Global Health — Expat Health Insurance / International Health Insurance comparison

- Candidate ID: `foyer_global_health`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_destination_reference
    - Why not fully sourced: Foyer publicly references Georgia in dedicated destination and health guides, which is useful country-specific discovery evidence even though it is not a formal eligibility table.
    - Primary supporting source: Expat Health Insurance | Foyer Global Health — https://www.foyerglobalhealth.com/
    - Evidence excerpt: The official site lists destination guides for Georgia, including living, health, and budget content, and markets worldwide expat-family coverage.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_destination_reference
    - Why not fully sourced: Foyer’s official public footprint includes Germany destination content, which supports Germany relevance even though a Germany selector was not captured.
    - Primary supporting source: Foyer Global Health: Expat Health Insurance — https://www.foyerglobalhealth.com/
    - Evidence excerpt: The official search result describes Foyer Global Health as an international health insurance and service provider for digital nomads, expats, and globally mobile people; a public plan comparison page is also available.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Foyer Global Health: Expat Health Insurance — https://www.foyerglobalhealth.com/
    - Evidence excerpt: The official search result describes Foyer Global Health as an international health insurance and service provider for digital nomads, expats, and globally mobile people; a public plan comparison page is also available.

## Henner — Individuals & Families international health solutions

- Candidate ID: `henner`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_global_network_only
    - Why not fully sourced: Henner shows individuals-and-families living-abroad positioning and a 240-country healthcare-professional network, but Georgia was not explicitly captured.
    - Primary supporting source: Individuals & Families - Henner — https://www.henner.com/en/customers/individuals-families/
    - Evidence excerpt: Henner says it supports people living abroad and has a network of healthcare professionals in 240 countries.
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_not_named_global_network_only
    - Why not fully sourced: Henner’s reviewed public evidence supported international-family positioning, but did not directly verify Germany.
    - Primary supporting source: Individuals & Families - Henner — https://www.henner.com/en/customers/individuals-families/
    - Evidence excerpt: Henner says it has developed unique expertise in personal insurance for people living abroad, offers international health insurance tailored to all lifestyles, and works with healthcare professionals in 240 countries.
  - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources` → `missing_or_partial`
    - Basis: not_verified
    - Why not fully sourced: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Primary supporting source: Individuals & Families - Henner — https://www.henner.com/en/customers/individuals-families/
    - Evidence excerpt: Henner says it has developed unique expertise in personal insurance for people living abroad, offers international health insurance tailored to all lifestyles, and works with healthcare professionals in 240 countries.

## Globality Health — YouGenio individual international health insurance plans

- Candidate ID: `globality_health`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_residency_dropdown_unresolved
    - Why not fully sourced: Globality’s application flow uses a broad country list for future residency, but the extracted summary did not explicitly name Georgia even though the flow is country-driven.
    - Primary supporting source: Globality Health application / quote flow — https://application.globality-health.com/?locale=en
    - Evidence excerpt: The application requires a country of future residency from a broad country list and warns: “Please choose another country of future residency for the quotation.”
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_residence_rules_relevant_not_verified
    - Why not fully sourced: Globality surfaced Germany-relevant residency distinctions in related public evidence, but Germany coverage itself was not verified from the captured plan page.
    - Primary supporting source: International Health Insurance for Expats - Globality Health — https://www.globality-health.com/
    - Evidence excerpt: Globality Health describes itself as an international health insurer for expatriates and says its YouGenio individual plans offer comprehensive coverage with various plan levels tailored to personal needs.
  - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources` → `missing_or_partial`
    - Basis: not_verified
    - Why not fully sourced: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Primary supporting source: International Health Insurance for Expats - Globality Health — https://www.globality-health.com/
    - Evidence excerpt: Globality Health describes itself as an international health insurer for expatriates and says its YouGenio individual plans offer comprehensive coverage with various plan levels tailored to personal needs.

## Morgan Price International Healthcare — Evolution Health / Flexible Choices individual international healthcare plans

- Candidate ID: `morgan_price`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_residence_rules_relevant_not_verified
    - Why not fully sourced: Morgan Price publishes unusually concrete residence rules and area-of-cover wording, but Georgia itself was not named in the captured public text.
    - Primary supporting source: International Healthcare Insurance Plans for Expats | Morgan Price International Healthcare — https://www.morgan-price.com/individual/
    - Evidence excerpt: Morgan Price states some plans are only available to applicants whose primary residence is in named countries, and Flexible Choices applicants must reside outside the EU or EEA.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_home_country_cover_relevant
    - Why not fully sourced: Morgan Price exposes Europe/EU residency restrictions and home-country cover wording that are materially relevant to Germany.
    - Primary supporting source: International Healthcare Insurance Plans for Expats | Morgan Price International Healthcare — https://www.morgan-price.com/individual/
    - Evidence excerpt: Morgan Price says Evolution Health is an expatriate health insurance product for expatriates and eligible dependants, includes home-country cover excluding USA, and publishes policy wording and tables of benefits on the public page.
  - `worldwide_ex_us_status` → `indirectly_indicated` → `missing_or_partial`
    - Basis: strong_but_indirect
    - Why not fully sourced: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Primary supporting source: International Healthcare Insurance Plans for Expats | Morgan Price International Healthcare — https://www.morgan-price.com/individual/
    - Evidence excerpt: Morgan Price says Evolution Health is an expatriate health insurance product for expatriates and eligible dependants, includes home-country cover excluding USA, and publishes policy wording and tables of benefits on the public page.

## PassportCard — Expat Basic / Expat Comprehensive / Executive

- Candidate ID: `passportcard_global`
- Fully sourced required coverage details: `germany_country_coverage_assessment`
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_worldwide_ex_us_structure
    - Why not fully sourced: PassportCard provides clear worldwide-ex-USA territory structure and Germany home-visit wording, but Georgia itself was not captured in public text.
    - Primary supporting source: International health insurance for expats & nomads | PassportCard — https://www.passportcard.de/en/international-health-insurance/
    - Evidence excerpt: PassportCard says the contract currency is EUR for coverage worldwide excluding USA territories, and that some zones include treatment in Germany during home visits.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_home_country_reference
    - Primary supporting source: International health insurance for expats & nomads | PassportCard — https://www.passportcard.de/en/international-health-insurance/
    - Evidence excerpt: PassportCard says it offers international private health insurance for expats, students, and digital nomads, advertises worldwide medical coverage for long-term stays abroad, and shows three plan tiers with example EUR pricing.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International health insurance for expats & nomads | PassportCard — https://www.passportcard.de/en/international-health-insurance/
    - Evidence excerpt: PassportCard says it offers international private health insurance for expats, students, and digital nomads, advertises worldwide medical coverage for long-term stays abroad, and shows three plan tiers with example EUR pricing.

## VUMI — Absolute VIP / Universal VIP / Special VIP / Access VIP product family

- Candidate ID: `vumi`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_worldwide_only
    - Why not fully sourced: VUMI markets worldwide coverage for individuals and families, but no Georgia-specific country evidence was captured.
    - Primary supporting source: Home - VUMI — https://www.vumigroup.com/
    - Evidence excerpt: VUMI says it offers international health insurance with worldwide coverage and that families are protected anywhere, anytime.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_global_only
    - Why not fully sourced: VUMI shows broad worldwide positioning, but no captured source named Germany directly.
    - Primary supporting source: Home - VUMI — https://www.vumigroup.com/
    - Evidence excerpt: VUMI's homepage markets international health insurance with worldwide coverage, says its solutions protect you and your family anywhere anytime, and lists multiple long-term medical plan families plus a Europe region selector.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Home - VUMI — https://www.vumigroup.com/
    - Evidence excerpt: VUMI's homepage markets international health insurance with worldwide coverage, says its solutions protect you and your family anywhere anytime, and lists multiple long-term medical plan families plus a Europe region selector.

## Integra Global — yourLife / PremierLife / yourFamily / PremierFamily

- Candidate ID: `integra_global`
- Fully sourced required coverage details: `worldwide_ex_us_status`
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=True, broker_url=True

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_area_of_cover_structure
    - Why not fully sourced: Integra Global clearly exposes worldwide including or excluding US+Canada cover options, but Georgia itself was not named.
    - Primary supporting source: Integra Global | HCI Group Global — https://hcigroupglobal.com/integra-global/
    - Evidence excerpt: Integra says Cover 1 is worldwide including the US and Canada, Cover 2 is worldwide excluding the US and Canada, and coverage depends on the chosen region.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_area_of_cover_structure
    - Why not fully sourced: Integra’s reviewed broker-backed evidence points to selectable geography structures, but Germany was not directly named.
    - Primary supporting source: Integra Health Insurance | Integra Expat Medical Plan — https://www.internationalinsurance.com/integra-global/
    - Evidence excerpt: The broker page describes Integra Global as international health insurance for expatriates, globally mobile individuals, and families, and explicitly lists two coverage areas: worldwide and worldwide excluding the U.S., Canada, and their territories.
  - `worldwide_ex_us_status` → `explicitly_indicated` → `fully_sourced`
    - Basis: explicit
    - Primary supporting source: Integra Health Insurance | Integra Expat Medical Plan — https://www.internationalinsurance.com/integra-global/
    - Evidence excerpt: The broker page describes Integra Global as international health insurance for expatriates, globally mobile individuals, and families, and explicitly lists two coverage areas: worldwide and worldwide excluding the U.S., Canada, and their territories.

## MediHelp International — My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal

- Candidate ID: `medihelp_international`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_worldwide_only
    - Why not fully sourced: MediHelp describes My Global Health as international coverage with worldwide treatment choice, but Georgia was not named directly.
    - Primary supporting source: Individual plans | MediHelp International — https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans
    - Evidence excerpt: MediHelp describes My Global Health as international coverage with top medical services worldwide and freedom to choose the doctor and clinic.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_global_only
    - Why not fully sourced: MediHelp’s reviewed public evidence supports global positioning but did not name Germany directly.
    - Primary supporting source: Individual plans | MediHelp International — https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans
    - Evidence excerpt: MediHelp's official individual-plans page says My Global Health offers international coverage and top medical services worldwide, allows freedom to choose the doctor and clinic, and lists five individual plan tiers.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Individual plans | MediHelp International — https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans
    - Evidence excerpt: MediHelp's official individual-plans page says My Global Health offers international coverage and top medical services worldwide, allows freedom to choose the doctor and clinic, and lists five individual plan tiers.

## ALC Health — Global Prima Medical Insurance / Flying Colours legacy entrypoints

- Candidate ID: `alc_health`
- Fully sourced required coverage details: `germany_country_coverage_assessment`
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_country_pages_elsewhere
    - Why not fully sourced: ALC Health shows strong global-country marketing and a Germany regional page, but Georgia itself was not surfaced in captured text.
    - Primary supporting source: ALC Health home — https://www.alchealth.com/
    - Evidence excerpt: ALC describes global cover across many named countries and regions and links to a Germany page and 2026 downloads.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_country_pages_elsewhere
    - Primary supporting source: Global Health and International Medical Insurance For Expatriates — https://www.alchealth.com/
    - Evidence excerpt: ALC Health's homepage advertises global health and international medical insurance for expatriates, local nationals, and international travellers worldwide, includes an Individuals & Families path, and notes that new business quotes and sales moved to IMG's updated international website.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Global Health and International Medical Insurance For Expatriates — https://www.alchealth.com/
    - Evidence excerpt: ALC Health's homepage advertises global health and international medical insurance for expatriates, local nationals, and international travellers worldwide, includes an Individuals & Families path, and notes that new business quotes and sales moved to IMG's updated international website.

## Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) — Worldwide Premier / Outside U.S. / Outside U.S. Select

- Candidate ID: `geoblue_xplorer_bcbs_global_solutions`
- Fully sourced required coverage details: `worldwide_ex_us_status`
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=True, broker_url=True

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_outside_us_plan
    - Why not fully sourced: GeoBlue’s living-abroad page gives a clear Outside-the-U.S. plan structure, but Georgia was not named directly in the captured text.
    - Primary supporting source: Worldwide and Outside US | BCBS Global Solutions — https://bcbsglobalsolutions.com/individuals-and-families/international-medical-insurance/living-abroad/worldwide-and-outside-us/
    - Evidence excerpt: BCBS Global Solutions says customers can choose Worldwide Premier for comprehensive coverage anywhere in the world or Outside the U.S. if they do not need U.S. coverage.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_outside_us_plan
    - Why not fully sourced: GeoBlue/BCBS Global Solutions publicly verifies outside-U.S. plan structures, but Germany was not named directly in the captured evidence.
    - Primary supporting source: GeoBlue Health Insurance Plan - GeoBlue Xplorer — https://www.internationalinsurance.com/geoblue/xplorer/
    - Evidence excerpt: The broker page says the rebranded BCBS Global Solutions plans serve expatriates, global citizens, and families residing overseas, and that the Outside U.S. plan does not include U.S. coverage.
  - `worldwide_ex_us_status` → `explicitly_indicated` → `fully_sourced`
    - Basis: explicit
    - Primary supporting source: GeoBlue Health Insurance Plan - GeoBlue Xplorer — https://www.internationalinsurance.com/geoblue/xplorer/
    - Evidence excerpt: The broker page says the rebranded BCBS Global Solutions plans serve expatriates, global citizens, and families residing overseas, and that the Outside U.S. plan does not include U.S. coverage.

## SafetyWing — Nomad Insurance Complete

- Candidate ID: `safetywing_nomad_complete`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_global_only
    - Why not fully sourced: SafetyWing positions Nomad Insurance Complete for expats, digital nomads, and families with global provider access, but Georgia was not named.
    - Primary supporting source: SafetyWing | Complete Health And Travel Insurance For Nomads — https://explore.safetywing.com/nomad-insurance-complete
    - Evidence excerpt: SafetyWing says Nomad Insurance Complete is made for digital nomads, expats, and families living abroad, with global coverage and any licensed provider worldwide.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_global_only
    - Why not fully sourced: SafetyWing markets global family health insurance with licensed-provider access worldwide, but Germany was not named directly.
    - Primary supporting source: SafetyWing | Complete Health And Travel Insurance For Nomads — https://explore.safetywing.com/nomad-insurance-complete
    - Evidence excerpt: SafetyWing describes Nomad Insurance Complete as full health and travel insurance for digital nomads, expats, remote workers, and families living abroad, with global coverage and an add-on for Hong Kong, Singapore, and the US.
  - `worldwide_ex_us_status` → `indirectly_indicated` → `missing_or_partial`
    - Basis: strong_but_indirect
    - Why not fully sourced: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Primary supporting source: SafetyWing | Complete Health And Travel Insurance For Nomads — https://explore.safetywing.com/nomad-insurance-complete
    - Evidence excerpt: SafetyWing describes Nomad Insurance Complete as full health and travel insurance for digital nomads, expats, remote workers, and families living abroad, with global coverage and an add-on for Hong Kong, Singapore, and the US.

## Genki — Native / international health insurance

- Candidate ID: `genki_native`
- Fully sourced required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Missing or partial required coverage details: `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=False, broker_url=False

  - `georgia_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: georgia_explicitly_listed
    - Primary supporting source: Genki consultation flow — https://consultation.genki.world/v2
    - Evidence excerpt: The public options list includes Georgia and Germany, alongside messaging that users can join from anywhere.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_explicitly_listed
    - Primary supporting source: Genki • Health Insurance for Digital Nomads — https://genki.world/
    - Evidence excerpt: Genki says it offers two health-insurance options for digital nomads, including an international health insurance option, covers treatment at every licensed healthcare provider worldwide, and lets users get an instant price online.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Genki • Health Insurance for Digital Nomads — https://genki.world/
    - Evidence excerpt: Genki says it offers two health-insurance options for digital nomads, including an international health insurance option, covers treatment at every licensed healthcare provider worldwide, and lets users get an instant price online.

## ACS — ACS Expat

- Candidate ID: `acs_expat`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_worldwide_only
    - Why not fully sourced: ACS Expat is publicly framed as worldwide expatriate health insurance with trips outside the selected coverage area, but Georgia was not named directly.
    - Primary supporting source: ACS Expat — https://www.acs-ami.com/en/expat-health-insurance/acs-expat/
    - Evidence excerpt: ACS Expat says the plan covers international stays of 12+ months, worldwide healthcare access and support, and temporary stays worldwide outside the selected coverage area.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_home_country_cover_relevant
    - Why not fully sourced: ACS does not name Germany directly, but its public wording about home-country visit cover is materially relevant to Germany for this family.
    - Primary supporting source: ACS Expat - Customisable Expatriate Health Insurance Solution — https://www.acs-ami.com/en/expat-health-insurance/acs-expat/
    - Evidence excerpt: ACS Expat is presented as modular lifetime international health insurance for expatriates of all nationalities, available individually, as a couple, or as a family, with home-country visit coverage and geography-zone based cover.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: ACS Expat - Customisable Expatriate Health Insurance Solution — https://www.acs-ami.com/en/expat-health-insurance/acs-expat/
    - Evidence excerpt: ACS Expat is presented as modular lifetime international health insurance for expatriates of all nationalities, available individually, as a couple, or as a family, with home-country visit coverage and geography-zone based cover.

## HCI Group — Health Protect / Protector Plans / Nimbl Health

- Candidate ID: `hci_group_health_protect`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: georgia_not_named_global_only
    - Why not fully sourced: Health Protect is clearly an IPMI product for people living abroad, but Georgia was not named in the captured page text.
    - Primary supporting source: International Healthcare | Healthcare International | HCI Global — https://hcigroupglobal.com/health-protect/
    - Evidence excerpt: Health Protect is described as international healthcare for expatriates, digital nomads, international students, and others living abroad.
  - `germany_country_coverage_assessment` → `unknown` → `missing_or_partial`
    - Basis: germany_not_named_global_positioning_only
    - Why not fully sourced: HCI Group’s reviewed public evidence supports international coverage positioning, but no Germany-specific evidence was captured.
    - Primary supporting source: International Healthcare | Healthcare International | HCI Global — https://hcigroupglobal.com/health-protect/
    - Evidence excerpt: HCI Group says its Health Protect policies offer international private medical insurance for expatriates, digital nomads, international students, and others living abroad, with five Protector Plan levels from $500,000 to $2,500,000 and optional maternity/newborn benefits.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: International Healthcare | Healthcare International | HCI Global — https://hcigroupglobal.com/health-protect/
    - Evidence excerpt: HCI Group says its Health Protect policies offer international private medical insurance for expatriates, digital nomads, international students, and others living abroad, with five Protector Plan levels from $500,000 to $2,500,000 and optional maternity/newborn benefits.

## Expatriate Group — International Health Insurance / Select / Primary+ / Primary+ Lite / Primary

- Candidate ID: `expatriate_group_global_health`
- Fully sourced required coverage details: `worldwide_ex_us_status`
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_regionally_implied_not_named
    - Why not fully sourced: Expatriate Group’s public quote flow clearly defines Area 1, 2, and 3 geography, and Georgia is plausibly within Area 1’s Europe–Middle East–Africa–Asia–Oceania band, but Georgia was not named explicitly.
    - Primary supporting source: Expatriate Group healthcare quote — https://quote.expatriatehealthcare.com/healthcare/
    - Evidence excerpt: The form offers Area 1 – Europe, Middle East, Africa, Asia and Oceania; Area 2 – Worldwide excluding the USA; and Area 3 – Worldwide.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_regionally_implied_not_named
    - Why not fully sourced: Expatriate Group’s quote structure includes a Europe-centered area option, which makes Germany a strong regional inference even without direct naming.
    - Primary supporting source: International Health Insurance | Global Private Medical Cover — https://www.expatriatehealthcare.com/international-health-insurance/
    - Evidence excerpt: Expatriate Group markets international private medical insurance for expat families and individuals, provides four plan levels, and publicly shows area-of-cover options including Europe/Middle East/Africa/Asia/Oceania, worldwide excluding the USA, and worldwide.
  - `worldwide_ex_us_status` → `explicitly_indicated` → `fully_sourced`
    - Basis: explicit
    - Primary supporting source: International Health Insurance | Global Private Medical Cover — https://www.expatriatehealthcare.com/international-health-insurance/
    - Evidence excerpt: Expatriate Group markets international private medical insurance for expat families and individuals, provides four plan levels, and publicly shows area-of-cover options including Europe/Middle East/Africa/Asia/Oceania, worldwide excluding the USA, and worldwide.

## WellAway — EXPAT / Gold / Diamond / Expat Plus

- Candidate ID: `wellaway_expat`
- Fully sourced required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
- Missing or partial required coverage details: `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=True, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: georgia_explicitly_listed
    - Primary supporting source: Wellaway EXPAT quote Step 1 — https://portal.wellaway.com/Quote/EXPAT/Step1
    - Evidence excerpt: The country options include Georgia and Germany in the public quote form.
  - `germany_country_coverage_assessment` → `confirmed` → `fully_sourced`
    - Basis: germany_explicitly_listed
    - Primary supporting source: Expat health insurance plans for living abroad | WellAway — https://www.wellaway.com/en/our-plans/expat/
    - Evidence excerpt: WellAway presents EXPAT as a top-tier expat health insurance plan for individuals, families, and groups living abroad, with Gold, Diamond, and Expat Plus coverage levels and annual limits up to $3,000,000.
  - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources` → `missing_or_partial`
    - Basis: not_verified
    - Why not fully sourced: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Primary supporting source: Expat health insurance plans for living abroad | WellAway — https://www.wellaway.com/en/our-plans/expat/
    - Evidence excerpt: WellAway presents EXPAT as a top-tier expat health insurance plan for individuals, families, and groups living abroad, with Gold, Diamond, and Expat Plus coverage levels and annual limits up to $3,000,000.

## Securus International — Global Health Cover

- Candidate ID: `securus_global_health_cover`
- Fully sourced required coverage details: none
- Missing or partial required coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
- Verified source slots: official_url=True, quote_or_application_url=False, policy_wording_or_pdf_url=True, broker_url=False

  - `georgia_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: georgia_not_named_worldwide_only
    - Why not fully sourced: Securus publicly markets global health cover with treatment choice around the world, but Georgia-specific evidence was not captured.
    - Primary supporting source: Securus global health cover — https://www.securus.co.uk/benefits/global-health-cover
    - Evidence excerpt: Securus says global health cover gives access to the best hospitals and doctors around the world and links its 2026 benefit-plan PDFs.
  - `germany_country_coverage_assessment` → `inferred` → `missing_or_partial`
    - Basis: germany_not_named_global_only
    - Why not fully sourced: Securus uses global coverage wording, but the reviewed public evidence did not name Germany directly.
    - Primary supporting source: Securus - Global Health Cover — https://www.securus.co.uk/benefits/global-health-cover
    - Evidence excerpt: Securus says international health insurance is a necessity in a connected world, that its packages let clients choose who, how, and where they are treated, and that they provide access to hospitals and doctors around the world with public brochure links.
  - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified` → `missing_or_partial`
    - Basis: global_positioning_only
    - Why not fully sourced: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Primary supporting source: Securus - Global Health Cover — https://www.securus.co.uk/benefits/global-health-cover
    - Evidence excerpt: Securus says international health insurance is a necessity in a connected world, that its packages let clients choose who, how, and where they are treated, and that they provide access to hospitals and doctors around the world with public brochure links.

