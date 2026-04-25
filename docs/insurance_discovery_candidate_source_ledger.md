# Insurance discovery candidate source ledger

Discovery-only companion ledger for AC 40101 sub-AC 1. It maps the collected public source set to each staged insurer/plan, marks which sources are applicable to that candidate's evidence entry, and now classifies source directness for each applicable source.

Machine-readable source: `data/insurance_discovery/candidate_source_ledger.discovery.json`

Summary counts:
- total_candidates: 27
- with_official_url: 27
- with_quote_or_application_url: 20
- with_broker_url: 2
- with_policy_wording_or_pdf_url: 12
- total_mapped_sources: 62
- total_applicable_sources: 62
- source_directness_high: 27
- source_directness_medium: 32
- source_directness_low: 3
- evidence_entry_completeness_complete: 0
- evidence_entry_completeness_partial: 27
- evidence_entry_completeness_insufficiently_sourced: 0
- all_candidates_have_applicable_source_map: yes
- all_applicable_sources_have_directness_classification: yes
- all_candidates_have_evidence_entry_completeness_summary: yes

Notes:
- `source_set` contains both original collected source refs and, when needed, derived rows for verified quote/application or PDF document URLs captured elsewhere in the discovery artifacts.
- `applicability_roles` explains why a source belongs in the candidate evidence entry, such as candidate identification, access-path evidence, pricing/quote-path evidence, broker substitution, geography relevance, or plan-document support.
- `evidence_entry_source_map` gives per-candidate source-id lists for the main evidence roles without implying any final recommendation or ranking.
- `source_directness_classification` uses a simple discovery rubric: high = official quote/application path or official plan document/PDF; medium = official insurer landing/marketing page; low = broker/intermediary or other indirect public source.
- `evidence_entry_completeness_summary` adds a per-candidate completeness summary section that normalizes the audit result to `complete`, `partial`, or `insufficiently_sourced` based on the recorded evidence gaps.

