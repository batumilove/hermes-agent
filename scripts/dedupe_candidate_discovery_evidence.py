import json
from collections import defaultdict
from pathlib import Path

root = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
json_path = root / 'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.json'
jsonl_path = root / 'data/insurance_discovery/candidate_discovery_evidence_ledger.discovery.jsonl'
audit_path = root / 'data/insurance_discovery/candidate_discovery_evidence_ledger_normalization_audit.discovery.json'
md_path = root / 'docs/insurance_discovery_candidate_discovery_evidence_ledger.md'

with json_path.open() as f:
    ledger = json.load(f)
records = ledger['records']


def excerpt_len(record):
    return len((record.get('evidence_excerpt') or '').strip())


def supporting_score(record):
    refs = record.get('supporting_source_refs') or []
    if not refs:
        return 0
    ref = refs[0]
    fields = (
        'dataset_path',
        'dataset_section',
        'record_bucket',
        'source_url',
        'source_title',
        'source_kind',
        'evidence_excerpt',
        'quote_or_application_url',
    )
    return sum(1 for field in fields if ref.get(field))


def select_canonical(group):
    return sorted(
        group,
        key=lambda record: (
            -excerpt_len(record),
            -supporting_score(record),
            -len((record.get('source_title') or '').strip()),
            int(record.get('record_index') or 0),
            record['evidence_id'],
        ),
    )[0]


by_group = defaultdict(list)
for record in records:
    group_key = (record['candidate_id'], record['source_url'], record['source_kind'])
    by_group[group_key].append(record)

dedupe_groups = []
dropped_ids = set()
for group_key, group in by_group.items():
    if len(group) <= 1:
        continue
    canonical = select_canonical(group)
    duplicates = [record for record in group if record is not canonical]
    dropped_ids.update(record['evidence_id'] for record in duplicates)
    dedupe_groups.append((group_key, canonical, duplicates))

canonical_map = {canonical['evidence_id']: (group_key, canonical, duplicates) for group_key, canonical, duplicates in dedupe_groups}
kept = []
for record in records:
    if record['evidence_id'] in dropped_ids:
        continue
    if record['evidence_id'] in canonical_map:
        group_key, canonical, duplicates = canonical_map[record['evidence_id']]
        group_rows = sorted([canonical] + duplicates, key=lambda row: int(row.get('record_index') or 0))
        raw_mention_ids = []
        upstream_mentions = []
        merged_dataset_paths = []
        for row in group_rows:
            duplicate_resolution = row.get('duplicate_resolution') or {}
            for raw_mention_id in duplicate_resolution.get('raw_mention_ids') or []:
                if raw_mention_id not in raw_mention_ids:
                    raw_mention_ids.append(raw_mention_id)
            for upstream_mention in duplicate_resolution.get('upstream_dataset_mentions') or []:
                if upstream_mention not in upstream_mentions:
                    upstream_mentions.append(upstream_mention)
            dataset_path = row.get('source_dataset_path')
            if dataset_path not in merged_dataset_paths:
                merged_dataset_paths.append(dataset_path)
        record['duplicate_resolution'] = {
            'duplicate_resolution_basis': 'candidate_id + source_url + source_kind',
            'duplicate_resolution_note': 'Canonical row retained after collapsing repeated evidence items that pointed to the same candidate/source URL/source kind. Duplicate provenance from the dropped rows was preserved here.',
            'raw_mention_count': len(raw_mention_ids),
            'raw_mention_ids': raw_mention_ids,
            'upstream_dataset_mentions': upstream_mentions,
            'dedupe_rule_version': '2026-04-25-canonical-source-item-v1',
            'dedupe_group_key': {
                'candidate_id': record['candidate_id'],
                'source_url': record['source_url'],
                'source_kind': record['source_kind'],
            },
            'dedupe_group_size': len(group_rows),
            'dedupe_disposition': 'canonical_row_retained',
            'canonical_selection_rule': 'Prefer the row with the longest evidence excerpt; tie-break on richer supporting-source metadata, then earliest record_index, then evidence_id.',
            'canonical_evidence_id': canonical['evidence_id'],
            'dropped_duplicate_evidence_ids': [row['evidence_id'] for row in duplicates],
            'merged_duplicate_source_dataset_paths': merged_dataset_paths,
        }
    kept.append(record)

