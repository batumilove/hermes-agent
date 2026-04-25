import json
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'


def load_json(name: str):
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


shared = load_json('shared_candidate_staging.discovery.json')
georgia = load_json('georgia_coverage_evidence_ledger.discovery.json')
germany = load_json('germany_coverage_evidence_ledger.discovery.json')
global_cov = load_json('global_coverage_evidence_ledger.discovery.json')

georgia_by_id = {record['candidate_id']: record for record in georgia['records']}
germany_by_id = {record['candidate_id']: record for record in germany['records']}
global_by_id = {record['candidate_id']: record for record in global_cov['records']}


DIMENSION_RULES = {
    'georgia': {
        'state_field': 'georgia_country_coverage_assessment',
        'status_field': 'georgia_coverage_status',
        'summary_field': 'georgia_coverage_summary',
        'evidence_field': 'direct_georgia_relevance_evidence',
        'rationale_field': 'georgia_country_coverage_rationale',
        'dimension_label': 'Georgia country applicability',
        'certainty_states': {'confirmed'},
        'ambiguity_reason_map': {
            'confirmed': 'Country presence was publicly observed, but plan fit may still be incomplete or conditional.',
            'inferred': 'Country fit is only inferred from region, area-of-cover, or similar structure rather than named directly.',
            'unknown': 'Reviewed public evidence was too generic, gated, or incomplete to verify this country dimension.'
        }
    },
    'germany': {
        'state_field': 'germany_country_coverage_assessment',
        'status_field': 'germany_coverage_status',
        'summary_field': 'germany_coverage_summary',
        'evidence_field': 'direct_germany_relevance_evidence',
        'rationale_field': 'germany_country_coverage_rationale',
        'dimension_label': 'Germany country applicability',
        'certainty_states': {'confirmed'},
        'ambiguity_reason_map': {
            'confirmed': 'Country presence was publicly observed, but the reviewed sources still do not settle all family-specific coverage conditions.',
            'inferred': 'Germany fit is supported by structure or market context, but not by directly captured Germany-specific wording in every case.',
            'unknown': 'Reviewed public evidence was too generic, gated, or incomplete to verify this country dimension.'
        }
    },
    'worldwide_ex_us': {
        'state_field': 'worldwide_ex_us_status',
        'status_field': 'worldwide_ex_us_status',
        'summary_field': 'worldwide_ex_us_status_reason',
        'evidence_field': 'global_coverage_evidence',
        'rationale_field': 'worldwide_ex_us_status_reason',
        'dimension_label': 'Worldwide excluding U.S. configuration relevance',
        'certainty_states': {'explicitly_indicated'},
        'ambiguity_reason_map': {
            'explicitly_indicated': 'A non-U.S. global structure was explicitly indicated, but exact family pricing/issueability may still be unresolved.',
            'indirectly_indicated': 'The reviewed sources suggest a non-U.S. global option indirectly, without exact ex-U.S. wording for the captured plan evidence.',
            'worldwide_positioned_but_ex_us_not_verified': 'The plan is globally positioned, but the reviewed evidence did not verify an ex-U.S. configuration.',
            'not_verified_from_reviewed_sources': 'Reviewed public sources did not verify enough geography detail to support an ex-U.S. configuration claim.'
        }
    }
}


STATE_UNCERTAINTY_FLAGS = {
    'confirmed': 'partial_because_country_presence_does_not_equal_final_family_fit',
    'inferred': 'ambiguous_because_country_fit_is_inferred_not_directly_named',
    'unknown': 'ambiguous_because_reviewed_public_sources_did_not_verify_dimension',
    'explicitly_indicated': 'partial_because_configuration_exists_but_family_specific_terms_remain_unverified',
    'indirectly_indicated': 'ambiguous_because_non_us_global_configuration_is_only_indirectly_supported',
    'worldwide_positioned_but_ex_us_not_verified': 'ambiguous_because_global_positioning_does_not_verify_ex_us_configuration',
    'not_verified_from_reviewed_sources': 'ambiguous_because_reviewed_sources_did_not_verify_ex_us_configuration'
}


records = []
summary = {
    'verified_total_unique_candidates': 0,
    'dimensions_with_explicit_uncertainty_flags': {
        'georgia': 0,
        'germany': 0,
        'worldwide_ex_us': 0,
    },
    'dimension_state_counts': {
        'georgia': {},
        'germany': {},
        'worldwide_ex_us': {},
    },
    'candidates_with_any_partial_or_ambiguous_coverage_dimension': 0,
    'candidates_with_all_three_dimensions_still_partial_or_ambiguous': 0,
    'notes': [
        'Discovery-only artifact: this ledger annotates partial or ambiguous coverage details without ranking plans or asserting final eligibility.',
        'Each candidate entry carries source-backed evidence notes plus an explicit uncertainty flag for Georgia, Germany, and worldwide-excluding-U.S. screening.'
    ]
}

