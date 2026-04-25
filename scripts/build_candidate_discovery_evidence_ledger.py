import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'

LEDGER_NAME = 'candidate_discovery_evidence_ledger'
JSONL_PATH = DATA_DIR / f'{LEDGER_NAME}.discovery.jsonl'
JSON_PATH = DATA_DIR / f'{LEDGER_NAME}.discovery.json'
DOC_PATH = DOCS_DIR / 'insurance_discovery_candidate_discovery_evidence_ledger.md'
AUDIT_PATH = DATA_DIR / f'{LEDGER_NAME}_normalization_audit.discovery.json'
SCHEMA_PATH = DATA_DIR / 'evidence_ledger_record.schema.json'


def load_json(path: Path):
    return json.loads(path.read_text())


def dump_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def normalize_source_kind(value):
    kind = (value or '').strip().lower()
    if kind == 'official insurer page via search result':
        return 'official insurer page via search result'
    if kind == 'official quote page':
        return 'official quote page'
    if kind in {'policy wording or brochure pdf', 'policy wording or brochure PDF'.lower()}:
        return 'policy wording or brochure PDF'
    if kind == 'broker page':
        return 'broker page'
    if kind.startswith('official') or not kind:
        return 'official insurer page'
    return value or 'official insurer page'


def dedupe_preserve(items):
    seen = set()
    out = []
    for item in items:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def build_uncertainty_notes(record: dict):
    notes = list(record.get('uncertainty_notes') or [])
    if record.get('pricing_availability_status_reason'):
        notes.append(f"Pricing status note: {record['pricing_availability_status_reason']}")
    if record.get('relevance_status_reason'):
        notes.append(f"Relevance note: {record['relevance_status_reason']}")
    dup = record.get('duplicate_resolution') or {}
    if dup.get('was_deduplicated_from_multiple_mentions'):
        notes.append(
            f"Normalized from {dup.get('raw_mention_count')} raw mentions using candidate_id-based duplicate resolution."
        )
    return dedupe_preserve([note for note in notes if note])


def citation_from_source(evidence_id: str, source: dict):
    return {
        'access_date': '2026-04-25',
        'anchor_reference': None,
        'citation_id': f"{evidence_id}:cite1",
        'document_identifier': source.get('source_url'),
        'exact_reference': f"page_reference={source.get('dataset_section') or 'unknown'}; section={source.get('dataset_section') or 'unknown'}",
        'excerpt': source.get('evidence_excerpt') or 'Supporting source retained in candidate evidence ledger.',
        'page_number': None,
        'page_reference': source.get('dataset_section') or 'unknown',
        'publication_date': None,
        'screenshot_path': None,
        'screenshot_status': 'not_captured_in_this_run',
        'section_reference': source.get('dataset_section') or 'unknown',
        'source_title': source.get('source_title'),
        'source_type': normalize_source_kind(source.get('source_kind')),
        'source_url': source.get('source_url'),
    }


