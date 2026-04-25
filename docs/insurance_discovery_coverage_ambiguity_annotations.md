# Coverage ambiguity annotations

Machine-readable source: `data/insurance_discovery/coverage_ambiguity_annotations.discovery.json`

This discovery-only ledger annotates partial or ambiguous coverage details for each candidate insurer/plan using source-backed evidence from the Georgia, Germany, and worldwide-excluding-U.S. coverage ledgers.

## Summary

- Verified total unique candidates: 27
- Candidates with any partial or ambiguous coverage dimension: 27
- Candidates with all three dimensions still partial or ambiguous: 27

- georgia dimension entries with explicit uncertainty flags: 27
- germany dimension entries with explicit uncertainty flags: 27
- worldwide_ex_us dimension entries with explicit uncertainty flags: 27

## Candidate annotations

## Cigna Global — International Health Insurance / Global Medical Cover

- Candidate ID: `cigna_global`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Official quote country selector explicitly lists both Georgia and Germany as selectable countries.
    - Evidence note: Cigna's quote country selector HTML includes Georgia and Germany as adjacent options, giving direct public evidence that Georgia can at least be entered in the residence/selection flow.
    - Unresolved: Selector presence does not by itself prove final underwriting acceptance or pricing for a German family resident in Georgia.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Cigna’s public quote selector explicitly lists Germany, giving direct Germany-country evidence rather than only broad global marketing.
    - Evidence note: Cigna’s official quote country selector HTML includes Germany as a selectable option.
    - Unresolved: Need quote-flow or policy guide review to verify whether Georgia residence is accepted for this family profile.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Global area-of-cover options likely exist, but worldwide excluding US configuration was not directly verified from the public landing page in this run.
    - Unresolved: Need quote-flow or policy guide review to verify whether Georgia residence is accepted for this family profile.

## Allianz Care — Care International Health Insurance Plans

- Candidate ID: `allianz_care`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Official public page confirms long-stay international cover for people living abroad, but this run did not verify Georgia in the quote flow because the quote page was challenge-protected.
    - Evidence note: Allianz says its Care plans are for people working, studying, or living abroad for long periods and provides an official long-term quote path.
    - Unresolved: Georgia-resident eligibility remains unverified because the public quote flow was challenge-gated during this run.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Allianz publicly markets long-term international cover for people living abroad, but no captured source in this run named Germany directly.
    - Evidence note: The official public page frames Care plans as long-term international insurance for people living, working, or studying abroad.
    - Unresolved: Need brochure or table of benefits to verify coverage geography options relevant to Georgia and Germany.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Likely available through area-of-cover selections, but the extracted page did not directly confirm a worldwide excluding US option.
    - Unresolved: Need brochure or table of benefits to verify coverage geography options relevant to Georgia and Germany.

## Bupa Global — Private Health Insurance & Medical Insurance for Individuals and Families

- Candidate ID: `bupa_global`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Existing official-source evidence already captured Georgia and Germany in Bupa Global’s individuals-and-families selector.
    - Evidence note: Bupa Global's official individuals-and-families selector publicly lists Georgia and Germany in the residence-country dropdown.
    - Unresolved: Need full quote output or policy wording to verify available plans and final acceptance for Georgian residents.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Bupa Global’s individuals-and-families selector explicitly lists Germany in the residence-country dropdown.
    - Evidence note: Official Bupa selector evidence already captured Germany in the residence-country dropdown.
    - Unresolved: Need actual quote or brochure for Georgia-resident family to confirm available plans and area-of-cover options.
  - `worldwide_ex_us`: state=`not_verified_from_reviewed_sources`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_sources_did_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Evidence note: Area-of-cover configuration was not captured from the extracted page; likely quote-flow dependent.
    - Unresolved: Need actual quote or brochure for Georgia-resident family to confirm available plans and area-of-cover options.

## AXA Global Healthcare — International Health Insurance Plans

