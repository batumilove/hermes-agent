# Insurance discovery raw candidate capture

Discovery-only artifact for AC 70101 sub-AC 1. This file enumerates the staged discovery-source datasets and flattens every source-backed insurer/plan mention captured in `shared_candidate_staging.source_refs` for the German family of four living in Georgia.

Machine-readable source: `data/insurance_discovery/raw_candidate_capture.discovery.json`

Summary counts:
- verified_total_unique_candidates: 27
- raw_candidate_mentions: 34
- unique_candidates_covered_in_raw_capture: 27
- mentions_from_file_backed_source_datasets: 27
- mentions_from_staged_supplemental_public_sources: 7
- unique_source_urls_in_raw_capture: 27
- missing_candidates_from_raw_capture: none
- source_kind_broker_plan_page: 2
- source_kind_official_insurer_page: 31
- source_kind_official_insurer_page_via_search_result: 1

## Discovery source inventory

| Dataset path | Section | Used in raw capture | Raw mentions | Unique candidates | Source kinds seen | Note |
|---|---|---|---:|---:|---|---|
| data/insurance_discovery/global_providers.discovery.json | global_providers | yes | 11 | 11 | official insurer page, official insurer page via search result | Directly represented in shared_candidate_staging.source_refs and therefore contributes raw candidate mentions. |
| data/insurance_discovery/european_providers.discovery.json | european_providers | yes | 16 | 16 | broker plan page, official insurer page | Directly represented in shared_candidate_staging.source_refs and therefore contributes raw candidate mentions. |
| data/insurance_discovery/pricing_evidence_ledger.discovery.json | pricing_evidence_ledger | no | 0 | 0 | — | Present in shared_candidate_staging.source_datasets but not directly represented in source_refs for raw mention capture; used elsewhere in derivative coverage/pricing/access evidence. |
| data/insurance_discovery/global_coverage_evidence_ledger.discovery.json | global_coverage_evidence_ledger | no | 0 | 0 | — | Present in shared_candidate_staging.source_datasets but not directly represented in source_refs for raw mention capture; used elsewhere in derivative coverage/pricing/access evidence. |
| data/insurance_discovery/access_and_intake_evidence_ledger.discovery.json | access_and_intake_evidence_ledger | no | 0 | 0 | — | Present in shared_candidate_staging.source_datasets but not directly represented in source_refs for raw mention capture; used elsewhere in derivative coverage/pricing/access evidence. |
| data/insurance_discovery/eligibility_ambiguity_evidence_ledger.discovery.json | eligibility_ambiguity_evidence_ledger | no | 0 | 0 | — | Present in shared_candidate_staging.source_datasets but not directly represented in source_refs for raw mention capture; used elsewhere in derivative coverage/pricing/access evidence. |
| data/insurance_discovery/coverage_ambiguity_annotations.discovery.json | coverage_ambiguity_annotations | no | 0 | 0 | — | Present in shared_candidate_staging.source_datasets but not directly represented in source_refs for raw mention capture; used elsewhere in derivative coverage/pricing/access evidence. |
| staged-only (no file path) | supplemental_public_sources | yes | 7 | 7 | official insurer page | These raw mentions were staged directly inside shared_candidate_staging as supplemental public sources rather than imported from a separate upstream discovery dataset file. |

## Raw candidate capture

