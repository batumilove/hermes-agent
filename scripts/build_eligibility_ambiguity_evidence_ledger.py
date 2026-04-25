import json
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'


def load(name: str):
    return json.loads((DATA_DIR / name).read_text())


def dump_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


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


shared = load('shared_candidate_staging.discovery.json')
source_ledger = load('candidate_source_ledger.discovery.json')
quote = load('quote_intake_gating_requirements.discovery.json')
germany = load('germany_coverage_evidence_ledger.discovery.json')
georgia = load('georgia_coverage_evidence_ledger.discovery.json')

source_by_id = {r['candidate_id']: r for r in source_ledger['records']}
quote_by_id = {r['candidate_id']: r for r in quote['records']}
germany_by_id = {r['candidate_id']: r for r in germany['records']}
georgia_by_id = {r['candidate_id']: r for r in georgia['records']}


STATUS_LEGEND = {
    'explicit_country_selector_but_final_eligibility_unverified': 'Public sources explicitly named Georgia and/or Germany in an insurer-controlled selector or country list, but those sources still did not prove final issueability or underwriting acceptance for this German family resident in Georgia.',
    'residence_or_nationality_rules_visible_but_family_fit_unresolved': 'Public sources exposed country-of-residence, nationality, home-country, or residence-zone mechanics materially relevant to this family profile, but the exact Germany-plus-Georgia fit remains unresolved.',
    'geography_structure_visible_but_country_fit_inferred': 'Public sources exposed area-of-cover, regional, worldwide, or outside-U.S. structure that makes Germany/Georgia plausibly in scope, but the country combination was not directly confirmed.',
    'generic_international_positioning_only': 'Public sources only showed broad expat or international positioning without enough country-specific eligibility detail to resolve the family context.',
    'gated_or_incomplete_public_evidence': 'Public quote, application, or member materials were challenge-gated, broker-led, or otherwise incomplete, leaving eligibility materially ambiguous in reviewed sources.'
}


ASSESSMENT_LEGEND = {
    'mixed_signals': 'There is some direct or structured geography evidence, but it stops short of proving that German nationality plus Georgia residence is acceptable for enrollment.',
    'unclear': 'The reviewed public evidence remained too generic, too incomplete, or too gated to support a reliable eligibility read for this family context.'
}


def classify_record(shared_rec, quote_rec, germany_rec, georgia_rec, source_rec):
    germany_status = germany_rec['germany_coverage_status']
    georgia_status = georgia_rec['georgia_coverage_status']
    quote_path = quote_rec['quote_intake_path_type']
    required_fields = ' '.join(quote_rec.get('required_quote_form_fields_observed', []))
    eligibility_details = ' '.join(quote_rec.get('eligibility_precheck_details', []))
    all_uncertainty = ' '.join(shared_rec.get('uncertainty_notes', []))

    if 'nationality' in required_fields.lower() or 'nationality' in eligibility_details.lower() or 'country of nationality' in required_fields.lower():
        return 'residence_or_nationality_rules_visible_but_family_fit_unresolved', 'mixed_signals'
    if 'home-country' in ' '.join(shared_rec.get('germany_evidence', []) + shared_rec.get('georgia_evidence', [])).lower():
        return 'residence_or_nationality_rules_visible_but_family_fit_unresolved', 'mixed_signals'
    if georgia_status == 'georgia_explicitly_listed' or germany_status == 'germany_explicitly_listed':
        return 'explicit_country_selector_but_final_eligibility_unverified', 'mixed_signals'
    if quote_path in {'challenge_wall_before_quote', 'technical_barrier_on_external_quote_tool'}:
        return 'gated_or_incomplete_public_evidence', 'unclear'
    if 'broker' in (source_rec.get('broker_url') or '').lower() or source_rec.get('broker_url'):
        return 'gated_or_incomplete_public_evidence', 'unclear'
    if any(token in all_uncertainty.lower() for token in ['citizenship', 'nationality', 'eligible residence', 'country eligibility', 'residency/citizenship']):
        return 'residence_or_nationality_rules_visible_but_family_fit_unresolved', 'mixed_signals'
    structured_geo_markers = [
        'area_of_cover', 'area-of-cover', 'region', 'zone', 'worldwide', 'outside u.s.', 'outside-us', 'exclude the usa', 'excluding us', 'europe'
    ]
    geo_text = ' '.join(
        shared_rec.get('worldwide_ex_us_evidence', [])
        + shared_rec.get('germany_evidence', [])
        + shared_rec.get('georgia_evidence', [])
        + quote_rec.get('eligibility_precheck_details', [])
    ).lower()
    if any(marker in geo_text for marker in structured_geo_markers):
        return 'geography_structure_visible_but_country_fit_inferred', 'mixed_signals'
    return 'generic_international_positioning_only', 'unclear'


