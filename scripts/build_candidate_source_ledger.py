import json
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'


def load_json(path: Path):
    return json.loads(path.read_text())


def dump_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def dedupe_preserve(seq):
    seen = set()
    out = []
    for item in seq:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def slugify(value: str) -> str:
    chars = []
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
        else:
            chars.append('_')
    slug = ''.join(chars)
    while '__' in slug:
        slug = slug.replace('__', '_')
    return slug.strip('_') or 'source'


def classify_applicability(source_ref, candidate, source_urls):
    dataset_path = source_ref.get('dataset_path') or ''
    title = (source_ref.get('source_title') or '').lower()
    source_url = source_ref.get('source_url')
    quote_url = source_ref.get('quote_or_application_url')
    excerpt = (source_ref.get('evidence_excerpt') or '').lower()
    roles = ['candidate_discovery_entry']

    if 'global_providers' in dataset_path or 'european_providers' in dataset_path:
        roles.append('candidate_identification')
    if source_ref.get('source_kind') == 'broker page':
        roles.append('broker_substitute_evidence')
    if source_url == candidate.get('official_url'):
        roles.append('official_plan_positioning')
    if source_url == candidate.get('broker_url'):
        roles.append('broker_substitute_evidence')
    if quote_url and quote_url == candidate.get('quote_or_application_url'):
        roles.append('quote_or_application_path_reference')
    if candidate.get('policy_wording_or_pdf_url') and source_url == candidate.get('policy_wording_or_pdf_url'):
        roles.append('plan_document_reference')

    geography_terms = ['georgia', 'germany', 'worldwide', 'outside u.s', 'outside us', 'area of cover', 'zone', 'europe']
    if any(term in excerpt for term in geography_terms) or any(term in title for term in geography_terms):
        roles.append('geography_relevance_evidence')

    pricing_terms = ['price', 'pricing', 'premium', 'quote', 'quick quote', 'sample prices']
    if any(term in excerpt for term in pricing_terms) or source_url == candidate.get('quote_or_application_url'):
        roles.append('pricing_or_quote_path_evidence')

    access_terms = ['login', 'quote', 'application', 'selector', 'signup', 'challenge', 'broker']
    if any(term in title for term in access_terms) or source_url == candidate.get('quote_or_application_url') or source_url == candidate.get('broker_url'):
        roles.append('access_path_evidence')

    if source_url in source_urls.get('plan_document_urls', set()):
        roles.append('plan_document_reference')

    return dedupe_preserve(roles)


def build_synthetic_source(source_id, source_url, source_title, source_kind, evidence_excerpt, applicable_roles, dataset_path='data/insurance_discovery/candidate_source_ledger.discovery.json', dataset_section='candidate_source_ledger', record_bucket='records'):
    source_entry = {
        'source_id': source_id,
        'source_origin': 'derived_candidate_source_ledger_reference',
        'dataset_path': dataset_path,
        'dataset_section': dataset_section,
        'record_bucket': record_bucket,
        'source_url': source_url,
        'source_title': source_title,
        'source_kind': source_kind,
        'quote_or_application_url': source_url if 'quote' in source_kind or 'application' in source_kind else None,
        'evidence_excerpt': evidence_excerpt,
        'applicable_to_candidate_evidence_entry': True,
        'applicability_roles': dedupe_preserve(['candidate_discovery_entry', *applicable_roles]),
        'applicability_note': evidence_excerpt,
    }
    source_entry.update(classify_source_directness(source_entry))
    return source_entry


def infer_source_kind(shared_record, source_ref):
    explicit = source_ref.get('source_kind')
    if explicit:
        return explicit
    source_url = (source_ref.get('source_url') or '').lower()
    title = (source_ref.get('source_title') or '').lower()
    known_kinds = shared_record.get('source_kinds') or []
    if 'broker' in source_url or 'broker' in title or 'internationalinsurance.com' in source_url:
        return 'broker page'
    if known_kinds:
        return known_kinds[0]
    return 'reviewed public source'