- Candidate ID: `axa_global_healthcare`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: AXA publicly documents area-of-cover logic and optional exclusion of the USA, but Georgia itself was not named in the captured public text.
    - Evidence note: AXA says customers can exclude the USA from their area of cover, which is relevant for worldwide-ex-US screening.
    - Unresolved: Need quote or policy wording to verify that Georgia falls within the relevant AXA area-of-cover choice for this family.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: AXA exposes area-of-cover mechanics and USA exclusion options, but Germany was not named directly in the captured public text.
    - Evidence note: AXA states treatment is covered anywhere within the selected region of cover and that customers can choose whether to include the USA.
    - Unresolved: Need quote-flow or brochure to verify exact geography bands including Georgia and Germany.
  - `worldwide_ex_us`: state=`indirectly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_non_us_global_configuration_is_only_indirectly_supported`
    - Summary: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Evidence note: Selected region-of-cover approach suggests a possible worldwide excluding US configuration, but this was not directly confirmed in the extracted text.
    - Unresolved: Need quote-flow or brochure to verify exact geography bands including Georgia and Germany.

## APRIL International — Long-term Expat International Health Insurance

- Candidate ID: `april_international`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: APRIL’s public page and entry flow explicitly expose Georgia in the country-of-coverage list.
    - Evidence note: APRIL's public page text includes a country-of-coverage list containing Georgia and Germany.
    - Unresolved: Need quote output or benefits guide to confirm which APRIL area-of-cover choice applies to Georgia-resident German citizens.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: APRIL’s public country-of-coverage examples explicitly include Germany.
    - Evidence note: APRIL’s extracted country selector examples include Germany by name.
    - Unresolved: Need full quote-flow or benefits guide to verify Georgia selection and geography bands.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: APRIL explicitly states worldwide cover depending on selected geographic area, but a specific worldwide excluding US option was not captured verbatim.
    - Unresolved: Need full quote-flow or benefits guide to verify Georgia selection and geography bands.

## William Russell — International Health Insurance

- Candidate ID: `william_russell`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: William Russell publicly shows region-based and worldwide cover examples, but Georgia was not named directly in captured text.
    - Evidence note: William Russell says customers can buy anything from worldwide cover to one-region cover.
    - Unresolved: Need quote or plan wording to test Georgia specifically.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: William Russell shows region-based and worldwide cover structures, but Germany was not named directly in the captured sources.
    - Evidence note: William Russell says customers can choose anything from worldwide cover to one-region cover.
    - Unresolved: Need quote results or policy wording to confirm Georgia residence acceptance.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Coverage-zone structure suggests configurable geography, but a worldwide excluding US option was not directly verified in this run.
    - Unresolved: Need quote results or policy wording to confirm Georgia residence acceptance.

## MSH International — First'Expat+ / International Health Insurance for Individuals

- Candidate ID: `msh_international`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: MSH positions First’Expat+ as international cover for mobility needs, but no Georgia-specific country evidence was visible in this run.
    - Evidence note: MSH markets itself as a healthcare partner for mobility needs and offers a quote path for international individual plans.
    - Unresolved: Georgia-resident eligibility still needs quote-flow or document confirmation.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: MSH positions the plan for international mobility, but the reviewed public evidence did not place Germany inside a named territory or selector.
    - Evidence note: MSH describes First’Expat+ as flexible international health insurance for mobility needs.
    - Unresolved: Need plan comparison output or PDF wording to confirm eligible residence/country combinations.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Global mobility orientation suggests possible geography options, but a worldwide excluding US option was not directly confirmed.
    - Unresolved: Need plan comparison output or PDF wording to confirm eligible residence/country combinations.

## Now Health International — SimpleCare / WorldCare International Health Insurance Plans

- Candidate ID: `now_health_international`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Now Health exposes international-plan geography mechanics tied to country of nationality or residence, but Georgia was not named directly in the captured page text.
    - Evidence note: The public comparison table repeatedly refers to repatriation or transportation to the country of nationality or residence.
    - Unresolved: Need member handbook or quote flow to verify whether Georgia is an accepted country of residence.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Now Health exposes country-of-residence mechanics relevant to Germany screening, but Germany was not named directly.
    - Evidence note: The public comparison table repeatedly refers to the country of nationality or residence in benefit wording.
    - Unresolved: Need member handbook or quote flow to verify geography bands and Georgian residency eligibility.
  - `worldwide_ex_us`: state=`indirectly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_non_us_global_configuration_is_only_indirectly_supported`
    - Summary: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Evidence note: The public table separately mentions USA elective treatment, which implies geography distinctions, but a precise worldwide excluding US option was not directly verified.
    - Unresolved: Need member handbook or quote flow to verify geography bands and Georgian residency eligibility.