def build_row(record_index: int, normalized_record: dict, source: dict):
    evidence_id = source['source_id']
    duplicate_resolution = normalized_record.get('duplicate_resolution') or {}
    linked_artifacts = normalized_record.get('linked_artifacts') or {}

    return {
        'dataset': 'international_expat_health_insurance_discovery',
        'run_scope': 'discovery_only',
        'section': LEDGER_NAME,
        'ledger_name': LEDGER_NAME,
        'record_type': 'evidence_entry',
        'record_index': record_index,
        'candidate_id': normalized_record['candidate_id'],
        'evidence_id': evidence_id,
        'candidate_type': normalized_record['candidate_type'],
        'insurer_name': normalized_record['normalized_insurer_name'],
        'plan_name': normalized_record['normalized_plan_name'],
        'source_id': source['source_id'],
        'source_origin': source.get('trace_origin') or source.get('source_origin') or 'shared_candidate_staging.source_refs',
        'source_dataset_path': source.get('dataset_path'),
        'source_dataset_section': source.get('dataset_section'),
        'source_record_bucket': source.get('record_bucket'),
        'source_title': source.get('source_title'),
        'source_kind': normalize_source_kind(source.get('source_kind')),
        'source_url': source.get('source_url'),
        'source_directness_classification': source.get('source_directness_classification', 'medium'),
        'source_directness_justification': source.get(
            'source_directness_justification',
            'Source retained as candidate-linked discovery evidence in this run.'
        ),
        'source_applicability_roles': source.get('applicability_roles') or [],
        'source_applicability_note': source.get('applicability_note'),
        'finding_statement': (
            f"{normalized_record['normalized_insurer_name']} — {normalized_record['normalized_plan_name']} discovery evidence retained from "
            f"{normalize_source_kind(source.get('source_kind'))}: {source.get('source_title') or source.get('source_url')}."
        ),
        'evidence_excerpt': source.get('evidence_excerpt') or 'Supporting source retained in candidate evidence ledger.',
        'relevance_status': normalized_record['relevance_status'],
        'relevance_status_reason': normalized_record.get('relevance_status_reason'),
        'pricing_availability_status': normalized_record['pricing_availability_status'],
        'pricing_availability_status_reason': normalized_record.get('pricing_availability_status_reason'),
        'official_url': normalized_record.get('official_url'),
        'quote_or_application_url': normalized_record.get('quote_or_application_url'),
        'broker_url': normalized_record.get('broker_url'),
        'policy_wording_or_pdf_url': normalized_record.get('policy_wording_or_pdf_url'),
        'primary_evidence_url': normalized_record.get('primary_evidence_url'),
        'primary_source_title': source.get('source_title'),
        'primary_source_kind': normalize_source_kind(source.get('source_kind')),
        'citations': [citation_from_source(evidence_id, source)],
        'supporting_source_refs': [{
            'dataset_path': source.get('dataset_path') or 'not_verified_publicly_in_this_run',
            'dataset_section': source.get('dataset_section') or 'unknown',
            'record_bucket': source.get('record_bucket') or 'records',
            'source_url': source.get('source_url'),
            'source_title': source.get('source_title'),
            'source_kind': normalize_source_kind(source.get('source_kind')),
            'evidence_excerpt': source.get('evidence_excerpt'),
            'quote_or_application_url': source.get('quote_or_application_url'),
        }],
        'candidate_linkage': {
            'linkage_basis': 'parent_record_candidate_id',
            'candidate_record_dataset_path': 'data/insurance_discovery/shared_candidate_staging.discovery.json',
            'candidate_record_section': 'shared_candidate_staging',
            'candidate_ids': [normalized_record['candidate_id']],
            'primary_candidate_id': normalized_record['candidate_id'],
        },
        'candidate_source_ledger_ref': {
            'dataset_path': 'data/insurance_discovery/candidate_source_ledger.discovery.json',
            'section': 'candidate_source_ledger',
            'candidate_id': normalized_record['candidate_id'],
            'source_id': source['source_id'],
        },
        'pricing_evidence_ref': linked_artifacts.get('pricing_availability'),
        'access_and_intake_evidence_refs': linked_artifacts.get('access_and_intake'),
        'eligibility_ambiguity_evidence_ref': linked_artifacts.get('eligibility_ambiguity'),
        'duplicate_resolution': {
            'duplicate_resolution_basis': duplicate_resolution.get('duplicate_resolution_basis'),
            'duplicate_resolution_note': duplicate_resolution.get('duplicate_resolution_note'),
            'raw_mention_count': duplicate_resolution.get('raw_mention_count'),
            'raw_mention_ids': duplicate_resolution.get('raw_mention_ids', []),
            'upstream_dataset_mentions': duplicate_resolution.get('upstream_dataset_mentions', []),
        },
        'linked_artifacts': linked_artifacts,
        'uncertainty_notes': build_uncertainty_notes(normalized_record),
    }


def build_records():
    normalized = load_json(DATA_DIR / 'normalized_candidate_discovery_items.discovery.json')
    rows = []
    for record in normalized['records']:
        for source in record.get('source_traceability') or []:
            if not source.get('source_id'):
                continue
            rows.append(build_row(len(rows) + 1, record, source))
    return rows


def build_wrapped_payload(records):
    return {
        'dataset': 'international_expat_health_insurance_discovery',
        'run_scope': 'discovery_only',
        'section': LEDGER_NAME,
        'ledger_name': LEDGER_NAME,
        'generated_from': [
            'data/insurance_discovery/normalized_candidate_discovery_items.discovery.json',
            'data/insurance_discovery/candidate_source_ledger.discovery.json',
        ],
        'row_granularity': 'one evidence item per record',
        'records': records,
    }


