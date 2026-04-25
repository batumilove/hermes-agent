# Insurance discovery: plan material public-access ledger

Discovery-only companion ledger for AC 50102 sub-AC 2. It records whether each staged insurer/plan publicly exposed terms/conditions, policy wording, or benefit schedules in the reviewed sources for this run.

Machine-readable source: `data/insurance_discovery/plan_material_public_access_ledger.discovery.json`

Summary counts:
- total_candidates: 27
- public_policy_wording_verified_or_likely_count: 2
- public_benefit_schedule_verified_count: 5
- generic_terms_only_count: 2
- public_brochure_only_count: 5
- public_plan_description_only_count: 1
- materials_missing_or_blocked_count: 12
- partial_or_js_placeholder_count: 1

Notes:
- “Publicly accessible” in this ledger means accessible from the reviewed public page/PDF evidence captured in this run, not merely mentioned in marketing copy.
- Brochure-only access is kept separate from public policy wording or a public table of benefits.
- Generic website terms/conditions pages are recorded when visible, but they are not treated as plan-specific insurance wording unless the source tied them directly to the product.
- Missing/blocked means not found in reviewed public sources in this run; it does not prove the insurer lacks those materials behind quote, broker, alternate-locale, or login flows.

| Candidate | Terms/conditions | Policy wording | Benefit schedule | Overall assessment | Verified doc URL |
|---|---|---|---|---|---|
| cigna_global | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | no_plan_material_publicly_verified | — |
| allianz_care | not_verified_due_to_access_or_review_limits | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| bupa_global | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| axa_global_healthcare | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| april_international | public_generic_website_terms_verified_but_not_plan_specific | no_public_policy_wording_verified | no_public_benefit_schedule_verified | generic_terms_only_no_plan_material_publicly_verified | — |
| william_russell | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | no_plan_material_publicly_verified | — |
| msh_international | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| now_health_international | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| img_global | no_public_terms_conditions_material_verified | brochure_only_policy_wording_not_verified | brochure_only_benefit_schedule_not_verified | public_brochure_only | https://www.imglobal.com/docs/library/forms-library/gmi-brochure.pdf |
| aetna_international | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| foyer_global_health | no_public_terms_conditions_material_verified | brochure_only_policy_wording_not_verified | brochure_only_benefit_schedule_not_verified | public_brochure_only | https://www.foyerglobalhealth.com/wp-content/uploads/2024/02/Brochure-commerciale-EN_V2-General.pdf |
| henner | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| globality_health | no_public_terms_conditions_material_verified | not_publicly_verified_because_document_link_placeholder_or_js_gated | not_publicly_verified_because_document_link_placeholder_or_js_gated | partially_exposed_but_not_retrievable_in_run | — |
| morgan_price | no_public_terms_conditions_material_verified | public_policy_wording_verified | public_benefit_schedule_verified | policy_wording_and_benefit_schedule_publicly_verified | https://morgan-price.com/media/4ibjjwku/evolutionhealth_ant_policywording_04-26_v2-final.pdf |
| passportcard_global | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | public_benefit_schedule_verified | public_benefit_schedule_only | https://www.passportcard.de/wp-content/uploads/2023/02/TOB_Individuals_ENG-MGAWP.pdf |
| vumi | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| integra_global | no_public_terms_conditions_material_verified | brochure_only_policy_wording_not_verified | brochure_only_benefit_schedule_not_verified | public_brochure_only | https://www.internationalinsurance.com/pdf-brochures/integra-global-brochure.pdf |
| medihelp_international | public_generic_website_terms_page_verified_but_not_plan_specific | no_public_policy_wording_verified | no_public_benefit_schedule_verified | generic_terms_only_no_plan_material_publicly_verified | — |
| alc_health | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| geoblue_xplorer_bcbs_global_solutions | no_public_terms_conditions_material_verified | plan_description_public_but_policy_wording_not_verified | plan_description_public_but_benefit_schedule_not_verified | public_plan_description_only | https://c8s387h5.media.zestyio.com/4ELI-Medical-Coverage-for-Living-Abroad-Worldwide-Premier.HJBCYKZQhel.pdf?download=true |
| safetywing_nomad_complete | no_public_terms_conditions_material_verified | public_policy_wording_likely_verified_from_named_policy_pdf | no_public_benefit_schedule_verified | public_policy_pdf_only | https://safetywing.com/nomad-insurance/complete/standard_nomad_insurance_complete_vumi+carrier_2024-12.pdf?referenceID=24734754&utm_source=24734754&utm_medium=Ambassador |
| genki_native | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | no_public_benefit_schedule_verified | materials_missing_or_blocked_in_reviewed_public_sources | — |
| acs_expat | no_public_terms_conditions_material_verified | information_booklet_public_but_policy_wording_not_separately_verified | public_benefit_schedule_verified | public_benefit_schedule_and_info_booklet_verified | https://www.acs-ami.com/files/pdf/ACS-Expat-ALC-IB-en.pdf |
| hci_group_health_protect | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | public_benefit_schedule_verified | public_benefit_schedule_only | https://hcigroupglobal.com/wp-content/uploads/2024/06/Inpatient.pdf |
| expatriate_group_global_health | no_public_terms_conditions_material_verified | brochure_only_policy_wording_not_verified | brochure_only_benefit_schedule_not_verified | public_brochure_only | https://www.expatriatehealthcare.com/wp-content/uploads/2016/09/Expatriate-Group-Brochure-International-Insurance-Specialists.pdf |
| wellaway_expat | no_public_terms_conditions_material_verified | brochure_only_policy_wording_not_verified | brochure_only_benefit_schedule_not_verified | public_brochure_only | https://www.wellaway.com/media/filer_public/31/5d/315d7f0b-9976-4fd2-8b43-ad597fd59b4a/expat-plans-brochure_1292026.pdf |
| securus_global_health_cover | no_public_terms_conditions_material_verified | no_public_policy_wording_verified | public_benefit_schedule_verified | public_benefit_schedule_only | https://www.securus.co.uk/storage/JZJIi-Securus%20Individual%20&%20Group%20Benefit%20Table%202026%20(USD).pdf |
