# Eligibility ambiguity evidence ledger

Machine-readable source: `data/insurance_discovery/eligibility_ambiguity_evidence_ledger.discovery.json`

This discovery-only ledger records source-backed ambiguity signals for whether each candidate plan appears compatible with a German citizen family of four residing in Georgia and seeking coverage relevant to Georgia and Germany, ideally worldwide excluding the US.

## Methodology

### Evidence-confidence rubric

This ledger assigns evidence-confidence labels by combining two dimensions:

- Source directness: how close the evidence is to the insurer-controlled source of truth.
- Source completeness: how much of the family-specific question the reviewed public material actually resolves.

High
- Directness: official insurer-controlled source, official quote/application path, or official policy wording/benefit table/PDF.
- Completeness: directly names the relevant country, geography option, residence rule, or underwriting-relevant selector needed for this family context.
- Typical examples in this run: insurer quote selectors that explicitly include Georgia or Germany; official brochures or policy documents that define area-of-cover options.

Medium
- Directness: still official, or a clearly attributable public source derived from the insurer, but not the most authoritative artifact for the exact claim.
- Completeness: partially resolves the question by showing geography structure, worldwide/outside-US mechanics, or family/expat positioning without fully proving the Germany-plus-Georgia fit.
- Typical examples in this run: official marketing pages describing regional coverage structure; official plan pages that imply relevance but stop short of confirming the exact family profile.

Low
- Directness: broker/intermediary pages, incomplete extracts, challenge-blocked flows, or evidence derived from the absence of explicit wording in reviewed public sources.
- Completeness: leaves major parts of the family-specific question unresolved, so it can only support discovery relevance or uncertainty capture, not eligibility confirmation.
- Typical examples in this run: broker summaries without insurer-controlled corroboration; challenge pages; ledger notes recording that nationality or issued-policy fit was not explicitly addressed in reviewed public material.

Rubric application notes
- High confidence in this discovery run still does not mean final eligibility is proven; it only means the observed public evidence is both direct and comparatively complete for the specific sub-claim being cited.
- When directness and completeness disagree, the lower dimension wins. Example: an official page with only generic expat marketing stays medium or low rather than high.
- Uncertainty is recorded explicitly when the best available evidence remains gated, partial, or indirect.

## Summary

- Verified total unique candidates: 27
- explicit_country_selector_but_final_eligibility_unverified: 5
- gated_or_incomplete_public_evidence: 4
- geography_structure_visible_but_country_fit_inferred: 11
- residence_or_nationality_rules_visible_but_family_fit_unresolved: 5
- generic_international_positioning_only: 2

## Notes

- The ledger does not rank plans or determine final eligibility.
- Each record aggregates publicly visible statements touching residence-country screening, nationality variables, and geography applicability.
- Explicit selector presence was treated as stronger than generic worldwide marketing, but it still does not prove final issueability for a German family resident in Georgia.
- Where quote flows or policy materials were blocked, the ledger preserves uncertainty explicitly rather than inferring acceptance.
- Each record now includes eligibility_ambiguity_citations with exact ledger/source locations plus concise confirms-versus-not-explicitly-addressed notes for residence-country, nationality, and geography ambiguity entries.
- Each record now also includes unresolved_questions_note with family-specific country/residency questions plus concrete follow-up verification steps.

## Cigna Global — International Health Insurance / Global Medical Cover

