#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / 'candidate_discovery_evidence_ledger.discovery.json'
AUDIT_PATH = ROOT / 'candidate_discovery_evidence_ledger_normalization_audit.discovery.json'
OUTPUT_JSON_PATH = ROOT / 'candidate_discovery_overlap_resolution.discovery.json'
OUTPUT_MD_PATH = ROOT.parent.parent / 'docs' / 'insurance_discovery_candidate_discovery_overlap_resolution.md'


def load_json(path: Path):
    return json.loads(path.read_text())


def build_overlap_index(records, fields):
    groups = defaultdict(list)
    for record in records:
        key = tuple(record.get(field) for field in fields)
        groups[key].append(record)
    return {
        key: value
        for key, value in groups.items()
        if len(value) > 1
    }


def main():
    ledger = load_json(LEDGER_PATH)
    audit = load_json(AUDIT_PATH)
    records = ledger['records']

    by_candidate = defaultdict(list)
    for record in records:
        by_candidate[record['candidate_id']].append(record)

    residual_checks = {
        'candidate_plus_source_url_and_source_kind': build_overlap_index(records, ['candidate_id', 'source_url', 'source_kind']),
        'candidate_plus_source_url': build_overlap_index(records, ['candidate_id', 'source_url']),
        'candidate_plus_source_title_and_source_kind': build_overlap_index(records, ['candidate_id', 'source_title', 'source_kind']),
        'candidate_plus_source_title': build_overlap_index(records, ['candidate_id', 'source_title']),
    }

    repeated_source_url_groups = audit.get('repeated_source_url_groups', [])
    repeated_source_title_groups = audit.get('repeated_source_title_groups', [])

    affected_canonical_evidence_ids = sorted({
        group['canonical_evidence_id']
        for group in repeated_source_url_groups
    })

    candidate_overlap_summary = []
    for candidate_id in sorted(by_candidate):
        candidate_records = by_candidate[candidate_id]
        canonical_rows = [
            {
                'evidence_id': record['evidence_id'],
                'source_title': record['source_title'],
                'source_kind': record['source_kind'],
                'source_url': record['source_url'],
                'dedupe_disposition': record['duplicate_resolution'].get('dedupe_disposition', 'not_applicable'),
                'dropped_duplicate_evidence_ids': record['duplicate_resolution'].get('dropped_duplicate_evidence_ids', []),
            }
            for record in candidate_records
        ]
        candidate_overlap_summary.append({
            'candidate_id': candidate_id,
            'record_count': len(candidate_records),
            'canonical_rows_with_merged_overlap': sum(1 for row in canonical_rows if row['dropped_duplicate_evidence_ids']),
            'rows': canonical_rows,
        })

    output = {
        'dataset': ledger['dataset'],
        'run_scope': ledger['run_scope'],
        'section': 'candidate_discovery_overlap_resolution',
        'ledger_name': 'candidate_discovery_overlap_resolution',
        'generated_from': [
            'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.json',
            'data/insurance_discovery/candidate_discovery_evidence_ledger_normalization_audit.discovery.json',
        ],
        'purpose': 'Final non-overlap audit for candidate discovery evidence rows after canonical deduplication of partially overlapping source evidence items.',
        'summary': {
            'final_record_count': len(records),
            'unique_candidate_ids': len(by_candidate),
            'canonical_rows_with_merged_overlap': len(affected_canonical_evidence_ids),
            'resolved_repeated_source_url_groups': len(repeated_source_url_groups),
            'resolved_repeated_source_title_groups': len(repeated_source_title_groups),
            'residual_candidate_plus_source_url_and_source_kind_groups': len(residual_checks['candidate_plus_source_url_and_source_kind']),
            'residual_candidate_plus_source_url_groups': len(residual_checks['candidate_plus_source_url']),
            'residual_candidate_plus_source_title_and_source_kind_groups': len(residual_checks['candidate_plus_source_title_and_source_kind']),
            'residual_candidate_plus_source_title_groups': len(residual_checks['candidate_plus_source_title']),
            'final_ledger_is_non_overlapping': not any(residual_checks.values()),
        },
        'resolution_policy': {
            'policy_name': 'candidate_discovery_canonical_source_item_resolution',
            'policy_version': '2026-04-25-canonical-source-item-v1',
            'canonical_selection_rule': 'Prefer the row with the longest evidence excerpt; tie-break on richer supporting-source metadata, then earliest record_index, then evidence_id.',
            'merge_behavior': 'Retain one canonical evidence row per overlapping source item and preserve dropped duplicate evidence IDs plus merged provenance in duplicate_resolution.',
        },
        'resolved_overlap_groups': {
            'source_url_groups': repeated_source_url_groups,
            'source_title_groups': repeated_source_title_groups,
        },
        'residual_overlap_groups': {
            name: [
                {
                    'group_key': {
                        fields[i]: key[i]
                        for i, fields in enumerate([
                            ['candidate_id', 'source_url', 'source_kind'],
                            ['candidate_id', 'source_url'],
                            ['candidate_id', 'source_title', 'source_kind'],
                            ['candidate_id', 'source_title'],
                        ][['candidate_plus_source_url_and_source_kind', 'candidate_plus_source_url', 'candidate_plus_source_title_and_source_kind', 'candidate_plus_source_title'].index(name)])
                    },
                    'evidence_ids': [record['evidence_id'] for record in overlapping_records],
                }
                for key, overlapping_records in groups.items()
            ]
            for name, groups in residual_checks.items()
        },
        'candidate_overlap_summary': candidate_overlap_summary,
    }

    OUTPUT_JSON_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')

    lines = [
        '# Candidate discovery overlap resolution',
        '',
        'Machine-readable source: `data/insurance_discovery/candidate_discovery_overlap_resolution.discovery.json`',
        '',
        'Discovery-only companion artifact for AC 70304 sub-AC 4. This verifies that partially overlapping evidence rows were either merged into one canonical row or kept separate only when they represent distinct source items.',
        '',
        '## Summary',
        '',
        f"- final_record_count: {output['summary']['final_record_count']}",
        f"- unique_candidate_ids: {output['summary']['unique_candidate_ids']}",
        f"- canonical_rows_with_merged_overlap: {output['summary']['canonical_rows_with_merged_overlap']}",
        f"- resolved_repeated_source_url_groups: {output['summary']['resolved_repeated_source_url_groups']}",
        f"- resolved_repeated_source_title_groups: {output['summary']['resolved_repeated_source_title_groups']}",
        f"- residual_candidate_plus_source_url_and_source_kind_groups: {output['summary']['residual_candidate_plus_source_url_and_source_kind_groups']}",
        f"- residual_candidate_plus_source_url_groups: {output['summary']['residual_candidate_plus_source_url_groups']}",
        f"- residual_candidate_plus_source_title_and_source_kind_groups: {output['summary']['residual_candidate_plus_source_title_and_source_kind_groups']}",
        f"- residual_candidate_plus_source_title_groups: {output['summary']['residual_candidate_plus_source_title_groups']}",
        f"- final_ledger_is_non_overlapping: {str(output['summary']['final_ledger_is_non_overlapping']).lower()}",
        '',
        '## Resolution policy',
        '',
        f"- policy_version: {output['resolution_policy']['policy_version']}",
        f"- canonical_selection_rule: {output['resolution_policy']['canonical_selection_rule']}",
        f"- merge_behavior: {output['resolution_policy']['merge_behavior']}",
        '',
        '## Resolved overlap groups',
        '',
        '| Candidate ID | Group type | Canonical evidence ID | Dropped duplicate evidence IDs | Source URL / title |',
        '|---|---|---|---|---|',
    ]

    for group in repeated_source_url_groups:
        lines.append(
            f"| {group['candidate_id']} | source_url | {group['canonical_evidence_id']} | {', '.join(group['dropped_duplicate_evidence_ids'])} | {group['source_url']} |"
        )
    for group in repeated_source_title_groups:
        lines.append(
            f"| {group['candidate_id']} | source_title | {group['canonical_evidence_id']} | {', '.join(group['dropped_duplicate_evidence_ids'])} | {group['source_title']} |"
        )

    lines.extend([
        '',
        '## Candidate rows carrying merged overlap provenance',
        '',
        '| Candidate ID | Canonical evidence rows with merged overlap |',
        '|---|---:|',
    ])

    for candidate in candidate_overlap_summary:
        if candidate['canonical_rows_with_merged_overlap']:
            lines.append(f"| {candidate['candidate_id']} | {candidate['canonical_rows_with_merged_overlap']} |")

    OUTPUT_MD_PATH.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