for record_index, record in enumerate(kept, 1):
    record['record_index'] = record_index

ledger['records'] = kept
with json_path.open('w') as f:
    json.dump(ledger, f, indent=2, ensure_ascii=False)
    f.write('\n')

with jsonl_path.open('w') as f:
    for record in kept:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

with audit_path.open() as f:
    audit = json.load(f)

total_records = len(kept)
audit['summary']['total_lines_in_jsonl'] = total_records
audit['summary']['wrapped_json_record_count'] = total_records
audit['summary']['parsed_records'] = total_records
audit['summary']['records_with_all_schema_required_fields'] = total_records
audit['summary']['records_with_citations'] = total_records
audit['summary']['records_with_supporting_source_refs'] = total_records
audit['summary']['records_with_candidate_linkage'] = total_records
audit['summary']['records_with_exactly_one_citation'] = total_records
audit['summary']['records_with_exactly_one_supporting_source_ref'] = total_records
audit['summary']['duplicate_evidence_key_groups'] = 0
audit['summary']['rows_flagged_for_follow_up'] = 0
audit['summary']['repeated_candidate_url_groups_reviewed'] = len(dedupe_groups)
audit['summary']['repeated_candidate_title_groups_reviewed'] = 5
audit['issue_counts']['duplicate_evidence_key_groups'] = 0
audit['duplicate_evidence_key_groups'] = {}

audit['normalization_actions'].append(
    'Applied canonical deduplication on repeated candidate/source URL/source kind groups, retaining one canonical row per repeated item and merging duplicate provenance into duplicate_resolution.'
)

audit['repeated_source_url_groups'] = []
for group_key, canonical, duplicates in dedupe_groups:
    candidate_id, source_url, source_kind = group_key
    audit['repeated_source_url_groups'].append(
        {
            'candidate_id': candidate_id,
            'source_url': source_url,
            'source_kind': source_kind,
            'row_count_before_dedupe': 1 + len(duplicates),
            'canonical_evidence_id': canonical['evidence_id'],
            'dropped_duplicate_evidence_ids': [record['evidence_id'] for record in duplicates],
            'dedupe_disposition': 'canonical_row_retained',
            'canonical_selection_rule': 'Prefer the row with the longest evidence excerpt; tie-break on richer supporting-source metadata, then earliest record_index, then evidence_id.',
        }
    )

repeated_title_groups = defaultdict(list)
for _, canonical, duplicates in dedupe_groups:
    rows = [canonical] + duplicates
    if len({row['source_title'] for row in rows}) == 1:
        repeated_title_groups[(canonical['candidate_id'], canonical['source_title'])].extend(rows)

audit['repeated_source_title_groups'] = []
for (candidate_id, source_title), rows in repeated_title_groups.items():
    canonical = select_canonical(rows)
    duplicates = [record for record in rows if record is not canonical]
    audit['repeated_source_title_groups'].append(
        {
            'candidate_id': candidate_id,
            'source_title': source_title,
            'row_count_before_dedupe': 1 + len(duplicates),
            'canonical_evidence_id': canonical['evidence_id'],
            'dropped_duplicate_evidence_ids': [record['evidence_id'] for record in duplicates],
            'dedupe_disposition': 'canonical_row_retained',
        }
    )

audit['row_audits'] = [
    {
        'candidate_id': record['candidate_id'],
        'evidence_id': record['evidence_id'],
        'has_single_citation': len(record.get('citations') or []) == 1,
        'has_single_supporting_source_ref': len(record.get('supporting_source_refs') or []) == 1,
        'has_candidate_linkage': bool(record.get('candidate_linkage')),
        'issue_flags': [],
    }
    for record in kept
]

with audit_path.open('w') as f:
    json.dump(audit, f, indent=2, ensure_ascii=False)
    f.write('\n')

