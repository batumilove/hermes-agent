import json
from collections import Counter
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'

PRICING_LEDGER_PATH = DATA_DIR / 'pricing_evidence_ledger.discovery.json'
SHARED_PATH = DATA_DIR / 'shared_candidate_staging.discovery.json'
DOC_PATH = DOCS_DIR / 'insurance_discovery_pricing_availability.md'

STATUS_LEGEND = {
    'public_pricing_available': 'A reviewed public source displayed a numeric premium, from-price, or calculator output during discovery without requiring a completed quote, broker contact, or login.',
    'quote_flow_gated': 'Public evidence indicates pricing is available through a quote or application flow, but no numeric premium was visible in the reviewed discovery extract.',
    'partially_visible': 'Public evidence exposed some pricing signal such as discounts, premium mechanics, rate-related UI, or price-result placeholders, but not a usable numeric premium for discovery.',
    'unavailable_during_discovery': 'No usable pricing was visible in reviewed public evidence during discovery because pricing was absent, blocked, challenge-gated, brochure-only, or otherwise not observable.'
}

PRICING_TRANSPARENCY_FRICTION_LEGEND = {
    'public_numeric_pricing_visible': 'A usable numeric premium or from-price was visible in reviewed public evidence during discovery.',
    'blocked_public_pricing': 'A public pricing or quote path existed but was blocked by a challenge wall, login wall, reCAPTCHA, or similar access barrier before usable pricing was visible.',
    'incomplete_quote_output': 'The reviewed public source showed pricing-related UI, placeholders, price-result fields, or quote CTAs, but the run did not obtain a usable numeric premium from that path.',
    'eligibility_gated_results': 'Pricing appears to depend on passing upfront eligibility or household-profile steps such as residency, duration, nationality, age, or dependent composition before any result is visible.',
    'failed_or_abandoned_quote_retrieval': 'A quote path appeared to exist, but retrieval did not reach usable pricing because the observed path failed technically, redirected away, depended on an unverified successor flow, or was otherwise abandoned during discovery.',
    'contact_or_account_gated_pricing': 'Pricing appears to require account creation, login, or contact submission before any usable quote result is shown.',
    'broker_or_intermediary_only_public_path': 'The reviewed public path was broker-led or intermediary-led rather than a verified insurer-controlled pricing path, so direct public pricing transparency remained unresolved.',
    'brochure_or_marketing_only_no_price': 'Reviewed public materials were brochure-led or marketing-led and did not expose a public pricing path with usable numeric premiums.'
}