## IMG (International Medical Group) — Global Medical international health insurance

- Candidate ID: `img_global`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: IMG’s public HTML includes Georgia and Germany in country-selection lists on both the main site and international health page.
    - Evidence note: IMG country lists publicly include Georgia and Germany, providing direct evidence that Georgia can be selected in IMG’s public web flow.
    - Unresolved: Need product-specific quote or plan wording to confirm Georgia-resident acceptance for full expat medical products, not just travel products.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: IMG’s public HTML includes Germany in country-selection lists on its public insurance pages.
    - Evidence note: Captured public HTML contains country options including Germany.
    - Unresolved: Need Global Medical plan docs or quote flow to verify country eligibility and area-of-cover choices.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Likely plan-area options exist, but the homepage extract did not directly confirm worldwide excluding US configuration.
    - Unresolved: Need Global Medical plan docs or quote flow to verify country eligibility and area-of-cover choices.

## Aetna International — Aetna International global healthcare entrypoint

- Candidate ID: `aetna_international`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Aetna International publicly shows worldwide telehealth or network reach, but no Georgia-specific country selector evidence was captured.
    - Evidence note: Aetna says members can access care anywhere, anytime through global solutions and worldwide telehealth services.
    - Unresolved: Need individual or family market evidence or quote path showing Georgia residence support.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Aetna International shows worldwide service reach, but no Germany-specific plan or selector evidence was captured.
    - Evidence note: Reviewed public evidence focused on global healthcare positioning and worldwide support.
    - Unresolved: Need pricing and underwriting evidence; Aetna may skew employer/group depending on market.
  - `worldwide_ex_us`: state=`indirectly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_non_us_global_configuration_is_only_indirectly_supported`
    - Summary: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Evidence note: The extracted page states 100% coverage for eligible medical care outside the U.S. for members, but plan design and resident eligibility for this family profile are still unclear.
    - Unresolved: Need pricing and underwriting evidence; Aetna may skew employer/group depending on market.

## Foyer Global Health — Expat Health Insurance / International Health Insurance comparison

- Candidate ID: `foyer_global_health`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Foyer publicly references Georgia in dedicated destination and health guides, which is useful country-specific discovery evidence even though it is not a formal eligibility table.
    - Evidence note: Foyer’s official site highlights Georgia among popular destination guides, including Georgia living, health, and budget guides.
    - Unresolved: Georgia guide presence does not prove plan eligibility or underwriting acceptance for Georgia residents.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Foyer’s official public footprint includes Germany destination content, which supports Germany relevance even though a Germany selector was not captured.
    - Evidence note: Official destination or footprint content surfaced Germany among public country references.
    - Unresolved: Need direct page extraction or brochure review for stronger evidence.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Search result language indicates worldwide/flexible expat positioning but did not verify a specific worldwide excluding US option.
    - Unresolved: Need direct page extraction or brochure review for stronger evidence.

## Henner — Individuals & Families international health solutions

- Candidate ID: `henner`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Henner shows individuals-and-families living-abroad positioning and a 240-country healthcare-professional network, but Georgia was not explicitly captured.
    - Evidence note: Henner says it developed personal-insurance expertise for people living abroad and supports members wherever they are.
    - Unresolved: Need product-specific quote or brochure evidence naming Georgia or clarifying country-of-residence eligibility.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Henner’s reviewed public evidence supported international-family positioning, but did not directly verify Germany.
    - Evidence note: Henner markets international health solutions for individuals and families.
    - Unresolved: Need product-specific public plan page, brochure, or broker evidence to identify exact retail plan names.
  - `worldwide_ex_us`: state=`not_verified_from_reviewed_sources`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_sources_did_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Evidence note: Not directly verified from the extracted page in this run.
    - Unresolved: Need product-specific public plan page, brochure, or broker evidence to identify exact retail plan names.

## Globality Health — YouGenio individual international health insurance plans