for index, candidate in enumerate(shared['staging_candidates'], start=1):
    candidate_id = candidate['candidate_id']
    georgia_record = georgia_by_id[candidate_id]
    germany_record = germany_by_id[candidate_id]
    global_record = global_by_id[candidate_id]

    dimension_annotations = {}
    partial_dimension_keys = []

    for dimension_key, config in DIMENSION_RULES.items():
        source_record = {
            'georgia': georgia_record,
            'germany': germany_record,
            'worldwide_ex_us': global_record,
        }[dimension_key]
        state = source_record[config['state_field']]
        supporting_refs = source_record.get('supporting_source_refs', [])
        evidence_notes = dedupe_preserve(source_record.get(config['evidence_field'], []))
        uncertainty_notes = dedupe_preserve(
            source_record.get('uncertainty_notes', [])
            + candidate.get('uncertainty_notes', [])
        )
        partial_or_ambiguous = state not in config['certainty_states'] or bool(uncertainty_notes)
        explicit_flag = STATE_UNCERTAINTY_FLAGS[state]
        unresolved_aspects = dedupe_preserve([
            note for note in uncertainty_notes
            if any(token in note.lower() for token in ['georgia', 'germany', 'worldwide', 'u.s', 'us', 'area-of-cover', 'area of cover', 'eligibility', 'pricing', 'underwriting', 'quote', 'brochure', 'benefits', 'residence'])
        ])

        annotation = {
            'dimension': dimension_key,
            'dimension_label': config['dimension_label'],
            'coverage_state': state,
            'coverage_status_detail': source_record[config['status_field']],
            'coverage_summary': source_record[config['summary_field']],
            'source_backed_evidence_notes': evidence_notes,
            'source_backed_rationale': source_record[config['rationale_field']],
            'supporting_source_refs': supporting_refs,
            'partial_or_ambiguous': partial_or_ambiguous,
            'explicit_uncertainty_flag': explicit_flag,
            'ambiguity_reason': config['ambiguity_reason_map'][state],
            'unresolved_aspects': unresolved_aspects,
            'source_ledger_ref': {
                'dataset_path': {
                    'georgia': 'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json',
                    'germany': 'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json',
                    'worldwide_ex_us': 'data/insurance_discovery/global_coverage_evidence_ledger.discovery.json'
                }[dimension_key],
                'candidate_id': candidate_id
            }
        }
        dimension_annotations[dimension_key] = annotation
        summary['verified_total_unique_candidates'] = index
        summary['dimensions_with_explicit_uncertainty_flags'][dimension_key] += 1
        bucket = summary['dimension_state_counts'][dimension_key]
        bucket[state] = bucket.get(state, 0) + 1
        if partial_or_ambiguous:
            partial_dimension_keys.append(dimension_key)

    if partial_dimension_keys:
        summary['candidates_with_any_partial_or_ambiguous_coverage_dimension'] += 1
    if len(partial_dimension_keys) == 3:
        summary['candidates_with_all_three_dimensions_still_partial_or_ambiguous'] += 1

    overall_flags = [
        dimension_annotations[key]['explicit_uncertainty_flag']
        for key in ['georgia', 'germany', 'worldwide_ex_us']
    ]

    record = {
        'index': index,
        'candidate_id': candidate_id,
        'insurer_name': candidate['normalized_insurer_name'],
        'plan_name': candidate['normalized_plan_name'],
        'candidate_type': candidate['candidate_type'],
        'coverage_ambiguity_annotations': dimension_annotations,
        'has_partial_or_ambiguous_coverage_details': bool(partial_dimension_keys),
        'partial_or_ambiguous_dimensions': partial_dimension_keys,
        'overall_uncertainty_flags': overall_flags,
        'artifact_note': 'Discovery-only: coverage annotations preserve source-backed ambiguity and unresolved questions rather than claiming final eligibility or coverage issuance.'
    }
    records.append(record)

    candidate['coverage_ambiguity_annotations'] = dimension_annotations
    candidate['has_partial_or_ambiguous_coverage_details'] = bool(partial_dimension_keys)
    candidate['partial_or_ambiguous_coverage_dimensions'] = partial_dimension_keys
    candidate['coverage_uncertainty_flags'] = overall_flags

