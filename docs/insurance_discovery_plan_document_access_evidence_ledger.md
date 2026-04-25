# Insurance discovery: plan document access evidence ledger

Discovery-only companion ledger for AC 50101 sub-AC 1. It records whether each staged insurer/plan currently exposes a directly downloadable public brochure, policy wording PDF, table of benefits, plan description, or similar plan document.

Machine-readable source: `data/insurance_discovery/plan_document_access_evidence_ledger.discovery.json`

Summary counts:
- total_candidates: 27
- direct_public_plan_document_accessible_count: 12
- no_downloadable_plan_document_found_count: 15
- official_or_captured_pdf_verified_live_count: 6
- public_page_linked_pdf_verified_live_count: 6

Notes:
- A plan document counts only if it was directly downloadable from a public URL or a reviewed public page in this run.
- Generic legal/compliance PDFs were excluded from the “plan document accessible” count.
- `no_downloadable_plan_document_found` means not found in reviewed public sources during this run; it does not prove the insurer has no brochure behind forms, alternate locales, or broker/logged-in paths.

| Candidate | Status | Direct doc? | Flag no downloadable doc found? | Document URL | Supporting page |
|---|---|---|---|---|---|
| cigna_global | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.cignaglobal.com/ |
| allianz_care | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.allianzcare.com/en/personal-international-health-insurance.html |
| bupa_global | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.bupaglobal.com/en/private-health-insurance |
| axa_global_healthcare | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.axaglobalhealthcare.com/en/international-health-insurance/ |
| april_international | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.april-international.com/en/long-term-international-health-insurance |
| william_russell | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.william-russell.com/international-health-insurance/ |
| msh_international | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.msh-intl.com/en/individuals/ |
| now_health_international | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.now-health.com/en/insurance-plans/ |
| img_global | direct_public_plan_document_verified | yes | no | https://www.imglobal.com/docs/library/forms-library/gmi-brochure.pdf | https://www.imglobal.com/international-health-insurance |
| aetna_international | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.aetnainternational.com/ |
| foyer_global_health | direct_public_plan_document_verified | yes | no | https://www.foyerglobalhealth.com/wp-content/uploads/2024/02/Brochure-commerciale-EN_V2-General.pdf | https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/ |
| henner | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.henner.com/en/customers/individuals-families/ |
| globality_health | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.globality-health.com/ |
| morgan_price | direct_public_plan_document_verified | yes | no | https://morgan-price.com/media/4ibjjwku/evolutionhealth_ant_policywording_04-26_v2-final.pdf | https://www.morgan-price.com/individual/ |
| passportcard_global | direct_public_plan_document_verified | yes | no | https://www.passportcard.de/wp-content/uploads/2023/02/TOB_Individuals_ENG-MGAWP.pdf | https://www.passportcard.de/en/international-health-insurance/ |
| vumi | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.vumigroup.com/ |
| integra_global | direct_public_plan_document_verified | yes | no | https://www.internationalinsurance.com/pdf-brochures/integra-global-brochure.pdf | https://www.internationalinsurance.com/integra-global/ |
| medihelp_international | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans |
| alc_health | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://www.alchealth.com/ |
| geoblue_xplorer_bcbs_global_solutions | direct_public_plan_document_verified | yes | no | https://c8s387h5.media.zestyio.com/4ELI-Medical-Coverage-for-Living-Abroad-Worldwide-Premier.HJBCYKZQhel.pdf?download=true | https://bcbsglobalsolutions.com/individuals-and-families/international-medical-insurance/living-abroad/worldwide-and-outside-us/ |
| safetywing_nomad_complete | direct_public_plan_document_verified | yes | no | https://safetywing.com/nomad-insurance/complete/standard_nomad_insurance_complete_vumi+carrier_2024-12.pdf?referenceID=24734754&utm_source=24734754&utm_medium=Ambassador | https://explore.safetywing.com/nomad-insurance-complete |
| genki_native | no_downloadable_plan_document_found_in_reviewed_public_sources | no | yes | — | https://genki.world/ |
| acs_expat | direct_public_plan_document_verified | yes | no | https://www.acs-ami.com/files/pdf/ACS-Expat-ALC-IB-en.pdf | https://www.acs-ami.com/en/expat-health-insurance/acs-expat/ |
| hci_group_health_protect | direct_public_plan_document_verified | yes | no | https://hcigroupglobal.com/wp-content/uploads/2024/06/Inpatient.pdf | https://hcigroupglobal.com/health-protect/ |
| expatriate_group_global_health | direct_public_plan_document_verified | yes | no | https://www.expatriatehealthcare.com/wp-content/uploads/2016/09/Expatriate-Group-Brochure-International-Insurance-Specialists.pdf | https://quote.expatriatehealthcare.com/healthcare/ |
| wellaway_expat | direct_public_plan_document_verified | yes | no | https://www.wellaway.com/media/filer_public/31/5d/315d7f0b-9976-4fd2-8b43-ad597fd59b4a/expat-plans-brochure_1292026.pdf | https://www.wellaway.com/en/our-plans/expat/ |
| securus_global_health_cover | direct_public_plan_document_verified | yes | no | https://www.securus.co.uk/storage/JZJIi-Securus%20Individual%20&%20Group%20Benefit%20Table%202026%20(USD).pdf | https://www.securus.co.uk/benefits/global-health-cover |