- Candidate ID: `globality_health`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Globality’s application flow uses a broad country list for future residency, but the extracted summary did not explicitly name Georgia even though the flow is country-driven.
    - Evidence note: The application requires country of future residency and country where the application is signed, using a broad country or territory list.
    - Unresolved: Need a live test of Georgia as future residency to determine whether the quote flow accepts or rejects it.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Globality surfaced Germany-relevant residency distinctions in related public evidence, but Germany coverage itself was not verified from the captured plan page.
    - Evidence note: Captured evidence noted a related public signal involving non-German expat distinctions on specific plan pages.
    - Unresolved: Need plan-page or brochure review to confirm residency/citizenship rules relevant to German citizens living in Georgia.
  - `worldwide_ex_us`: state=`not_verified_from_reviewed_sources`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_sources_did_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Evidence note: Not directly verified from the extracted home page in this run.
    - Unresolved: Need plan-page or brochure review to confirm residency/citizenship rules relevant to German citizens living in Georgia.

## Morgan Price International Healthcare — Evolution Health / Flexible Choices individual international healthcare plans

- Candidate ID: `morgan_price`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Morgan Price publishes unusually concrete residence rules and area-of-cover wording, but Georgia itself was not named in the captured public text.
    - Evidence note: Morgan Price states some products are available only where applicants’ primary residence is in specified countries or regions, proving residence-country rules are material.
    - Unresolved: Need Georgia-specific quote attempt or policy wording review to confirm whether Georgia residence fits available Morgan Price products.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Morgan Price exposes Europe/EU residency restrictions and home-country cover wording that are materially relevant to Germany.
    - Evidence note: Flexible Choices applicants must reside outside the EU or EEA, which directly matters for Germany screening.
    - Unresolved: Need policy wording review to see whether residents of Georgia are eligible.
  - `worldwide_ex_us`: state=`indirectly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_non_us_global_configuration_is_only_indirectly_supported`
    - Summary: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Evidence note: Strong evidence exists for a non-US configuration because the page explicitly mentions home-country cover excluding USA; broader worldwide geography still needs plan-document review.
    - Unresolved: Need policy wording review to see whether residents of Georgia are eligible.

## PassportCard — Expat Basic / Expat Comprehensive / Executive

- Candidate ID: `passportcard_global`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: PassportCard provides clear worldwide-ex-USA territory structure and Germany home-visit wording, but Georgia itself was not captured in public text.
    - Evidence note: PassportCard states contract currency is EUR for coverage worldwide excluding USA territories and USD for policies including the USA.
    - Unresolved: Need zone table or quote flow confirming whether Georgia sits in a supported destination zone.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: PassportCard is one of the strongest Germany-specific non-selector cases because the public source is Germany-facing and explicitly discusses treatment in Germany during home visits.
    - Evidence note: Reviewed public evidence came from PassportCard’s Germany-facing expat site.
    - Unresolved: Need quote or policy wording to confirm whether residents of Georgia are accepted.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: The page repeatedly says worldwide coverage, but a precise worldwide-excluding-US area of cover was not quoted verbatim from the extracted page.
    - Unresolved: Need quote or policy wording to confirm whether residents of Georgia are accepted.

## VUMI — Absolute VIP / Universal VIP / Special VIP / Access VIP product family

- Candidate ID: `vumi`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: VUMI markets worldwide coverage for individuals and families, but no Georgia-specific country evidence was captured.
    - Evidence note: VUMI’s homepage repeatedly says its international health insurance provides worldwide coverage.
    - Unresolved: Need plan brochure, application flow, or regional site to verify Georgia residence explicitly.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: VUMI shows broad worldwide positioning, but no captured source named Germany directly.
    - Evidence note: The reviewed public evidence describes VUMI’s plans as international or worldwide in scope.
    - Unresolved: Need a Europe-market quote or plan overview to verify Georgian residency acceptance and practical availability for this family.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: Worldwide coverage is explicit, but no extracted wording confirmed an exclude-US area-of-cover option in this run.
    - Unresolved: Need a Europe-market quote or plan overview to verify Georgian residency acceptance and practical availability for this family.

## Integra Global — yourLife / PremierLife / yourFamily / PremierFamily