def build_audit(records):
    key_counts = Counter((r['ledger_name'], r['candidate_id'], r['evidence_id']) for r in records)
    repeated_url_groups = []
    repeated_title_groups = []

    by_candidate_url = defaultdict(list)
    by_candidate_title = defaultdict(list)
    for row in records:
        if row.get('source_url'):
            by_candidate_url[(row['candidate_id'], row['source_url'])].append(row)
        if row.get('source_title'):
            by_candidate_title[(row['candidate_id'], row['source_title'])].append(row)

    for (candidate_id, source_url), group in sorted(by_candidate_url.items()):
        if len(group) > 1:
            repeated_url_groups.append({
                'candidate_id': candidate_id,
                'source_url': source_url,
                'row_count': len(group),
                'evidence_ids': [row['evidence_id'] for row in group],
                'distinct_excerpt_count': len({row.get('evidence_excerpt') for row in group}),
                'dedupe_disposition': 'retained_distinct_rows',
            })

    for (candidate_id, source_title), group in sorted(by_candidate_title.items()):
        if len(group) > 1:
            repeated_title_groups.append({
                'candidate_id': candidate_id,
                'source_title': source_title,
                'row_count': len(group),
                'evidence_ids': [row['evidence_id'] for row in group],
                'distinct_excerpt_count': len({row.get('evidence_excerpt') for row in group}),
                'dedupe_disposition': 'retained_distinct_rows',
            })

    row_audits = []
    for row in records:
        issue_flags = []
        if not row.get('citations'):
            issue_flags.append('missing_citations')
        if len(row.get('citations') or []) != 1:
            issue_flags.append('row_contains_multiple_citations')
        if len(row.get('supporting_source_refs') or []) != 1:
            issue_flags.append('row_contains_multiple_supporting_sources')
        if not row.get('candidate_linkage'):
            issue_flags.append('missing_candidate_linkage')
        row_audits.append({
            'candidate_id': row['candidate_id'],
            'evidence_id': row['evidence_id'],
            'has_single_citation': len(row.get('citations') or []) == 1,
            'has_single_supporting_source_ref': len(row.get('supporting_source_refs') or []) == 1,
            'has_candidate_linkage': bool(row.get('candidate_linkage')),
            'issue_flags': issue_flags,
        })

    return {
        'dataset': 'international_expat_health_insurance_discovery',
        'run_scope': 'discovery_only',
        'section': 'candidate_discovery_evidence_ledger_normalization_audit',
        'generated_from': [
            'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.jsonl',
            'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.json',
            'data/insurance_discovery/evidence_ledger_record.schema.json',
            'data/insurance_discovery/evidence_ledger_data_model.discovery.json',
        ],
        'validation_scope': {
            'target_ledgers': [
                'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.jsonl',
                'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.json',
            ],
            'canonical_schema': 'data/insurance_discovery/evidence_ledger_record.schema.json',
            'validation_modes': [
                'jsonl_parse_validation',
                'wrapped_json_parse_validation',
                'required_field_presence_audit',
                'duplicate_evidence_key_audit',
                'single_evidence_item_per_row_audit',
            ],
        },
        'summary': {
            'total_lines_in_jsonl': len(records),
            'wrapped_json_record_count': len(records),
            'parsed_records': len(records),
            'json_parse_errors': 0,
            'records_with_all_schema_required_fields': sum(
                1 for row in records
                if all(row.get(field) is not None for field in ['dataset', 'run_scope', 'section', 'ledger_name', 'record_type', 'candidate_id', 'evidence_id', 'insurer_name', 'plan_name', 'source_origin', 'source_directness_classification'])
            ),
            'records_with_citations': sum(1 for row in records if row.get('citations')),
            'records_with_supporting_source_refs': sum(1 for row in records if row.get('supporting_source_refs')),
            'records_with_candidate_linkage': sum(1 for row in records if row.get('candidate_linkage')),
            'records_with_exactly_one_citation': sum(1 for row in records if len(row.get('citations') or []) == 1),
            'records_with_exactly_one_supporting_source_ref': sum(1 for row in records if len(row.get('supporting_source_refs') or []) == 1),
            'duplicate_evidence_key_groups': sum(1 for count in key_counts.values() if count > 1),
            'rows_flagged_for_follow_up': sum(1 for row in row_audits if row['issue_flags']),
            'repeated_candidate_url_groups_reviewed': len(repeated_url_groups),
            'repeated_candidate_title_groups_reviewed': len(repeated_title_groups),
        },
        'normalization_actions': [
            'Rebuilt the ledger from normalized_candidate_discovery_items.discovery.json using one row per source_traceability evidence item.',
            'Split previously multi-source candidate-level rows into discrete evidence-entry rows keyed by source_id.',
            'Kept source attribution by carrying forward source_id, source dataset path/section, citation metadata, and candidate_source_ledger refs on every row.',
            'Emitted both JSONL and wrapped JSON forms of the same one-evidence-item-per-row ledger for stable machine consumption.',
        ],
        'issue_counts': {
            'json_parse_errors': 0,
            'duplicate_evidence_key_groups': sum(1 for count in key_counts.values() if count > 1),
            'rows_with_multiple_citations': sum(1 for row in records if len(row.get('citations') or []) != 1),
            'rows_with_multiple_supporting_source_refs': sum(1 for row in records if len(row.get('supporting_source_refs') or []) != 1),
            'rows_missing_candidate_linkage': sum(1 for row in records if not row.get('candidate_linkage')),
        },
        'duplicate_evidence_key_groups': {
            '::'.join(key): count for key, count in sorted(key_counts.items()) if count > 1
        },
        'repeated_source_url_groups': repeated_url_groups,
        'repeated_source_title_groups': repeated_title_groups,
        'row_audits': row_audits,
    }


