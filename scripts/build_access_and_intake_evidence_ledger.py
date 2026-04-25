import json
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'


def load(name: str):
    return json.loads((DATA_DIR / name).read_text())


shared = load('shared_candidate_staging.discovery.json')
access_paths = load('access_path_contact_models.discovery.json')
account_friction = load('account_access_friction.discovery.json')
quote_intake = load('quote_intake_gating_requirements.discovery.json')

shared_by_id = {r['candidate_id']: r for r in shared['staging_candidates']}
path_by_id = {r['candidate_id']: r for r in access_paths['records']}
account_by_id = {r['candidate_id']: r for r in account_friction['records']}
intake_by_id = {r['candidate_id']: r for r in quote_intake['records']}
ordered_ids = [r['candidate_id'] for r in account_friction['records']]


def dedupe_preserve(items):
    out = []
    seen = set()
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def listify(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def first_non_empty(*values):
    for value in values:
        if value not in (None, '', []):
            return value
    return None


def build_missing_documentation_notes(path_rec, account_rec, intake_rec):
    notes = []
    if path_rec.get('broker_or_intermediary_only'):
        notes.append('No insurer-owned direct quote or application flow was verified from public sources in this run.')
    if path_rec.get('contact_model') == 'official_information_page_without_public_quote_path_verified':
        notes.append('No separate public quote or application path was verified beyond marketing pages, brochures, or summary materials in this run.')
    if path_rec.get('contact_model') == 'legacy_brand_redirect_to_other_insurer':
        notes.append('Current new-business documentation on the successor insurer/platform was not obtained beyond the redirect evidence captured in this run.')
    if path_rec.get('contact_model') == 'official_marketing_page_to_external_or_specialized_quote_flow':
        notes.append('Downstream quote or application documentation on the external/specialized portal was not fully obtained in this run.')
    if account_rec.get('login_wall_before_quote_or_plan_details'):
        notes.append('Deeper quote fields, plan contents, or member-only documents behind the visible login/challenge gate were not obtained in this run.')
    if account_rec.get('forced_signup_before_quote_or_plan_details') or account_rec.get('mandatory_account_creation_before_quote_or_plan_details'):
        notes.append('Post-signup quote contents or gated plan documents were not obtained without creating an account in this run.')
    if intake_rec.get('contact_submission_required_before_pricing_observed') is True:
        notes.append('Personalized pricing or proposal output after mandatory contact submission was not obtained in this run.')
    elif intake_rec.get('contact_submission_required_before_pricing_observed') == 'conditional':
        notes.append('Manual-fallback pricing or proposal material triggered after contact capture was not obtained in this run.')
    if intake_rec.get('household_submission_required_before_pricing_observed') is True:
        notes.append('Plan output beyond the visible household-composition intake step was not obtained in this run.')
    if not intake_rec.get('evidence') and not intake_rec.get('supporting_evidence_ledger_entries'):
        notes.append('No downstream intake fields or deeper quote documentation were captured from reviewed public sources in this run.')
    if not notes:
        notes.append('No specific documentation loss attributable to a documented friction case was observed at the captured public entry step.')
    return dedupe_preserve(notes)


def build_friction_case_entries(candidate_id, path_rec, account_rec, intake_rec):
    access_url = first_non_empty(
        account_rec.get('quote_or_application_url'),
        intake_rec.get('quote_or_application_url'),
        path_rec.get('quote_or_application_url'),
        account_rec.get('primary_source_url'),
        intake_rec.get('primary_source_url'),
        path_rec.get('primary_source_url'),
    )
    page_reference = first_non_empty(
        intake_rec.get('quote_intake_path_type'),
        path_rec.get('initial_access_path_type'),
        'reviewed public access step',
    )
    base_citations = dedupe_preserve(
        [
            *listify(account_rec.get('evidence_ledger_entries')),
            *listify(intake_rec.get('supporting_evidence_ledger_entries')),
            *[cite.get('excerpt') for cite in intake_rec.get('evidence', []) if cite.get('excerpt')],
        ]
    )
    missing_documentation_notes = build_missing_documentation_notes(path_rec, account_rec, intake_rec)

    barrier_specs = []
    if account_rec.get('login_wall_before_quote_or_plan_details'):
        barrier_specs.append({
            'barrier_type': 'login_or_challenge_wall_before_quote_or_plan_details',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not obtain deeper quote fields, gated plan contents, or member-only documentation past the visible login/challenge wall.',
        })
    if account_rec.get('forced_signup_before_quote_or_plan_details'):
        barrier_specs.append({
            'barrier_type': 'forced_signup_before_quote_or_plan_details',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not obtain quote contents or plan documents beyond the forced signup step without creating an account.',
        })
    if account_rec.get('mandatory_account_creation_before_quote_or_plan_details'):
        barrier_specs.append({
            'barrier_type': 'mandatory_account_creation_before_quote_or_plan_details',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not obtain deeper quote or plan documentation without completing mandatory account creation.',
        })
    if account_rec.get('email_required_before_quote_output_observed'):
        barrier_specs.append({
            'barrier_type': 'email_capture_before_quote_output',
            'finding_scope': 'intake',
            'could_not_obtain_note': 'Could not obtain full personalized pricing or proposal output without proceeding past the visible email-capture step.',
        })
    if account_rec.get('phone_required_before_quote_output_observed'):
        barrier_specs.append({
            'barrier_type': 'phone_capture_before_quote_output',
            'finding_scope': 'intake',
            'could_not_obtain_note': 'Could not obtain full personalized pricing or proposal output without proceeding past the visible phone-capture step.',
        })
    if path_rec.get('broker_or_intermediary_only'):
        barrier_specs.append({
            'barrier_type': 'broker_or_intermediary_led_entry_only',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not obtain an insurer-owned direct quote/application path or insurer-hosted deeper documentation from reviewed public sources in this run.',
        })
    if path_rec.get('contact_model') == 'legacy_brand_redirect_to_other_insurer':
        barrier_specs.append({
            'barrier_type': 'legacy_brand_redirect_for_new_business',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not obtain current successor-platform new-business quote or policy documentation beyond the legacy redirect path captured in this run.',
        })
    if path_rec.get('contact_model') == 'official_information_page_without_public_quote_path_verified':
        barrier_specs.append({
            'barrier_type': 'no_public_quote_path_verified_in_this_run',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not obtain a separate public quote/application path or downstream quote-form documentation from reviewed sources in this run.',
        })
    if path_rec.get('contact_model') == 'official_marketing_page_to_external_or_specialized_quote_flow':
        barrier_specs.append({
            'barrier_type': 'external_or_specialized_quote_portal_handoff',
            'finding_scope': 'access',
            'could_not_obtain_note': 'Could not fully obtain downstream quote/application documentation on the handed-off external or specialized portal in this run.',
        })
    if intake_rec.get('contact_submission_required_before_pricing_observed') is True:
        barrier_specs.append({
            'barrier_type': 'contact_submission_required_before_pricing',
            'finding_scope': 'intake',
            'could_not_obtain_note': 'Could not obtain personalized pricing or proposal output without submitting visible contact details first.',
        })
    elif intake_rec.get('contact_submission_required_before_pricing_observed') == 'conditional':
        barrier_specs.append({
            'barrier_type': 'conditional_contact_submission_before_pricing',
            'finding_scope': 'intake',
            'could_not_obtain_note': 'Could not obtain manual-fallback pricing or proposal material that appears to require contact submission under some paths.',
        })
    if intake_rec.get('household_submission_required_before_pricing_observed') is True:
        barrier_specs.append({
            'barrier_type': 'household_composition_submission_required_before_pricing',
            'finding_scope': 'intake',
            'could_not_obtain_note': 'Could not obtain plan recommendations or pricing beyond the visible household-composition data requirements in this run.',
        })

    entries = []
    for barrier_idx, spec in enumerate(barrier_specs, start=1):
        entries.append({
            'friction_case_id': f'{candidate_id}:friction:{barrier_idx}',
            'finding_scope': spec['finding_scope'],
            'access_url': access_url,
            'page_reference': page_reference,
            'observed_barrier_type': spec['barrier_type'],
            'source_backed_evidence': base_citations,
            'could_not_obtain_documentation_note': spec['could_not_obtain_note'],
            'related_uncertainty_notes': dedupe_preserve(listify(account_rec.get('uncertainty_notes')) + listify(intake_rec.get('uncertainty_notes'))),
            'general_missing_documentation_notes': missing_documentation_notes,
        })
    return entries


records = []
for idx, candidate_id in enumerate(ordered_ids, start=1):
    shared_rec = shared_by_id[candidate_id]
    path_rec = path_by_id[candidate_id]
    account_rec = account_by_id[candidate_id]
    intake_rec = intake_by_id[candidate_id]

    access_page_reference = intake_rec.get('quote_intake_path_type') or path_rec.get('initial_access_path_type') or 'reviewed public access step'
    access_citations = []
    for cidx, cite in enumerate(account_rec.get('evidence_ledger_entries', []), start=1):
        access_citations.append({
            'citation_id': f'{candidate_id}:access:cite{cidx}',
            'source_url': account_rec.get('quote_or_application_url') or account_rec.get('primary_source_url'),
            'source_type': 'reviewed public source',
            'page_reference': access_page_reference,
            'excerpt': cite,
            'screenshot_path': None,
            'screenshot_status': 'not_captured_in_this_run'
        })

    intake_citations = []
    for cidx, cite in enumerate(intake_rec.get('evidence', []), start=1):
        intake_citations.append({
            'citation_id': f'{candidate_id}:intake:cite{cidx}',
            'source_url': cite['source_url'],
            'source_type': cite['source_type'],
            'page_reference': intake_rec.get('quote_intake_path_type', 'reviewed public intake step'),
            'excerpt': cite['excerpt'],
            'screenshot_path': None,
            'screenshot_status': 'not_captured_in_this_run'
        })
    start_idx = len(intake_citations) + 1
    for offset, cite in enumerate(intake_rec.get('supporting_evidence_ledger_entries', []), start=start_idx):
        intake_citations.append({
            'citation_id': f'{candidate_id}:intake:cite{offset}',
            'source_url': intake_rec.get('quote_or_application_url') or intake_rec.get('primary_source_url'),
            'source_type': 'reviewed public source',
            'page_reference': intake_rec.get('quote_intake_path_type', 'reviewed public intake step'),
            'excerpt': cite,
            'screenshot_path': None,
            'screenshot_status': 'not_captured_in_this_run'
        })

    visible_obstacles = []
    if account_rec.get('login_wall_before_quote_or_plan_details'):
        visible_obstacles.append('login_or_challenge_wall_before_quote_or_plan_details')
    if account_rec.get('forced_signup_before_quote_or_plan_details'):
        visible_obstacles.append('forced_signup_before_quote_or_plan_details')
    if account_rec.get('mandatory_account_creation_before_quote_or_plan_details'):
        visible_obstacles.append('mandatory_account_creation_before_quote_or_plan_details')
    if account_rec.get('email_required_before_quote_output_observed'):
        visible_obstacles.append('email_capture_before_quote_output')
    if account_rec.get('phone_required_before_quote_output_observed'):
        visible_obstacles.append('phone_capture_before_quote_output')
    if path_rec.get('broker_or_intermediary_only'):
        visible_obstacles.append('broker_or_intermediary_led_entry_only')
    if path_rec.get('contact_model') == 'legacy_brand_redirect_to_other_insurer':
        visible_obstacles.append('legacy_brand_redirect_for_new_business')
    if path_rec.get('contact_model') == 'official_information_page_without_public_quote_path_verified':
        visible_obstacles.append('no_public_quote_path_verified_in_this_run')
    if path_rec.get('contact_model') == 'official_marketing_page_to_external_or_specialized_quote_flow':
        visible_obstacles.append('external_or_specialized_quote_portal_handoff')
    if intake_rec.get('contact_submission_required_before_pricing_observed') is True:
        visible_obstacles.append('contact_submission_required_before_pricing')
    elif intake_rec.get('contact_submission_required_before_pricing_observed') == 'conditional':
        visible_obstacles.append('conditional_contact_submission_before_pricing')
    if intake_rec.get('household_submission_required_before_pricing_observed') is True:
        visible_obstacles.append('household_composition_submission_required_before_pricing')

    documentation_steps_required = []
    if path_rec.get('primary_source_url'):
        documentation_steps_required.append('Open the staged public insurer or broker evidence page.')
    if path_rec.get('contact_model') == 'broker_or_intermediary_led':
        documentation_steps_required.append('Use the broker/intermediary plan page because no insurer-owned direct path was verified in this run.')
    elif path_rec.get('contact_model') == 'legacy_brand_redirect_to_other_insurer':
        documentation_steps_required.append('Follow the legacy-brand new-business redirect to the successor insurer/platform for current documentation.')
    elif path_rec.get('contact_model') == 'official_information_page_without_public_quote_path_verified':
        documentation_steps_required.append('Use public marketing pages and brochures; no separate public quote/application URL was verified for deeper documentation in this run.')
    elif path_rec.get('contact_model') == 'official_marketing_page_to_external_or_specialized_quote_flow':
        documentation_steps_required.append('Leave the primary marketing page and continue into the external or specialized quote/application portal.')
    else:
        documentation_steps_required.append('Continue from the public insurer page into the captured official quote/application flow.')

    if account_rec.get('login_wall_before_quote_or_plan_details'):
        documentation_steps_required.append('Pass a visible login, signup, or challenge gate before deeper quote contents or member-only documentation can be reviewed.')
    if intake_rec.get('contact_submission_required_before_pricing_observed') is True:
        documentation_steps_required.append('Submit contact details before personalized pricing or proposal output is available.')
    elif intake_rec.get('contact_submission_required_before_pricing_observed') == 'conditional':
        documentation_steps_required.append('Be prepared for a manual fallback that requests contact details when instant online pricing is unavailable.')
    if intake_rec.get('household_submission_required_before_pricing_observed') is True:
        documentation_steps_required.append('Provide household composition details before pricing or plan recommendations appear.')
    if not intake_rec.get('evidence') and not intake_rec.get('supporting_evidence_ledger_entries'):
        documentation_steps_required.append('Public intake fields were not captured; downstream documentation steps remain unresolved in reviewed sources.')

    friction_case_evidence_entries = build_friction_case_entries(candidate_id, path_rec, account_rec, intake_rec)

    access_path_friction = {
        'initial_access_path_type': path_rec['initial_access_path_type'],
        'contact_model': path_rec['contact_model'],
        'direct_to_consumer_availability': path_rec['direct_to_consumer_availability'],
        'official_quote_or_application_url_captured': path_rec['official_quote_or_application_url_captured'],
        'broker_or_intermediary_only': path_rec['broker_or_intermediary_only'],
        'source_quote_same_registrable_domain_family': path_rec['source_quote_same_registrable_domain_family'],
        'access_channel_markers': path_rec.get('access_channel_markers', []),
        'account_access_friction_classification': account_rec['account_access_friction_classification'],
        'account_access_friction_level': account_rec['account_access_friction_level'],
        'quote_intake_path_type': intake_rec['quote_intake_path_type'],
        'pricing_or_plan_details_visible_before_contact_submission': intake_rec['pricing_or_plan_details_visible_before_contact_submission'],
        'verification_requirement_observed': account_rec['verification_requirement_observed'],
        'visible_obstacles': dedupe_preserve(visible_obstacles),
        'documentation_steps_required': dedupe_preserve(documentation_steps_required),
        'friction_summary': ' '.join(dedupe_preserve([
            path_rec['classification_reason'],
            *account_rec.get('evidence_ledger_entries', [])[:1],
            *intake_rec.get('supporting_evidence_ledger_entries', [])[:1]
        ])),
        'uncertainty_notes': dedupe_preserve(
            listify(path_rec.get('uncertainty_notes'))
            + listify(account_rec.get('uncertainty_notes'))
            + listify(intake_rec.get('uncertainty_notes'))
        )
    }

    records.append({
        'index': idx,
        'candidate_id': candidate_id,
        'insurer_name': account_rec['insurer_name'],
        'plan_name': account_rec['plan_name'],
        'candidate_type': account_rec['candidate_type'],
        'record_links': {
            'access_path_contact_models': {
                'dataset_path': 'data/insurance_discovery/access_path_contact_models.discovery.json',
                'record_index': path_rec['index'],
                'candidate_id': candidate_id
            },
            'account_access_friction': {
                'dataset_path': 'data/insurance_discovery/account_access_friction.discovery.json',
                'record_index': account_rec['index'],
                'candidate_id': candidate_id
            },
            'quote_intake_gating_requirements': {
                'dataset_path': 'data/insurance_discovery/quote_intake_gating_requirements.discovery.json',
                'record_index': intake_rec['index'],
                'candidate_id': candidate_id
            },
            'shared_candidate_staging': {
                'dataset_path': 'data/insurance_discovery/shared_candidate_staging.discovery.json',
                'candidate_id': candidate_id
            }
        },
        'access_path_friction': access_path_friction,
        'friction_case_evidence_entries': friction_case_evidence_entries,
        'access_friction_finding': {
            'evidence_id': f'{candidate_id}:access',
            'classification': account_rec['account_access_friction_classification'],
            'friction_level': account_rec['account_access_friction_level'],
            'finding_statement': '; '.join(account_rec.get('evidence_ledger_entries', [])),
            'observed_flags': {
                'forced_signup_before_quote_or_plan_details': account_rec['forced_signup_before_quote_or_plan_details'],
                'mandatory_account_creation_before_quote_or_plan_details': account_rec['mandatory_account_creation_before_quote_or_plan_details'],
                'login_wall_before_quote_or_plan_details': account_rec['login_wall_before_quote_or_plan_details'],
                'email_required_before_quote_output_observed': account_rec['email_required_before_quote_output_observed'],
                'phone_required_before_quote_output_observed': account_rec['phone_required_before_quote_output_observed'],
                'verification_requirement_observed': account_rec['verification_requirement_observed'],
                'public_plan_details_accessible_without_account_observed': account_rec['public_plan_details_accessible_without_account_observed'],
                'public_quote_entry_without_account_observed': account_rec['public_quote_entry_without_account_observed']
            },
            'citations': access_citations,
            'uncertainty_notes': account_rec.get('uncertainty_notes', [])
        },
        'intake_friction_finding': {
            'evidence_id': f'{candidate_id}:intake',
            'quote_intake_path_type': intake_rec['quote_intake_path_type'],
            'finding_statement': '; '.join(intake_rec.get('supporting_evidence_ledger_entries', [])),
            'eligibility_precheck_observed': intake_rec['eligibility_precheck_observed'],
            'eligibility_precheck_details': intake_rec.get('eligibility_precheck_details', []),
            'required_quote_form_fields_observed': intake_rec.get('required_quote_form_fields_observed', []),
            'required_contact_details_before_pricing_or_plan_details': intake_rec.get('required_contact_details_before_pricing_or_plan_details', []),
            'required_household_data_before_pricing_or_plan_details': intake_rec.get('required_household_data_before_pricing_or_plan_details', []),
            'contact_submission_required_before_pricing_observed': intake_rec['contact_submission_required_before_pricing_observed'],
            'household_submission_required_before_pricing_observed': intake_rec['household_submission_required_before_pricing_observed'],
            'pricing_or_plan_details_visible_before_contact_submission': intake_rec['pricing_or_plan_details_visible_before_contact_submission'],
            'citations': intake_citations,
            'uncertainty_notes': intake_rec.get('uncertainty_notes', [])
        },
        'staging_source_urls': dedupe_preserve([ref['source_url'] for ref in shared_rec.get('source_refs', []) if ref.get('source_url')]),
        'staging_quote_or_application_urls': dedupe_preserve([ref.get('quote_or_application_url') for ref in shared_rec.get('source_refs', []) if ref.get('quote_or_application_url')])
    })

summary = {
    'verified_total_unique_candidates': len(records),
    'documented_friction_case_count': sum(len(r['friction_case_evidence_entries']) for r in records),
    'records_with_documented_friction_cases_count': sum(bool(r['friction_case_evidence_entries']) for r in records),
    'total_citation_count': sum(len(r['access_friction_finding']['citations']) + len(r['intake_friction_finding']['citations']) for r in records),
    'records_without_screenshots_count': len(records),
    'login_or_challenge_wall_candidates_count': sum('login_or_challenge_wall_before_quote_or_plan_details' in r['access_path_friction']['visible_obstacles'] for r in records),
    'forced_signup_candidates_count': sum('forced_signup_before_quote_or_plan_details' in r['access_path_friction']['visible_obstacles'] for r in records),
    'broker_led_candidates_count': sum(r['access_path_friction']['broker_or_intermediary_only'] for r in records),
    'external_or_specialized_portal_handoff_candidates_count': sum('external_or_specialized_quote_portal_handoff' in r['access_path_friction']['visible_obstacles'] for r in records),
    'contact_submission_required_before_pricing_count': sum(r['intake_friction_finding']['contact_submission_required_before_pricing_observed'] is True for r in records),
    'conditional_contact_submission_before_pricing_count': sum(r['intake_friction_finding']['contact_submission_required_before_pricing_observed'] == 'conditional' for r in records),
    'no_public_quote_path_verified_count': sum('no_public_quote_path_verified_in_this_run' in r['access_path_friction']['visible_obstacles'] for r in records),
    'legacy_redirect_candidates_count': sum('legacy_brand_redirect_for_new_business' in r['access_path_friction']['visible_obstacles'] for r in records)
}

out = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'access_and_intake_evidence_ledger',
    'generated_by': 'gpt-5.4',
    'generated_from': [
        'data/insurance_discovery/access_path_contact_models.discovery.json',
        'data/insurance_discovery/account_access_friction.discovery.json',
        'data/insurance_discovery/quote_intake_gating_requirements.discovery.json',
        'data/insurance_discovery/shared_candidate_staging.discovery.json'
    ],
    'family_context': account_friction['family_context'],
    'method_notes': [
        'This ledger cross-links access-path shape, public account-access friction, and quote-intake gating for each staged candidate to structured citations with source URLs and page references.',
        'access_path_friction explicitly records the practical path friction needed to obtain deeper plan documentation, including broker-only starts, external handoffs, login/challenge walls, contact-capture requirements, and no-public-quote cases.',
        'friction_case_evidence_entries adds one structured entry per documented friction case, with the access URL, observed barrier type, source-backed evidence text, and a short note describing what documentation could not be obtained.',
        'No screenshots were captured in this run; screenshot fields remain null and are marked as not captured rather than inferred.',
        'Page references identify the reviewed public page or entry step where the friction was observed; they are not PDF page numbers unless a PDF was specifically reviewed.',
        'This run remains discovery-only and does not rank insurers or turn these findings into a recommendation dashboard.'
    ],
    'summary': summary,
    'records': records
}