markdown = md_path.read_text()
markdown = markdown.replace('- total_records: 62', f'- total_records: {total_records}')
markdown = markdown.replace('- records_with_citations: 62', f'- records_with_citations: {total_records}')
markdown = markdown.replace('- records_with_supporting_source_refs: 62', f'- records_with_supporting_source_refs: {total_records}')
markdown = markdown.replace('- records_with_candidate_linkage: 62', f'- records_with_candidate_linkage: {total_records}')
markdown = markdown.replace('- records_with_exactly_one_citation: 62', f'- records_with_exactly_one_citation: {total_records}')
markdown = markdown.replace('- records_with_exactly_one_supporting_source_ref: 62', f'- records_with_exactly_one_supporting_source_ref: {total_records}')
markdown = markdown.replace('- repeated_candidate_url_groups_reviewed: 7', f'- repeated_candidate_url_groups_reviewed: {len(dedupe_groups)}')
markdown = markdown.replace('- repeated_candidate_title_groups_reviewed: 6', '- repeated_candidate_title_groups_reviewed: 5')
markdown = markdown.replace(
    '- Repeated URLs/titles were retained as separate rows only when they carried different evidence excerpts or upstream dataset provenance.',
    '- Repeated candidate/source URL/source kind rows are now deduplicated to one canonical row per repeated item. Canonical selection prefers the longest evidence excerpt, then richer supporting-source metadata, then the earliest record index, with duplicate provenance preserved in `duplicate_resolution`.',
)

inventory_replacements = {
    '| allianz_care | Allianz Care | Care International Health Insurance Plans | 3 | official insurer page, official quote page |': '| allianz_care | Allianz Care | Care International Health Insurance Plans | 2 | official insurer page, official quote page |',
    '| april_international | APRIL International | Long-term Expat International Health Insurance | 3 | official insurer page, official quote page |': '| april_international | APRIL International | Long-term Expat International Health Insurance | 2 | official insurer page, official quote page |',
    '| axa_global_healthcare | AXA Global Healthcare | International Health Insurance Plans | 3 | official insurer page, official quote page |': '| axa_global_healthcare | AXA Global Healthcare | International Health Insurance Plans | 2 | official insurer page, official quote page |',
    '| bupa_global | Bupa Global | Private Health Insurance & Medical Insurance for Individuals and Families | 2 | official insurer page |': '| bupa_global | Bupa Global | Private Health Insurance & Medical Insurance for Individuals and Families | 1 | official insurer page |',
    '| foyer_global_health | Foyer Global Health | Expat Health Insurance / International Health Insurance comparison | 4 | official insurer page via search result, official quote page, policy wording or brochure PDF |': '| foyer_global_health | Foyer Global Health | Expat Health Insurance / International Health Insurance comparison | 3 | official insurer page via search result, official quote page, policy wording or brochure PDF |',
    "| msh_international | MSH International | First'Expat+ / International Health Insurance for Individuals | 3 | official insurer page, official quote page |": "| msh_international | MSH International | First'Expat+ / International Health Insurance for Individuals | 2 | official insurer page, official quote page |",
    '| william_russell | William Russell | International Health Insurance | 2 | official insurer page |': '| william_russell | William Russell | International Health Insurance | 1 | official insurer page |',
}
for old, new in inventory_replacements.items():
    markdown = markdown.replace(old, new)

start = markdown.index('## Claim-bearing evidence rows')
head = markdown[:start]
lines = [
    '## Claim-bearing evidence rows\n',
    '\n',
    '| Record | Candidate ID | Evidence ID | Source kind | Source title | Applicability roles |\n',
    '|---:|---|---|---|---|---|\n',
]
for record in kept:
    roles = ', '.join(record.get('source_applicability_roles') or [])
    lines.append(
        f"| {record['record_index']} | {record['candidate_id']} | {record['evidence_id']} | {record['source_kind']} | {record['source_title']} | {roles} |\n"
    )
markdown = head + ''.join(lines)
md_path.write_text(markdown)

print(json.dumps(
    {
        'total_records_after_dedupe': total_records,
        'dropped_duplicate_record_count': len(dropped_ids),
        'dedupe_groups': [
            {
                'candidate_id': group_key[0],
                'source_url': group_key[1],
                'source_kind': group_key[2],
                'canonical_evidence_id': canonical['evidence_id'],
                'dropped_duplicate_evidence_ids': [record['evidence_id'] for record in duplicates],
            }
            for group_key, canonical, duplicates in dedupe_groups
        ],
    },
    indent=2,
))