def build_source_statements(shared_rec, quote_rec, germany_rec, georgia_rec):
    statements = []
    for detail in quote_rec.get('eligibility_precheck_details', []):
        statements.append({
            'theme': 'country_of_residence_or_quote_precheck',
            'statement': detail,
            'source_url': quote_rec.get('quote_or_application_url') or quote_rec.get('primary_source_url'),
            'source_type': 'quote intake evidence'
        })
    for field in quote_rec.get('required_quote_form_fields_observed', []):
        lowered = field.lower()
        if any(token in lowered for token in ['living', 'residence', 'country', 'nationality']):
            statements.append({
                'theme': 'visible_required_field',
                'statement': field,
                'source_url': quote_rec.get('quote_or_application_url') or quote_rec.get('primary_source_url'),
                'source_type': 'quote intake evidence'
            })
    for excerpt in shared_rec.get('source_refs', []):
        text = excerpt.get('evidence_excerpt', '')
        lowered = text.lower()
        if any(token in lowered for token in ['georgia', 'germany', 'worldwide', 'usa', 'u.s.', 'country of residence', 'nationality', 'home-country', 'home country', 'eu', 'eea', 'abroad', 'living abroad']):
            statements.append({
                'theme': 'captured_public_source_excerpt',
                'statement': text,
                'source_url': excerpt.get('source_url'),
                'source_type': excerpt.get('dataset_section', 'source excerpt')
            })
    for statement in germany_rec.get('direct_germany_relevance_evidence', []):
        statements.append({
            'theme': 'germany_applicability',
            'statement': statement,
            'source_url': (germany_rec.get('supporting_source_refs') or [{}])[0].get('source_url'),
            'source_type': 'germany coverage ledger'
        })
    for statement in georgia_rec.get('direct_georgia_relevance_evidence', []):
        statements.append({
            'theme': 'georgia_applicability',
            'statement': statement,
            'source_url': (georgia_rec.get('supporting_source_refs') or [{}])[0].get('source_url'),
            'source_type': 'georgia coverage ledger'
        })
    for note in shared_rec.get('uncertainty_notes', []):
        lowered = note.lower()
        if any(token in lowered for token in ['residence', 'citizenship', 'nationality', 'georgia', 'germany', 'eligibility']):
            statements.append({
                'theme': 'captured_uncertainty',
                'statement': note,
                'source_url': None,
                'source_type': 'shared discovery uncertainty note'
            })
    return dedupe_preserve(statements)


def summarize_dimensions(shared_rec, quote_rec, germany_rec, georgia_rec):
    required_fields = quote_rec.get('required_quote_form_fields_observed', [])
    visible_residence = [f for f in required_fields if any(t in f.lower() for t in ['living', 'residence', 'country'])]
    visible_nationality = [f for f in required_fields if 'nationality' in f.lower()]
    if visible_residence or quote_rec.get('eligibility_precheck_observed'):
        residence_summary = 'Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.'
    else:
        residence_summary = 'Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.'

    if visible_nationality:
        nationality_summary = 'Public quote flow explicitly asks about nationality or country of nationality, making German citizenship a visible intake variable rather than an inferred one.'
    else:
        nationality_summary = 'German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.'

    germany_assessment = germany_rec['germany_country_coverage_assessment']
    georgia_assessment = georgia_rec['georgia_country_coverage_assessment']
    geographic_summary = (
        f"Germany applicability is {germany_assessment} in the Germany coverage ledger and Georgia applicability is {georgia_assessment} in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context."
    )
    return residence_summary, nationality_summary, geographic_summary