payload = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'coverage_ambiguity_annotations',
    'generated_by': 'gpt-5.4',
    'generated_from': [
        'data/insurance_discovery/shared_candidate_staging.discovery.json',
        'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json',
        'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json',
        'data/insurance_discovery/global_coverage_evidence_ledger.discovery.json'
    ],
    'family_context': shared['family_context'],
    'field_legend': {
        'partial_or_ambiguous': 'True when the reviewed public evidence remains conditional, inferred, gated, or otherwise incomplete for this coverage dimension.',
        'explicit_uncertainty_flag': 'Stable machine-readable reason code describing why the coverage detail remains partial or ambiguous in discovery.',
        'source_backed_evidence_notes': 'Concise evidence notes grounded in the linked public-source coverage ledger.',
        'unresolved_aspects': 'Open questions or follow-up needs preserved from candidate/ledger uncertainty notes.'
    },
    'summary': summary,
    'records': records
}

json_path = DATA_DIR / 'coverage_ambiguity_annotations.discovery.json'
dump_json(json_path, payload)

source_entry = {
    'path': 'data/insurance_discovery/coverage_ambiguity_annotations.discovery.json',
    'section': 'coverage_ambiguity_annotations'
}
if source_entry not in shared.get('source_datasets', []):
    shared.setdefault('source_datasets', []).append(source_entry)

shared['coverage_ambiguity_annotations_artifact'] = {
    'path': 'data/insurance_discovery/coverage_ambiguity_annotations.discovery.json',
    'section': 'coverage_ambiguity_annotations',
    'purpose': 'Per-candidate, source-backed annotations of partial or ambiguous coverage details for Georgia, Germany, and worldwide excluding the U.S.'
}
shared['source_datasets'] = dedupe_preserve(shared.get('source_datasets', []))
dump_json(DATA_DIR / 'shared_candidate_staging.discovery.json', shared)

md_lines = [
    '# Coverage ambiguity annotations',
    '',
    'Machine-readable source: `data/insurance_discovery/coverage_ambiguity_annotations.discovery.json`',
    '',
    'This discovery-only ledger annotates partial or ambiguous coverage details for each candidate insurer/plan using source-backed evidence from the Georgia, Germany, and worldwide-excluding-U.S. coverage ledgers.',
    '',
    '## Summary',
    '',
    f"- Verified total unique candidates: {summary['verified_total_unique_candidates']}",
    f"- Candidates with any partial or ambiguous coverage dimension: {summary['candidates_with_any_partial_or_ambiguous_coverage_dimension']}",
    f"- Candidates with all three dimensions still partial or ambiguous: {summary['candidates_with_all_three_dimensions_still_partial_or_ambiguous']}",
    ''
]
for dimension_key in ['georgia', 'germany', 'worldwide_ex_us']:
    md_lines.append(f"- {dimension_key} dimension entries with explicit uncertainty flags: {summary['dimensions_with_explicit_uncertainty_flags'][dimension_key]}")
md_lines.extend(['', '## Candidate annotations', ''])

for record in records:
    md_lines.extend([
        f"## {record['insurer_name']} — {record['plan_name']}",
        '',
        f"- Candidate ID: `{record['candidate_id']}`",
        f"- Partial or ambiguous coverage details present: `{str(record['has_partial_or_ambiguous_coverage_details']).lower()}`",
        f"- Partial or ambiguous dimensions: `{', '.join(record['partial_or_ambiguous_dimensions']) if record['partial_or_ambiguous_dimensions'] else 'none'}`",
        '- Dimension details:'
    ])
    for dimension_key in ['georgia', 'germany', 'worldwide_ex_us']:
        detail = record['coverage_ambiguity_annotations'][dimension_key]
        md_lines.append(
            f"  - `{dimension_key}`: state=`{detail['coverage_state']}`; partial_or_ambiguous=`{str(detail['partial_or_ambiguous']).lower()}`; explicit_uncertainty_flag=`{detail['explicit_uncertainty_flag']}`"
        )
        md_lines.append(f"    - Summary: {detail['coverage_summary']}")
        if detail['source_backed_evidence_notes']:
            md_lines.append(f"    - Evidence note: {detail['source_backed_evidence_notes'][0]}")
        if detail['unresolved_aspects']:
            md_lines.append(f"    - Unresolved: {detail['unresolved_aspects'][0]}")
    md_lines.append('')

(DOCS_DIR / 'insurance_discovery_coverage_ambiguity_annotations.md').write_text('\n'.join(md_lines) + '\n')

print(f'Wrote {json_path}')
print(f'Wrote {DOCS_DIR / "insurance_discovery_coverage_ambiguity_annotations.md"}')
print(f'Updated {DATA_DIR / "shared_candidate_staging.discovery.json"}')
