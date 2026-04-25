import json
import pathlib

base = pathlib.Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
shared = json.loads((base / 'data/insurance_discovery/shared_candidate_staging.discovery.json').read_text())
source = json.loads((base / 'data/insurance_discovery/candidate_source_ledger.discovery.json').read_text())
source_map = {r['candidate_id']: r for r in source['records']}

custom = {
    'cigna_global': {
        'status': 'germany_explicitly_listed',
        'summary': 'Cigna’s public quote selector explicitly lists Germany, giving direct Germany-country evidence rather than only broad global marketing.',
        'evidence': [
            'Cigna’s official quote country selector HTML includes Germany as a selectable option.',
            'Cigna also maintains Germany within its publicly surfaced “Where We Cover” destination footprint, reinforcing Germany relevance beyond generic worldwide wording.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly lists Germany in an official insurer-controlled selector.'
    },
    'allianz_care': {
        'status': 'germany_not_named_global_positioning_only',
        'summary': 'Allianz publicly markets long-term international cover for people living abroad, but no captured source in this run named Germany directly.',
        'evidence': [
            'The official public page frames Care plans as long-term international insurance for people living, working, or studying abroad.',
            'The quote path was challenge-gated during this run, so no Germany-specific selector evidence could be captured.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because only generic living-abroad marketing was visible, while the quote path that might confirm Germany did not yield readable country evidence.'
    },
    'bupa_global': {
        'status': 'germany_explicitly_listed',
        'summary': 'Bupa Global’s individuals-and-families selector explicitly lists Germany in the residence-country dropdown.',
        'evidence': [
            'Official Bupa selector evidence already captured Germany in the residence-country dropdown.',
            'This is direct public evidence that Germany can be entered in the official discovery flow.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly lists Germany in an insurer-controlled selector.'
    },
    'axa_global_healthcare': {
        'status': 'germany_not_named_area_of_cover_structure',
        'summary': 'AXA exposes area-of-cover mechanics and USA exclusion options, but Germany was not named directly in the captured public text.',
        'evidence': [
            'AXA states treatment is covered anywhere within the selected region of cover and that customers can choose whether to include the USA.',
            'That geography structure makes Germany plausibly inside selectable cover bands, but Germany itself was not quoted verbatim in reviewed text.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because AXA publicly describes territory-based cover rules broad enough to place Germany within a plausible area-of-cover, even though Germany was not directly named.'
    },
    'april_international': {
        'status': 'germany_explicitly_listed',
        'summary': 'APRIL’s public country-of-coverage examples explicitly include Germany.',
        'evidence': [
            'APRIL’s extracted country selector examples include Germany by name.',
            'APRIL also states worldwide cover depends on the selected geographic area, so Germany is directly relevant but still area-dependent.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly names Germany in public country-of-coverage examples.'
    },
    'william_russell': {
        'status': 'germany_not_named_zone_based_cover',
        'summary': 'William Russell shows region-based and worldwide cover structures, but Germany was not named directly in the captured sources.',
        'evidence': [
            'William Russell says customers can choose anything from worldwide cover to one-region cover.',
            'Public examples rely on country of residence and coverage area logic, which makes Germany plausible without naming it.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because zone-based and worldwide geography wording makes Germany a reasonable fit within the selectable coverage structure.'
    },
    'msh_international': {
        'status': 'germany_not_named_mobility_positioning_only',
        'summary': 'MSH positions the plan for international mobility, but the reviewed public evidence did not place Germany inside a named territory or selector.',
        'evidence': [
            'MSH describes First’Expat+ as flexible international health insurance for mobility needs.',
            'The captured quote path did not expose Germany in visible country text.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because the captured evidence stayed at mobility positioning and did not reveal a Germany-specific geography rule.'
    },
    'now_health_international': {
        'status': 'germany_not_named_country_of_residence_mechanics',
        'summary': 'Now Health exposes country-of-residence mechanics relevant to Germany screening, but Germany was not named directly.',
        'evidence': [
            'The public comparison table repeatedly refers to the country of nationality or residence in benefit wording.',
            'Residence-specific issuance notes show that market eligibility is country-driven even though Germany was not explicitly captured.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because residence-country mechanics were visible, but no reviewed source explicitly named Germany or placed it within a named geography band.'
    },
    'img_global': {
        'status': 'germany_explicitly_listed',
        'summary': 'IMG’s public HTML includes Germany in country-selection lists on its public insurance pages.',
        'evidence': [
            'Captured public HTML contains country options including Germany.',
            'IMG also markets these plans as worldwide medical coverage for expats and global citizens.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly lists Germany in IMG’s public country-selection flow.'
    },
    'aetna_international': {
        'status': 'germany_not_named_global_network_only',
        'summary': 'Aetna International shows worldwide service reach, but no Germany-specific plan or selector evidence was captured.',
        'evidence': [
            'Reviewed public evidence focused on global healthcare positioning and worldwide support.',
            'Germany was not named in the captured landing or quote entry pages.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because network reach alone is weaker than country or territory evidence and does not support a reliable Germany coverage inference.'
    },
    'foyer_global_health': {
        'status': 'germany_destination_reference',
        'summary': 'Foyer’s official public footprint includes Germany destination content, which supports Germany relevance even though a Germany selector was not captured.',
        'evidence': [
            'Official destination or footprint content surfaced Germany among public country references.',
            'That is stronger than generic worldwide marketing, but weaker than a formal eligibility selector.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because Foyer’s official country-focused content indicates Germany sits inside the insurer’s intended public geography footprint, even without selector proof.'
    },
    'henner': {
        'status': 'germany_not_named_global_network_only',
        'summary': 'Henner’s reviewed public evidence supported international-family positioning, but did not directly verify Germany.',
        'evidence': [
            'Henner markets international health solutions for individuals and families.',
            'No captured source in this run named Germany or exposed Germany-specific territory wording.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because the reviewed evidence did not move beyond broad international positioning.'
    },
    'globality_health': {
        'status': 'germany_residence_rules_relevant_not_verified',
        'summary': 'Globality surfaced Germany-relevant residency distinctions in related public evidence, but Germany coverage itself was not verified from the captured plan page.',
        'evidence': [
            'Captured evidence noted a related public signal involving non-German expat distinctions on specific plan pages.',
            'That makes Germany screening relevant, but not directly verified for the reviewed plan entrypoint.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because the captured evidence points to Germany-related eligibility distinctions without directly confirming Germany coverage status for the candidate plan.'
    },
    'morgan_price': {
        'status': 'germany_home_country_cover_relevant',
        'summary': 'Morgan Price exposes Europe/EU residency restrictions and home-country cover wording that are materially relevant to Germany.',
        'evidence': [
            'Flexible Choices applicants must reside outside the EU or EEA, which directly matters for Germany screening.',
            'The public wording also references home-country cover excluding USA, which is materially relevant to a German family abroad even though Germany was not named verbatim.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because EU/EEA residence restrictions and home-country cover mechanics are concrete geography rules that materially implicate Germany, even without Germany named explicitly.'
    },
    'passportcard_global': {
        'status': 'germany_home_country_reference',
        'summary': 'PassportCard is one of the strongest Germany-specific non-selector cases because the public source is Germany-facing and explicitly discusses treatment in Germany during home visits.',
        'evidence': [
            'Reviewed public evidence came from PassportCard’s Germany-facing expat site.',
            'The public wording says some covered persons can receive treatment in Germany during home visits after relocation.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly names Germany as a place where treatment can be received under specified home-visit conditions.'
    },
    'vumi': {
        'status': 'germany_not_named_global_only',
        'summary': 'VUMI shows broad worldwide positioning, but no captured source named Germany directly.',
        'evidence': [
            'The reviewed public evidence describes VUMI’s plans as international or worldwide in scope.',
            'Germany was not explicitly named in the captured public text.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because explicit worldwide positioning is strong enough for discovery-stage geographic plausibility, though still weaker than named-country evidence.'
    },
    'integra_global': {
        'status': 'germany_not_named_area_of_cover_structure',
        'summary': 'Integra’s reviewed broker-backed evidence points to selectable geography structures, but Germany was not directly named.',
        'evidence': [
            'The captured public evidence supports region or area-of-cover logic rather than a single-country-only product.',
            'Germany itself was not named on the reviewed page.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because the captured geography structure implies broad territorial cover in which Germany plausibly falls.'
    },
    'medihelp_international': {
        'status': 'germany_not_named_global_only',
        'summary': 'MediHelp’s reviewed public evidence supports global positioning but did not name Germany directly.',
        'evidence': [
            'The public plan family is positioned as global health cover for internationally mobile customers.',
            'Germany was not explicitly named in the captured public material.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because the plan family is publicly positioned as global cover, though Germany-specific proof remains absent.'
    },
    'alc_health': {
        'status': 'germany_country_pages_elsewhere',
        'summary': 'ALC Health is Germany-relevant because the captured public evidence includes a Germany regional page and Germany in named-country marketing.',
        'evidence': [
            'ALC publicly references a Germany page in its regional marketing footprint.',
            'Captured public copy also lists Germany among named countries in broader global-country marketing.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly references Germany in insurer-controlled country marketing and Germany-specific page structure.'
    },
    'geoblue_xplorer_bcbs_global_solutions': {
        'status': 'germany_not_named_outside_us_plan',
        'summary': 'GeoBlue/BCBS Global Solutions publicly verifies outside-U.S. plan structures, but Germany was not named directly in the captured evidence.',
        'evidence': [
            'The reviewed plan family explicitly includes outside-U.S. products.',
            'That structure makes Germany plausible as a covered non-U.S. destination, but Germany itself was not quoted.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because an explicit outside-U.S. plan structure materially supports Germany plausibility, even though Germany was not named directly.'
    },
    'safetywing_nomad_complete': {
        'status': 'germany_not_named_global_only',
        'summary': 'SafetyWing markets global family health insurance with licensed-provider access worldwide, but Germany was not named directly.',
        'evidence': [
            'The captured page positions Nomad Insurance Complete as global health insurance for families living abroad.',
            'Germany was not explicitly named in the reviewed public text.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because worldwide family-health wording is strong enough for discovery-stage plausibility, though Germany-specific proof is still missing.'
    },
    'genki_native': {
        'status': 'germany_explicitly_listed',
        'summary': 'Genki’s public consultation flow explicitly lists Germany in country options and pairs that with join-from-anywhere messaging.',
        'evidence': [
            'Genki’s public options list includes Germany by name.',
            'The surrounding public copy says users can join from anywhere, reinforcing that Germany is part of an active intake flow rather than generic marketing only.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly lists Germany in Genki’s public consultation flow.'
    },
    'acs_expat': {
        'status': 'germany_home_country_cover_relevant',
        'summary': 'ACS does not name Germany directly, but its public wording about home-country visit cover is materially relevant to Germany for this family.',
        'evidence': [
            'ACS states home-country visit coverage applies if the home country is included in the selected geographical zone.',
            'That creates a concrete Germany-relevant benefit mechanic for a German family abroad, even though Germany is not named explicitly.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because home-country visit wording creates a concrete Germany-relevant coverage mechanic for this family context, though Germany was not named verbatim.'
    },
    'hci_group_health_protect': {
        'status': 'germany_not_named_global_positioning_only',
        'summary': 'HCI Group’s reviewed public evidence supports international coverage positioning, but no Germany-specific evidence was captured.',
        'evidence': [
            'The reviewed public page supports international/private medical positioning.',
            'Germany was not explicitly named or tied to a visible geography rule.'
        ],
        'assessment': 'unknown',
        'rationale': 'Germany remains unknown because the reviewed evidence stayed generic and did not reveal named-country or structured territory support.'
    },
    'expatriate_group_global_health': {
        'status': 'germany_regionally_implied_not_named',
        'summary': 'Expatriate Group’s quote structure includes a Europe-centered area option, which makes Germany a strong regional inference even without direct naming.',
        'evidence': [
            'The captured quote process includes an area option centered on Europe and adjacent regions.',
            'Germany sits naturally inside that public regional geography, although Germany was not quoted by name.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because a Europe-centered public area-of-cover option strongly implies Germany falls inside the intended territory.'
    },
    'wellaway_expat': {
        'status': 'germany_explicitly_listed',
        'summary': 'WellAway’s public quote form explicitly lists Germany in the country options.',
        'evidence': [
            'The public quote Step 1 HTML includes Germany in the country list.',
            'This is direct public country evidence in an insurer-controlled intake artifact.'
        ],
        'assessment': 'confirmed',
        'rationale': 'Germany is confirmed because captured public source language explicitly lists Germany in WellAway’s public quote form.'
    },
    'securus_global_health_cover': {
        'status': 'germany_not_named_global_only',
        'summary': 'Securus uses global coverage wording, but the reviewed public evidence did not name Germany directly.',
        'evidence': [
            'The public product positioning supports globally oriented health cover.',
            'Germany was not explicitly named in the captured page text.'
        ],
        'assessment': 'inferred',
        'rationale': 'Germany is inferred because the reviewed public wording supports global cover broadly, though not with Germany-specific proof.'
    }
}

def normalize_ref(ref):
    return {
        'source_url': ref.get('source_url') or ref.get('quote_or_application_url') or ref.get('broker_url'),
        'source_title': ref.get('source_title'),
        'source_kind': ref.get('source_kind') or ref.get('record_bucket') or 'public source',
        'evidence_excerpt': ref.get('evidence_excerpt') or ''
    }

records = []
for idx, c in enumerate(shared['staging_candidates'], 1):
    cid = c['candidate_id']
    cfg = custom[cid]
    refs = [normalize_ref(r) for r in c.get('source_refs', [])]
    src = source_map[cid]
    existing_urls = {r['source_url'] for r in refs if r.get('source_url')}
    if src.get('quote_or_application_url') and src['quote_or_application_url'] not in existing_urls:
        refs.append({
            'source_url': src['quote_or_application_url'],
            'source_title': f"{c['normalized_insurer_name']} quote or application path",
            'source_kind': 'official quote page',
            'evidence_excerpt': 'Verified public quote or application URL captured in the candidate source ledger; some Germany-relevant selector evidence may live here even when not fully quoted in the main source excerpt.'
        })
        existing_urls.add(src['quote_or_application_url'])
    if src.get('broker_url') and src['broker_url'] not in existing_urls:
        refs.append({
            'source_url': src['broker_url'],
            'source_title': f"{c['normalized_insurer_name']} broker or intermediary page",
            'source_kind': 'broker page',
            'evidence_excerpt': 'Verified broker/intermediary URL captured in the candidate source ledger; retained because it materially supplemented discovery evidence in this run.'
        })
        existing_urls.add(src['broker_url'])
    if src.get('policy_wording_or_pdf_url'):
        refs.append({
            'source_url': src['policy_wording_or_pdf_url'],
            'source_title': f"{c['normalized_insurer_name']} public brochure or policy PDF",
            'source_kind': 'public pdf link',
            'evidence_excerpt': 'Public PDF link captured in this discovery run; not fully parsed into this Germany ledger unless quoted above.'
        })
    records.append({
        'index': idx,
        'candidate_id': cid,
        'insurer_name': c['normalized_insurer_name'],
        'plan_name': c['normalized_plan_name'],
        'candidate_type': c['candidate_type'],
        'germany_coverage_status': cfg['status'],
        'germany_coverage_summary': cfg['summary'],
        'direct_germany_relevance_evidence': cfg['evidence'] or c.get('germany_evidence', []),
        'supporting_source_refs': refs,
        'uncertainty_notes': c.get('uncertainty_notes', []),
        'germany_country_coverage_assessment': cfg['assessment'],
        'germany_country_coverage_rationale': cfg['rationale']
    })

status_counts = {}
assessment_counts = {}
for r in records:
    status_counts[r['germany_coverage_status']] = status_counts.get(r['germany_coverage_status'], 0) + 1
    assessment_counts[r['germany_country_coverage_assessment']] = assessment_counts.get(r['germany_country_coverage_assessment'], 0) + 1

out = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'germany_coverage_evidence_ledger',
    'generated_from': 'data/insurance_discovery/shared_candidate_staging.discovery.json',
    'generated_by': 'gpt-5.4',
    'family_context': shared['family_context'],
    'status_legend': {
        'germany_explicitly_listed': 'Germany was directly observed in an official country selector, quote form, or equivalent public plan artifact.',
        'germany_destination_reference': 'Germany was directly referenced in official country or destination content, but not as a formal selector.',
        'germany_home_country_reference': 'Germany was directly referenced in public wording about home-country treatment, visits, or Germany-specific market positioning.',
        'germany_home_country_cover_relevant': 'Public wording did not name Germany directly but described home-country cover mechanics materially relevant to a German family abroad.',
        'germany_not_named_area_of_cover_structure': 'Public evidence gave area-of-cover or regional structure broad enough to make Germany plausible, but Germany was not named directly.',
        'germany_not_named_zone_based_cover': 'Public evidence exposed zone-based or worldwide-versus-region cover choices, but Germany was not named directly.',
        'germany_not_named_country_of_residence_mechanics': 'Public evidence showed country-of-residence or nationality mechanics relevant to Germany screening, but Germany was not directly named.',
        'germany_not_named_global_positioning_only': 'Public evidence only confirmed broad international or living-abroad positioning; Germany remained unverified.',
        'germany_not_named_mobility_positioning_only': 'Public evidence confirmed mobility positioning, but no Germany-specific geography evidence was visible.',
        'germany_not_named_global_network_only': 'Public evidence showed worldwide network or service access, but no Germany-specific coverage evidence.',
        'germany_residence_rules_relevant_not_verified': 'Public sources showed Germany-relevant residence or market restrictions, but did not verify Germany coverage directly.',
        'germany_not_named_outside_us_plan': 'Public evidence verified an outside-U.S. or non-U.S. plan structure relevant to Germany, but Germany itself was not named.',
        'germany_not_named_global_only': 'Public evidence verified worldwide or global positioning only, without Germany-specific proof.',
        'germany_country_pages_elsewhere': 'Public sources showed Germany-specific pages or explicit Germany country marketing elsewhere in the insurer-controlled footprint.',
        'germany_regionally_implied_not_named': 'Public evidence exposed a Europe-centered region or similar named geography that strongly implies Germany, without quoting Germany verbatim.',
        'germany_country_coverage_assessment_confirmed': 'Germany coverage is directly confirmed by captured public source language, such as a Germany country selector or explicit Germany treatment wording.',
        'germany_country_coverage_assessment_inferred': 'Germany was not directly confirmed, but the captured source language provides geography rules, regional structure, worldwide wording, or Germany-targeted market context that makes Germany coverage a reasonable discovery-stage inference pending plan-specific verification.',
        'germany_country_coverage_assessment_unknown': 'Captured source language did not directly confirm Germany and was too generic, gated, or incomplete to support a reliable Germany coverage inference.'
    },
    'summary': {
        'verified_total_unique_candidates': len(records),
        'germany_coverage_status_counts': status_counts,
        'notes': [
            'Discovery-only ledger: this artifact does not rank, recommend, or determine eligibility.',
            'Confirmed Germany evidence is strongest when Germany appeared in a selector, quote artifact, or explicit Germany treatment wording.',
            'Unknown entries preserve uncertainty where reviewed sources stayed generic or challenge-gated.'
        ],
        'germany_country_coverage_assessment_counts': assessment_counts,
        'germany_country_coverage_logic': [
            'confirmed = captured public evidence explicitly named Germany in an insurer-controlled selector, quote artifact, or direct Germany treatment wording.',
            'inferred = captured public evidence did not name Germany directly, but did expose geography wording, regional structure, home-country mechanics, or Germany-targeted market context strong enough for a cautious discovery-stage inference.',
            'unknown = captured public evidence stayed too generic, too gated, or too incomplete to infer Germany responsibly.'
        ]
    },
    'records': records
}

path = base / 'data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json'
path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
print(path)