| Mention ID | Candidate ID | Insurer | Plan | Status | Source kind | Dataset | Source URL | Quote/Application |
|---|---|---|---|---|---|---|---|---|
| cigna_global:mention:1 | cigna_global | Cigna Global | International Health Insurance / Global Medical Cover | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.cignaglobal.com/ | https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html |
| allianz_care:mention:1 | allianz_care | Allianz Care | Care International Health Insurance Plans | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.allianzcare.com/en/personal-international-health-insurance.html | https://my.allianzcare.com/myquote/5 |
| allianz_care:mention:2 | allianz_care | Allianz Care | Care International Health Insurance Plans | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.allianzcare.com/en/personal-international-health-insurance.html | https://my.allianzcare.com/myquote/5 |
| bupa_global:mention:1 | bupa_global | Bupa Global | Private Health Insurance & Medical Insurance for Individuals and Families | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.bupaglobal.com/en/private-health-insurance | https://www.bupaglobal.com/en/private-health-insurance |
| bupa_global:mention:2 | bupa_global | Bupa Global | Private Health Insurance & Medical Insurance for Individuals and Families | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.bupaglobal.com/en/private-health-insurance | https://www.bupaglobal.com/en/private-health-insurance |
| axa_global_healthcare:mention:1 | axa_global_healthcare | AXA Global Healthcare | International Health Insurance Plans | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.axaglobalhealthcare.com/en/international-health-insurance/ | https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default |
| axa_global_healthcare:mention:2 | axa_global_healthcare | AXA Global Healthcare | International Health Insurance Plans | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.axaglobalhealthcare.com/en/international-health-insurance/ | https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default |
| april_international:mention:1 | april_international | APRIL International | Long-term Expat International Health Insurance | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.april-international.com/en/long-term-international-health-insurance | https://www.april-international.com/en |
| april_international:mention:2 | april_international | APRIL International | Long-term Expat International Health Insurance | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.april-international.com/en/long-term-international-health-insurance | https://www.april-international.com/en |
| william_russell:mention:1 | william_russell | William Russell | International Health Insurance | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.william-russell.com/international-health-insurance/ | https://www.william-russell.com/international-health-insurance/ |
| william_russell:mention:2 | william_russell | William Russell | International Health Insurance | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.william-russell.com/international-health-insurance/ | https://apps.william-russell.com/quote/individual/ |
| msh_international:mention:1 | msh_international | MSH International | First'Expat+ / International Health Insurance for Individuals | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.msh-intl.com/en/individuals/ | https://www.msh-intl.com/en/comparisons-offers-msh |
| msh_international:mention:2 | msh_international | MSH International | First'Expat+ / International Health Insurance for Individuals | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.msh-intl.com/en/individuals/ | https://www.msh-intl.com/en/comparisons-offers-msh |
| now_health_international:mention:1 | now_health_international | Now Health International | SimpleCare / WorldCare International Health Insurance Plans | likely_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.now-health.com/en/insurance-plans/ | https://www.now-health.com/en/insurance-plans/ |
| img_global:mention:1 | img_global | IMG (International Medical Group) | Global Medical international health insurance | possibly_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.imglobal.com/ | https://www.imglobal.com/international-health-insurance |
| aetna_international:mention:1 | aetna_international | Aetna International | Aetna International global healthcare entrypoint | possibly_relevant | official insurer page | data/insurance_discovery/global_providers.discovery.json | https://www.aetnainternational.com/ | https://www.aetna.com/employers-organizations/aetna-international-insurance.html |
| foyer_global_health:mention:1 | foyer_global_health | Foyer Global Health | Expat Health Insurance / International Health Insurance comparison | likely_relevant | official insurer page via search result | data/insurance_discovery/global_providers.discovery.json | https://www.foyerglobalhealth.com/ | https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/ |
| foyer_global_health:mention:2 | foyer_global_health | Foyer Global Health | Expat Health Insurance / International Health Insurance comparison | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.foyerglobalhealth.com/ | https://www.foyerglobalhealth.com/quick-quote-and-online-application/ |
| henner:mention:1 | henner | Henner | Individuals & Families international health solutions | possibly_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.henner.com/en/customers/individuals-families/ | https://www.henner.com/en/customers/individuals-families/ |
| globality_health:mention:1 | globality_health | Globality Health | YouGenio individual international health insurance plans | possibly_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.globality-health.com/ | https://application.globality-health.com/?locale=en |
| morgan_price:mention:1 | morgan_price | Morgan Price International Healthcare | Evolution Health / Flexible Choices individual international healthcare plans | likely_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.morgan-price.com/individual/ | https://morgan-price.com/quick-quote/ |
| passportcard_global:mention:1 | passportcard_global | PassportCard | Expat Basic / Expat Comprehensive / Executive | possibly_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.passportcard.de/en/international-health-insurance/ | — |
| vumi:mention:1 | vumi | VUMI | Absolute VIP / Universal VIP / Special VIP / Access VIP product family | possibly_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.vumigroup.com/ | — |
| integra_global:mention:1 | integra_global | Integra Global | yourLife / PremierLife / yourFamily / PremierFamily | possibly_relevant | broker plan page | data/insurance_discovery/european_providers.discovery.json | https://www.internationalinsurance.com/integra-global/ | — |
| medihelp_international:mention:1 | medihelp_international | MediHelp International | My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal | possibly_relevant | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans | — |
| alc_health:mention:1 | alc_health | ALC Health | Global Prima Medical Insurance / Flying Colours legacy entrypoints | excluded | official insurer page | data/insurance_discovery/european_providers.discovery.json | https://www.alchealth.com/ | — |
| geoblue_xplorer_bcbs_global_solutions:mention:1 | geoblue_xplorer_bcbs_global_solutions | Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) | Worldwide Premier / Outside U.S. / Outside U.S. Select | excluded | broker plan page | data/insurance_discovery/european_providers.discovery.json | https://www.internationalinsurance.com/geoblue/xplorer/ | — |
| safetywing_nomad_complete:mention:1 | safetywing_nomad_complete | SafetyWing | Nomad Insurance Complete | likely_relevant | official insurer page | staged-only | https://explore.safetywing.com/nomad-insurance-complete | https://safetywing.com/signup?product=nomad-health |
| genki_native:mention:1 | genki_native | Genki | Native / international health insurance | possibly_relevant | official insurer page | staged-only | https://genki.world/ | https://consultation.genki.world/v2 |
| acs_expat:mention:1 | acs_expat | ACS | ACS Expat | likely_relevant | official insurer page | staged-only | https://www.acs-ami.com/en/expat-health-insurance/acs-expat/ | https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/ |
| hci_group_health_protect:mention:1 | hci_group_health_protect | HCI Group | Health Protect / Protector Plans / Nimbl Health | possibly_relevant | official insurer page | staged-only | https://hcigroupglobal.com/health-protect/ | https://hcigroup.outgrow.us/P21-2026 |
| expatriate_group_global_health:mention:1 | expatriate_group_global_health | Expatriate Group | International Health Insurance / Select / Primary+ / Primary+ Lite / Primary | likely_relevant | official insurer page | staged-only | https://www.expatriatehealthcare.com/international-health-insurance/ | https://quote.expatriatehealthcare.com/healthcare/ |
| wellaway_expat:mention:1 | wellaway_expat | WellAway | EXPAT / Gold / Diamond / Expat Plus | excluded | official insurer page | staged-only | https://www.wellaway.com/en/our-plans/expat/ | https://portal.wellaway.com/Quote/EXPAT/Step1 |
| securus_global_health_cover:mention:1 | securus_global_health_cover | Securus International | Global Health Cover | possibly_relevant | official insurer page | staged-only | https://www.securus.co.uk/benefits/global-health-cover | — |

