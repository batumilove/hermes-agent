import json
import pathlib

root = pathlib.Path(__file__).resolve().parents[2]
source = json.loads((root / 'data/insurance_discovery/plan_document_access_evidence_ledger.discovery.json').read_text())

manual = {
  'cigna_global': {
    'terms_status': 'no_public_terms_conditions_material_verified',
    'terms_note': 'Only a corporate modern-slavery PDF appeared in the reviewed public page scan; no plan terms/conditions, policy wording, or benefit schedule was publicly verified.',
    'policy_status': 'no_public_policy_wording_verified',
    'benefits_status': 'no_public_benefit_schedule_verified',
    'overall': 'no_plan_material_publicly_verified'
  },
  'allianz_care': {'terms_status':'not_verified_due_to_access_or_review_limits','terms_note':'Official landing page returned HTTP 403 in this environment and the quote path showed no PDFs; missing-material status may reflect access controls.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'bupa_global': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on the reviewed public page.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'axa_global_healthcare': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on the reviewed public landing or quote entry pages.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'april_international': {'terms_status':'public_generic_website_terms_verified_but_not_plan_specific','terms_note':'A footer Terms & Conditions PDF was publicly visible, but it appeared to be generic website/legal terms rather than plan wording or benefits.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'generic_terms_only_no_plan_material_publicly_verified'},
  'william_russell': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Only a complaints-policy PDF surfaced; no plan terms/policy wording/benefit schedule was publicly verified.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'no_plan_material_publicly_verified'},
  'msh_international': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'now_health_international': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'img_global': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public brochure was verified, but no separate public terms/conditions or policy wording file was verified from reviewed sources.','policy_status':'brochure_only_policy_wording_not_verified','benefits_status':'brochure_only_benefit_schedule_not_verified','overall':'public_brochure_only'},
  'aetna_international': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'foyer_global_health': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public brochure was verified, but no separate plan terms/conditions, policy wording, or benefits table was verified in reviewed public sources.','policy_status':'brochure_only_policy_wording_not_verified','benefits_status':'brochure_only_benefit_schedule_not_verified','overall':'public_brochure_only'},
  'henner': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'globality_health': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'The application flow exposed a brochure-download placeholder/template path, but no live public brochure/policy/benefit document was resolved in this run.','policy_status':'not_publicly_verified_because_document_link_placeholder_or_js_gated','benefits_status':'not_publicly_verified_because_document_link_placeholder_or_js_gated','overall':'partially_exposed_but_not_retrievable_in_run'},
  'morgan_price': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public policy wording and table-of-benefits links were visible on the official individual page.','policy_status':'public_policy_wording_verified','benefits_status':'public_benefit_schedule_verified','overall':'policy_wording_and_benefit_schedule_publicly_verified'},
  'passportcard_global': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'A public TOB PDF was verified; no separate policy wording or plan terms/conditions file was verified in reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'public_benefit_schedule_verified','overall':'public_benefit_schedule_only'},
  'vumi': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'integra_global': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Broker-hosted brochure was verified, but no separate public policy wording or benefits table was verified in reviewed sources.','policy_status':'brochure_only_policy_wording_not_verified','benefits_status':'brochure_only_benefit_schedule_not_verified','overall':'public_brochure_only'},
  'medihelp_international': {'terms_status':'public_generic_website_terms_page_verified_but_not_plan_specific','terms_note':'A generic website terms-and-conditions page was public, but no downloadable plan wording or benefits schedule was publicly verified in reviewed sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'generic_terms_only_no_plan_material_publicly_verified'},
  'alc_health': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'geoblue_xplorer_bcbs_global_solutions': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public plan-description PDFs were verified on the official page, but no separate policy wording or benefits schedule was explicitly verified in this run.','policy_status':'plan_description_public_but_policy_wording_not_verified','benefits_status':'plan_description_public_but_benefit_schedule_not_verified','overall':'public_plan_description_only'},
  'safetywing_nomad_complete': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'A public PDF titled as the standard Nomad Insurance Complete document was verified; it plausibly functions as policy wording, but no separate benefits table or terms file was independently verified.','policy_status':'public_policy_wording_likely_verified_from_named_policy_pdf','benefits_status':'no_public_benefit_schedule_verified','overall':'public_policy_pdf_only'},
  'genki_native': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'No downloadable plan materials were exposed on reviewed public sources.','policy_status':'no_public_policy_wording_verified','benefits_status':'no_public_benefit_schedule_verified','overall':'materials_missing_or_blocked_in_reviewed_public_sources'},
  'acs_expat': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public information booklet(s), brochure, and table-of-benefits PDF were visible on the official page; a separate policy wording file was not independently verified from the reviewed public sample.','policy_status':'information_booklet_public_but_policy_wording_not_separately_verified','benefits_status':'public_benefit_schedule_verified','overall':'public_benefit_schedule_and_info_booklet_verified'},
  'hci_group_health_protect': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Multiple public plan-benefits PDFs were visible for tier variants, but no policy wording or terms file was independently verified.','policy_status':'no_public_policy_wording_verified','benefits_status':'public_benefit_schedule_verified','overall':'public_benefit_schedule_only'},
  'expatriate_group_global_health': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public brochure was verified, but no separate policy wording or benefits schedule was verified in reviewed public sources.','policy_status':'brochure_only_policy_wording_not_verified','benefits_status':'brochure_only_benefit_schedule_not_verified','overall':'public_brochure_only'},
  'wellaway_expat': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public brochure was verified, but no separate policy wording or benefits schedule was verified in reviewed public sources.','policy_status':'brochure_only_policy_wording_not_verified','benefits_status':'brochure_only_benefit_schedule_not_verified','overall':'public_brochure_only'},
  'securus_global_health_cover': {'terms_status':'no_public_terms_conditions_material_verified','terms_note':'Public benefit-table PDFs were verified, but no separate policy wording or terms file was independently verified from the reviewed public sample.','policy_status':'no_public_policy_wording_verified','benefits_status':'public_benefit_schedule_verified','overall':'public_benefit_schedule_only'},
}