- Candidate ID: `integra_global`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Integra Global clearly exposes worldwide including or excluding US+Canada cover options, but Georgia itself was not named.
    - Evidence note: Integra says members can choose worldwide coverage including the US and Canada or worldwide coverage excluding the US and Canada.
    - Unresolved: Need quote or eligibility wording to verify Georgia-resident acceptance.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Integra’s reviewed broker-backed evidence points to selectable geography structures, but Germany was not directly named.
    - Evidence note: The captured public evidence supports region or area-of-cover logic rather than a single-country-only product.
    - Unresolved: Need official plan wording or quote output to confirm eligibility for German citizens resident in Georgia.
  - `worldwide_ex_us`: state=`explicitly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_configuration_exists_but_family_specific_terms_remain_unverified`
    - Summary: At least one reviewed source used explicit outside-U.S./worldwide-excluding-U.S. wording for this candidate.
    - Evidence note: Strongly supported by broker evidence because the page explicitly lists worldwide excluding the U.S., Canada, and their territories as a coverage area.
    - Unresolved: Need official plan wording or quote output to confirm eligibility for German citizens resident in Georgia.

## MediHelp International — My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal

- Candidate ID: `medihelp_international`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: MediHelp describes My Global Health as international coverage with worldwide treatment choice, but Georgia was not named directly.
    - Evidence note: MediHelp says its individual plans provide international coverage and top medical services worldwide.
    - Unresolved: Need brochure or application evidence naming accepted residence countries including or excluding Georgia.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: MediHelp’s reviewed public evidence supports global positioning but did not name Germany directly.
    - Evidence note: The public plan family is positioned as global health cover for internationally mobile customers.
    - Unresolved: Need underwriting territory and residence-country rules for Georgia.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: The page states international coverage and top medical services worldwide, but it does not explicitly describe a worldwide-excluding-US option.
    - Unresolved: Need underwriting territory and residence-country rules for Georgia.

## ALC Health — Global Prima Medical Insurance / Flying Colours legacy entrypoints

- Candidate ID: `alc_health`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: ALC Health shows strong global-country marketing and a Germany regional page, but Georgia itself was not surfaced in captured text.
    - Evidence note: ALC says it provides global cover across the world and explicitly references multiple countries plus a Germany regional page.
    - Unresolved: Need Georgia-specific country page, quote result, or broker confirmation.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: ALC Health is Germany-relevant because the captured public evidence includes a Germany regional page and Germany in named-country marketing.
    - Evidence note: ALC publicly references a Germany page in its regional marketing footprint.
    - Unresolved: Need current policy documents because the landing page mixes legacy servicing information with new-business routing.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: The page says worldwide/global coverage in broad terms, but no extracted wording confirmed an exclude-US configuration.
    - Unresolved: Need current policy documents because the landing page mixes legacy servicing information with new-business routing.

## Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) — Worldwide Premier / Outside U.S. / Outside U.S. Select

- Candidate ID: `geoblue_xplorer_bcbs_global_solutions`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: GeoBlue’s living-abroad page gives a clear Outside-the-U.S. plan structure, but Georgia was not named directly in the captured text.
    - Evidence note: The page offers an Outside the U.S. plan for people who do not need U.S. coverage, which is directly relevant to worldwide-ex-US screening.
    - Unresolved: Need eligibility wording clarifying whether Georgia residents can buy the individual or family plan.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: GeoBlue/BCBS Global Solutions publicly verifies outside-U.S. plan structures, but Germany was not named directly in the captured evidence.
    - Evidence note: The reviewed plan family explicitly includes outside-U.S. products.
    - Unresolved: This product may be more suitable for U.S. citizens abroad or people with U.S. coverage links than for a German family resident in Georgia.
  - `worldwide_ex_us`: state=`explicitly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_configuration_exists_but_family_specific_terms_remain_unverified`
    - Summary: At least one reviewed source used explicit outside-U.S./worldwide-excluding-U.S. wording for this candidate.
    - Evidence note: Strong broker evidence exists because the Outside U.S. plan is described as similar to the premier plan but without U.S. coverage.
    - Unresolved: This product may be more suitable for U.S. citizens abroad or people with U.S. coverage links than for a German family resident in Georgia.

## SafetyWing — Nomad Insurance Complete

