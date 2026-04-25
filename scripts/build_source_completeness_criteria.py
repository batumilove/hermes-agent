import json
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text())


def dump_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value != ''
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return True


shared = load_json('shared_candidate_staging.discovery.json')
source_ledger = load_json('candidate_source_ledger.discovery.json')
germany = load_json('germany_coverage_evidence_ledger.discovery.json')
georgia = load_json('georgia_coverage_evidence_ledger.discovery.json')
global_cov = load_json('global_coverage_evidence_ledger.discovery.json')

source_by_id = {record['candidate_id']: record for record in source_ledger['records']}
germany_by_id = {record['candidate_id']: record for record in germany['records']}
georgia_by_id = {record['candidate_id']: record for record in georgia['records']}
global_by_id = {record['candidate_id']: record for record in global_cov['records']}

required_entry_fields = [
    {
        'field': 'candidate_id',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Stable machine-readable join key across all discovery ledgers.'
    },
    {
        'field': 'normalized_insurer_name',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Identifies the insurer responsible for the candidate plan family.'
    },
    {
        'field': 'normalized_plan_name',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Retains the discovered plan or product-family label even at discovery stage.'
    },
    {
        'field': 'source_refs',
        'required_presence': 'must_be_present_and_non_empty',
        'why_it_matters': 'Every candidate evidence entry must remain source-backed rather than being a memory-only stub.'
    },
    {
        'field': 'relevance_status',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Preserves whether the candidate still looks plausibly relevant to this family profile.'
    },
    {
        'field': 'relevance_status_reason',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Keeps the relevance classification explainable and auditable.'
    },
    {
        'field': 'uncertainty_notes',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Discovery is allowed to be incomplete, but the uncertainty must be explicit.'
    },
    {
        'field': 'pricing_availability_status',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Quote/pricing visibility is a recurring gating factor in this market.'
    },
    {
        'field': 'pricing_availability_status_reason',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Explains whether public pricing was visible, gated, partial, or absent.'
    },
    {
        'field': 'access_and_intake_evidence_refs',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Links the candidate entry to access and gating evidence rather than hiding friction.'
    },
    {
        'field': 'eligibility_ambiguity_evidence_ref',
        'required_presence': 'must_be_present',
        'why_it_matters': 'Links the candidate entry to the ambiguity ledger for unresolved residence/eligibility questions.'
    }
]

required_coverage_fields = [
    {
        'field': 'georgia_country_coverage_assessment',
        'artifact': 'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json',
        'required_presence': 'must_be_present',
        'allowed_states': ['confirmed', 'inferred', 'unknown'],
        'unknown_is_allowed': True,
        'partial_or_inferred_is_allowed': True,
        'why_it_matters': 'The family resides in Georgia, so Georgia coverage is a non-optional screening dimension.'
    },
    {
        'field': 'germany_country_coverage_assessment',
        'artifact': 'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json',
        'required_presence': 'must_be_present',
        'allowed_states': ['confirmed', 'inferred', 'unknown'],
        'unknown_is_allowed': True,
        'partial_or_inferred_is_allowed': True,
        'why_it_matters': 'The family is German, so Germany relevance and home-country treatment matter even when living abroad.'
    },
    {
        'field': 'worldwide_ex_us_status',
        'artifact': 'data/insurance_discovery/global_coverage_evidence_ledger.discovery.json',
        'required_presence': 'must_be_present',
        'allowed_states': [
            'explicitly_indicated',
            'indirectly_indicated',
            'worldwide_positioned_but_ex_us_not_verified',
            'not_verified_from_reviewed_sources'
        ],
        'unknown_is_allowed': True,
        'partial_or_inferred_is_allowed': True,
        'why_it_matters': 'The target preference is worldwide excluding the US, but discovery must still preserve weaker evidence states.'
    }
]

