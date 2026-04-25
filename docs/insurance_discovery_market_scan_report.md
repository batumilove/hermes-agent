# Insurance discovery market scan report

Discovery-only summary for a German citizen family of four living in Georgia (adults 42 and 37, two children), focused on international/expat health insurance plans that could plausibly cover Georgia and Germany and ideally support worldwide excluding the U.S.

## Scope and artifacts used

This report summarizes the staged discovery artifacts rather than producing a recommendation or dashboard.

Primary machine-readable sources:
- `data/insurance_discovery/shared_candidate_staging.discovery.json`
- `data/insurance_discovery/candidate_source_ledger.discovery.json`
- `data/insurance_discovery/source_completeness_criteria.discovery.json`
- `data/insurance_discovery/pricing_evidence_ledger.discovery.json`
- `data/insurance_discovery/plan_document_access_evidence_ledger.discovery.json`
- `data/insurance_discovery/access_path_contact_models.discovery.json`
- `data/insurance_discovery/account_access_friction.discovery.json`
- `data/insurance_discovery/quote_intake_gating_requirements.discovery.json`

Companion markdown ledgers:
- `docs/insurance_discovery_candidate_source_ledger.md`
- `docs/insurance_discovery_pricing_availability.md`
- `docs/insurance_discovery_plan_document_access_evidence_ledger.md`
- `docs/insurance_discovery_quote_intake_gating.md`
- `docs/insurance_discovery_account_access_friction.md`
- `docs/insurance_discovery_access_paths.md`
- `docs/insurance_discovery_source_completeness_criteria.md`

## Market scan summary

- 27 unique candidate insurer/plan families were staged.
- Relevance split:
  - 13 `likely_relevant`
  - 11 `possibly_relevant`
  - 3 `excluded`
- Discovery completeness remains intentionally partial:
  - 27/27 candidates meet the minimum discovery schema.
  - 0/27 are fully sourced across all required coverage dimensions.
  - 27/27 are classified as `source_backed_partial_but_usable`.

Interpretation: the scan is broad enough to support a serious shortlist-building phase later, but it is not yet deep enough to support a defensible final recommendation ranking.

## Candidate coverage picture

Required discovery coverage dimensions were Georgia applicability, Germany applicability, and worldwide-excluding-U.S. relevance.

Coverage-state counts from the staged dataset:

- Georgia:
  - 6 confirmed
  - 12 inferred
  - 9 unknown
- Germany:
  - 8 confirmed
  - 12 inferred
  - 7 unknown
- Worldwide excluding U.S.:
  - 3 explicitly indicated
  - 5 indirectly indicated
  - 15 worldwide-positioned but ex-U.S. not verified
  - 4 not verified from reviewed sources

Strongest country-coverage signals found in reviewed public evidence:
- Georgia confirmed: Cigna Global, Bupa Global, APRIL International, IMG Global Medical, Genki, WellAway
- Germany confirmed: Cigna Global, Bupa Global, APRIL International, IMG Global Medical, PassportCard, ALC Health, Genki, WellAway
- Explicit worldwide-excluding-U.S. evidence: Integra Global, Blue Cross Blue Shield Global Solutions / former GeoBlue Xplorer, Expatriate Group

Important caution: even where Georgia or Germany was confirmed, that usually means public selector, country-list, or geography wording evidence was found. It does not yet prove final underwriting acceptance, premium viability, or family-specific eligibility for German citizens resident in Georgia.

## Strongest source types found

The best evidence in this run came from insurer-controlled quote/application paths and directly downloadable plan PDFs, with official landing pages used as supporting context.

Source-directness summary across mapped sources:
- 27 high-directness sources
- 32 medium-directness sources
- 3 low-directness sources

What counted as strongest evidence here:
- Official quote/application paths: 20 candidates have a captured quote or application URL.
- Direct public plan documents: 12 candidates have a directly downloadable brochure, policy wording, benefit table, or comparable plan PDF.
- Official insurer public landing pages: 27/27 candidates have an official insurer URL in the source map.

Why this matters:
- Quote/application paths were the strongest public evidence for country selectors, intake requirements, and visible eligibility screens.
- Public PDFs were the strongest evidence for benefit structure, geography wording, and terms that are more reliable than marketing copy.
- Broker pages helped discovery in a few cases, but they were the weakest evidence class and should not be treated as final truth when insurer-controlled material is missing.

## Pricing and access realities

Public pricing transparency is weak across this market.

Pricing visibility summary:
- 2 public-pricing-available
- 7 partially-visible
- 13 quote-flow-gated
- 5 unavailable during discovery
- 25/27 had no directly usable public price in reviewed sources