### Cigna Global — International Health Insurance / Global Medical Cover (cigna_global:mention:1)

- Candidate ID: `cigna_global`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance & Global Medical Cover | Cigna
- Source URL: https://www.cignaglobal.com/
- Quote/Application URL: https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html
- Evidence excerpt: Cigna says its international health insurance serves individuals and families, offers access to medical support in over 200 markets and territories, and provides flexible plans with global medical care essentials.

### Allianz Care — Care International Health Insurance Plans (allianz_care:mention:1)

- Candidate ID: `allianz_care`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance for Individuals | Allianz
- Source URL: https://www.allianzcare.com/en/personal-international-health-insurance.html
- Quote/Application URL: https://my.allianzcare.com/myquote/5
- Evidence excerpt: Allianz states its Care international health insurance plans are designed for people who spend long periods overseas and offers dedicated paths for professionals, families, students, and nomads.

### Allianz Care — Care International Health Insurance Plans (allianz_care:mention:2)

- Candidate ID: `allianz_care`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance for Individuals | Allianz
- Source URL: https://www.allianzcare.com/en/personal-international-health-insurance.html
- Quote/Application URL: https://my.allianzcare.com/myquote/5
- Evidence excerpt: Allianz says its Care plans are designed for people who spend long periods overseas and are aimed at individuals, families, students, nomads, professionals, and expats living, working, or studying abroad.

### Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families (bupa_global:mention:1)