allowed_partial_unknown_states = {
    'country_coverage_assessment': {
        'confirmed': 'Captured public source language directly names the country or equivalent selector evidence.',
        'inferred': 'Captured public source language is source-backed but indirect, such as region/area-of-cover wording, home-country mechanics, or destination references.',
        'unknown': 'Reviewed sources were too generic, gated, or incomplete to support a responsible country-level inference.'
    },
    'worldwide_ex_us_status': global_cov['worldwide_ex_us_status_legend'],
    'source_url_capture_state': {
        'verified_public_url': 'A public URL was verified and stored in the candidate source ledger.',
        'not_verified_publicly_in_this_run': 'Represented as null for official_url, broker_url, quote_or_application_url, or policy_wording_or_pdf_url; null does not mean impossible or unavailable, only unverified in this run.'
    },
    'source_directness_classification': source_ledger['source_directness_rubric'],
    'evidence_entry_completeness_level': {
        'source_backed_complete_for_discovery': 'Entry has all required join/explanation fields and all three required coverage fields are present with source-backed states. Some states may still be inferred rather than direct.',
        'source_backed_partial_but_usable': 'Entry is still usable for discovery because required fields are present, but one or more coverage dimensions remain inferred or unknown, or important source URLs/documents remain unverified.',
        'insufficient_for_discovery': 'Entry is missing required structural or source-backed fields and should not be treated as a valid discovery candidate entry.'
    }
}

audit_records = []
summary = {
    'total_candidates': 0,
    'required_entry_field_presence': {item['field']: 0 for item in required_entry_fields},
    'required_coverage_field_presence': {item['field']: 0 for item in required_coverage_fields},
    'coverage_state_counts': {
        'georgia_country_coverage_assessment': {},
        'germany_country_coverage_assessment': {},
        'worldwide_ex_us_status': {},
    },
    'completeness_level_counts': {},
    'candidates_missing_required_entry_fields': 0,
    'candidates_missing_required_coverage_fields': 0,
    'all_candidates_meet_minimum_discovery_schema': True,
}