Public numeric pricing was only captured for:
- PassportCard
- SafetyWing Nomad Insurance Complete

Main pricing friction patterns:
- 7 eligibility-gated-results cases
- 5 incomplete-quote-output cases
- 3 blocked-public-pricing cases
- 3 contact-or-account-gated-pricing cases
- 2 broker-or-intermediary-only pricing paths

Access friction summary:
- 9 high-friction candidates
- 7 moderate-friction candidates
- 11 low-friction candidates
- 1 forced signup / mandatory account-creation case before quote visibility
- 5 cases with a login wall before quote output or plan details
- 5 cases where email was observed before quote output

Quote-intake gating is common rather than exceptional:
- 18/27 candidates showed an eligibility precheck before deeper quote output.
- Household data before pricing/details was clearly observed in 5 cases.
- Contact submission before pricing/details was clearly required in 4 cases, conditional in 1 more case.

Interpretation: discovery can identify plausible candidates, but a later completion pass will need controlled quote-flow execution or broker/insurer follow-up to get comparable premiums and family-specific acceptance details.

## Most meaningful evidence gaps

1. Worldwide-excluding-U.S. is still the weakest required dimension.
   - Only 3 candidates had explicit ex-U.S. evidence.
   - 24/27 candidates remain partial or unverified on that specific dimension.

2. Georgia-resident fit is still under-proven.
   - Only 6 candidates had confirmed Georgia evidence.
   - 21/27 remain inferred or unknown for the residence country that matters most to this family.

3. Public pricing is mostly absent.
   - Only 2 candidates exposed usable public numeric premiums.
   - Most insurers route pricing behind quote flows, account gates, eligibility screens, or contact capture.

4. Plan-document depth is uneven.
   - 12 candidates had direct public plan documents.
   - 15 candidates had no downloadable plan document found in reviewed public sources.

5. Two candidates remain materially broker-dependent in the staged evidence.
   - Integra Global
   - BCBS Global Solutions / former GeoBlue Xplorer

6. Every candidate is still only partially sourced for discovery.
   - This is acceptable for discovery.
   - It is not acceptable yet for a final recommendation table or ranking.

## Discovery takeaways

- The market is large enough and sufficiently source-backed to move into a focused completion phase later.
- Strongest next-shortlist candidates should probably come from insurers that already show some combination of: official quote path, public plan PDF, confirmed Georgia or Germany evidence, and clear family-oriented positioning.
- Plans that only surfaced through broker-led evidence or generic marketing copy should stay in the candidate pool for now, but deserve lower operational priority for the next pass unless they have unusually strong geography wording.

## Recommended next data-completion steps

1. Deepen Georgia-residency validation.
   - Re-run insurer quote selectors and geography screens specifically for Georgia residence.
   - Prioritize candidates currently marked `inferred` or `unknown` for Georgia.

2. Close the worldwide-excluding-U.S. gap.
   - Review policy wording PDFs, tables of benefits, and quote area-of-cover selectors for explicit ex-U.S. options.
   - Prioritize candidates already showing indirect ex-U.S. signals, such as U.S. toggle/add-on wording.

3. Collect family-specific quote evidence for a consistent profile.
   - One profile: German citizens, living in Georgia, adults 42 and 37, two children, preference for worldwide excluding U.S.
   - Capture whether pricing is shown, what extra data is required, and whether the flow rejects or redirects the family.

4. Expand plan-document capture for the 15 candidates without downloadable public documents.
   - Search alternate locale pages, benefits libraries, brochures, and application attachments.
   - Record when documentation is absent versus merely gated.

5. Reduce broker dependence where possible.
   - For Integra Global and BCBS Global Solutions / former GeoBlue Xplorer, look for insurer-controlled plan pages, PDFs, or direct application paths before using them in any recommendation stage.

6. Normalize underwriting and exclusions evidence.
   - Continue collecting public wording on pre-existing conditions, waiting periods, maternity, evacuation, Germany/home-country treatment, and area-of-cover restrictions.
   - These are likely to be major differentiators once recommendation work begins.

7. Only after the above, build the recommendation layer.
   - Do not rank yet.
   - The current state supports discovery and evidence collection, not final scoring.

## Bottom line

This discovery run successfully built a source-backed candidate universe and evidence base for the target family profile. It found enough credible international/expat insurers to justify a follow-up completion pass, but the market still has major transparency problems: Georgia residence is often only inferred, worldwide-excluding-U.S. is rarely explicit, and public pricing is mostly hidden behind quote or signup flows.

That means the next useful move is not a dashboard. It is a targeted completion pass focused on geography confirmation, ex-U.S. area-of-cover proof, quote-output capture, and document retrieval for the strongest candidates.