def classify_source_directness(source_entry):
    source_kind = (source_entry.get('source_kind') or '').lower()
    source_origin = source_entry.get('source_origin') or ''
    source_url = source_entry.get('source_url') or ''
    dataset_path = source_entry.get('dataset_path') or ''
    applicable_roles = set(source_entry.get('applicability_roles') or [])
    source_url_lower = source_url.lower()

    if source_kind == 'broker page' or 'broker_substitute_evidence' in applicable_roles or 'internationalinsurance.com' in source_url_lower:
        return {
            'source_directness_classification': 'low',
            'source_directness_justification': 'Broker/intermediary-hosted source rather than an insurer-controlled primary source, so it is less direct even when useful for discovery.'
        }

    if source_kind == 'official quote page':
        return {
            'source_directness_classification': 'high',
            'source_directness_justification': 'Official insurer-controlled quote/application path, which is a direct primary source for intake and availability evidence.'
        }

    if source_kind == 'policy wording or brochure pdf':
        return {
            'source_directness_classification': 'high',
            'source_directness_justification': 'Official or directly captured public plan document/PDF, which is a primary insurer-controlled benefits source when publicly available.'
        }

    if source_kind.startswith('official') or 'official_plan_positioning' in applicable_roles:
        justification = 'Official insurer-controlled public page, but more marketing-oriented than a quote screen or policy wording document.'
        if source_origin == 'shared_candidate_staging.source_refs' and ('global_providers' in dataset_path or 'european_providers' in dataset_path):
            justification = 'Official insurer-controlled public page captured from the discovery provider lists; direct, but usually less authoritative than quote or policy documents for exact eligibility details.'
        return {
            'source_directness_classification': 'medium',
            'source_directness_justification': justification
        }

    return {
        'source_directness_classification': 'low',
        'source_directness_justification': 'Reviewed public source could not be tied to a stronger insurer-controlled artifact in this ledger, so it is treated as low directness.'
    }


shared = load_json(DATA_DIR / 'shared_candidate_staging.discovery.json')
plan_document = load_json(DATA_DIR / 'plan_document_access_evidence_ledger.discovery.json')
plan_doc_by_id = {record['candidate_id']: record for record in plan_document['records']}