records = []
status_counts = {}
assessment_counts = {}
for idx, shared_rec in enumerate(shared['staging_candidates'], start=1):
    candidate_id = shared_rec['candidate_id']
    source_rec = source_by_id[candidate_id]
    quote_rec = quote_by_id[candidate_id]
    germany_rec = germany_by_id[candidate_id]
    georgia_rec = georgia_by_id[candidate_id]

    ambiguity_status, ambiguity_assessment = classify_record(shared_rec, quote_rec, germany_rec, georgia_rec, source_rec)
    status_counts[ambiguity_status] = status_counts.get(ambiguity_status, 0) + 1
    assessment_counts[ambiguity_assessment] = assessment_counts.get(ambiguity_assessment, 0) + 1

    statements = build_source_statements(shared_rec, quote_rec, germany_rec, georgia_rec)
    residence_summary, nationality_summary, geographic_summary = summarize_dimensions(shared_rec, quote_rec, germany_rec, georgia_rec)

    records.append({
        'index': idx,
        'candidate_id': candidate_id,
        'insurer_name': shared_rec['normalized_insurer_name'],
        'plan_name': shared_rec['normalized_plan_name'],
        'candidate_type': shared_rec['candidate_type'],
        'eligibility_ambiguity_status': ambiguity_status,
        'eligibility_ambiguity_assessment': ambiguity_assessment,
        'eligibility_ambiguity_summary': ' '.join([
            residence_summary,
            nationality_summary,
            geographic_summary
        ]),
        'family_context_specificity': {
            'residence_country_focus': 'Georgia',
            'nationality_focus': 'German',
            'coverage_focus': 'Georgia and Germany, ideally worldwide excluding the US'
        },
        'residence_country_ambiguity_summary': residence_summary,
        'nationality_ambiguity_summary': nationality_summary,
        'geographic_applicability_summary': geographic_summary,
        'source_statements': statements,
        'linked_evidence': {
            'quote_intake_gating_requirements': {
                'dataset_path': 'data/insurance_discovery/quote_intake_gating_requirements.discovery.json',
                'record_index': quote_rec['index'],
                'candidate_id': candidate_id
            },
            'germany_coverage_evidence_ledger': {
                'dataset_path': 'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json',
                'record_index': germany_rec['index'],
                'candidate_id': candidate_id
            },
            'georgia_coverage_evidence_ledger': {
                'dataset_path': 'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json',
                'record_index': georgia_rec['index'],
                'candidate_id': candidate_id
            },
            'candidate_source_ledger': {
                'dataset_path': 'data/insurance_discovery/candidate_source_ledger.discovery.json',
                'candidate_id': candidate_id
            },
            'shared_candidate_staging': {
                'dataset_path': 'data/insurance_discovery/shared_candidate_staging.discovery.json',
                'candidate_id': candidate_id
            }
        },
        'uncertainty_notes': dedupe_preserve(
            listify(shared_rec.get('uncertainty_notes'))
            + listify(quote_rec.get('uncertainty_notes'))
            + [
                'Discovery-only ledger entry: this record captures publicly visible ambiguity signals, not final eligibility or underwriting acceptance.'
            ]
        )
    })

payload = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'eligibility_ambiguity_evidence_ledger',
    'generated_by': 'gpt-5.4',
    'generated_from': [
        'data/insurance_discovery/shared_candidate_staging.discovery.json',
        'data/insurance_discovery/candidate_source_ledger.discovery.json',
        'data/insurance_discovery/quote_intake_gating_requirements.discovery.json',
        'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json',
        'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json'
    ],
    'family_context': shared['family_context'],
    'status_legend': STATUS_LEGEND,
    'assessment_legend': ASSESSMENT_LEGEND,
    'summary': {
        'verified_total_unique_candidates': len(records),
        'eligibility_ambiguity_status_counts': status_counts,
        'eligibility_ambiguity_assessment_counts': assessment_counts,
        'notes': [
            'Discovery-only ledger: this artifact identifies publicly visible ambiguity signals related to nationality, residence-country fit, and geography applicability.',
            'Explicit selector presence was treated as stronger than generic worldwide marketing, but it still does not prove final issueability for a German family resident in Georgia.',
            'Where quote flows or policy materials were blocked, the ledger preserves uncertainty explicitly rather than inferring acceptance.'
        ]
    },
    'records': records
}