PRICING_TRANSPARENCY_FRICTION = {
    'cigna_global': {
        'primary_friction': 'incomplete_quote_output',
        'secondary_frictions': ['eligibility_gated_results'],
        'friction_summary': 'Pricing is pushed into a quote flow and no usable numeric premium was visible from the reviewed public entrypoint.',
    },
    'allianz_care': {
        'primary_friction': 'blocked_public_pricing',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'A public promotion is visible, but the quote path itself was challenge-gated before any usable premium appeared.',
    },
    'bupa_global': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'The public flow starts with residence-country selection and did not expose a usable premium in the reviewed extract.',
    },
    'axa_global_healthcare': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'Pricing depends on passing cover-length and product-type screening, while the reviewed public page only exposed premium mechanics and discounts.',
    },
    'april_international': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'APRIL exposes a plan-finder path with country and cover-type selection, but no usable premium was visible during discovery.',
    },
    'william_russell': {
        'primary_friction': 'incomplete_quote_output',
        'secondary_frictions': [],
        'friction_summary': 'The site says prices are shown in the online quote, but the reviewed public evidence did not include an actual numeric result.',
    },
    'msh_international': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'The public quote path starts with profile and region selection, but no usable premium was captured in the reviewed extract.',
    },
    'now_health_international': {
        'primary_friction': 'incomplete_quote_output',
        'secondary_frictions': [],
        'friction_summary': 'Public plan-comparison materials show premium mechanics and benefit structure, but not a usable numeric premium.',
    },
    'img_global': {
        'primary_friction': 'incomplete_quote_output',
        'secondary_frictions': [],
        'friction_summary': 'Per-plan price entrypoints were visible, but the reviewed public extract did not expose the actual numeric premiums.',
    },
    'aetna_international': {
        'primary_friction': 'brochure_or_marketing_only_no_price',
        'secondary_frictions': ['failed_or_abandoned_quote_retrieval'],
        'friction_summary': 'The reviewed Aetna path was audience-routing marketing without a verified individual-family pricing path or usable public premium.',
    },
    'foyer_global_health': {
        'primary_friction': 'blocked_public_pricing',
        'secondary_frictions': ['failed_or_abandoned_quote_retrieval'],
        'friction_summary': 'The public quick-quote/application path was blocked by reCAPTCHA before pricing could be observed.',
    },
    'henner': {
        'primary_friction': 'brochure_or_marketing_only_no_price',
        'secondary_frictions': [],
        'friction_summary': 'The reviewed public materials were informational only and did not expose usable public pricing.',
    },
    'globality_health': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'Quote results appear to depend on nationality, future residency, location, and household steps before any usable premium is visible.',
    },
    'morgan_price': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['contact_or_account_gated_pricing', 'failed_or_abandoned_quote_retrieval'],
        'friction_summary': 'The quick-quote path asks for household and eligibility data and can fall back to manual contact capture instead of immediate public pricing.',
    },
    'passportcard_global': {
        'primary_friction': 'public_numeric_pricing_visible',
        'secondary_frictions': [],
        'friction_summary': 'Illustrative monthly example pricing was visible on the public insurer page.',
    },
    'vumi': {
        'primary_friction': 'incomplete_quote_output',
        'secondary_frictions': [],
        'friction_summary': 'The public site exposed rate-comparison navigation and plan maxima, but no usable premium was captured.',
    },
    'integra_global': {
        'primary_friction': 'broker_or_intermediary_only_public_path',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'Discovery only verified a broker-led path with quote CTAs rather than a direct insurer pricing path or usable public premium.',
    },
    'medihelp_international': {
        'primary_friction': 'blocked_public_pricing',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'Public quote and buy-online entrypoints were visible, but downstream pricing remained inaccessible in the reviewed run because the deeper path was not captured and access friction was documented separately.',
    },
    'alc_health': {
        'primary_friction': 'failed_or_abandoned_quote_retrieval',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'Legacy ALC quote messaging redirects new business to IMG, leaving standalone current pricing retrieval unresolved in this run.',
    },
    'geoblue_xplorer_bcbs_global_solutions': {
        'primary_friction': 'broker_or_intermediary_only_public_path',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'The reviewed evidence was broker-led and only exposed relative premium positioning, not a verified insurer-controlled public price.',
    },
    'safetywing_nomad_complete': {
        'primary_friction': 'public_numeric_pricing_visible',
        'secondary_frictions': [],
        'friction_summary': 'A public calculator displayed a numeric monthly premium without requiring a completed personalized quote.',
    },
    'genki_native': {
        'primary_friction': 'eligibility_gated_results',
        'secondary_frictions': ['incomplete_quote_output'],
        'friction_summary': 'Instant pricing is advertised, but the recommendation flow first screens for trip duration, destination, and home-country healthcare access.',
    },
    'acs_expat': {
        'primary_friction': 'contact_or_account_gated_pricing',
        'secondary_frictions': ['eligibility_gated_results'],
        'friction_summary': 'Pricing is tailored to country of residence and the reviewed quote path collects family composition and contact details before advisor follow-up.',
    },
    'hci_group_health_protect': {
        'primary_friction': 'failed_or_abandoned_quote_retrieval',
        'secondary_frictions': [],
        'friction_summary': 'The external quote tool showed a technical barrier instead of usable quote questions or pricing.',
    },
    'expatriate_group_global_health': {
        'primary_friction': 'contact_or_account_gated_pricing',
        'secondary_frictions': ['eligibility_gated_results', 'incomplete_quote_output'],
        'friction_summary': 'The quote flow includes a premium placeholder but requires household data and email before a usable result was observed.',
    },
    'wellaway_expat': {
        'primary_friction': 'contact_or_account_gated_pricing',
        'secondary_frictions': ['eligibility_gated_results'],
        'friction_summary': 'The public quote path collects identity, family, and contact fields at the basics step before any plan pricing is shown.',
    },
    'securus_global_health_cover': {
        'primary_friction': 'brochure_or_marketing_only_no_price',
        'secondary_frictions': [],
        'friction_summary': 'Reviewed public evidence exposed brochures and marketing materials but no usable numeric premium or quote result.',
    },
}