- Candidate ID: `safetywing_nomad_complete`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: SafetyWing positions Nomad Insurance Complete for expats, digital nomads, and families with global provider access, but Georgia was not named.
    - Evidence note: SafetyWing says Complete offers global coverage and lets members use any licensed provider at any clinic or hospital, private or public.
    - Unresolved: Need policy wording or quote-input testing to determine whether Georgia residence is accepted.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: SafetyWing markets global family health insurance with licensed-provider access worldwide, but Germany was not named directly.
    - Evidence note: The captured page positions Nomad Insurance Complete as global health insurance for families living abroad.
    - Unresolved: Need policy wording or quote results to verify residence-country acceptance for a German family living in Georgia.
  - `worldwide_ex_us`: state=`indirectly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_non_us_global_configuration_is_only_indirectly_supported`
    - Summary: Reviewed evidence strongly suggests a non-U.S. global option exists, but the exact worldwide-excluding-U.S. wording was not captured verbatim.
    - Evidence note: Strong discovery evidence exists because the page presents global coverage and separately prices a Hong Kong, Singapore, and US add-on, implying a base configuration that is not full USA-inclusive.
    - Unresolved: Need policy wording or quote results to verify residence-country acceptance for a German family living in Georgia.

## Genki — Native / international health insurance

- Candidate ID: `genki_native`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Genki’s consultation flow explicitly lists Georgia and Germany and markets worldwide cover from any country.
    - Evidence note: Genki’s public consultation flow contains Georgia and Germany in its country options.
    - Unresolved: Need product-specific terms to confirm family fit and whether long-term Georgia residence is acceptable for this exact plan.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Genki’s public consultation flow explicitly lists Germany in country options and pairs that with join-from-anywhere messaging.
    - Evidence note: Genki’s public options list includes Germany by name.
    - Unresolved: Need the specific Native/Resident product page or policy wording to verify whether this is a full expat-medical fit for a Germany/Georgia family rather than a lighter nomad-oriented product.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: The homepage states worldwide treatment access at every licensed provider, but it does not explicitly spell out a worldwide-excluding-US coverage area.
    - Unresolved: Need the specific Native/Resident product page or policy wording to verify whether this is a full expat-medical fit for a Germany/Georgia family rather than a lighter nomad-oriented product.

## ACS — ACS Expat

- Candidate ID: `acs_expat`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: ACS Expat is publicly framed as worldwide expatriate health insurance with trips outside the selected coverage area, but Georgia was not named directly.
    - Evidence note: ACS Expat says it covers international stays of 12+ months with worldwide healthcare access and support.
    - Unresolved: Need policy wording or quote-flow testing to verify whether Georgia is within the selectable or accepted residence geography.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: ACS does not name Germany directly, but its public wording about home-country visit cover is materially relevant to Germany for this family.
    - Evidence note: ACS states home-country visit coverage applies if the home country is included in the selected geographical zone.
    - Unresolved: Need brochure/table-of-benefits review to confirm Georgia eligibility and the exact geography zone that would include Germany and Georgia.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: ACS uses geographical zones and temporary worldwide trip language, but a verbatim worldwide-excluding-US option was not captured from the extracted page.
    - Unresolved: Need brochure/table-of-benefits review to confirm Georgia eligibility and the exact geography zone that would include Germany and Georgia.

## HCI Group — Health Protect / Protector Plans / Nimbl Health

- Candidate ID: `hci_group_health_protect`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: Health Protect is clearly an IPMI product for people living abroad, but Georgia was not named in the captured page text.
    - Evidence note: HCI says Health Protect serves expatriates, digital nomads, international students, and holiday-home owners living abroad or traveling frequently.
    - Unresolved: Need quote-flow or PDF review to verify Georgia residence rules.
  - `germany`: state=`unknown`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
    - Summary: HCI Group’s reviewed public evidence supports international coverage positioning, but no Germany-specific evidence was captured.
    - Evidence note: The reviewed public page supports international/private medical positioning.
    - Unresolved: Need quote or plan-PDF review to verify whether residents of Georgia are eligible and how Germany fits into geography rules.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: The extracted page confirms international IPMI for people living abroad, but no explicit worldwide-excluding-US wording was captured.
    - Unresolved: Need quote or plan-PDF review to verify whether residents of Georgia are eligible and how Germany fits into geography rules.

## Expatriate Group — International Health Insurance / Select / Primary+ / Primary+ Lite / Primary