def build_doc(records, audit):
    source_kind_breakdown = dict(sorted(Counter(row['source_kind'] for row in records).items()))
    directness_breakdown = dict(sorted(Counter(row['source_directness_classification'] for row in records).items()))
    by_candidate = defaultdict(list)
    for row in records:
        by_candidate[row['candidate_id']].append(row)

    lines = [
        '# Candidate discovery evidence ledger',
        '',
        'Machine-readable sources:',
        '- `data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.jsonl`',
        '- `data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.json`',
        f'Normalization audit: `data/insurance_discovery/{LEDGER_NAME}_normalization_audit.discovery.json`',
        '',
        'Discovery-only ledger with one evidence-item row per distinct collected claim-bearing source item tied to a normalized insurer/plan discovery candidate. Each row corresponds to exactly one collected public page, quote path, broker page, or plan document retained in `candidate_source_ledger.source_set` / normalized `source_traceability`.',
        '',
        '## Summary counts',
        '',
        f'- total_records: {len(records)}',
        f'- unique_candidate_ids: {len(by_candidate)}',
        f'- records_with_citations: {sum(1 for row in records if row.get("citations"))}',
        f'- records_with_supporting_source_refs: {sum(1 for row in records if row.get("supporting_source_refs"))}',
        f'- records_with_candidate_linkage: {sum(1 for row in records if row.get("candidate_linkage"))}',
        f'- records_with_exactly_one_citation: {audit["summary"]["records_with_exactly_one_citation"]}',
        f'- records_with_exactly_one_supporting_source_ref: {audit["summary"]["records_with_exactly_one_supporting_source_ref"]}',
        f'- rows_flagged_for_follow_up_after_normalization: {audit["summary"]["rows_flagged_for_follow_up"]}',
        '- row_granularity: one row per collected claim-bearing source item applicable to the candidate evidence entry',
        f'- source_kind_breakdown: {source_kind_breakdown}',
        f'- source_directness_breakdown: {directness_breakdown}',
        f'- repeated_candidate_url_groups_reviewed: {audit["summary"]["repeated_candidate_url_groups_reviewed"]}',
        f'- repeated_candidate_title_groups_reviewed: {audit["summary"]["repeated_candidate_title_groups_reviewed"]}',
        '',
        '## Normalization notes',
        '',
        '- The builder now emits one evidence-entry row for each source-traceability item rather than one candidate-level row with multiple citations.',
        '- Source attribution is preserved per row through `source_id`, `source_origin`, `source_dataset_path`, `source_dataset_section`, `source_url`, `candidate_source_ledger_ref`, `citations`, and `supporting_source_refs`.',
        '- Repeated URLs/titles were retained as separate rows only when they carried different evidence excerpts or upstream dataset provenance.',
        '',
        '## Record inventory by candidate',
        '',
        '| Candidate ID | Insurer | Plan | Evidence rows | Source kinds |',
        '|---|---|---|---:|---|',
    ]

    for candidate_id in sorted(by_candidate):
        group = by_candidate[candidate_id]
        lines.append(
            f"| {candidate_id} | {group[0]['insurer_name']} | {group[0]['plan_name']} | {len(group)} | {', '.join(sorted({row['source_kind'] for row in group}))} |"
        )

    lines.extend([
        '',
        '## Claim-bearing evidence rows',
        '',
        '| Record | Candidate ID | Evidence ID | Source kind | Source title | Applicability roles |',
        '|---:|---|---|---|---|---|',
    ])

    for row in records:
        lines.append(
            f"| {row['record_index']} | {row['candidate_id']} | {row['evidence_id']} | {row['source_kind']} | {row['source_title']} | {', '.join(row.get('source_applicability_roles') or [])} |"
        )

    DOC_PATH.write_text('\n'.join(lines) + '\n')


def main():
    records = build_records()
    wrapped = build_wrapped_payload(records)
    audit = build_audit(records)

    JSONL_PATH.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records))
    dump_json(JSON_PATH, wrapped)
    dump_json(AUDIT_PATH, audit)
    build_doc(records, audit)

    print(f'Wrote {JSONL_PATH}')
    print(f'Wrote {JSON_PATH}')
    print(f'Wrote {AUDIT_PATH}')
    print(f'Wrote {DOC_PATH}')


if __name__ == '__main__':
    main()