for candidate in shared['staging_candidates']:
    candidate_id = candidate['candidate_id']
    summary['total_candidates'] += 1
    source_record = source_by_id[candidate_id]
    georgia_record = georgia_by_id[candidate_id]
    germany_record = germany_by_id[candidate_id]
    global_record = global_by_id[candidate_id]

    missing_entry_fields = []
    for item in required_entry_fields:
        field = item['field']
        value = candidate.get(field)
        ok = present(value)
        if ok:
            summary['required_entry_field_presence'][field] += 1
        else:
            missing_entry_fields.append(field)

    coverage_values = {
        'georgia_country_coverage_assessment': georgia_record.get('georgia_country_coverage_assessment'),
        'germany_country_coverage_assessment': germany_record.get('germany_country_coverage_assessment'),
        'worldwide_ex_us_status': global_record.get('worldwide_ex_us_status'),
    }
    missing_coverage_fields = []
    for item in required_coverage_fields:
        field = item['field']
        value = coverage_values.get(field)
        ok = present(value)
        if ok:
            summary['required_coverage_field_presence'][field] += 1
            bucket = summary['coverage_state_counts'][field]
            bucket[value] = bucket.get(value, 0) + 1
        else:
            missing_coverage_fields.append(field)

    inferred_or_unknown_dimensions = []
    if georgia_record.get('georgia_country_coverage_assessment') in {'inferred', 'unknown'}:
        inferred_or_unknown_dimensions.append({
            'field': 'georgia_country_coverage_assessment',
            'state': georgia_record.get('georgia_country_coverage_assessment')
        })
    if germany_record.get('germany_country_coverage_assessment') in {'inferred', 'unknown'}:
        inferred_or_unknown_dimensions.append({
            'field': 'germany_country_coverage_assessment',
            'state': germany_record.get('germany_country_coverage_assessment')
        })
    if global_record.get('worldwide_ex_us_status') in {'indirectly_indicated', 'worldwide_positioned_but_ex_us_not_verified', 'not_verified_from_reviewed_sources'}:
        inferred_or_unknown_dimensions.append({
            'field': 'worldwide_ex_us_status',
            'state': global_record.get('worldwide_ex_us_status')
        })

    url_capture_state = {
        'official_url': 'verified_public_url' if present(source_record.get('official_url')) else 'not_verified_publicly_in_this_run',
        'quote_or_application_url': 'verified_public_url' if present(source_record.get('quote_or_application_url')) else 'not_verified_publicly_in_this_run',
        'broker_url': 'verified_public_url' if present(source_record.get('broker_url')) else 'not_verified_publicly_in_this_run',
        'policy_wording_or_pdf_url': 'verified_public_url' if present(source_record.get('policy_wording_or_pdf_url')) else 'not_verified_publicly_in_this_run',
    }

    if missing_entry_fields or missing_coverage_fields:
        completeness_level = 'insufficient_for_discovery'
        summary['all_candidates_meet_minimum_discovery_schema'] = False
    elif inferred_or_unknown_dimensions or 'not_verified_publicly_in_this_run' in url_capture_state.values():
        completeness_level = 'source_backed_partial_but_usable'
    else:
        completeness_level = 'source_backed_complete_for_discovery'

    summary['completeness_level_counts'][completeness_level] = summary['completeness_level_counts'].get(completeness_level, 0) + 1
    if missing_entry_fields:
        summary['candidates_missing_required_entry_fields'] += 1
    if missing_coverage_fields:
        summary['candidates_missing_required_coverage_fields'] += 1

    audit_records.append({
        'candidate_id': candidate_id,
        'insurer_name': candidate['normalized_insurer_name'],
        'plan_name': candidate['normalized_plan_name'],
        'required_entry_fields_missing': missing_entry_fields,
        'required_coverage_fields_missing': missing_coverage_fields,
        'coverage_states': coverage_values,
        'source_url_capture_state': url_capture_state,
        'source_directness_present': {
            'high': sum(1 for src in source_record.get('source_set', []) if src.get('source_directness_classification') == 'high'),
            'medium': sum(1 for src in source_record.get('source_set', []) if src.get('source_directness_classification') == 'medium'),
            'low': sum(1 for src in source_record.get('source_set', []) if src.get('source_directness_classification') == 'low'),
        },
        'inferred_or_unknown_dimensions': inferred_or_unknown_dimensions,
        'completeness_level': completeness_level,
        'completeness_reason': (
            'Missing one or more required structural or coverage fields.'
            if completeness_level == 'insufficient_for_discovery'
            else 'Required discovery fields are present, but at least one coverage dimension remains inferred/unknown or one or more source URLs remain unverified publicly in this run.'
            if completeness_level == 'source_backed_partial_but_usable'
            else 'Required discovery fields are present, coverage dimensions are fully direct, and all tracked source URL slots were publicly verified.'
        )
    })

payload = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'source_completeness_criteria',
    'generated_by': 'gpt-5.4',
    'family_context': shared['family_context'],
    'generated_from': [
        'data/insurance_discovery/shared_candidate_staging.discovery.json',
        'data/insurance_discovery/candidate_source_ledger.discovery.json',
        'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json',
        'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json',
        'data/insurance_discovery/global_coverage_evidence_ledger.discovery.json'
    ],
    'purpose': 'Defines the minimum source-completeness contract for discovery-stage candidate evidence entries and audits the current candidate set against that contract.',
    'notes': [
        'Discovery-only rubric: this artifact does not rank, score, or recommend plans.',
        'A field can be required-to-present while still allowing an unknown or partial evidence state.',
        'Null URL slots are allowed in discovery if the record explicitly preserves that the URL was not verified publicly in this run.',
        'Unknown/inferred states are not failures by themselves; silent omission is the failure mode this rubric is meant to prevent.'
    ],
    'required_entry_fields': required_entry_fields,
    'required_coverage_fields': required_coverage_fields,
    'allowed_partial_unknown_states': allowed_partial_unknown_states,
    'summary': summary,
    'audit_records': audit_records,
}

dump_json(DATA_DIR / 'source_completeness_criteria.discovery.json', payload)