| Candidate | Mapped sources | Applicable sources | Official | Quote/Application | Broker | Plan doc/PDF | Completeness | Evidence gaps |
|---|---|---|---|---|---|---|---|---|
| cigna_global | 2 | 2 | https://www.cignaglobal.com/ | https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html | — | — | `partial` | `worldwide_ex_us_status` |
| allianz_care | 3 | 3 | https://www.allianzcare.com/en/personal-international-health-insurance.html | https://my.allianzcare.com/myquote/5 | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| bupa_global | 2 | 2 | https://www.bupaglobal.com/en/private-health-insurance | https://www.bupaglobal.com/en/private-health-insurance | — | — | `partial` | `worldwide_ex_us_status` |
| axa_global_healthcare | 3 | 3 | https://www.axaglobalhealthcare.com/en/international-health-insurance/ | https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| april_international | 3 | 3 | https://www.april-international.com/en/long-term-international-health-insurance | https://www.april-international.com/en | — | — | `partial` | `worldwide_ex_us_status` |
| william_russell | 2 | 2 | https://www.william-russell.com/international-health-insurance/ | https://www.william-russell.com/international-health-insurance/ | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| msh_international | 3 | 3 | https://www.msh-intl.com/en/individuals/ | https://www.msh-intl.com/en/comparisons-offers-msh | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| now_health_international | 1 | 1 | https://www.now-health.com/en/insurance-plans/ | https://www.now-health.com/en/insurance-plans/ | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| img_global | 3 | 3 | https://www.imglobal.com/ | https://www.imglobal.com/international-health-insurance | — | https://www.imglobal.com/docs/library/forms-library/gmi-brochure.pdf | `partial` | `worldwide_ex_us_status` |
| aetna_international | 2 | 2 | https://www.aetnainternational.com/ | https://www.aetna.com/employers-organizations/aetna-international-insurance.html | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| foyer_global_health | 4 | 4 | https://www.foyerglobalhealth.com/ | https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/ | — | https://www.foyerglobalhealth.com/wp-content/uploads/2024/02/Brochure-commerciale-EN_V2-General.pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| henner | 1 | 1 | https://www.henner.com/en/customers/individuals-families/ | https://www.henner.com/en/customers/individuals-families/ | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| globality_health | 2 | 2 | https://www.globality-health.com/ | https://application.globality-health.com/?locale=en | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| morgan_price | 3 | 3 | https://www.morgan-price.com/individual/ | https://morgan-price.com/quick-quote/ | — | https://morgan-price.com/media/4ibjjwku/evolutionhealth_ant_policywording_04-26_v2-final.pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| passportcard_global | 2 | 2 | https://www.passportcard.de/en/international-health-insurance/ | — | — | https://www.passportcard.de/wp-content/uploads/2023/02/TOB_Individuals_ENG-MGAWP.pdf | `partial` | `georgia_country_coverage_assessment`, `worldwide_ex_us_status` |
| vumi | 1 | 1 | https://www.vumigroup.com/ | — | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| integra_global | 2 | 2 | https://hcigroupglobal.com/integra-global/ | — | https://www.internationalinsurance.com/integra-global/ | https://www.internationalinsurance.com/pdf-brochures/integra-global-brochure.pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` |
| medihelp_international | 1 | 1 | https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans | — | — | — | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| alc_health | 1 | 1 | https://www.alchealth.com/ | — | — | — | `partial` | `georgia_country_coverage_assessment`, `worldwide_ex_us_status` |
| geoblue_xplorer_bcbs_global_solutions | 2 | 2 | https://bcbsglobalsolutions.com/individuals-and-families/international-medical-insurance/living-abroad/worldwide-and-outside-us/ | — | https://www.internationalinsurance.com/geoblue/xplorer/ | https://c8s387h5.media.zestyio.com/4ELI-Medical-Coverage-for-Living-Abroad-Worldwide-Premier.HJBCYKZQhel.pdf?download=true | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` |
| safetywing_nomad_complete | 3 | 3 | https://explore.safetywing.com/nomad-insurance-complete | https://safetywing.com/signup?product=nomad-health | — | https://safetywing.com/nomad-insurance/complete/standard_nomad_insurance_complete_vumi+carrier_2024-12.pdf?referenceID=24734754&utm_source=24734754&utm_medium=Ambassador | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| genki_native | 2 | 2 | https://genki.world/ | https://consultation.genki.world/v2 | — | — | `partial` | `worldwide_ex_us_status` |
| acs_expat | 3 | 3 | https://www.acs-ami.com/en/expat-health-insurance/acs-expat/ | https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/ | — | https://www.acs-ami.com/files/pdf/ACS-Expat-ALC-IB-en.pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| hci_group_health_protect | 3 | 3 | https://hcigroupglobal.com/health-protect/ | https://hcigroup.outgrow.us/P21-2026 | — | https://hcigroupglobal.com/wp-content/uploads/2024/06/Inpatient.pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |
| expatriate_group_global_health | 3 | 3 | https://www.expatriatehealthcare.com/international-health-insurance/ | https://quote.expatriatehealthcare.com/healthcare/ | — | https://www.expatriatehealthcare.com/wp-content/uploads/2016/09/Expatriate-Group-Brochure-International-Insurance-Specialists.pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment` |
| wellaway_expat | 3 | 3 | https://www.wellaway.com/en/our-plans/expat/ | https://portal.wellaway.com/Quote/EXPAT/Step1 | — | https://www.wellaway.com/media/filer_public/31/5d/315d7f0b-9976-4fd2-8b43-ad597fd59b4a/expat-plans-brochure_1292026.pdf | `partial` | `worldwide_ex_us_status` |
| securus_global_health_cover | 2 | 2 | https://www.securus.co.uk/benefits/global-health-cover | — | — | https://www.securus.co.uk/storage/JZJIi-Securus%20Individual%20&%20Group%20Benefit%20Table%202026%20(USD).pdf | `partial` | `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status` |

## Cigna Global — International Health Insurance / Global Medical Cover

- Candidate ID: `cigna_global`
- Applicable source count: 2
- Applicable source directness classifications:
  - `cigna_global:source:1` — medium directness — official insurer page — https://www.cignaglobal.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `cigna_global:source:quote_path` — high directness — official quote page — https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - Recorded evidence gaps: `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Allianz Care — Care International Health Insurance Plans

- Candidate ID: `allianz_care`
- Applicable source count: 3
- Applicable source directness classifications:
  - `allianz_care:source:1` — medium directness — official insurer page — https://www.allianzcare.com/en/personal-international-health-insurance.html
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `allianz_care:source:2` — medium directness — official insurer page — https://www.allianzcare.com/en/personal-international-health-insurance.html
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `allianz_care:source:quote_path` — high directness — official quote page — https://my.allianzcare.com/myquote/5
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families