PARTIAL_VISIBILITY_MARKERS = {
    'discount shown, base premium not shown',
    'pricing mechanic only',
    'premium mechanics only',
    'price entrypoint visible, number not shown',
    'relative pricing claim only',
    'pricing-related link referenced, no number shown',
    'quote-result field present, no number shown in reviewed extract',
}

UNAVAILABLE_MARKERS = {
    'blocked/challenge',
    'marketing only',
    'no consumer pricing shown',
    'brochure links only',
}

QUOTE_GATED_MARKERS = {
    'quote CTA only',
    'quote-generated pricing referenced, no number shown',
    'quote form only',
    'selector UI only',
    'quote/buy entrypoints only',
    'quote/buy CTA only',
    'product detail page without reviewed numeric premium',
    'marketing + quote CTA only',
}


def classify(record: dict) -> tuple[str, str]:
    old_status = record['public_pricing_status']
    visibilities = [e.get('pricing_visibility', '').strip() for e in record.get('pricing_evidence', [])]
    vis_set = set(v for v in visibilities if v)

    if old_status == 'public_pricing_available':
        return 'public_pricing_available', 'Reviewed public evidence included a numeric premium or from-price.'

    if vis_set & PARTIAL_VISIBILITY_MARKERS:
        return 'partially_visible', 'Reviewed public evidence exposed pricing-related signals, but not a usable numeric premium.'

    if old_status == 'quote_flow_pricing_only' or vis_set & QUOTE_GATED_MARKERS:
        return 'quote_flow_gated', 'Reviewed public evidence pointed to quote-generated pricing, but the numeric premium was not visible during discovery.'

    if vis_set & UNAVAILABLE_MARKERS or old_status == 'no_public_pricing_found':
        return 'unavailable_during_discovery', 'Reviewed public evidence did not expose usable pricing during discovery.'

    raise ValueError(f"Unclassified pricing record: {record['candidate_id']} with visibilities={sorted(vis_set)}")


pricing = json.loads(PRICING_LEDGER_PATH.read_text())
shared = json.loads(SHARED_PATH.read_text())

pricing_by_id = {record['candidate_id']: record for record in pricing['records']}

for record in pricing['records']:
    status, reason = classify(record)
    record['pricing_availability_status'] = status
    record['pricing_availability_reason'] = reason

    friction = PRICING_TRANSPARENCY_FRICTION[record['candidate_id']]
    record['pricing_transparency_friction'] = {
        'primary_friction': friction['primary_friction'],
        'secondary_frictions': friction['secondary_frictions'],
        'friction_summary': friction['friction_summary'],
        'has_directly_usable_public_price': record['pricing_availability_status'] == 'public_pricing_available',
    }

pricing['pricing_availability_status_legend'] = STATUS_LEGEND
pricing['pricing_transparency_friction_legend'] = PRICING_TRANSPARENCY_FRICTION_LEGEND

availability_counts = Counter(record['pricing_availability_status'] for record in pricing['records'])
friction_counts = Counter(
    record['pricing_transparency_friction']['primary_friction']
    for record in pricing['records']
)
pricing['summary']['pricing_availability_status_counts'] = dict(sorted(availability_counts.items()))
pricing['summary']['pricing_transparency_primary_friction_counts'] = dict(sorted(friction_counts.items()))
pricing['summary']['records_without_directly_usable_public_price_count'] = sum(
    1 for record in pricing['records']
    if not record['pricing_transparency_friction']['has_directly_usable_public_price']
)
pricing['summary']['pricing_transparency_method_notes'] = [
    'pricing_transparency_friction records the specific obstacle observed for each candidate during public pricing discovery, rather than pretending every no-price case failed in the same way.',
    'Candidates with no directly usable public price are explicitly separated into blocked public pricing, incomplete quote output, eligibility-gated results, failed or abandoned quote retrieval, contact/account-gated pricing, broker-led public paths, and brochure-or-marketing-only public evidence.',
    'A candidate can carry secondary frictions when multiple barriers were visible in reviewed evidence, but primary_friction captures the best single explanation for why usable public pricing was not obtained in this run.',
]
pricing['summary']['pricing_availability_method_notes'] = [
    'The pricing_availability_status field normalizes discovery-time visibility into four buckets: public_pricing_available, quote_flow_gated, partially_visible, and unavailable_during_discovery.',
    'partially_visible means the source exposed pricing-adjacent evidence such as discounts, premium mechanics, or price-result placeholders without a usable numeric premium.',
    'quote_flow_gated means the reviewed source suggested pricing should appear after interactive quote inputs, but this run did not capture the numeric result.',
]