lines = [
    '# Insurance discovery source completeness criteria',
    '',
    'Discovery-only companion rubric for AC 40201. It defines what a source-backed candidate evidence entry must contain, which coverage fields are mandatory, and which partial/unknown states are explicitly allowed instead of being treated as silent schema failure.',
    '',
    'Machine-readable source: `data/insurance_discovery/source_completeness_criteria.discovery.json`',
    '',
    'Summary counts:',
    f"- total_candidates_audited: {summary['total_candidates']}",
    f"- candidates_missing_required_entry_fields: {summary['candidates_missing_required_entry_fields']}",
    f"- candidates_missing_required_coverage_fields: {summary['candidates_missing_required_coverage_fields']}",
    f"- source_backed_complete_for_discovery: {summary['completeness_level_counts'].get('source_backed_complete_for_discovery', 0)}",
    f"- source_backed_partial_but_usable: {summary['completeness_level_counts'].get('source_backed_partial_but_usable', 0)}",
    f"- insufficient_for_discovery: {summary['completeness_level_counts'].get('insufficient_for_discovery', 0)}",
    f"- all_candidates_meet_minimum_discovery_schema: {'yes' if summary['all_candidates_meet_minimum_discovery_schema'] else 'no'}",
    '',
    'Notes:',
    '- Required means the field/state must be represented in the discovery entry or its linked ledger, not that the answer must always be fully confirmed.',
    '- Allowed partial/unknown states are deliberate and source-backed: they preserve uncertainty from quote gates, broker handoffs, login walls, missing PDFs, or generic marketing pages.',
    '- Silent omission is worse than an explicit unknown. This rubric prefers `unknown`, `inferred`, or `not_verified_from_reviewed_sources` over a missing field.',
    '',
    '## Required candidate-entry fields',
    '',
    '| Field | Presence rule | Why it matters |',
    '|---|---|---|',
]
for item in required_entry_fields:
    lines.append(f"| `{item['field']}` | {item['required_presence']} | {item['why_it_matters']} |")

lines.extend([
    '',
    '## Required coverage fields',
    '',
    '| Coverage field | Artifact | Allowed states | Partial/unknown allowed? | Why it matters |',
    '|---|---|---|---|---|',
])
for item in required_coverage_fields:
    lines.append(
        f"| `{item['field']}` | `{item['artifact']}` | {', '.join('`' + state + '`' for state in item['allowed_states'])} | yes | {item['why_it_matters']} |"
    )

lines.extend([
    '',
    '## Allowed partial and unknown states',
    '',
    'Country coverage assessments:',
])
for key, text in allowed_partial_unknown_states['country_coverage_assessment'].items():
    lines.append(f"- `{key}`: {text}")

lines.extend([
    '',
    'Worldwide excluding US status states:',
])
for key, text in allowed_partial_unknown_states['worldwide_ex_us_status'].items():
    lines.append(f"- `{key}`: {text}")

lines.extend([
    '',
    'URL capture states:',
])
for key, text in allowed_partial_unknown_states['source_url_capture_state'].items():
    lines.append(f"- `{key}`: {text}")

lines.extend([
    '',
    'Source directness states:',
])
for key, text in allowed_partial_unknown_states['source_directness_classification'].items():
    lines.append(f"- `{key}`: {text}")

lines.extend([
    '',
    'Evidence-entry completeness levels:',
])
for key, text in allowed_partial_unknown_states['evidence_entry_completeness_level'].items():
    lines.append(f"- `{key}`: {text}")

lines.extend([
    '',
    '## Audit of current discovery candidates',
    '',
    '| Candidate | Georgia | Germany | Worldwide ex-US | Completeness level | Missing required fields |',
    '|---|---|---|---|---|---|',
])
for record in audit_records:
    missing = ', '.join(record['required_entry_fields_missing'] + record['required_coverage_fields_missing']) or 'none'
    lines.append(
        f"| {record['candidate_id']} | `{record['coverage_states']['georgia_country_coverage_assessment']}` | `{record['coverage_states']['germany_country_coverage_assessment']}` | `{record['coverage_states']['worldwide_ex_us_status']}` | `{record['completeness_level']}` | {missing} |"
    )

(DOCS_DIR / 'insurance_discovery_source_completeness_criteria.md').write_text('\n'.join(lines) + '\n')