- Candidate ID: `bupa_global`
- Applicable source count: 2
- Applicable source directness classifications:
  - `bupa_global:source:1` — medium directness — official insurer page — https://www.bupaglobal.com/en/private-health-insurance
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `bupa_global:source:2` — medium directness — official insurer page — https://www.bupaglobal.com/en/private-health-insurance
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - Recorded evidence gaps: `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources`

## AXA Global Healthcare — International Health Insurance Plans

- Candidate ID: `axa_global_healthcare`
- Applicable source count: 3
- Applicable source directness classifications:
  - `axa_global_healthcare:source:1` — medium directness — official insurer page — https://www.axaglobalhealthcare.com/en/international-health-insurance/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `axa_global_healthcare:source:2` — medium directness — official insurer page — https://www.axaglobalhealthcare.com/en/international-health-insurance/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `axa_global_healthcare:source:quote_path` — high directness — official quote page — https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `indirectly_indicated`

## APRIL International — Long-term Expat International Health Insurance

- Candidate ID: `april_international`
- Applicable source count: 3
- Applicable source directness classifications:
  - `april_international:source:1` — medium directness — official insurer page — https://www.april-international.com/en/long-term-international-health-insurance
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `april_international:source:2` — medium directness — official insurer page — https://www.april-international.com/en/long-term-international-health-insurance
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `april_international:source:quote_path` — high directness — official quote page — https://www.april-international.com/en
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - Recorded evidence gaps: `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## William Russell — International Health Insurance

- Candidate ID: `william_russell`
- Applicable source count: 2
- Applicable source directness classifications:
  - `william_russell:source:1` — medium directness — official insurer page — https://www.william-russell.com/international-health-insurance/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `william_russell:source:2` — medium directness — official insurer page — https://www.william-russell.com/international-health-insurance/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## MSH International — First'Expat+ / International Health Insurance for Individuals

- Candidate ID: `msh_international`
- Applicable source count: 3
- Applicable source directness classifications:
  - `msh_international:source:1` — medium directness — official insurer page — https://www.msh-intl.com/en/individuals/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `msh_international:source:2` — medium directness — official insurer page — https://www.msh-intl.com/en/individuals/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `msh_international:source:quote_path` — high directness — official quote page — https://www.msh-intl.com/en/comparisons-offers-msh
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Now Health International — SimpleCare / WorldCare International Health Insurance Plans

- Candidate ID: `now_health_international`
- Applicable source count: 1
- Applicable source directness classifications:
  - `now_health_international:source:1` — medium directness — official insurer page — https://www.now-health.com/en/insurance-plans/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `indirectly_indicated`

## IMG (International Medical Group) — Global Medical international health insurance

- Candidate ID: `img_global`
- Applicable source count: 3
- Applicable source directness classifications:
  - `img_global:source:1` — medium directness — official insurer page — https://www.imglobal.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `img_global:source:quote_path` — high directness — official quote page — https://www.imglobal.com/international-health-insurance
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `img_global:source:plan_document` — high directness — policy wording or brochure PDF — https://www.imglobal.com/docs/library/forms-library/gmi-brochure.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - Recorded evidence gaps: `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Aetna International — Aetna International global healthcare entrypoint

- Candidate ID: `aetna_international`
- Applicable source count: 2
- Applicable source directness classifications:
  - `aetna_international:source:1` — medium directness — official insurer page — https://www.aetnainternational.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `aetna_international:source:quote_path` — high directness — official quote page — https://www.aetna.com/employers-organizations/aetna-international-insurance.html
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `indirectly_indicated`

## Foyer Global Health — Expat Health Insurance / International Health Insurance comparison

- Candidate ID: `foyer_global_health`
- Applicable source count: 4
- Applicable source directness classifications:
  - `foyer_global_health:source:1` — medium directness — official insurer page via search result — https://www.foyerglobalhealth.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `foyer_global_health:source:2` — medium directness — official insurer page via search result — https://www.foyerglobalhealth.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `foyer_global_health:source:quote_path` — high directness — official quote page — https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `foyer_global_health:source:plan_document` — high directness — policy wording or brochure PDF — https://www.foyerglobalhealth.com/wp-content/uploads/2024/02/Brochure-commerciale-EN_V2-General.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Henner — Individuals & Families international health solutions

- Candidate ID: `henner`
- Applicable source count: 1
- Applicable source directness classifications:
  - `henner:source:1` — medium directness — official insurer page — https://www.henner.com/en/customers/individuals-families/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources`

## Globality Health — YouGenio individual international health insurance plans