- Candidate ID: `bupa_global`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Private Health Insurance & Medical Insurance | Bupa Global
- Source URL: https://www.bupaglobal.com/en/private-health-insurance
- Quote/Application URL: https://www.bupaglobal.com/en/private-health-insurance
- Evidence excerpt: Bupa Global's official plan selector for individuals and families includes both Georgia and Germany in the residence-country dropdown, indicating public-facing availability exploration for those markets.

### Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families (bupa_global:mention:2)

- Candidate ID: `bupa_global`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Private Health Insurance & Medical Insurance | Bupa Global
- Source URL: https://www.bupaglobal.com/en/private-health-insurance
- Quote/Application URL: https://www.bupaglobal.com/en/private-health-insurance
- Evidence excerpt: Bupa Global's official individuals-and-families selector asks where the customer will be living and publicly lists both Georgia and Germany in the residence-country dropdown.

### AXA Global Healthcare — International Health Insurance Plans (axa_global_healthcare:mention:1)

- Candidate ID: `axa_global_healthcare`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance Plans: AXA Global Healthcare
- Source URL: https://www.axaglobalhealthcare.com/en/international-health-insurance/
- Quote/Application URL: https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default
- Evidence excerpt: AXA describes its product as international private health insurance for people living abroad or travelling overseas frequently, with treatment anywhere within the selected region of cover.

### AXA Global Healthcare — International Health Insurance Plans (axa_global_healthcare:mention:2)

- Candidate ID: `axa_global_healthcare`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance Plans: AXA Global Healthcare
- Source URL: https://www.axaglobalhealthcare.com/en/international-health-insurance/
- Quote/Application URL: https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default
- Evidence excerpt: AXA describes international health insurance for people living abroad or travelling overseas frequently, with five cover levels and an explicit step allowing customers to choose whether to include the USA.

### APRIL International — Long-term Expat International Health Insurance (april_international:mention:1)

- Candidate ID: `april_international`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Long-term expat international health insurance | APRIL International
- Source URL: https://www.april-international.com/en/long-term-international-health-insurance
- Quote/Application URL: https://www.april-international.com/en
- Evidence excerpt: APRIL positions this as long-term international health insurance for expatriates and globally mobile individuals staying abroad longer than 12 months, with worldwide cover depending on area of cover. The country selector excerpt includes Germany.

### APRIL International — Long-term Expat International Health Insurance (april_international:mention:2)

- Candidate ID: `april_international`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Long-term expat international health insurance | APRIL International
- Source URL: https://www.april-international.com/en/long-term-international-health-insurance
- Quote/Application URL: https://www.april-international.com/en
- Evidence excerpt: APRIL positions the product for expatriates and globally mobile people staying abroad longer than 12 months, says policies are annually renewable, and states that worldwide cover depends on the selected geographic area.

### William Russell — International Health Insurance (william_russell:mention:1)

- Candidate ID: `william_russell`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance | William Russell
- Source URL: https://www.william-russell.com/international-health-insurance/
- Quote/Application URL: https://www.william-russell.com/international-health-insurance/
- Evidence excerpt: William Russell describes its product as international health insurance for expats and people living, working, or studying abroad, with members able to choose doctors or hospitals within their coverage zone.

### William Russell — International Health Insurance (william_russell:mention:2)

- Candidate ID: `william_russell`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance | William Russell
- Source URL: https://www.william-russell.com/international-health-insurance/
- Quote/Application URL: https://apps.william-russell.com/quote/individual/
- Evidence excerpt: William Russell markets international health insurance for expats, digital nomads, families, and students living, working, or studying abroad, with freedom to choose any doctor or hospital within the coverage zone.

### MSH International — First'Expat+ / International Health Insurance for Individuals (msh_international:mention:1)

- Candidate ID: `msh_international`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance for Individuals - MSH
- Source URL: https://www.msh-intl.com/en/individuals/
- Quote/Application URL: https://www.msh-intl.com/en/comparisons-offers-msh
- Evidence excerpt: MSH presents First'Expat+ as a comprehensive, flexible international health insurance plan for individuals and frames itself as a healthcare partner for mobility needs with 24/7 support.

### MSH International — First'Expat+ / International Health Insurance for Individuals (msh_international:mention:2)