- Candidate ID: `cigna_global`
- Eligibility ambiguity status: `explicit_country_selector_but_final_eligibility_unverified`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is confirmed in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - Even though both Georgia and Germany appear in public discovery evidence, does the issued family policy still permit Georgia residence together with Germany treatment/home-country use under the desired geography option?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Run the official quote far enough to capture the Georgia-resident family result and verify that the available area-of-cover can include Germany under a worldwide-excluding-US or equivalent option.
  - Need quote-flow or policy guide review to verify whether Georgia residence is accepted for this family profile.
  - Need plan documents to confirm worldwide excluding US pricing and underwriting for German citizens resident in Georgia.
  - This run did not progress far enough to determine whether later quote retrieval, save-and-return, or purchase steps require account verification.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Policy residence / living country must be selected before continuing. (https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html)
  - [visible_required_field] Where will you be living for the duration of the policy? (https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html)
  - [germany_applicability] Cigna’s official quote country selector HTML includes Germany as a selectable option. (https://www.cignaglobal.com/)
  - [germany_applicability] Cigna also maintains Germany within its publicly surfaced “Where We Cover” destination footprint, reinforcing Germany relevance beyond generic worldwide wording. (https://www.cignaglobal.com/)
  - [georgia_applicability] Cigna's quote country selector HTML includes Georgia and Germany as adjacent options, giving direct public evidence that Georgia can at least be entered in the residence/selection flow. (https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html)
  - [georgia_applicability] The main site also exposes a Where We Cover destination list with a Germany page and broader global positioning, but Georgia evidence in this run comes from the quote selector rather than a dedicated country page. (https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html)
  - [captured_uncertainty] Need quote-flow or policy guide review to verify whether Georgia residence is accepted for this family profile. (shared-dataset note)
  - [captured_uncertainty] Need plan documents to confirm worldwide excluding US pricing and underwriting for German citizens resident in Georgia. (shared-dataset note)

## Allianz Care — Care International Health Insurance Plans

- Candidate ID: `allianz_care`
- Eligibility ambiguity status: `gated_or_incomplete_public_evidence`
- Assessment: `unclear`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence is incomplete because key eligibility material stayed gated, broker-led, or otherwise unavailable in this run; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Obtain an insurer-controlled quote screen, brochure, benefit table, or policy wording that is not challenge-blocked or broker-only, then re-check Georgia residence and Germany coverage fit from that primary source.
  - Retry the official Allianz quote/application path from a browser session that can clear or satisfy the challenge page, then capture any residence-country selector, brochure PDF, or family eligibility wording exposed after the gate.
  - Need brochure or table of benefits to verify coverage geography options relevant to Georgia and Germany.
  - Need quote-flow output to confirm eligibility for residents of Georgia.
  - Need brochure, table of benefits, or quote output to verify Georgia-resident eligibility.
  - Need direct evidence for a worldwide-excluding-US configuration and family pricing.
  - The public challenge may be anti-bot rather than a customer login requirement; account-creation or email/phone verification requirements beyond the challenge remain unverified.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Challenge validation blocks quote contents before any customer fields became visible. (https://my.allianzcare.com/myquote/5)
  - [captured_public_source_excerpt] Allianz says its Care plans are designed for people who spend long periods overseas and are aimed at individuals, families, students, nomads, professionals, and expats living, working, or studying abroad. (https://www.allianzcare.com/en/personal-international-health-insurance.html)
  - [germany_applicability] The official public page frames Care plans as long-term international insurance for people living, working, or studying abroad. (https://www.allianzcare.com/en/personal-international-health-insurance.html)
  - [germany_applicability] The quote path was challenge-gated during this run, so no Germany-specific selector evidence could be captured. (https://www.allianzcare.com/en/personal-international-health-insurance.html)
  - [georgia_applicability] Allianz says its Care plans are for people working, studying, or living abroad for long periods and provides an official long-term quote path. (https://www.allianzcare.com/en/personal-international-health-insurance.html)
  - [georgia_applicability] The public quote URL returned a challenge-validation page in this run, so Georgia-specific selector evidence could not be captured publicly. (https://www.allianzcare.com/en/personal-international-health-insurance.html)
  - [captured_uncertainty] Need brochure or table of benefits to verify coverage geography options relevant to Georgia and Germany. (shared-dataset note)
  - [captured_uncertainty] Need quote-flow output to confirm eligibility for residents of Georgia. (shared-dataset note)
  - [captured_uncertainty] Need brochure, table of benefits, or quote output to verify Georgia-resident eligibility. (shared-dataset note)

## Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families

- Candidate ID: `bupa_global`
- Eligibility ambiguity status: `explicit_country_selector_but_final_eligibility_unverified`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is confirmed in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - Even though both Georgia and Germany appear in public discovery evidence, does the issued family policy still permit Georgia residence together with Germany treatment/home-country use under the desired geography option?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Run the official quote far enough to capture the Georgia-resident family result and verify that the available area-of-cover can include Germany under a worldwide-excluding-US or equivalent option.
  - Need actual quote or brochure for Georgia-resident family to confirm available plans and area-of-cover options.
  - Need pricing and underwriting evidence; public page is mostly selector UI.
  - Need actual quote output or brochure to verify plan availability for a Georgia-resident German family.
  - Need explicit area-of-cover evidence for worldwide excluding the US.
  - This run did not verify whether later premium output or application steps introduce account or contact-verification requirements.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Residence country selection is required before seeing plan availability in region. (https://www.bupaglobal.com/en/private-health-insurance)
  - [visible_required_field] Tell us where you'll be living when you want your plan to start. (https://www.bupaglobal.com/en/private-health-insurance)
  - [visible_required_field] Select country (https://www.bupaglobal.com/en/private-health-insurance)
  - [captured_public_source_excerpt] Bupa Global's official plan selector for individuals and families includes both Georgia and Germany in the residence-country dropdown, indicating public-facing availability exploration for those markets. (https://www.bupaglobal.com/en/private-health-insurance)
  - [captured_public_source_excerpt] Bupa Global's official individuals-and-families selector asks where the customer will be living and publicly lists both Georgia and Germany in the residence-country dropdown. (https://www.bupaglobal.com/en/private-health-insurance)
  - [germany_applicability] Official Bupa selector evidence already captured Germany in the residence-country dropdown. (https://www.bupaglobal.com/en/private-health-insurance)
  - [germany_applicability] This is direct public evidence that Germany can be entered in the official discovery flow. (https://www.bupaglobal.com/en/private-health-insurance)
  - [georgia_applicability] Bupa Global's official individuals-and-families selector publicly lists Georgia and Germany in the residence-country dropdown. (https://www.bupaglobal.com/en/private-health-insurance)
  - [georgia_applicability] This is one of the clearest direct-public indicators that the family can at least test Georgia residence in an official flow. (https://www.bupaglobal.com/en/private-health-insurance)
  - [captured_uncertainty] Need actual quote or brochure for Georgia-resident family to confirm available plans and area-of-cover options. (shared-dataset note)
  - [captured_uncertainty] Need actual quote output or brochure to verify plan availability for a Georgia-resident German family. (shared-dataset note)

## AXA Global Healthcare — International Health Insurance Plans

- Candidate ID: `axa_global_healthcare`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need quote-flow or brochure to verify exact geography bands including Georgia and Germany.
  - Need family pricing and underwriting specifics.
  - Need quote or policy documents to confirm Georgian residency acceptance.
  - Need exact Germany/Georgia area-of-cover rules and pricing for a family of four.
  - The downstream quote.axaglobalhealthcare.com flow was not fully traversed here, so later verification requirements remain unverified.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] AXA first screens by intended cover length and whether the user wants travel insurance or international healthcare insurance. (https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default)
  - [country_of_residence_or_quote_precheck] Less than 3 months is screened out from this product. (https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default)
  - [captured_public_source_excerpt] AXA describes its product as international private health insurance for people living abroad or travelling overseas frequently, with treatment anywhere within the selected region of cover. (https://www.axaglobalhealthcare.com/en/international-health-insurance/)
  - [captured_public_source_excerpt] AXA describes international health insurance for people living abroad or travelling overseas frequently, with five cover levels and an explicit step allowing customers to choose whether to include the USA. (https://www.axaglobalhealthcare.com/en/international-health-insurance/)
  - [germany_applicability] AXA states treatment is covered anywhere within the selected region of cover and that customers can choose whether to include the USA. (https://www.axaglobalhealthcare.com/en/international-health-insurance/)
  - [germany_applicability] That geography structure makes Germany plausibly inside selectable cover bands, but Germany itself was not quoted verbatim in reviewed text. (https://www.axaglobalhealthcare.com/en/international-health-insurance/)
  - [georgia_applicability] AXA says customers can exclude the USA from their area of cover, which is relevant for worldwide-ex-US screening. (https://www.axaglobalhealthcare.com/en/international-health-insurance/)
  - [georgia_applicability] AXA states treatment outside the selected area of cover is not covered, so Georgia fit depends on selecting an area that includes Georgia; this was not directly resolved from captured text. (https://www.axaglobalhealthcare.com/en/international-health-insurance/)
  - [captured_uncertainty] Need quote-flow or brochure to verify exact geography bands including Georgia and Germany. (shared-dataset note)
  - [captured_uncertainty] Need quote or policy documents to confirm Georgian residency acceptance. (shared-dataset note)
  - [captured_uncertainty] Need exact Germany/Georgia area-of-cover rules and pricing for a family of four. (shared-dataset note)

## APRIL International — Long-term Expat International Health Insurance

- Candidate ID: `april_international`
- Eligibility ambiguity status: `explicit_country_selector_but_final_eligibility_unverified`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is confirmed in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - Even though both Georgia and Germany appear in public discovery evidence, does the issued family policy still permit Georgia residence together with Germany treatment/home-country use under the desired geography option?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Run the official quote far enough to capture the Georgia-resident family result and verify that the available area-of-cover can include Germany under a worldwide-excluding-US or equivalent option.
  - Need full quote-flow or benefits guide to verify Georgia selection and geography bands.
  - Need pricing and family underwriting details.
  - Need quote-flow or benefits guide to verify Georgia selection and geography bands.
  - Need pricing and underwriting details for the specific family profile.
  - The run did not confirm whether APRIL later requires email verification or account creation before showing final prices or binding coverage.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Public entry asks the user to choose cover type, country, and language before deeper quote output. (https://www.april-international.com/en)
  - [visible_required_field] Country (https://www.april-international.com/en)
  - [captured_public_source_excerpt] APRIL positions this as long-term international health insurance for expatriates and globally mobile individuals staying abroad longer than 12 months, with worldwide cover depending on area of cover. The country selector excerpt includes Germany. (https://www.april-international.com/en/long-term-international-health-insurance)
  - [captured_public_source_excerpt] APRIL positions the product for expatriates and globally mobile people staying abroad longer than 12 months, says policies are annually renewable, and states that worldwide cover depends on the selected geographic area. (https://www.april-international.com/en/long-term-international-health-insurance)
  - [germany_applicability] APRIL’s extracted country selector examples include Germany by name. (https://www.april-international.com/en/long-term-international-health-insurance)
  - [germany_applicability] APRIL also states worldwide cover depends on the selected geographic area, so Germany is directly relevant but still area-dependent. (https://www.april-international.com/en/long-term-international-health-insurance)
  - [georgia_applicability] APRIL's public page text includes a country-of-coverage list containing Georgia and Germany. (https://www.april-international.com/en/long-term-international-health-insurance)
  - [georgia_applicability] APRIL also states long-term plans provide worldwide cover depending on the chosen area of cover, making Georgia-relevance direct but still area-dependent. (https://www.april-international.com/en/long-term-international-health-insurance)
  - [captured_uncertainty] Need full quote-flow or benefits guide to verify Georgia selection and geography bands. (shared-dataset note)
  - [captured_uncertainty] Need quote-flow or benefits guide to verify Georgia selection and geography bands. (shared-dataset note)

## William Russell — International Health Insurance

- Candidate ID: `william_russell`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need quote results or policy wording to confirm Georgia residence acceptance.
  - Need confirmation of Germany/Georgia geography band and family rates.
  - Need quote or policy wording to confirm Georgian residency acceptance and geography band details.
  - Need family pricing and underwriting evidence.
  - The dedicated quote app was referenced but not deeply traversed here, so later account, email, or phone verification remains unverified.
- Source-backed statements:
  - [captured_public_source_excerpt] William Russell describes its product as international health insurance for expats and people living, working, or studying abroad, with members able to choose doctors or hospitals within their coverage zone. (https://www.william-russell.com/international-health-insurance/)
  - [captured_public_source_excerpt] William Russell markets international health insurance for expats, digital nomads, families, and students living, working, or studying abroad, with freedom to choose any doctor or hospital within the coverage zone. (https://www.william-russell.com/international-health-insurance/)
  - [germany_applicability] William Russell says customers can choose anything from worldwide cover to one-region cover. (https://www.william-russell.com/international-health-insurance/)
  - [germany_applicability] Public examples rely on country of residence and coverage area logic, which makes Germany plausible without naming it. (https://www.william-russell.com/international-health-insurance/)
  - [georgia_applicability] William Russell says customers can buy anything from worldwide cover to one-region cover. (https://www.william-russell.com/international-health-insurance/)
  - [georgia_applicability] Public pricing examples show that country of residence and coverage outside country of residence are core underwriting variables, which makes Georgia relevant even though it was not directly named. (https://www.william-russell.com/international-health-insurance/)
  - [captured_uncertainty] Need quote results or policy wording to confirm Georgia residence acceptance. (shared-dataset note)
  - [captured_uncertainty] Need confirmation of Germany/Georgia geography band and family rates. (shared-dataset note)
  - [captured_uncertainty] Need quote or policy wording to confirm Georgian residency acceptance and geography band details. (shared-dataset note)

## MSH International — First'Expat+ / International Health Insurance for Individuals

- Candidate ID: `msh_international`
- Eligibility ambiguity status: `residence_or_nationality_rules_visible_but_family_fit_unresolved`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Use the official quote/application flow or policy wording to test the exact combination of German nationality plus Georgia residence for two adults and two children, then confirm whether Germany is treated as home-country access, covered territory, or a restricted market.
  - Need plan comparison output or PDF wording to confirm eligible residence/country combinations.
  - Need family suitability and pricing evidence.
  - Need product comparison or policy wording to confirm geography fit for Georgia and Germany.
  - Need explicit family suitability and pricing evidence.
  - This run did not advance through the individual flow, so any later contact capture, account creation, or verification requirements remain unverified.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] User must confirm location region and select profile type before deeper comparison/offer flow. (https://www.msh-intl.com/en/comparisons-offers-msh)
  - [germany_applicability] MSH describes First’Expat+ as flexible international health insurance for mobility needs. (https://www.msh-intl.com/en/individuals/)
  - [germany_applicability] The captured quote path did not expose Germany in visible country text. (https://www.msh-intl.com/en/individuals/)
  - [georgia_applicability] MSH markets itself as a healthcare partner for mobility needs and offers a quote path for international individual plans. (https://www.msh-intl.com/en/individuals/)
  - [georgia_applicability] The public page showed region-selection context including an Asia site version but did not expose a country dropdown naming Georgia in the extracted text. (https://www.msh-intl.com/en/individuals/)
  - [captured_uncertainty] Need plan comparison output or PDF wording to confirm eligible residence/country combinations. (shared-dataset note)
  - [captured_uncertainty] Need product comparison or policy wording to confirm geography fit for Georgia and Germany. (shared-dataset note)

## Now Health International — SimpleCare / WorldCare International Health Insurance Plans

- Candidate ID: `now_health_international`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need member handbook or quote flow to verify geography bands and Georgian residency eligibility.
  - Need family pricing and underwriting specifics.
  - A distinct public quote-entry path was not verified in this run, so quote-stage friction remains unknown.
- Source-backed statements:
  - [captured_public_source_excerpt] Now Health compares seven international plan options across SimpleCare and WorldCare product lines and includes evacuation/repatriation and optional USA treatment mechanics in the public comparison table. (https://www.now-health.com/en/insurance-plans/)
  - [germany_applicability] The public comparison table repeatedly refers to the country of nationality or residence in benefit wording. (https://www.now-health.com/en/insurance-plans/)
  - [germany_applicability] Residence-specific issuance notes show that market eligibility is country-driven even though Germany was not explicitly captured. (https://www.now-health.com/en/insurance-plans/)
  - [georgia_applicability] The public comparison table repeatedly refers to repatriation or transportation to the country of nationality or residence. (https://www.now-health.com/en/insurance-plans/)
  - [georgia_applicability] Now Health also lists plan-issuance restrictions for some UAE residence-visa holders, showing country-of-residence matters even though Georgia was not named. (https://www.now-health.com/en/insurance-plans/)
  - [captured_uncertainty] Need member handbook or quote flow to verify geography bands and Georgian residency eligibility. (shared-dataset note)

## IMG (International Medical Group) — Global Medical international health insurance

- Candidate ID: `img_global`
- Eligibility ambiguity status: `explicit_country_selector_but_final_eligibility_unverified`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is confirmed in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - Even though both Georgia and Germany appear in public discovery evidence, does the issued family policy still permit Georgia residence together with Germany treatment/home-country use under the desired geography option?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Run the official quote far enough to capture the Georgia-resident family result and verify that the available area-of-cover can include Germany under a worldwide-excluding-US or equivalent option.
  - Need Global Medical plan docs or quote flow to verify country eligibility and area-of-cover choices.
  - Need suitability check because some IMG products are travel-medical rather than full expat medical.
  - This run did not open the downstream purchase/price pages, so later signup or identity verification requirements remain unverified.
- Source-backed statements:
  - [germany_applicability] Captured public HTML contains country options including Germany. (https://www.imglobal.com/)
  - [germany_applicability] IMG also markets these plans as worldwide medical coverage for expats and global citizens. (https://www.imglobal.com/)
  - [georgia_applicability] IMG country lists publicly include Georgia and Germany, providing direct evidence that Georgia can be selected in IMG’s public web flow. (https://www.imglobal.com/international-health-insurance)
  - [georgia_applicability] IMG also says international health plans provide medical coverage worldwide and are suitable for expats and their families. (https://www.imglobal.com/international-health-insurance)
  - [captured_uncertainty] Need Global Medical plan docs or quote flow to verify country eligibility and area-of-cover choices. (shared-dataset note)

## Aetna International — Aetna International global healthcare entrypoint

- Candidate ID: `aetna_international`
- Eligibility ambiguity status: `generic_international_positioning_only`
- Assessment: `unclear`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence remains mostly high-level marketing rather than product-level eligibility proof; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Locate a product-specific brochure, quote path, or underwriting guide before treating this candidate as a serious family option, because the current public evidence is too generic to resolve Georgia residence or Germany applicability.
  - Need an individual/family-specific public plan page or broker evidence to confirm retail availability for this profile.
  - Need pricing and underwriting evidence; Aetna may skew employer/group depending on market.
  - Because this page appears employer/member oriented, it is unclear whether a separate retail individual/family quote flow exists elsewhere or what verification it would require.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] No public individual-family quote intake was verified; observed path is oriented around members, brokers, employers, or portal handoff. (https://www.aetna.com/employers-organizations/aetna-international-insurance.html)
  - [germany_applicability] Reviewed public evidence focused on global healthcare positioning and worldwide support. (https://www.aetnainternational.com/)
  - [germany_applicability] Germany was not named in the captured landing or quote entry pages. (https://www.aetnainternational.com/)
  - [georgia_applicability] Aetna says members can access care anywhere, anytime through global solutions and worldwide telehealth services. (https://www.aetnainternational.com/)
  - [georgia_applicability] This is relevant but weaker than a country selector because Georgia was not named or surfaced in an individual quote flow during this run. (https://www.aetnainternational.com/)

## Foyer Global Health — Expat Health Insurance / International Health Insurance comparison

- Candidate ID: `foyer_global_health`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need direct page extraction or brochure review for stronger evidence.
  - Added as an eleventh backup provider to keep the shortlist above the required minimum of 10.
  - Need plan comparison details or policy wording to confirm Georgia-resident eligibility.
  - Need exact geography split and pricing for this family profile.
  - The quick-quote/application flow itself was not fully traversed here, so later account or verification requirements remain unverified.
- Source-backed statements:
  - [captured_public_source_excerpt] Foyer Global Health says it provides international health insurance for expats, digital nomads, globally mobile people, and families moving abroad, and describes its health coverage as worldwide and flexible. (https://www.foyerglobalhealth.com/)
  - [germany_applicability] Official destination or footprint content surfaced Germany among public country references. (https://www.foyerglobalhealth.com/)
  - [germany_applicability] That is stronger than generic worldwide marketing, but weaker than a formal eligibility selector. (https://www.foyerglobalhealth.com/)
  - [georgia_applicability] Foyer’s official site highlights Georgia among popular destination guides, including Georgia living, health, and budget guides. (https://www.foyerglobalhealth.com/)
  - [georgia_applicability] Foyer also positions its plans as worldwide and suitable for expats and families going abroad. (https://www.foyerglobalhealth.com/)
  - [captured_uncertainty] Need plan comparison details or policy wording to confirm Georgia-resident eligibility. (shared-dataset note)

## Henner — Individuals & Families international health solutions

- Candidate ID: `henner`
- Eligibility ambiguity status: `generic_international_positioning_only`
- Assessment: `unclear`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence remains mostly high-level marketing rather than product-level eligibility proof; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Locate a product-specific brochure, quote path, or underwriting guide before treating this candidate as a serious family option, because the current public evidence is too generic to resolve Georgia residence or Germany applicability.
  - Need product-specific public plan page, brochure, or broker evidence to identify exact retail plan names.
  - Need quote or underwriting evidence for Georgian residency and family pricing.
  - Because no quote flow was verified, signup, account creation, email capture, or phone verification requirements are unknown.
- Source-backed statements:
  - [captured_public_source_excerpt] Henner says it has developed unique expertise in personal insurance for people living abroad, offers international health insurance tailored to all lifestyles, and works with healthcare professionals in 240 countries. (https://www.henner.com/en/customers/individuals-families/)
  - [germany_applicability] Henner markets international health solutions for individuals and families. (https://www.henner.com/en/customers/individuals-families/)
  - [germany_applicability] No captured source in this run named Germany or exposed Germany-specific territory wording. (https://www.henner.com/en/customers/individuals-families/)
  - [georgia_applicability] Henner says it developed personal-insurance expertise for people living abroad and supports members wherever they are. (https://www.henner.com/en/customers/individuals-families/)
  - [georgia_applicability] Henner also states it works with healthcare professionals in 240 countries, which is geography-relevant but not a Georgia-specific eligibility proof. (https://www.henner.com/en/customers/individuals-families/)
  - [captured_uncertainty] Need quote or underwriting evidence for Georgian residency and family pricing. (shared-dataset note)

## Globality Health — YouGenio individual international health insurance plans

- Candidate ID: `globality_health`
- Eligibility ambiguity status: `residence_or_nationality_rules_visible_but_family_fit_unresolved`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: Public quote flow explicitly asks about nationality or country of nationality, making German citizenship a visible intake variable rather than an inferred one.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - How does German citizenship interact with the visible nationality/residency intake rules for this plan, especially for two adults and two dependent children applying from Georgia?
- Follow-up verification needed:
  - Use the official quote/application flow or policy wording to test the exact combination of German nationality plus Georgia residence for two adults and two children, then confirm whether Germany is treated as home-country access, covered territory, or a restricted market.
  - Capture the official application steps past nationality, future residency, and signing-country prompts to verify whether a German citizen signing from Georgia can continue and whether Germany can still be included within the selected cover geography.
  - Need plan-page or brochure review to confirm residency/citizenship rules relevant to German citizens living in Georgia.
  - Need exact area-of-cover and family-pricing evidence.
  - The observed consent requirement is not an account gate, but later identity verification, email confirmation, or portal creation requirements were not verified.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Direct-business-only notice is shown. (https://application.globality-health.com/?locale=en)
  - [country_of_residence_or_quote_precheck] User must waive personal consultation or contact support before proceeding. (https://application.globality-health.com/?locale=en)
  - [country_of_residence_or_quote_precheck] Applicant nationality, future residency, and signing-country location are part of the entry steps. (https://application.globality-health.com/?locale=en)
  - [visible_required_field] Nationality (https://application.globality-health.com/?locale=en)
  - [visible_required_field] Country of future residency (https://application.globality-health.com/?locale=en)
  - [visible_required_field] Country where the application is signed (https://application.globality-health.com/?locale=en)
  - [germany_applicability] Captured evidence noted a related public signal involving non-German expat distinctions on specific plan pages. (https://www.globality-health.com/)
  - [germany_applicability] That makes Germany screening relevant, but not directly verified for the reviewed plan entrypoint. (https://www.globality-health.com/)
  - [georgia_applicability] The application requires country of future residency and country where the application is signed, using a broad country or territory list. (https://application.globality-health.com/?locale=en)
  - [georgia_applicability] The page warns that some future-residency countries may not be supported for quotation, which is directly relevant to Georgia screening. (https://application.globality-health.com/?locale=en)
  - [captured_uncertainty] Need plan-page or brochure review to confirm residency/citizenship rules relevant to German citizens living in Georgia. (shared-dataset note)

## Morgan Price International Healthcare — Evolution Health / Flexible Choices individual international healthcare plans

- Candidate ID: `morgan_price`
- Eligibility ambiguity status: `residence_or_nationality_rules_visible_but_family_fit_unresolved`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: Public quote flow explicitly asks about nationality or country of nationality, making German citizenship a visible intake variable rather than an inferred one.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - How does German citizenship interact with the visible nationality/residency intake rules for this plan, especially for two adults and two dependent children applying from Georgia?
- Follow-up verification needed:
  - Use the official quote/application flow or policy wording to test the exact combination of German nationality plus Georgia residence for two adults and two children, then confirm whether Germany is treated as home-country access, covered territory, or a restricted market.
  - Review the public policy wording and benefits for both Evolution Health and Flexible Choices to determine whether a German citizen living in Georgia is treated as outside-EU/EEA for eligibility purposes and whether Germany remains valid as home-country or treatment territory.
  - Need policy wording review to see whether residents of Georgia are eligible.
  - Need to reconcile residence exclusions and geography zones for this family profile.
  - The page clearly captures email for manual follow-up in some cases, but this run did not confirm whether email is mandatory for all quote outputs or only fallback scenarios.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Online quote asks residence, currency, age, nationality, and number of dependents. (https://morgan-price.com/quick-quote/)
  - [country_of_residence_or_quote_precheck] If online quote is unavailable for the circumstances, the flow falls back to manual quote request. (https://morgan-price.com/quick-quote/)
  - [visible_required_field] Country Of Residence (https://morgan-price.com/quick-quote/)
  - [visible_required_field] Nationality (https://morgan-price.com/quick-quote/)
  - [captured_public_source_excerpt] Morgan Price says Evolution Health is an expatriate health insurance product for expatriates and eligible dependants, includes home-country cover excluding USA, and publishes policy wording and tables of benefits on the public page. (https://www.morgan-price.com/individual/)
  - [germany_applicability] Flexible Choices applicants must reside outside the EU or EEA, which directly matters for Germany screening. (https://www.morgan-price.com/individual/)
  - [germany_applicability] The public wording also references home-country cover excluding USA, which is materially relevant to a German family abroad even though Germany was not named verbatim. (https://www.morgan-price.com/individual/)
  - [georgia_applicability] Morgan Price states some products are available only where applicants’ primary residence is in specified countries or regions, proving residence-country rules are material. (https://www.morgan-price.com/individual/)
  - [georgia_applicability] The page also says Flexible Choices applicants must reside outside the EU or EEA and includes emergency treatment outside area of cover and home-country cover excluding USA, all directly relevant to a Germany-citizen and Georgia-resident family. (https://www.morgan-price.com/individual/)
  - [captured_uncertainty] Need policy wording review to see whether residents of Georgia are eligible. (shared-dataset note)
  - [captured_uncertainty] Need to reconcile residence exclusions and geography zones for this family profile. (shared-dataset note)

## PassportCard — Expat Basic / Expat Comprehensive / Executive

- Candidate ID: `passportcard_global`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need quote or policy wording to confirm whether residents of Georgia are accepted.
  - Sample monthly prices on the page are illustrative and not specific to this family profile.
  - A direct quote/application path was not verified in this run, so quote-stage contact or verification friction remains unknown.
- Source-backed statements:
  - [captured_public_source_excerpt] PassportCard says it offers international private health insurance for expats, students, and digital nomads, advertises worldwide medical coverage for long-term stays abroad, and shows three plan tiers with example EUR pricing. (https://www.passportcard.de/en/international-health-insurance/)
  - [germany_applicability] Reviewed public evidence came from PassportCard’s Germany-facing expat site. (https://www.passportcard.de/en/international-health-insurance/)
  - [germany_applicability] The public wording says some covered persons can receive treatment in Germany during home visits after relocation. (https://www.passportcard.de/en/international-health-insurance/)
  - [georgia_applicability] PassportCard states contract currency is EUR for coverage worldwide excluding USA territories and USD for policies including the USA. (https://www.passportcard.de/en/international-health-insurance/)
  - [georgia_applicability] It also says insureds traveling to countries in zones 0–3 can receive treatment in Germany during home visits after relocation, which is highly relevant for Germany but does not itself verify Georgia as destination. (https://www.passportcard.de/en/international-health-insurance/)
  - [captured_uncertainty] Need quote or policy wording to confirm whether residents of Georgia are accepted. (shared-dataset note)

## VUMI — Absolute VIP / Universal VIP / Special VIP / Access VIP product family

- Candidate ID: `vumi`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need a Europe-market quote or plan overview to verify Georgian residency acceptance and practical availability for this family.
  - Need plan documents to distinguish broad IPMI products from any more region-specific offerings.
  - A direct public quote path for individuals/families was not verified here, so pre-quote account or verification friction remains unknown.
- Source-backed statements:
  - [captured_public_source_excerpt] VUMI's homepage markets international health insurance with worldwide coverage, says its solutions protect you and your family anywhere anytime, and lists multiple long-term medical plan families plus a Europe region selector. (https://www.vumigroup.com/)
  - [germany_applicability] The reviewed public evidence describes VUMI’s plans as international or worldwide in scope. (https://www.vumigroup.com/)
  - [germany_applicability] Germany was not explicitly named in the captured public text. (https://www.vumigroup.com/)
  - [georgia_applicability] VUMI’s homepage repeatedly says its international health insurance provides worldwide coverage. (https://www.vumigroup.com/)
  - [georgia_applicability] The company frames its plans as protecting families anywhere, anytime, but that is still weaker than a country-specific selector. (https://www.vumigroup.com/)
  - [captured_uncertainty] Need a Europe-market quote or plan overview to verify Georgian residency acceptance and practical availability for this family. (shared-dataset note)

## Integra Global — yourLife / PremierLife / yourFamily / PremierFamily

- Candidate ID: `integra_global`
- Eligibility ambiguity status: `gated_or_incomplete_public_evidence`
- Assessment: `unclear`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence is incomplete because key eligibility material stayed gated, broker-led, or otherwise unavailable in this run; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Obtain an insurer-controlled quote screen, brochure, benefit table, or policy wording that is not challenge-blocked or broker-only, then re-check Georgia residence and Germany coverage fit from that primary source.
  - This evidence is broker-sourced rather than directly from an official insurer page.
  - Need official plan wording or quote output to confirm eligibility for German citizens resident in Georgia.
  - The broker’s own quote form friction and any eventual insurer-side account requirements were not fully traced in this run.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Only broker-led discovery evidence was captured, so insurer intake requirements remain unresolved. (https://www.internationalinsurance.com/integra-global/)
  - [captured_public_source_excerpt] The broker page describes Integra Global as international health insurance for expatriates, globally mobile individuals, and families, and explicitly lists two coverage areas: worldwide and worldwide excluding the U.S., Canada, and their territories. (https://www.internationalinsurance.com/integra-global/)
  - [germany_applicability] The captured public evidence supports region or area-of-cover logic rather than a single-country-only product. (https://www.internationalinsurance.com/integra-global/)
  - [germany_applicability] Germany itself was not named on the reviewed page. (https://www.internationalinsurance.com/integra-global/)
  - [georgia_applicability] Integra says members can choose worldwide coverage including the US and Canada or worldwide coverage excluding the US and Canada. (https://hcigroupglobal.com/integra-global/)
  - [georgia_applicability] It also states the plan will cover members worldwide depending on the chosen region and the country where they intend to stay longest. (https://hcigroupglobal.com/integra-global/)
  - [captured_uncertainty] Need official plan wording or quote output to confirm eligibility for German citizens resident in Georgia. (shared-dataset note)

## MediHelp International — My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal

- Candidate ID: `medihelp_international`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need underwriting territory and residence-country rules for Georgia.
  - Need family-plan or dependent-enrollment details beyond the individual-plan landing page.
  - This run did not inspect the separate Get a quote form, so any email/phone verification there remains unverified.
- Source-backed statements:
  - [captured_public_source_excerpt] MediHelp's official individual-plans page says My Global Health offers international coverage and top medical services worldwide, allows freedom to choose the doctor and clinic, and lists five individual plan tiers. (https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans)
  - [germany_applicability] The public plan family is positioned as global health cover for internationally mobile customers. (https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans)
  - [germany_applicability] Germany was not explicitly named in the captured public material. (https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans)
  - [georgia_applicability] MediHelp says its individual plans provide international coverage and top medical services worldwide. (https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans)
  - [georgia_applicability] The page emphasizes freedom to choose doctor and clinic worldwide, which is relevant but not Georgia-specific. (https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans)
  - [captured_uncertainty] Need underwriting territory and residence-country rules for Georgia. (shared-dataset note)

## ALC Health — Global Prima Medical Insurance / Flying Colours legacy entrypoints

- Candidate ID: `alc_health`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Trace the IMG replacement path referenced by ALC and identify which current IMG-issued policy wording governs new business, because the legacy ALC entrypoint alone cannot resolve present-day Georgia residence eligibility.
  - Need to follow the IMG migration path to confirm which current IMG products replace the older ALC entrypoints.
  - Need current policy documents because the landing page mixes legacy servicing information with new-business routing.
  - This run did not inspect the new IMG-branded destination from the redirect notice for any additional friction.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] ALC appears to route new business away from the legacy brand, so direct new-business intake requirements were not captured on ALC itself. (https://www.alchealth.com/)
  - [captured_public_source_excerpt] ALC Health's homepage advertises global health and international medical insurance for expatriates, local nationals, and international travellers worldwide, includes an Individuals & Families path, and notes that new business quotes and sales moved to IMG's updated international website. (https://www.alchealth.com/)
  - [germany_applicability] ALC publicly references a Germany page in its regional marketing footprint. (https://www.alchealth.com/)
  - [germany_applicability] Captured public copy also lists Germany among named countries in broader global-country marketing. (https://www.alchealth.com/)
  - [georgia_applicability] ALC says it provides global cover across the world and explicitly references multiple countries plus a Germany regional page. (https://www.alchealth.com/)
  - [georgia_applicability] This suggests geography-specific sales structure, but Georgia was not named among the captured country references. (https://www.alchealth.com/)

## Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) — Worldwide Premier / Outside U.S. / Outside U.S. Select

- Candidate ID: `geoblue_xplorer_bcbs_global_solutions`
- Eligibility ambiguity status: `gated_or_incomplete_public_evidence`
- Assessment: `unclear`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence is incomplete because key eligibility material stayed gated, broker-led, or otherwise unavailable in this run; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Obtain an insurer-controlled quote screen, brochure, benefit table, or policy wording that is not challenge-blocked or broker-only, then re-check Georgia residence and Germany coverage fit from that primary source.
  - Confirm from official BCBS Global Solutions materials whether non-U.S. citizens resident in Georgia can enroll directly, or whether the plan is effectively limited to U.S.-linked members despite the outside-U.S. geography wording.
  - This product may be more suitable for U.S. citizens abroad or people with U.S. coverage links than for a German family resident in Georgia.
  - Need direct eligibility rules to verify whether this family profile can actually enroll.
  - Broker form friction and any subsequent insurer verification requirements were not traced in this run.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Discovery is broker-led, not insurer-led, so direct quote-intake fields were not verified from an official insurer quote flow. (https://www.internationalinsurance.com/geoblue/xplorer/)
  - [captured_public_source_excerpt] The broker page says the rebranded BCBS Global Solutions plans serve expatriates, global citizens, and families residing overseas, and that the Outside U.S. plan does not include U.S. coverage. (https://www.internationalinsurance.com/geoblue/xplorer/)
  - [germany_applicability] The reviewed plan family explicitly includes outside-U.S. products. (https://www.internationalinsurance.com/geoblue/xplorer/)
  - [germany_applicability] That structure makes Germany plausible as a covered non-U.S. destination, but Germany itself was not quoted. (https://www.internationalinsurance.com/geoblue/xplorer/)
  - [georgia_applicability] The page offers an Outside the U.S. plan for people who do not need U.S. coverage, which is directly relevant to worldwide-ex-US screening. (https://bcbsglobalsolutions.com/individuals-and-families/international-medical-insurance/living-abroad/worldwide-and-outside-us/)
  - [georgia_applicability] Germany appears in Schengen-insurance support text, but Georgia was not named or tied to residence eligibility. (https://bcbsglobalsolutions.com/individuals-and-families/international-medical-insurance/living-abroad/worldwide-and-outside-us/)
  - [captured_uncertainty] This product may be more suitable for U.S. citizens abroad or people with U.S. coverage links than for a German family resident in Georgia. (shared-dataset note)
  - [captured_uncertainty] Need direct eligibility rules to verify whether this family profile can actually enroll. (shared-dataset note)

## SafetyWing — Nomad Insurance Complete

- Candidate ID: `safetywing_nomad_complete`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need policy wording or quote results to verify residence-country acceptance for a German family living in Georgia.
  - Need exact Germany/Georgia geography treatment and underwriting details beyond the marketing page.
  - The run did not determine whether any anonymous quote-only path exists elsewhere outside this signup URL.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Account creation or login is the first gate before quote contents become visible. (https://safetywing.com/signup?product=nomad-health)
  - [captured_public_source_excerpt] SafetyWing describes Nomad Insurance Complete as full health and travel insurance for digital nomads, expats, remote workers, and families living abroad, with global coverage and an add-on for Hong Kong, Singapore, and the US. (https://explore.safetywing.com/nomad-insurance-complete)
  - [germany_applicability] The captured page positions Nomad Insurance Complete as global health insurance for families living abroad. (https://explore.safetywing.com/nomad-insurance-complete)
  - [germany_applicability] Germany was not explicitly named in the reviewed public text. (https://explore.safetywing.com/nomad-insurance-complete)
  - [georgia_applicability] SafetyWing says Complete offers global coverage and lets members use any licensed provider at any clinic or hospital, private or public. (https://explore.safetywing.com/nomad-insurance-complete)
  - [georgia_applicability] The visible add-on logic references the U.S., Hong Kong, and Singapore rather than Georgia, so country-specific Georgia support remains unresolved. (https://explore.safetywing.com/nomad-insurance-complete)
  - [captured_uncertainty] Need policy wording or quote results to verify residence-country acceptance for a German family living in Georgia. (shared-dataset note)
  - [captured_uncertainty] Need exact Germany/Georgia geography treatment and underwriting details beyond the marketing page. (shared-dataset note)

## Genki — Native / international health insurance

- Candidate ID: `genki_native`
- Eligibility ambiguity status: `explicit_country_selector_but_final_eligibility_unverified`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is confirmed in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - Even though both Georgia and Germany appear in public discovery evidence, does the issued family policy still permit Georgia residence together with Germany treatment/home-country use under the desired geography option?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Run the official quote far enough to capture the Georgia-resident family result and verify that the available area-of-cover can include Germany under a worldwide-excluding-US or equivalent option.
  - Read the Native/Resident wording closely to confirm whether the product requires maintained home-country healthcare access, whether Germany can serve as that home-country anchor, and whether a Georgia-resident family of four fits the intended long-term profile.
  - Need the specific Native/Resident product page or policy wording to verify whether this is a full expat-medical fit for a Germany/Georgia family rather than a lighter nomad-oriented product.
  - Need residence eligibility and family-plan details.
  - The wording implies signup later in the process, but this run did not verify whether account creation is required before the final price display or only at purchase.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Genki screens for travel duration and excludes trips shorter than one month. (https://consultation.genki.world/v2)
  - [country_of_residence_or_quote_precheck] Questionnaire asks whether user is planning travel or already abroad, main destination, home-country healthcare access, and long-term illness preference before recommendation. (https://consultation.genki.world/v2)
  - [visible_required_field] Do you have access to healthcare in your home country? (https://consultation.genki.world/v2)
  - [captured_public_source_excerpt] Genki says it offers two health-insurance options for digital nomads, including an international health insurance option, covers treatment at every licensed healthcare provider worldwide, and lets users get an instant price online. (https://genki.world/)
  - [germany_applicability] Genki’s public options list includes Germany by name. (https://genki.world/)
  - [germany_applicability] The surrounding public copy says users can join from anywhere, reinforcing that Germany is part of an active intake flow rather than generic marketing only. (https://genki.world/)
  - [georgia_applicability] Genki’s public consultation flow contains Georgia and Germany in its country options. (https://consultation.genki.world/v2)
  - [georgia_applicability] Genki also says it offers worldwide cover and that users can join from anywhere, which materially supports Georgia discovery. (https://consultation.genki.world/v2)
  - [captured_uncertainty] Need the specific Native/Resident product page or policy wording to verify whether this is a full expat-medical fit for a Germany/Georgia family rather than a lighter nomad-oriented product. (shared-dataset note)
  - [captured_uncertainty] Need residence eligibility and family-plan details. (shared-dataset note)

## ACS — ACS Expat

- Candidate ID: `acs_expat`
- Eligibility ambiguity status: `residence_or_nationality_rules_visible_but_family_fit_unresolved`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: Public quote flow explicitly asks about nationality or country of nationality, making German citizenship a visible intake variable rather than an inferred one.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - How does German citizenship interact with the visible nationality/residency intake rules for this plan, especially for two adults and two dependent children applying from Georgia?
- Follow-up verification needed:
  - Use the official quote/application flow or policy wording to test the exact combination of German nationality plus Georgia residence for two adults and two children, then confirm whether Germany is treated as home-country access, covered territory, or a restricted market.
  - Need brochure/table-of-benefits review to confirm Georgia eligibility and the exact geography zone that would include Germany and Georgia.
  - Need family-specific pricing and underwriting evidence.
  - The extract did not definitively show which contact fields are mandatory beyond the visible personal-data fields, but the quote path clearly shifts to advisor follow-up rather than instant anonymous pricing.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] ACS first asks destination country and intended length/stay category. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/)
  - [country_of_residence_or_quote_precheck] Coverage need, currency, and beneficiary composition are captured before advisor follow-up. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/)
  - [visible_required_field] Destination country (https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/)
  - [visible_required_field] I am living abroad... (up to 12 months / Working Holiday Program / more than one year) (https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/)
  - [visible_required_field] Country of nationality (https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/)
  - [captured_public_source_excerpt] ACS Expat is presented as modular lifetime international health insurance for expatriates of all nationalities, available individually, as a couple, or as a family, with home-country visit coverage and geography-zone based cover. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat/)
  - [germany_applicability] ACS states home-country visit coverage applies if the home country is included in the selected geographical zone. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat/)
  - [germany_applicability] That creates a concrete Germany-relevant benefit mechanic for a German family abroad, even though Germany is not named explicitly. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat/)
  - [georgia_applicability] ACS Expat says it covers international stays of 12+ months with worldwide healthcare access and support. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat/)
  - [georgia_applicability] ACS also says temporary stays worldwide are covered for up to 7 weeks outside the selected coverage area, which is geography-relevant but still not Georgia-specific. (https://www.acs-ami.com/en/expat-health-insurance/acs-expat/)
  - [captured_uncertainty] Need brochure/table-of-benefits review to confirm Georgia eligibility and the exact geography zone that would include Germany and Georgia. (shared-dataset note)

## HCI Group — Health Protect / Protector Plans / Nimbl Health

- Candidate ID: `hci_group_health_protect`
- Eligibility ambiguity status: `gated_or_incomplete_public_evidence`
- Assessment: `unclear`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is unknown in the Germany coverage ledger and Georgia applicability is unknown in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence is incomplete because key eligibility material stayed gated, broker-led, or otherwise unavailable in this run; the reviewed sources still did not directly prove that one issued policy can support Georgia residence with Germany-relevant coverage.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Obtain an insurer-controlled quote screen, brochure, benefit table, or policy wording that is not challenge-blocked or broker-only, then re-check Georgia residence and Germany coverage fit from that primary source.
  - Need quote or plan-PDF review to verify whether residents of Georgia are eligible and how Germany fits into geography rules.
  - Need plan-zone and family pricing details.
  - The observed failure may be transient or tool-specific rather than a customer-facing hard gate.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] A technical/availability barrier prevented capture of quote questions. (https://hcigroup.outgrow.us/P21-2026)
  - [captured_public_source_excerpt] HCI Group says its Health Protect policies offer international private medical insurance for expatriates, digital nomads, international students, and others living abroad, with five Protector Plan levels from $500,000 to $2,500,000 and optional maternity/newborn benefits. (https://hcigroupglobal.com/health-protect/)
  - [germany_applicability] The reviewed public page supports international/private medical positioning. (https://hcigroupglobal.com/health-protect/)
  - [germany_applicability] Germany was not explicitly named or tied to a visible geography rule. (https://hcigroupglobal.com/health-protect/)
  - [georgia_applicability] HCI says Health Protect serves expatriates, digital nomads, international students, and holiday-home owners living abroad or traveling frequently. (https://hcigroupglobal.com/health-protect/)
  - [georgia_applicability] The page provides plan-benefits PDFs and quote entry points, but the captured content did not expose a Georgia country list or territorial exclusion table. (https://hcigroupglobal.com/health-protect/)
  - [captured_uncertainty] Need quote or plan-PDF review to verify whether residents of Georgia are eligible and how Germany fits into geography rules. (shared-dataset note)

## Expatriate Group — International Health Insurance / Select / Primary+ / Primary+ Lite / Primary

- Candidate ID: `expatriate_group_global_health`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need quote-form output or policy wording to confirm Georgian residency acceptance for this family profile.
  - Need exact plan availability, underwriting, and pricing for two adults and two children.
  - This run did not verify whether the system displays pricing before or only after email submission, but email capture is clearly embedded in the initial quote stage.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Quote requires people-to-insure count, area of cover, and dates of birth before proceeding. (https://quote.expatriatehealthcare.com/healthcare/)
  - [country_of_residence_or_quote_precheck] Supports explicit worldwide excluding USA area selection. (https://quote.expatriatehealthcare.com/healthcare/)
  - [captured_public_source_excerpt] Expatriate Group markets international private medical insurance for expat families and individuals, provides four plan levels, and publicly shows area-of-cover options including Europe/Middle East/Africa/Asia/Oceania, worldwide excluding the USA, and worldwide. (https://www.expatriatehealthcare.com/international-health-insurance/)
  - [germany_applicability] The captured quote process includes an area option centered on Europe and adjacent regions. (https://www.expatriatehealthcare.com/international-health-insurance/)
  - [germany_applicability] Germany sits naturally inside that public regional geography, although Germany was not quoted by name. (https://www.expatriatehealthcare.com/international-health-insurance/)
  - [georgia_applicability] The quote form lists Area 1 as Europe, Middle East, Africa, Asia and Oceania excluding China, Hong Kong and Singapore, Area 2 as Worldwide excluding the USA, and Area 3 as Worldwide. (https://quote.expatriatehealthcare.com/healthcare/)
  - [georgia_applicability] The page also states treatment outside the selected Area of Cover is limited, making the area definition directly relevant to Georgia screening. (https://quote.expatriatehealthcare.com/healthcare/)
  - [captured_uncertainty] Need quote-form output or policy wording to confirm Georgian residency acceptance for this family profile. (shared-dataset note)

## WellAway — EXPAT / Gold / Diamond / Expat Plus

- Candidate ID: `wellaway_expat`
- Eligibility ambiguity status: `residence_or_nationality_rules_visible_but_family_fit_unresolved`
- Assessment: `mixed_signals`
- Residence ambiguity: Public sources show residence-country or living-country screening in the quote or eligibility flow, so Georgia residence matters to plan access even when final acceptance stays unverified.
- Nationality ambiguity: Public quote flow explicitly asks about nationality or country of nationality, making German citizenship a visible intake variable rather than an inferred one.
- Geographic applicability: Germany applicability is confirmed in the Germany coverage ledger and Georgia applicability is confirmed in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows that residence-country and/or nationality inputs materially affect access to this plan; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - Even though both Georgia and Germany appear in public discovery evidence, does the issued family policy still permit Georgia residence together with Germany treatment/home-country use under the desired geography option?
  - How does German citizenship interact with the visible nationality/residency intake rules for this plan, especially for two adults and two dependent children applying from Georgia?
- Follow-up verification needed:
  - Use the official quote/application flow or policy wording to test the exact combination of German nationality plus Georgia residence for two adults and two children, then confirm whether Germany is treated as home-country access, covered territory, or a restricted market.
  - Inspect later quote steps or brochure wording to verify whether the available cover areas genuinely support a worldwide-excluding-US style configuration for a Georgia-resident German family, rather than a primarily U.S.-oriented structure.
  - Need brochure or quote-flow review to determine whether non-US area-of-cover options exist and whether Georgia residence is accepted.
  - Medical underwriting and family pricing require follow-up.
  - The flow was only inspected at Step 1, so any later account creation or email/phone verification mechanism remains unverified.
- Source-backed statements:
  - [country_of_residence_or_quote_precheck] Step 1 requires coverage start, destination, state, identity, and consent fields before the Plans step. (https://portal.wellaway.com/Quote/EXPAT/Step1)
  - [country_of_residence_or_quote_precheck] Family/dependent composition is collected at the basics stage. (https://portal.wellaway.com/Quote/EXPAT/Step1)
  - [visible_required_field] Nationality (https://portal.wellaway.com/Quote/EXPAT/Step1)
  - [captured_public_source_excerpt] WellAway presents EXPAT as a top-tier expat health insurance plan for individuals, families, and groups living abroad, with Gold, Diamond, and Expat Plus coverage levels and annual limits up to $3,000,000. (https://www.wellaway.com/en/our-plans/expat/)
  - [germany_applicability] The public quote Step 1 HTML includes Germany in the country list. (https://www.wellaway.com/en/our-plans/expat/)
  - [germany_applicability] This is direct public country evidence in an insurer-controlled intake artifact. (https://www.wellaway.com/en/our-plans/expat/)
  - [georgia_applicability] The public quote Step 1 HTML includes Georgia and Germany in the country list. (https://portal.wellaway.com/Quote/EXPAT/Step1)
  - [georgia_applicability] The official EXPAT page positions the plan for individuals, families, and groups living abroad, making the selector evidence directly relevant to this family. (https://portal.wellaway.com/Quote/EXPAT/Step1)
  - [captured_uncertainty] Need brochure or quote-flow review to determine whether non-US area-of-cover options exist and whether Georgia residence is accepted. (shared-dataset note)

## Securus International — Global Health Cover

- Candidate ID: `securus_global_health_cover`
- Eligibility ambiguity status: `geography_structure_visible_but_country_fit_inferred`
- Assessment: `mixed_signals`
- Residence ambiguity: Reviewed public sources did not surface a clear residence-country rule, leaving Georgia-residence acceptance unresolved.
- Nationality ambiguity: German nationality remained mostly indirect in reviewed public sources; no clear public rule resolved whether citizenship changes eligibility for this plan.
- Geographic applicability: Germany applicability is inferred in the Germany coverage ledger and Georgia applicability is inferred in the Georgia coverage ledger; together this supports discovery relevance but not final enrollment certainty for the family context.
- Unresolved-questions note: Public evidence shows geography structure, but not enough country-specific proof to resolve this family profile; the reviewed sources still did not prove that the family can be issued cover as German citizens resident in Georgia under the desired Germany-plus-Georgia use case.
- Remaining country/residency questions:
  - Does the insurer actually issue this plan to a family whose legal residence is Georgia, rather than merely listing Georgia in a selector, regional description, or broker flow?
  - If the family resides in Georgia, does the chosen area of cover still allow treatment or home-country-style access in Germany under the same family policy?
  - Does German citizenship trigger any eligibility, underwriting, signing-country, or home-country restrictions that were not visible in the reviewed public sources?
- Follow-up verification needed:
  - Find the plan brochure, membership guide, or quote output that names the residence-country rules and geography bands explicitly enough to prove whether Georgia residence and Germany treatment/home-country use can coexist on one family policy.
  - Need the published benefit tables and membership guide reviewed to confirm geographic eligibility and whether the plans suit a German citizen family resident in Georgia.
  - Pricing and area-of-cover selection remain uncertain from the landing page alone.
  - A direct public quote or application path was not verified, so quote-stage friction remains unknown.
- Source-backed statements:
  - [germany_applicability] The public product positioning supports globally oriented health cover. (https://www.securus.co.uk/benefits/global-health-cover)
  - [germany_applicability] Germany was not explicitly named in the captured page text. (https://www.securus.co.uk/benefits/global-health-cover)
  - [georgia_applicability] Securus says global health cover lets customers choose who, how, and where they are treated around the world. (https://www.securus.co.uk/benefits/global-health-cover)
  - [georgia_applicability] The page links benefit-plan PDFs, but no Georgia-specific country list or exclusion wording was captured in the HTML reviewed here. (https://www.securus.co.uk/benefits/global-health-cover)
  - [captured_uncertainty] Need the published benefit tables and membership guide reviewed to confirm geographic eligibility and whether the plans suit a German citizen family resident in Georgia. (shared-dataset note)