- Candidate ID: `globality_health`
- Applicable source count: 2
- Applicable source directness classifications:
  - `globality_health:source:1` — medium directness — official insurer page — https://www.globality-health.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `globality_health:source:quote_path` — high directness — official quote page — https://application.globality-health.com/?locale=en
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources`

## Morgan Price International Healthcare — Evolution Health / Flexible Choices individual international healthcare plans

- Candidate ID: `morgan_price`
- Applicable source count: 3
- Applicable source directness classifications:
  - `morgan_price:source:1` — medium directness — official insurer page — https://www.morgan-price.com/individual/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `morgan_price:source:quote_path` — high directness — official quote page — https://morgan-price.com/quick-quote/
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `morgan_price:source:plan_document` — high directness — policy wording or brochure PDF — https://morgan-price.com/media/4ibjjwku/evolutionhealth_ant_policywording_04-26_v2-final.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `indirectly_indicated`

## PassportCard — Expat Basic / Expat Comprehensive / Executive

- Candidate ID: `passportcard_global`
- Applicable source count: 2
- Applicable source directness classifications:
  - `passportcard_global:source:1` — medium directness — official insurer page — https://www.passportcard.de/en/international-health-insurance/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
  - `passportcard_global:source:plan_document` — high directness — policy wording or brochure PDF — https://www.passportcard.de/wp-content/uploads/2023/02/TOB_Individuals_ENG-MGAWP.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `germany_country_coverage_assessment`
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## VUMI — Absolute VIP / Universal VIP / Special VIP / Access VIP product family

- Candidate ID: `vumi`
- Applicable source count: 1
- Applicable source directness classifications:
  - `vumi:source:1` — medium directness — official insurer page — https://www.vumigroup.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Integra Global — yourLife / PremierLife / yourFamily / PremierFamily

- Candidate ID: `integra_global`
- Applicable source count: 2
- Applicable source directness classifications:
  - `integra_global:source:1` — low directness — broker page — https://www.internationalinsurance.com/integra-global/
    - Justification: Broker/intermediary-hosted source rather than an insurer-controlled primary source, so it is less direct even when useful for discovery.
  - `integra_global:source:plan_document` — low directness — policy wording or brochure PDF — https://www.internationalinsurance.com/pdf-brochures/integra-global-brochure.pdf
    - Justification: Broker/intermediary-hosted source rather than an insurer-controlled primary source, so it is less direct even when useful for discovery.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `worldwide_ex_us_status`
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`

## MediHelp International — My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal

- Candidate ID: `medihelp_international`
- Applicable source count: 1
- Applicable source directness classifications:
  - `medihelp_international:source:1` — medium directness — official insurer page — https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## ALC Health — Global Prima Medical Insurance / Flying Colours legacy entrypoints

- Candidate ID: `alc_health`
- Applicable source count: 1
- Applicable source directness classifications:
  - `alc_health:source:1` — medium directness — official insurer page — https://www.alchealth.com/
    - Justification: Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `germany_country_coverage_assessment`
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) — Worldwide Premier / Outside U.S. / Outside U.S. Select

- Candidate ID: `geoblue_xplorer_bcbs_global_solutions`
- Applicable source count: 2
- Applicable source directness classifications:
  - `geoblue_xplorer_bcbs_global_solutions:source:1` — low directness — broker page — https://www.internationalinsurance.com/geoblue/xplorer/
    - Justification: Broker/intermediary-hosted source rather than an insurer-controlled primary source, so it is less direct even when useful for discovery.
  - `geoblue_xplorer_bcbs_global_solutions:source:plan_document` — high directness — policy wording or brochure PDF — https://c8s387h5.media.zestyio.com/4ELI-Medical-Coverage-for-Living-Abroad-Worldwide-Premier.HJBCYKZQhel.pdf?download=true
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `worldwide_ex_us_status`
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`

## SafetyWing — Nomad Insurance Complete

- Candidate ID: `safetywing_nomad_complete`
- Applicable source count: 3
- Applicable source directness classifications:
  - `safetywing_nomad_complete:source:1` — medium directness — official insurer page — https://explore.safetywing.com/nomad-insurance-complete
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `safetywing_nomad_complete:source:quote_path` — high directness — official quote page — https://safetywing.com/signup?product=nomad-health
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `safetywing_nomad_complete:source:plan_document` — high directness — policy wording or brochure PDF — https://safetywing.com/nomad-insurance/complete/standard_nomad_insurance_complete_vumi+carrier_2024-12.pdf?referenceID=24734754&utm_source=24734754&utm_medium=Ambassador
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `indirectly_indicated`

## Genki — Native / international health insurance

- Candidate ID: `genki_native`
- Applicable source count: 2
- Applicable source directness classifications:
  - `genki_native:source:1` — medium directness — official insurer page — https://genki.world/
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `genki_native:source:quote_path` — high directness — official quote page — https://consultation.genki.world/v2
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - Recorded evidence gaps: `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## ACS — ACS Expat