- Candidate ID: `msh_international`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance for Individuals - MSH
- Source URL: https://www.msh-intl.com/en/individuals/
- Quote/Application URL: https://www.msh-intl.com/en/comparisons-offers-msh
- Evidence excerpt: MSH presents First'Expat+ as a comprehensive, flexible international health insurance plan with 100% coverage and frames itself as a healthcare partner for mobility needs.

### Now Health International — SimpleCare / WorldCare International Health Insurance Plans (now_health_international:mention:1)

- Candidate ID: `now_health_international`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Compare International Health Insurance Plans from Now Health
- Source URL: https://www.now-health.com/en/insurance-plans/
- Quote/Application URL: https://www.now-health.com/en/insurance-plans/
- Evidence excerpt: Now Health compares seven international plan options across SimpleCare and WorldCare product lines and includes evacuation/repatriation and optional USA treatment mechanics in the public comparison table.

### IMG (International Medical Group) — Global Medical international health insurance (img_global:mention:1)

- Candidate ID: `img_global`
- Candidate type: `primary`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health & Travel Medical Insurance - IMG
- Source URL: https://www.imglobal.com/
- Quote/Application URL: https://www.imglobal.com/international-health-insurance
- Evidence excerpt: IMG states that its international health insurance offers annually renewable private medical coverage for expats and global citizens living or working internationally, with Global Medical Silver, Gold, and Platinum plans.

### Aetna International — Aetna International global healthcare entrypoint (aetna_international:mention:1)

- Candidate ID: `aetna_international`
- Candidate type: `primary`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Home | Aetna
- Source URL: https://www.aetnainternational.com/
- Quote/Application URL: https://www.aetna.com/employers-organizations/aetna-international-insurance.html
- Evidence excerpt: Aetna International says it serves globally mobile members with access to quality care and support wherever they are, with provider network access in 200+ countries and territories.

### Foyer Global Health — Expat Health Insurance / International Health Insurance comparison (foyer_global_health:mention:1)

- Candidate ID: `foyer_global_health`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/global_providers.discovery.json` / section `global_providers` / bucket `records`
- Source kind: `official insurer page via search result`
- Source title: Foyer Global Health: Expat Health Insurance
- Source URL: https://www.foyerglobalhealth.com/
- Quote/Application URL: https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/
- Evidence excerpt: The official search result describes Foyer Global Health as an international health insurance and service provider for digital nomads, expats, and globally mobile people; a public plan comparison page is also available.

### Foyer Global Health — Expat Health Insurance / International Health Insurance comparison (foyer_global_health:mention:2)

- Candidate ID: `foyer_global_health`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Expat Health Insurance | Foyer Global Health
- Source URL: https://www.foyerglobalhealth.com/
- Quote/Application URL: https://www.foyerglobalhealth.com/quick-quote-and-online-application/
- Evidence excerpt: Foyer Global Health says it provides international health insurance for expats, digital nomads, globally mobile people, and families moving abroad, and describes its health coverage as worldwide and flexible.

### Henner — Individuals & Families international health solutions (henner:mention:1)

- Candidate ID: `henner`
- Candidate type: `primary`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: Individuals & Families - Henner
- Source URL: https://www.henner.com/en/customers/individuals-families/
- Quote/Application URL: https://www.henner.com/en/customers/individuals-families/
- Evidence excerpt: Henner says it has developed unique expertise in personal insurance for people living abroad, offers international health insurance tailored to all lifestyles, and works with healthcare professionals in 240 countries.

### Globality Health — YouGenio individual international health insurance plans (globality_health:mention:1)

- Candidate ID: `globality_health`
- Candidate type: `primary`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Health Insurance for Expats - Globality Health
- Source URL: https://www.globality-health.com/
- Quote/Application URL: https://application.globality-health.com/?locale=en
- Evidence excerpt: Globality Health describes itself as an international health insurer for expatriates and says its YouGenio individual plans offer comprehensive coverage with various plan levels tailored to personal needs.

### Morgan Price International Healthcare — Evolution Health / Flexible Choices individual international healthcare plans (morgan_price:mention:1)

- Candidate ID: `morgan_price`
- Candidate type: `primary`
- Relevance status: `likely_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `records`
- Source kind: `official insurer page`
- Source title: International Healthcare Insurance Plans for Expats | Morgan Price International Healthcare
- Source URL: https://www.morgan-price.com/individual/
- Quote/Application URL: https://morgan-price.com/quick-quote/
- Evidence excerpt: Morgan Price says Evolution Health is an expatriate health insurance product for expatriates and eligible dependants, includes home-country cover excluding USA, and publishes policy wording and tables of benefits on the public page.