json_path = DATA_DIR / 'access_and_intake_evidence_ledger.discovery.json'
json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')

lines = [
    '# Insurance discovery: access and intake evidence ledger',
    '',
    'Discovery-only companion ledger for AC 50004 sub-AC 4. This file cross-links the access-path shape, account-access-friction, and quote-intake-friction findings for each staged candidate to structured source citations and page references.',
    '',
    'Machine-readable source: `data/insurance_discovery/access_and_intake_evidence_ledger.discovery.json`',
    '',
    'Summary counts:',
]
for key, value in summary.items():
    lines.append(f'- {key}: {value}')
lines += [
    '',
    'Notes:',
    '- `access_path_friction.visible_obstacles` normalizes the practical barriers observed at the public entry point, such as login/challenge walls, forced signup, broker-only starts, external quote portals, no-public-quote verification gaps, and contact submission before pricing.',
    '- `access_path_friction.documentation_steps_required` describes the concrete steps a researcher would need to take to reach deeper documentation or quote material from the reviewed public path.',
    '- `friction_case_evidence_entries` gives one structured ledger entry per documented friction case, including the access URL, observed barrier type, source-backed evidence text, and what documentation could not be obtained.',
    '- This run did not capture screenshots; each citation explicitly records `screenshot_status: not_captured_in_this_run` instead of pretending otherwise.',
    '- Page references identify the reviewed public page or entry step where the friction was observed.',
    '- The ledger links back to `access_path_contact_models.discovery.json`, `account_access_friction.discovery.json`, `quote_intake_gating_requirements.discovery.json`, and `shared_candidate_staging.discovery.json` per candidate.',
    '',
    '| Candidate | Contact model | Obstacles | Documentation steps required | Access level | Intake path |',
    '|---|---|---|---|---|---|'
]
for record in records:
    apf = record['access_path_friction']
    lines.append(
        f"| {record['candidate_id']} | {apf['contact_model']} | {'; '.join(apf['visible_obstacles']) or 'none_observed_at_public_entry'} | {'; '.join(apf['documentation_steps_required'])} | {apf['account_access_friction_level']} | {apf['quote_intake_path_type']} |"
    )

md_path = DOCS_DIR / 'insurance_discovery_access_and_intake_evidence_ledger.md'
md_path.write_text('\n'.join(lines) + '\n')

print('Wrote', json_path)
print('Wrote', md_path)