- Candidate ID: `acs_expat`
- Applicable source count: 3
- Applicable source directness classifications:
  - `acs_expat:source:1` — medium directness — official insurer page — https://www.acs-ami.com/en/expat-health-insurance/acs-expat/
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `acs_expat:source:quote_path` — high directness — official quote page — https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `acs_expat:source:plan_document` — high directness — policy wording or brochure PDF — https://www.acs-ami.com/files/pdf/ACS-Expat-ALC-IB-en.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## HCI Group — Health Protect / Protector Plans / Nimbl Health

- Candidate ID: `hci_group_health_protect`
- Applicable source count: 3
- Applicable source directness classifications:
  - `hci_group_health_protect:source:1` — medium directness — official insurer page — https://hcigroupglobal.com/health-protect/
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `hci_group_health_protect:source:quote_path` — high directness — official quote page — https://hcigroup.outgrow.us/P21-2026
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `hci_group_health_protect:source:plan_document` — high directness — policy wording or brochure PDF — https://hcigroupglobal.com/wp-content/uploads/2024/06/Inpatient.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `unknown`
    - `germany_country_coverage_assessment` → `unknown`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`

## Expatriate Group — International Health Insurance / Select / Primary+ / Primary+ Lite / Primary

- Candidate ID: `expatriate_group_global_health`
- Applicable source count: 3
- Applicable source directness classifications:
  - `expatriate_group_global_health:source:1` — medium directness — official insurer page — https://www.expatriatehealthcare.com/international-health-insurance/
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `expatriate_group_global_health:source:quote_path` — high directness — official quote page — https://quote.expatriatehealthcare.com/healthcare/
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `expatriate_group_global_health:source:plan_document` — high directness — policy wording or brochure PDF — https://www.expatriatehealthcare.com/wp-content/uploads/2016/09/Expatriate-Group-Brochure-International-Insurance-Specialists.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `worldwide_ex_us_status`
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`

## WellAway — EXPAT / Gold / Diamond / Expat Plus

- Candidate ID: `wellaway_expat`
- Applicable source count: 3
- Applicable source directness classifications:
  - `wellaway_expat:source:1` — medium directness — official insurer page — https://www.wellaway.com/en/our-plans/expat/
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `wellaway_expat:source:quote_path` — high directness — official quote page — https://portal.wellaway.com/Quote/EXPAT/Step1
    - Justification: Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.
  - `wellaway_expat:source:plan_document` — high directness — policy wording or brochure PDF — https://www.wellaway.com/media/filer_public/31/5d/315d7f0b-9976-4fd2-8b43-ad597fd59b4a/expat-plans-brochure_1292026.pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`
  - Recorded evidence gaps: `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `worldwide_ex_us_status` → `not_verified_from_reviewed_sources`

## Securus International — Global Health Cover

- Candidate ID: `securus_global_health_cover`
- Applicable source count: 2
- Applicable source directness classifications:
  - `securus_global_health_cover:source:1` — medium directness — official insurer page — https://www.securus.co.uk/benefits/global-health-cover
    - Justification: Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.
  - `securus_global_health_cover:source:plan_document` — high directness — policy wording or brochure PDF — https://www.securus.co.uk/storage/JZJIi-Securus%20Individual%20&%20Group%20Benefit%20Table%202026%20(USD).pdf
    - Justification: Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.
- Completeness summary:
  - Classification: `partial`
  - Classification source value: `source_backed_partial_but_usable`
  - Reason: Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.
  - Required entry fields missing: none
  - Required coverage fields missing: none
  - Fully sourced coverage details: none
  - Recorded evidence gaps: `georgia_country_coverage_assessment`, `germany_country_coverage_assessment`, `worldwide_ex_us_status`
  - All required coverage details fully sourced: no
  - Inferred or unknown dimensions:
    - `georgia_country_coverage_assessment` → `inferred`
    - `germany_country_coverage_assessment` → `inferred`
    - `worldwide_ex_us_status` → `worldwide_positioned_but_ex_us_not_verified`