records = []
for shared_record in shared['staging_candidates']:
    candidate_id = shared_record['candidate_id']
    plan_doc_record = plan_doc_by_id.get(candidate_id, {})

    official_url = None
    quote_url = None
    broker_url = None
    policy_pdf_url = None

    source_set = []
    seen_urls = set()

    for idx, source_ref in enumerate(shared_record.get('source_refs', []), start=1):
        source_url = source_ref.get('source_url')
        quote_or_application_url = source_ref.get('quote_or_application_url')
        source_title = source_ref.get('source_title')
        source_kind = infer_source_kind(shared_record, source_ref)

        if source_kind and source_kind.startswith('official') and not official_url:
            official_url = source_url
        if source_kind == 'broker page' and not broker_url:
            broker_url = source_url
        if quote_or_application_url and not quote_url:
            quote_url = quote_or_application_url

        source_urls = {
            'plan_document_urls': {plan_doc_record.get('document_source_url')} if plan_doc_record.get('document_source_url') else set(),
        }
        applicable_roles = classify_applicability(source_ref, {
            'official_url': official_url or source_url,
            'quote_or_application_url': quote_url or quote_or_application_url,
            'broker_url': broker_url,
            'policy_wording_or_pdf_url': plan_doc_record.get('document_source_url'),
        }, source_urls)

        source_entry = {
            'source_id': f'{candidate_id}:source:{idx}',
            'source_origin': 'shared_candidate_staging.source_refs',
            'dataset_path': source_ref.get('dataset_path'),
            'dataset_section': source_ref.get('dataset_section'),
            'record_bucket': source_ref.get('record_bucket'),
            'source_url': source_url,
            'source_title': source_title,
            'source_kind': source_kind,
            'quote_or_application_url': quote_or_application_url,
            'evidence_excerpt': source_ref.get('evidence_excerpt'),
            'applicable_to_candidate_evidence_entry': True,
            'applicability_roles': applicable_roles,
            'applicability_note': 'Collected source ref retained as part of the discovery evidence set for this candidate entry.',
        }
        source_entry.update(classify_source_directness(source_entry))
        source_set.append(source_entry)
        if source_url:
            seen_urls.add(source_url)

    if plan_doc_record.get('document_source_url'):
        policy_pdf_url = plan_doc_record['document_source_url']
    elif shared_record.get('source_refs'):
        for source_ref in shared_record['source_refs']:
            src = source_ref.get('source_url') or ''
            if src.lower().endswith('.pdf'):
                policy_pdf_url = src
                break

    if quote_url and quote_url not in seen_urls:
        source_set.append(build_synthetic_source(
            f'{candidate_id}:source:quote_path',
            quote_url,
            f"{shared_record['normalized_insurer_name']} quote or application path",
            'official quote page',
            'Verified public quote or application path relevant to the candidate evidence entry.',
            ['quote_or_application_path_reference', 'pricing_or_quote_path_evidence', 'access_path_evidence'],
        ))
        seen_urls.add(quote_url)

    if broker_url and broker_url not in seen_urls:
        source_set.append(build_synthetic_source(
            f'{candidate_id}:source:broker_path',
            broker_url,
            f"{shared_record['normalized_insurer_name']} broker or intermediary evidence page",
            'broker page',
            'Broker/intermediary page retained because it materially supplements the discovery evidence entry.',
            ['broker_substitute_evidence', 'access_path_evidence'],
        ))
        seen_urls.add(broker_url)

    if policy_pdf_url and policy_pdf_url not in seen_urls:
        source_set.append(build_synthetic_source(
            f'{candidate_id}:source:plan_document',
            policy_pdf_url,
            f"{shared_record['normalized_insurer_name']} plan document or brochure",
            'policy wording or brochure PDF',
            'Public plan document captured and applicable to this candidate evidence entry.',
            ['plan_document_reference', 'benefit_detail_evidence', 'geography_relevance_evidence'],
        ))
        seen_urls.add(policy_pdf_url)

    official_url = official_url or plan_doc_record.get('official_url') or next((s.get('source_url') for s in source_set if (s.get('source_kind') or '').startswith('official')), None)
    quote_url = quote_url or plan_doc_record.get('quote_or_application_url')
    broker_url = broker_url or plan_doc_record.get('broker_url')
    primary_evidence_url = broker_url or official_url or quote_url or policy_pdf_url

    evidence_entry_source_map = {
        'candidate_source_entry': [src['source_id'] for src in source_set],
        'official_positioning_source_ids': [src['source_id'] for src in source_set if 'official_plan_positioning' in src['applicability_roles']],
        'quote_or_application_source_ids': [src['source_id'] for src in source_set if 'quote_or_application_path_reference' in src['applicability_roles']],
        'broker_source_ids': [src['source_id'] for src in source_set if 'broker_substitute_evidence' in src['applicability_roles']],
        'plan_document_source_ids': [src['source_id'] for src in source_set if 'plan_document_reference' in src['applicability_roles']],
        'geography_relevance_source_ids': [src['source_id'] for src in source_set if 'geography_relevance_evidence' in src['applicability_roles']],
        'pricing_or_quote_source_ids': [src['source_id'] for src in source_set if 'pricing_or_quote_path_evidence' in src['applicability_roles']],
        'access_path_source_ids': [src['source_id'] for src in source_set if 'access_path_evidence' in src['applicability_roles']],
    }

    url_capture_notes = []
    if broker_url:
        url_capture_notes.append('Broker URL retained because it contains relevant public discovery evidence.')
    if not quote_url:
        url_capture_notes.append('No separate quote/application URL was verified in this run.')
    if policy_pdf_url:
        url_capture_notes.append('Public policy wording, brochure, handbook, or benefit-table PDF was verified and captured.')
    else:
        url_capture_notes.append('No public policy wording or PDF URL was verified in this run.')

    applicable_source_count = sum(1 for src in source_set if src['applicable_to_candidate_evidence_entry'])

    records.append({
        'candidate_id': candidate_id,
        'insurer_name': shared_record['normalized_insurer_name'],
        'plan_name': shared_record['normalized_plan_name'],
        'official_url': official_url,
        'quote_or_application_url': quote_url,
        'broker_url': broker_url,
        'policy_wording_or_pdf_url': policy_pdf_url,
        'primary_evidence_url': primary_evidence_url,
        'source_refs_count': len(shared_record.get('source_refs', [])),
        'applicable_source_count': applicable_source_count,
        'all_collected_sources_apply_to_candidate_evidence_entry': applicable_source_count == len(source_set),
        'source_set': source_set,
        'evidence_entry_source_map': evidence_entry_source_map,
        'applicable_source_ids': [src['source_id'] for src in source_set if src['applicable_to_candidate_evidence_entry']],
        'url_capture_notes': dedupe_preserve(url_capture_notes),
    })