records = []
for r in source['records']:
    m = manual[r['candidate_id']]
    obs = []
    for so in r.get('scan_observations', []):
        entry = {
            'url': so.get('url'),
            'status_code': so.get('status_code'),
            'content_type': so.get('content_type'),
            'pdf_link_count': so.get('pdf_link_count', 0),
        }
        if so.get('pdf_links_sample'):
            entry['pdf_links_sample'] = so['pdf_links_sample'][:5]
        obs.append(entry)
    records.append({
        'index': r['index'],
        'candidate_id': r['candidate_id'],
        'insurer_name': r['insurer_name'],
        'plan_name': r['plan_name'],
        'official_url': r['official_url'],
        'quote_or_application_url': r['quote_or_application_url'],
        'supporting_page_url': r['supporting_page_url'],
        'document_access_source_ledger_ref': {
            'dataset_path': 'data/insurance_discovery/plan_document_access_evidence_ledger.discovery.json',
            'candidate_id': r['candidate_id']
        },
        'public_terms_conditions_accessibility_status': m['terms_status'],
        'public_policy_wording_accessibility_status': m['policy_status'],
        'public_benefit_schedule_accessibility_status': m['benefits_status'],
        'document_access_overall_assessment': m['overall'],
        'public_plan_document_accessible': r['direct_public_plan_document_accessible'],
        'verified_public_document_url': r['document_source_url'],
        'verified_public_document_source_type': r['document_source_type'],
        'verified_public_document_access_path': r['document_access_path'],
        'document_access_observations': obs,
        'evidence_citations': r['evidence_citations'],
        'uncertainty_notes': list(dict.fromkeys(r.get('uncertainty_notes', []) + [m['terms_note']]))
    })

summary = {
    'total_candidates': len(records),
    'public_policy_wording_verified_or_likely_count': sum(
        rec['public_policy_wording_accessibility_status'] in [
            'public_policy_wording_verified',
            'public_policy_wording_likely_verified_from_named_policy_pdf',
        ]
        for rec in records
    ),
    'public_benefit_schedule_verified_count': sum(
        rec['public_benefit_schedule_accessibility_status'] == 'public_benefit_schedule_verified'
        for rec in records
    ),
    'generic_terms_only_count': sum(
        rec['document_access_overall_assessment'] == 'generic_terms_only_no_plan_material_publicly_verified'
        for rec in records
    ),
    'public_brochure_only_count': sum(
        rec['document_access_overall_assessment'] == 'public_brochure_only'
        for rec in records
    ),
    'public_plan_description_only_count': sum(
        rec['document_access_overall_assessment'] == 'public_plan_description_only'
        for rec in records
    ),
    'materials_missing_or_blocked_count': sum(
        rec['document_access_overall_assessment'] in [
            'materials_missing_or_blocked_in_reviewed_public_sources',
            'no_plan_material_publicly_verified',
        ]
        for rec in records
    ),
    'partial_or_js_placeholder_count': sum(
        rec['document_access_overall_assessment'] == 'partially_exposed_but_not_retrievable_in_run'
        for rec in records
    ),
}

out = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'plan_material_public_access_ledger',
    'generated_by': 'gpt-5.4',
    'generated_from': [
        'data/insurance_discovery/plan_document_access_evidence_ledger.discovery.json',
        'data/insurance_discovery/shared_candidate_staging.discovery.json',
    ],
    'family_context': source['family_context'],
    'method_notes': [
        'This AC 50102 artifact records, per candidate, whether public terms/conditions, policy wording, or benefit schedules were accessible from reviewed public sources during this run.',
        'Statuses are intentionally granular: brochure-only and generic-website-terms-only are kept distinct from policy wording, benefit schedules, or fully missing/blocked materials.',
        'A record can show public brochure or plan-description access without proving that policy wording or a table of benefits was publicly accessible.',
        'Generic website legal terms were not treated as plan-specific wording unless the reviewed evidence tied them directly to the insurance product.',
        'When reviewed evidence exposed only template placeholders, blocked responses, or generic legal PDFs, the uncertainty is preserved explicitly rather than upgraded to verified plan-material access.',
    ],
    'summary': summary,
    'records': records,
}

json_path = root / 'data/insurance_discovery/plan_material_public_access_ledger.discovery.json'
json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')

lines = [
    '# Insurance discovery: plan material public-access ledger',
    '',
    'Discovery-only companion ledger for AC 50102 sub-AC 2. It records whether each staged insurer/plan publicly exposed terms/conditions, policy wording, or benefit schedules in the reviewed sources for this run.',
    '',
    'Machine-readable source: `data/insurance_discovery/plan_material_public_access_ledger.discovery.json`',
    '',
    'Summary counts:',
    f"- total_candidates: {summary['total_candidates']}",
    f"- public_policy_wording_verified_or_likely_count: {summary['public_policy_wording_verified_or_likely_count']}",
    f"- public_benefit_schedule_verified_count: {summary['public_benefit_schedule_verified_count']}",
    f"- generic_terms_only_count: {summary['generic_terms_only_count']}",
    f"- public_brochure_only_count: {summary['public_brochure_only_count']}",
    f"- public_plan_description_only_count: {summary['public_plan_description_only_count']}",
    f"- materials_missing_or_blocked_count: {summary['materials_missing_or_blocked_count']}",
    f"- partial_or_js_placeholder_count: {summary['partial_or_js_placeholder_count']}",
    '',
    'Notes:',
    '- “Publicly accessible” in this ledger means accessible from the reviewed public page/PDF evidence captured in this run, not merely mentioned in marketing copy.',
    '- Brochure-only access is kept separate from public policy wording or a public table of benefits.',
    '- Generic website terms/conditions pages are recorded when visible, but they are not treated as plan-specific insurance wording unless the source tied them directly to the product.',
    '- Missing/blocked means not found in reviewed public sources in this run; it does not prove the insurer lacks those materials behind quote, broker, alternate-locale, or login flows.',
    '',
    '| Candidate | Terms/conditions | Policy wording | Benefit schedule | Overall assessment | Verified doc URL |',
    '|---|---|---|---|---|---|',
]
for rec in records:
    doc = rec['verified_public_document_url'] or '—'
    lines.append(
        f"| {rec['candidate_id']} | {rec['public_terms_conditions_accessibility_status']} | {rec['public_policy_wording_accessibility_status']} | {rec['public_benefit_schedule_accessibility_status']} | {rec['document_access_overall_assessment']} | {doc} |"
    )
md_path = root / 'docs/insurance_discovery_plan_material_public_access_ledger.md'
md_path.write_text('\n'.join(lines) + '\n')

print(json_path)
print(md_path)
print(json.dumps(summary, indent=2))