for candidate in shared['staging_candidates']:
    pricing_record = pricing_by_id[candidate['candidate_id']]
    candidate['pricing_availability_status'] = pricing_record['pricing_availability_status']
    candidate['pricing_availability_status_reason'] = pricing_record['pricing_availability_reason']
    candidate['pricing_evidence_ref'] = {
        'ledger_dataset_path': 'data/insurance_discovery/pricing_evidence_ledger.discovery.json',
        'pricing_evidence_candidate_id': candidate['candidate_id'],
        'public_pricing_status_legacy': pricing_record['public_pricing_status'],
    }
    candidate['pricing_transparency_friction_primary'] = pricing_record['pricing_transparency_friction']['primary_friction']
    candidate['pricing_transparency_friction_summary'] = pricing_record['pricing_transparency_friction']['friction_summary']

shared['pricing_availability_status_legend'] = STATUS_LEGEND
shared['pricing_availability_summary'] = {
    'verified_total_unique_candidates': len(shared['staging_candidates']),
    'pricing_availability_status_counts': dict(sorted(availability_counts.items())),
    'pricing_transparency_primary_friction_counts': dict(sorted(friction_counts.items())),
    'records_without_directly_usable_public_price_count': sum(
        1 for record in pricing['records']
        if not record['pricing_transparency_friction']['has_directly_usable_public_price']
    ),
    'notes': [
        'This shared staging dataset now carries a normalized pricing_availability_status field for every candidate.',
        'pricing_transparency_friction_primary and pricing_transparency_friction_summary capture why usable public pricing was or was not obtainable during discovery.',
        'The field is discovery-only and records visibility observed in public evidence reviewed during this run; it is not a quote outcome or recommendation score.',
    ],
}

PRICING_LEDGER_PATH.write_text(json.dumps(pricing, indent=2, ensure_ascii=False) + '\n')
SHARED_PATH.write_text(json.dumps(shared, indent=2, ensure_ascii=False) + '\n')

lines = [
    '# Insurance discovery pricing availability',
    '',
    'Discovery-only normalization of how pricing was visible for each candidate insurer/plan during this run.',
    '',
    'Summary counts:',
]
for status, count in sorted(availability_counts.items()):
    lines.append(f'- {status}: {count}')
lines.append(f"- records_without_directly_usable_public_price: {pricing['summary']['records_without_directly_usable_public_price_count']}")
lines.extend([
    '',
    'Primary pricing-transparency friction counts:',
])
for friction, count in sorted(friction_counts.items()):
    lines.append(f'- {friction}: {count}')
lines.extend([
    '',
    'Legend:',
])
for status, description in STATUS_LEGEND.items():
    lines.append(f'- {status}: {description}')
lines.extend([
    '',
    'Pricing transparency friction legend:',
])
for friction, description in PRICING_TRANSPARENCY_FRICTION_LEGEND.items():
    lines.append(f'- {friction}: {description}')
lines.extend([
    '',
    '| candidate_id | insurer | plan | pricing_availability_status | primary_pricing_transparency_friction | friction_summary | legacy_public_pricing_status |',
    '| --- | --- | --- | --- | --- | --- | --- |',
])
for record in pricing['records']:
    friction = record['pricing_transparency_friction']
    lines.append(
        f"| {record['candidate_id']} | {record['insurer_name']} | {record['plan_name']} | {record['pricing_availability_status']} | {friction['primary_friction']} | {friction['friction_summary']} | {record['public_pricing_status']} |"
    )
DOC_PATH.write_text('\n'.join(lines) + '\n')

print(f'Updated {PRICING_LEDGER_PATH}')
print(f'Updated {SHARED_PATH}')
print(f'Wrote {DOC_PATH}')
print(dict(sorted(availability_counts.items())))