payload = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'candidate_source_ledger',
    'generated_from': 'data/insurance_discovery/shared_candidate_staging.discovery.json',
    'family_context': shared['family_context'],
    'field_definitions': {
        'official_url': 'Official insurer or official plan page URL when verified publicly.',
        'quote_or_application_url': 'Public quote, application, or buy path when verified publicly.',
        'broker_url': 'Relevant broker or intermediary page retained when it materially adds discovery evidence.',
        'policy_wording_or_pdf_url': 'Public policy wording, brochure, handbook, table of benefits, or similar PDF/public document URL when verified in this run.',
        'source_set': 'Collected and derived public sources mapped to the candidate and marked for applicability to the candidate evidence entry.',
        'evidence_entry_source_map': 'Source-id lists showing which collected sources are applicable to specific evidence roles within the candidate entry.',
        'source_directness_classification': 'How directly the source reflects insurer-controlled source-of-truth material for discovery evidence in this run.',
        'source_directness_justification': 'Brief explanation of why the source was classified as high, medium, or low directness.'
    },
    'source_directness_rubric': {
        'high': 'Official insurer-controlled quote/application paths and official policy wording, brochure, benefit-table, or similar plan documents.',
        'medium': 'Official insurer-controlled landing or marketing pages that are direct but less authoritative for exact eligibility or benefit details than quote or policy documents.',
        'low': 'Broker/intermediary pages or other indirect public sources that materially help discovery but are not primary insurer-controlled evidence.'
    },
    'url_verification_notes': [
        'This artifact is discovery-only and does not rank or recommend plans.',
        'Null means not verified publicly in this run, not necessarily unavailable.',
        'For some candidates, quote flows or policy documents are gated and therefore only the landing page URL could be captured.',
        'Broker URLs are included only where they materially supplement or substitute for official public discovery evidence.',
        'Synthetic source_set rows are allowed for verified quote/application paths or plan-document URLs that were captured outside the original shared source_refs array but still apply to the candidate evidence entry.'
    ],
    'records': records,
    'summary': {
        'total_candidates': len(records),
        'with_official_url': sum(1 for r in records if r.get('official_url')),
        'with_quote_or_application_url': sum(1 for r in records if r.get('quote_or_application_url')),
        'with_broker_url': sum(1 for r in records if r.get('broker_url')),
        'with_policy_wording_or_pdf_url': sum(1 for r in records if r.get('policy_wording_or_pdf_url')),
        'total_mapped_sources': sum(len(r['source_set']) for r in records),
        'total_applicable_sources': sum(r['applicable_source_count'] for r in records),
        'candidates_with_plan_document_source': sum(1 for r in records if r['evidence_entry_source_map']['plan_document_source_ids']),
        'candidates_with_broker_source': sum(1 for r in records if r['evidence_entry_source_map']['broker_source_ids']),
        'source_directness_counts': {
            'high': sum(1 for r in records for src in r['source_set'] if src.get('source_directness_classification') == 'high'),
            'medium': sum(1 for r in records for src in r['source_set'] if src.get('source_directness_classification') == 'medium'),
            'low': sum(1 for r in records for src in r['source_set'] if src.get('source_directness_classification') == 'low')
        },
        'all_candidates_have_applicable_source_map': all(bool(r['applicable_source_ids']) for r in records),
        'all_applicable_sources_have_directness_classification': all(
            all(src.get('source_directness_classification') and src.get('source_directness_justification') for src in r['source_set'] if src['applicable_to_candidate_evidence_entry'])
            for r in records
        ),
    }
}