### PassportCard — Expat Basic / Expat Comprehensive / Executive (passportcard_global:mention:1)

- Candidate ID: `passportcard_global`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `supplemental_candidates`
- Source kind: `official insurer page`
- Source title: International health insurance for expats & nomads | PassportCard
- Source URL: https://www.passportcard.de/en/international-health-insurance/
- Quote/Application URL: —
- Evidence excerpt: PassportCard says it offers international private health insurance for expats, students, and digital nomads, advertises worldwide medical coverage for long-term stays abroad, and shows three plan tiers with example EUR pricing.

### VUMI — Absolute VIP / Universal VIP / Special VIP / Access VIP product family (vumi:mention:1)

- Candidate ID: `vumi`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `supplemental_candidates`
- Source kind: `official insurer page`
- Source title: Home - VUMI
- Source URL: https://www.vumigroup.com/
- Quote/Application URL: —
- Evidence excerpt: VUMI's homepage markets international health insurance with worldwide coverage, says its solutions protect you and your family anywhere anytime, and lists multiple long-term medical plan families plus a Europe region selector.

### Integra Global — yourLife / PremierLife / yourFamily / PremierFamily (integra_global:mention:1)

- Candidate ID: `integra_global`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `supplemental_candidates`
- Source kind: `broker plan page`
- Source title: Integra Health Insurance | Integra Expat Medical Plan
- Source URL: https://www.internationalinsurance.com/integra-global/
- Quote/Application URL: —
- Evidence excerpt: The broker page describes Integra Global as international health insurance for expatriates, globally mobile individuals, and families, and explicitly lists two coverage areas: worldwide and worldwide excluding the U.S., Canada, and their territories.

### MediHelp International — My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal (medihelp_international:mention:1)

- Candidate ID: `medihelp_international`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `supplemental_candidates`
- Source kind: `official insurer page`
- Source title: Individual plans | MediHelp International
- Source URL: https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans
- Quote/Application URL: —
- Evidence excerpt: MediHelp's official individual-plans page says My Global Health offers international coverage and top medical services worldwide, allows freedom to choose the doctor and clinic, and lists five individual plan tiers.

### ALC Health — Global Prima Medical Insurance / Flying Colours legacy entrypoints (alc_health:mention:1)

- Candidate ID: `alc_health`
- Candidate type: `supplemental`
- Relevance status: `excluded`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `supplemental_candidates`
- Source kind: `official insurer page`
- Source title: Global Health and International Medical Insurance For Expatriates
- Source URL: https://www.alchealth.com/
- Quote/Application URL: —
- Evidence excerpt: ALC Health's homepage advertises global health and international medical insurance for expatriates, local nationals, and international travellers worldwide, includes an Individuals & Families path, and notes that new business quotes and sales moved to IMG's updated international website.

### Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) — Worldwide Premier / Outside U.S. / Outside U.S. Select (geoblue_xplorer_bcbs_global_solutions:mention:1)

- Candidate ID: `geoblue_xplorer_bcbs_global_solutions`
- Candidate type: `supplemental`
- Relevance status: `excluded`
- Source dataset: `data/insurance_discovery/european_providers.discovery.json` / section `european_providers` / bucket `supplemental_candidates`
- Source kind: `broker plan page`
- Source title: GeoBlue Health Insurance Plan - GeoBlue Xplorer
- Source URL: https://www.internationalinsurance.com/geoblue/xplorer/
- Quote/Application URL: —
- Evidence excerpt: The broker page says the rebranded BCBS Global Solutions plans serve expatriates, global citizens, and families residing overseas, and that the Outside U.S. plan does not include U.S. coverage.

### SafetyWing — Nomad Insurance Complete (safetywing_nomad_complete:mention:1)