- Candidate ID: `expatriate_group_global_health`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Expatriate Group’s public quote flow clearly defines Area 1, 2, and 3 geography, and Georgia is plausibly within Area 1’s Europe–Middle East–Africa–Asia–Oceania band, but Georgia was not named explicitly.
    - Evidence note: The quote form lists Area 1 as Europe, Middle East, Africa, Asia and Oceania excluding China, Hong Kong and Singapore, Area 2 as Worldwide excluding the USA, and Area 3 as Worldwide.
    - Unresolved: Georgia is inferred as falling within Area 1 geography but was not named directly; plan wording should confirm this.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Expatriate Group’s quote structure includes a Europe-centered area option, which makes Germany a strong regional inference even without direct naming.
    - Evidence note: The captured quote process includes an area option centered on Europe and adjacent regions.
    - Unresolved: Need quote-form output or policy wording to confirm Georgian residency acceptance for this family profile.
  - `worldwide_ex_us`: state=`explicitly_indicated`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_configuration_exists_but_family_specific_terms_remain_unverified`
    - Summary: At least one reviewed source used explicit outside-U.S./worldwide-excluding-U.S. wording for this candidate.
    - Evidence note: Strong official evidence exists because the quote flow publicly lists Area 2 as worldwide excluding the USA.
    - Unresolved: Need quote-form output or policy wording to confirm Georgian residency acceptance for this family profile.

## WellAway — EXPAT / Gold / Diamond / Expat Plus

- Candidate ID: `wellaway_expat`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: Wellaway’s EXPAT quote flow explicitly lists Georgia and Germany in country selections.
    - Evidence note: The public quote Step 1 HTML includes Georgia and Germany in the country list.
    - Unresolved: Need completed quote or brochure review to verify whether Georgia residence is fully accepted after underwriting.
  - `germany`: state=`confirmed`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`partial_because_country_presence_does_not_equal_final_family_fit`
    - Summary: WellAway’s public quote form explicitly lists Germany in the country options.
    - Evidence note: The public quote Step 1 HTML includes Germany in the country list.
    - Unresolved: Need brochure or quote-flow review to determine whether non-US area-of-cover options exist and whether Georgia residence is accepted.
  - `worldwide_ex_us`: state=`not_verified_from_reviewed_sources`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_reviewed_sources_did_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence did not verify enough geography detail to support a worldwide ex-U.S. indication.
    - Evidence note: The extracted page does not directly confirm a worldwide-excluding-US option; it prominently mentions USA coverage and U.S. provider access, so geography structure needs follow-up.
    - Unresolved: Need brochure or quote-flow review to determine whether non-US area-of-cover options exist and whether Georgia residence is accepted.

## Securus International — Global Health Cover

- Candidate ID: `securus_global_health_cover`
- Partial or ambiguous coverage details present: `true`
- Partial or ambiguous dimensions: `georgia, germany, worldwide_ex_us`
- Dimension details:
  - `georgia`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Securus publicly markets global health cover with treatment choice around the world, but Georgia-specific evidence was not captured.
    - Evidence note: Securus says global health cover lets customers choose who, how, and where they are treated around the world.
    - Unresolved: Need benefit-table or application review to verify whether Georgia is accepted as residence or treatment geography.
  - `germany`: state=`inferred`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_country_fit_is_inferred_not_directly_named`
    - Summary: Securus uses global coverage wording, but the reviewed public evidence did not name Germany directly.
    - Evidence note: The public product positioning supports globally oriented health cover.
    - Unresolved: Need the published benefit tables and membership guide reviewed to confirm geographic eligibility and whether the plans suit a German citizen family resident in Georgia.
  - `worldwide_ex_us`: state=`worldwide_positioned_but_ex_us_not_verified`; partial_or_ambiguous=`true`; explicit_uncertainty_flag=`ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
    - Summary: Reviewed evidence supports global/wide geography positioning, but not a verified ex-U.S. configuration.
    - Evidence note: The page supports global access to hospitals and doctors around the world, but it does not explicitly state a worldwide-excluding-US option.
    - Unresolved: Need the published benefit tables and membership guide reviewed to confirm geographic eligibility and whether the plans suit a German citizen family resident in Georgia.