dump_json(DATA_DIR / 'candidate_source_ledger.discovery.json', payload)

lines = [
    '# Insurance discovery candidate source ledger',
    '',
    'Discovery-only companion ledger for AC 40101 sub-AC 1. It maps the collected public source set to each staged insurer/plan, marks which sources are applicable to that candidate\'s evidence entry, and now classifies source directness for each applicable source.',
    '',
    'Machine-readable source: `data/insurance_discovery/candidate_source_ledger.discovery.json`',
    '',
    'Summary counts:',
    f"- total_candidates: {payload['summary']['total_candidates']}",
    f"- with_official_url: {payload['summary']['with_official_url']}",
    f"- with_quote_or_application_url: {payload['summary']['with_quote_or_application_url']}",
    f"- with_broker_url: {payload['summary']['with_broker_url']}",
    f"- with_policy_wording_or_pdf_url: {payload['summary']['with_policy_wording_or_pdf_url']}",
    f"- total_mapped_sources: {payload['summary']['total_mapped_sources']}",
    f"- total_applicable_sources: {payload['summary']['total_applicable_sources']}",
    f"- source_directness_high: {payload['summary']['source_directness_counts']['high']}",
    f"- source_directness_medium: {payload['summary']['source_directness_counts']['medium']}",
    f"- source_directness_low: {payload['summary']['source_directness_counts']['low']}",
    f"- all_candidates_have_applicable_source_map: {'yes' if payload['summary']['all_candidates_have_applicable_source_map'] else 'no'}",
    f"- all_applicable_sources_have_directness_classification: {'yes' if payload['summary']['all_applicable_sources_have_directness_classification'] else 'no'}",
    '',
    'Notes:',
    '- `source_set` contains both original collected source refs and, when needed, derived rows for verified quote/application or PDF document URLs captured elsewhere in the discovery artifacts.',
    '- `applicability_roles` explains why a source belongs in the candidate evidence entry, such as candidate identification, access-path evidence, pricing/quote-path evidence, broker substitution, geography relevance, or plan-document support.',
    '- `evidence_entry_source_map` gives per-candidate source-id lists for the main evidence roles without implying any final recommendation or ranking.',
    '- `source_directness_classification` uses a simple discovery rubric: high = official quote/application path or official plan document/PDF; medium = official insurer landing/marketing page; low = broker/intermediary or other indirect public source.',
    '',
    '| Candidate | Mapped sources | Applicable sources | Official | Quote/Application | Broker | Plan doc/PDF |',
    '|---|---|---|---|---|---|---|',
]

for record in records:
    lines.append(
        f"| {record['candidate_id']} | {len(record['source_set'])} | {record['applicable_source_count']} | "
        f"{record['official_url'] or '—'} | {record['quote_or_application_url'] or '—'} | "
        f"{record['broker_url'] or '—'} | {record['policy_wording_or_pdf_url'] or '—'} |"
    )

for record in records:
    lines.extend([
        '',
        f"## {record['insurer_name']} — {record['plan_name']}",
        '',
        f"- Candidate ID: `{record['candidate_id']}`",
        f"- Applicable source count: {record['applicable_source_count']}",
        '- Applicable source directness classifications:'
    ])
    for source in record['source_set']:
        if not source['applicable_to_candidate_evidence_entry']:
            continue
        lines.append(
            f"  - `{source['source_id']}` — {source['source_directness_classification']} directness — {source['source_kind']} — {source['source_url']}"
        )
        lines.append(f"    - Justification: {source['source_directness_justification']}")

(DOCS_DIR / 'insurance_discovery_candidate_source_ledger.md').write_text('\n'.join(lines) + '\n')

print(f"Updated {DATA_DIR / 'candidate_source_ledger.discovery.json'}")
print(f"Updated {DOCS_DIR / 'insurance_discovery_candidate_source_ledger.md'}")