- Candidate ID: `safetywing_nomad_complete`
- Candidate type: `supplemental`
- Relevance status: `likely_relevant`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: SafetyWing | Complete Health And Travel Insurance For Nomads
- Source URL: https://explore.safetywing.com/nomad-insurance-complete
- Quote/Application URL: https://safetywing.com/signup?product=nomad-health
- Evidence excerpt: SafetyWing describes Nomad Insurance Complete as full health and travel insurance for digital nomads, expats, remote workers, and families living abroad, with global coverage and an add-on for Hong Kong, Singapore, and the US.

### Genki — Native / international health insurance (genki_native:mention:1)

- Candidate ID: `genki_native`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: Genki • Health Insurance for Digital Nomads
- Source URL: https://genki.world/
- Quote/Application URL: https://consultation.genki.world/v2
- Evidence excerpt: Genki says it offers two health-insurance options for digital nomads, including an international health insurance option, covers treatment at every licensed healthcare provider worldwide, and lets users get an instant price online.

### ACS — ACS Expat (acs_expat:mention:1)

- Candidate ID: `acs_expat`
- Candidate type: `supplemental`
- Relevance status: `likely_relevant`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: ACS Expat - Customisable Expatriate Health Insurance Solution
- Source URL: https://www.acs-ami.com/en/expat-health-insurance/acs-expat/
- Quote/Application URL: https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/
- Evidence excerpt: ACS Expat is presented as modular lifetime international health insurance for expatriates of all nationalities, available individually, as a couple, or as a family, with home-country visit coverage and geography-zone based cover.

### HCI Group — Health Protect / Protector Plans / Nimbl Health (hci_group_health_protect:mention:1)

- Candidate ID: `hci_group_health_protect`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: International Healthcare | Healthcare International | HCI Global
- Source URL: https://hcigroupglobal.com/health-protect/
- Quote/Application URL: https://hcigroup.outgrow.us/P21-2026
- Evidence excerpt: HCI Group says its Health Protect policies offer international private medical insurance for expatriates, digital nomads, international students, and others living abroad, with five Protector Plan levels from $500,000 to $2,500,000 and optional maternity/newborn benefits.

### Expatriate Group — International Health Insurance / Select / Primary+ / Primary+ Lite / Primary (expatriate_group_global_health:mention:1)

- Candidate ID: `expatriate_group_global_health`
- Candidate type: `supplemental`
- Relevance status: `likely_relevant`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: International Health Insurance | Global Private Medical Cover
- Source URL: https://www.expatriatehealthcare.com/international-health-insurance/
- Quote/Application URL: https://quote.expatriatehealthcare.com/healthcare/
- Evidence excerpt: Expatriate Group markets international private medical insurance for expat families and individuals, provides four plan levels, and publicly shows area-of-cover options including Europe/Middle East/Africa/Asia/Oceania, worldwide excluding the USA, and worldwide.

### WellAway — EXPAT / Gold / Diamond / Expat Plus (wellaway_expat:mention:1)

- Candidate ID: `wellaway_expat`
- Candidate type: `supplemental`
- Relevance status: `excluded`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: Expat health insurance plans for living abroad | WellAway
- Source URL: https://www.wellaway.com/en/our-plans/expat/
- Quote/Application URL: https://portal.wellaway.com/Quote/EXPAT/Step1
- Evidence excerpt: WellAway presents EXPAT as a top-tier expat health insurance plan for individuals, families, and groups living abroad, with Gold, Diamond, and Expat Plus coverage levels and annual limits up to $3,000,000.

### Securus International — Global Health Cover (securus_global_health_cover:mention:1)

- Candidate ID: `securus_global_health_cover`
- Candidate type: `supplemental`
- Relevance status: `possibly_relevant`
- Source dataset: `staged-only (supplemental public source)` / section `supplemental_public_sources` / bucket `master_index_additions`
- Source kind: `official insurer page`
- Source title: Securus - Global Health Cover
- Source URL: https://www.securus.co.uk/benefits/global-health-cover
- Quote/Application URL: —
- Evidence excerpt: Securus says international health insurance is a necessity in a connected world, that its packages let clients choose who, how, and where they are treated, and that they provide access to hospitals and doctors around the world with public brochure links.