json_path = DATA_DIR / 'eligibility_ambiguity_evidence_ledger.discovery.json'
dump_json(json_path, payload)

md_lines = [
    '# Eligibility ambiguity evidence ledger',
    '',
    'Machine-readable source: `data/insurance_discovery/eligibility_ambiguity_evidence_ledger.discovery.json`',
    '',
    'This discovery-only ledger records source-backed ambiguity signals for whether each candidate plan appears compatible with a German citizen family of four residing in Georgia and seeking coverage relevant to Georgia and Germany, ideally worldwide excluding the US.',
    '',
    '## Summary',
    '',
    f"- Verified total unique candidates: {len(records)}",
]
for key, value in status_counts.items():
    md_lines.append(f'- {key}: {value}')
md_lines.extend([
    '',
    '## Notes',
    '',
    '- The ledger does not rank plans or determine final eligibility.',
    '- Each record aggregates publicly visible statements touching residence-country screening, nationality variables, and geography applicability.',
    '- When public evidence was gated, broker-led, or generic, the ambiguity remains explicit rather than guessed away.',
    ''
])
for rec in records:
    md_lines.extend([
        f"## {rec['insurer_name']} — {rec['plan_name']}",
        '',
        f"- Candidate ID: `{rec['candidate_id']}`",
        f"- Eligibility ambiguity status: `{rec['eligibility_ambiguity_status']}`",
        f"- Assessment: `{rec['eligibility_ambiguity_assessment']}`",
        f"- Residence ambiguity: {rec['residence_country_ambiguity_summary']}",
        f"- Nationality ambiguity: {rec['nationality_ambiguity_summary']}",
        f"- Geographic applicability: {rec['geographic_applicability_summary']}",
        '- Source-backed statements:'
    ])
    for statement in rec['source_statements'][:6]:
        src = statement['source_url'] or 'shared-dataset note'
        md_lines.append(f"  - [{statement['theme']}] {statement['statement']} ({src})")
    if len(rec['source_statements']) > 6:
        md_lines.append(f"  - ... {len(rec['source_statements']) - 6} additional machine-readable statement(s) retained in the JSON ledger.")
    md_lines.append('')

(DOCS_DIR / 'insurance_discovery_eligibility_ambiguity_evidence_ledger.md').write_text('\n'.join(md_lines) + '\n')

source_entry = {
    'path': 'data/insurance_discovery/eligibility_ambiguity_evidence_ledger.discovery.json',
    'section': 'eligibility_ambiguity_evidence_ledger'
}
if source_entry not in shared.get('source_datasets', []):
    shared.setdefault('source_datasets', []).append(source_entry)

shared['eligibility_ambiguity_evidence_ledger_artifact'] = {
    'path': 'data/insurance_discovery/eligibility_ambiguity_evidence_ledger.discovery.json',
    'section': 'eligibility_ambiguity_evidence_ledger',
    'purpose': 'Candidate-level record of source-backed ambiguity around residence-country fit, nationality relevance, and Georgia/Germany geographic applicability for the target family context.'
}

for rec in shared['staging_candidates']:
    rec['eligibility_ambiguity_evidence_ref'] = {
        'ledger_dataset_path': 'data/insurance_discovery/eligibility_ambiguity_evidence_ledger.discovery.json',
        'eligibility_ambiguity_candidate_id': rec['candidate_id']
    }

# keep source_datasets stable and deduped
shared['source_datasets'] = dedupe_preserve(shared.get('source_datasets', []))
dump_json(DATA_DIR / 'shared_candidate_staging.discovery.json', shared)
print(f'Wrote {json_path}')
print(f'Wrote {DOCS_DIR / "insurance_discovery_eligibility_ambiguity_evidence_ledger.md"}')
print(f'Updated {DATA_DIR / "shared_candidate_staging.discovery.json"}')
