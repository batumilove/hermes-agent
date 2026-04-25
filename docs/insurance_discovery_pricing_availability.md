# Insurance discovery pricing availability

Discovery-only normalization of how pricing was visible for each candidate insurer/plan during this run.

Summary counts:
- partially_visible: 7
- public_pricing_available: 2
- quote_flow_gated: 13
- unavailable_during_discovery: 5
- records_without_directly_usable_public_price: 25

Primary pricing-transparency friction counts:
- blocked_public_pricing: 3
- brochure_or_marketing_only_no_price: 3
- broker_or_intermediary_only_public_path: 2
- contact_or_account_gated_pricing: 3
- eligibility_gated_results: 7
- failed_or_abandoned_quote_retrieval: 2
- incomplete_quote_output: 5
- public_numeric_pricing_visible: 2

Legend:
- public_pricing_available: A reviewed public source displayed a numeric premium, from-price, or calculator output during discovery without requiring a completed quote, broker contact, or login.
- quote_flow_gated: Public evidence indicates pricing is available through a quote or application flow, but no numeric premium was visible in the reviewed discovery extract.
- partially_visible: Public evidence exposed some pricing signal such as discounts, premium mechanics, rate-related UI, or price-result placeholders, but not a usable numeric premium for discovery.
- unavailable_during_discovery: No usable pricing was visible in reviewed public evidence during discovery because pricing was absent, blocked, challenge-gated, brochure-only, or otherwise not observable.

Pricing transparency friction legend:
- public_numeric_pricing_visible: A usable numeric premium or from-price was visible in reviewed public evidence during discovery.
- blocked_public_pricing: A public pricing or quote path existed but was blocked by a challenge wall, login wall, reCAPTCHA, or similar access barrier before usable pricing was visible.
- incomplete_quote_output: The reviewed public source showed pricing-related UI, placeholders, price-result fields, or quote CTAs, but the run did not obtain a usable numeric premium from that path.
- eligibility_gated_results: Pricing appears to depend on passing upfront eligibility or household-profile steps such as residency, duration, nationality, age, or dependent composition before any result is visible.
- failed_or_abandoned_quote_retrieval: A quote path appeared to exist, but retrieval did not reach usable pricing because the observed path failed technically, redirected away, depended on an unverified successor flow, or was otherwise abandoned during discovery.
- contact_or_account_gated_pricing: Pricing appears to require account creation, login, or contact submission before any usable quote result is shown.
- broker_or_intermediary_only_public_path: The reviewed public path was broker-led or intermediary-led rather than a verified insurer-controlled pricing path, so direct public pricing transparency remained unresolved.
- brochure_or_marketing_only_no_price: Reviewed public materials were brochure-led or marketing-led and did not expose a public pricing path with usable numeric premiums.

| candidate_id | insurer | plan | pricing_availability_status | primary_pricing_transparency_friction | friction_summary | legacy_public_pricing_status |
| --- | --- | --- | --- | --- | --- | --- |
| cigna_global | Cigna Global | International Health Insurance / Global Medical Cover | quote_flow_gated | incomplete_quote_output | Pricing is pushed into a quote flow and no usable numeric premium was visible from the reviewed public entrypoint. | no_public_pricing_found |
| allianz_care | Allianz Care | Care International Health Insurance Plans | partially_visible | blocked_public_pricing | A public promotion is visible, but the quote path itself was challenge-gated before any usable premium appeared. | no_public_pricing_found |
| bupa_global | Bupa Global | Private Health Insurance & Medical Insurance for Individuals and Families | quote_flow_gated | eligibility_gated_results | The public flow starts with residence-country selection and did not expose a usable premium in the reviewed extract. | no_public_pricing_found |
| axa_global_healthcare | AXA Global Healthcare | International Health Insurance Plans | partially_visible | eligibility_gated_results | Pricing depends on passing cover-length and product-type screening, while the reviewed public page only exposed premium mechanics and discounts. | no_public_pricing_found |
| april_international | APRIL International | Long-term Expat International Health Insurance | quote_flow_gated | eligibility_gated_results | APRIL exposes a plan-finder path with country and cover-type selection, but no usable premium was visible during discovery. | no_public_pricing_found |
| william_russell | William Russell | International Health Insurance | quote_flow_gated | incomplete_quote_output | The site says prices are shown in the online quote, but the reviewed public evidence did not include an actual numeric result. | quote_flow_pricing_only |
| msh_international | MSH International | First'Expat+ / International Health Insurance for Individuals | quote_flow_gated | eligibility_gated_results | The public quote path starts with profile and region selection, but no usable premium was captured in the reviewed extract. | no_public_pricing_found |
| now_health_international | Now Health International | SimpleCare / WorldCare International Health Insurance Plans | partially_visible | incomplete_quote_output | Public plan-comparison materials show premium mechanics and benefit structure, but not a usable numeric premium. | no_public_pricing_found |
| img_global | IMG (International Medical Group) | Global Medical international health insurance | partially_visible | incomplete_quote_output | Per-plan price entrypoints were visible, but the reviewed public extract did not expose the actual numeric premiums. | quote_flow_pricing_only |
| aetna_international | Aetna International | Aetna International global healthcare entrypoint | unavailable_during_discovery | brochure_or_marketing_only_no_price | The reviewed Aetna path was audience-routing marketing without a verified individual-family pricing path or usable public premium. | no_public_pricing_found |
| foyer_global_health | Foyer Global Health | Expat Health Insurance / International Health Insurance comparison | unavailable_during_discovery | blocked_public_pricing | The public quick-quote/application path was blocked by reCAPTCHA before pricing could be observed. | no_public_pricing_found |
| henner | Henner | Individuals & Families international health solutions | unavailable_during_discovery | brochure_or_marketing_only_no_price | The reviewed public materials were informational only and did not expose usable public pricing. | no_public_pricing_found |
| globality_health | Globality Health | YouGenio individual international health insurance plans | quote_flow_gated | eligibility_gated_results | Quote results appear to depend on nationality, future residency, location, and household steps before any usable premium is visible. | quote_flow_pricing_only |
| morgan_price | Morgan Price International Healthcare | Evolution Health / Flexible Choices individual international healthcare plans | quote_flow_gated | eligibility_gated_results | The quick-quote path asks for household and eligibility data and can fall back to manual contact capture instead of immediate public pricing. | no_public_pricing_found |
| passportcard_global | PassportCard | Expat Basic / Expat Comprehensive / Executive | public_pricing_available | public_numeric_pricing_visible | Illustrative monthly example pricing was visible on the public insurer page. | public_pricing_available |
| vumi | VUMI | Absolute VIP / Universal VIP / Special VIP / Access VIP product family | partially_visible | incomplete_quote_output | The public site exposed rate-comparison navigation and plan maxima, but no usable premium was captured. | no_public_pricing_found |
| integra_global | Integra Global | yourLife / PremierLife / yourFamily / PremierFamily | quote_flow_gated | broker_or_intermediary_only_public_path | Discovery only verified a broker-led path with quote CTAs rather than a direct insurer pricing path or usable public premium. | no_public_pricing_found |
| medihelp_international | MediHelp International | My Global Health individual plans: Blue / Azure / Cobalt / Admiral / Royal | quote_flow_gated | blocked_public_pricing | Public quote and buy-online entrypoints were visible, but downstream pricing remained inaccessible in the reviewed run because the deeper path was not captured and access friction was documented separately. | no_public_pricing_found |
| alc_health | ALC Health | Global Prima Medical Insurance / Flying Colours legacy entrypoints | quote_flow_gated | failed_or_abandoned_quote_retrieval | Legacy ALC quote messaging redirects new business to IMG, leaving standalone current pricing retrieval unresolved in this run. | no_public_pricing_found |
| geoblue_xplorer_bcbs_global_solutions | Blue Cross Blue Shield Global Solutions (formerly GeoBlue Xplorer) | Worldwide Premier / Outside U.S. / Outside U.S. Select | partially_visible | broker_or_intermediary_only_public_path | The reviewed evidence was broker-led and only exposed relative premium positioning, not a verified insurer-controlled public price. | no_public_pricing_found |
| safetywing_nomad_complete | SafetyWing | Nomad Insurance Complete | public_pricing_available | public_numeric_pricing_visible | A public calculator displayed a numeric monthly premium without requiring a completed personalized quote. | public_pricing_available |
| genki_native | Genki | Native / international health insurance | quote_flow_gated | eligibility_gated_results | Instant pricing is advertised, but the recommendation flow first screens for trip duration, destination, and home-country healthcare access. | quote_flow_pricing_only |
| acs_expat | ACS | ACS Expat | unavailable_during_discovery | contact_or_account_gated_pricing | Pricing is tailored to country of residence and the reviewed quote path collects family composition and contact details before advisor follow-up. | no_public_pricing_found |
| hci_group_health_protect | HCI Group | Health Protect / Protector Plans / Nimbl Health | quote_flow_gated | failed_or_abandoned_quote_retrieval | The external quote tool showed a technical barrier instead of usable quote questions or pricing. | no_public_pricing_found |
| expatriate_group_global_health | Expatriate Group | International Health Insurance / Select / Primary+ / Primary+ Lite / Primary | partially_visible | contact_or_account_gated_pricing | The quote flow includes a premium placeholder but requires household data and email before a usable result was observed. | quote_flow_pricing_only |
| wellaway_expat | WellAway | EXPAT / Gold / Diamond / Expat Plus | quote_flow_gated | contact_or_account_gated_pricing | The public quote path collects identity, family, and contact fields at the basics step before any plan pricing is shown. | no_public_pricing_found |
| securus_global_health_cover | Securus International | Global Health Cover | unavailable_during_discovery | brochure_or_marketing_only_no_price | Reviewed public evidence exposed brochures and marketing materials but no usable numeric premium or quote result. | no_public_pricing_found |

## Priority shortlist: family pricing visibility outcomes

| candidate_id | insurer | family_pricing_visibility_status | quote/application journey outcome | next public step |
| --- | --- | --- | --- | --- |
| cigna_global | Cigna Global | unavailable | Public entry page advertised a two-minute quote but did not expose any family pricing or visible quote result in the reviewed extract. | Run the official quote flow through household and eligibility steps to see whether family pricing appears after residence and member inputs. |
| bupa_global | Bupa Global | unavailable | The journey demonstrated that Georgia and Germany can be selected, but the reviewed extract never reached a family-price screen. | Continue the official selector through household-profile steps to determine whether a family premium is returned or whether additional gating appears. |
| april_international | APRIL International | unavailable | The reviewed path confirmed quote intent and plan selection steps only; it did not surface a family premium. | Continue the official plan finder with residency and cover inputs to confirm whether household pricing appears before contact/account gating. |
| allianz_care | Allianz Care | partially_shown | Public marketing page showed a 10% promotion; the quote URL itself returned a challenge page before any family or household premium could be seen. | Clear the challenge gate or use another verified public session to continue into the quote flow and test whether family pricing becomes visible after profile inputs. |
| axa_global_healthcare | AXA Global Healthcare | partially_shown | The reviewed public page explained how price can vary, yet no household-specific premium or family total was displayed. | Advance through AXA cover-length and product screening to see whether a family quote appears after eligibility inputs. |
| william_russell | William Russell | unavailable | Pricing appears to live inside the quote tool, yet this run did not reach a visible family-of-four output. | Open the online quote tool and enter the household profile to test whether family pricing is shown without contact submission. |
| now_health_international | Now Health International | partially_shown | The reviewed materials showed benefit and premium-structure signals only, not a numeric family quote. | Find a live quote or application path and test household inputs to determine whether family pricing is generated publicly. |
| foyer_global_health | Foyer Global Health | unavailable | The quote journey could not progress past the challenge screen, so no household pricing visibility was verified. | Use a verified session that can pass reCAPTCHA, then retest whether Foyer shows family pricing after household inputs. |
| safetywing_nomad_complete | SafetyWing | partially_shown | The reviewed calculator exposed a usable public price signal, yet not a personalized family total for the German-in-Georgia household. | Test whether the public calculator or downstream flow can add spouse and children without forcing account creation before family pricing appears. |
| expatriate_group_global_health | Expatriate Group | gated_behind_contact_details | A quote-result scaffold exists, including a Selected Package Premium field, but the reviewed journey did not reveal a numeric family result before deeper input requirements. | Advance the official quote flow with household details and verify whether email or other contact submission is mandatory before the premium field populates. |
| img_global | IMG (International Medical Group) | partially_shown | The journey made pricing entrypoints visible without surfacing a household or family-of-four result. | Open the See Price flow for the relevant IMG Global Medical plan and test whether family pricing appears after household inputs. |
| genki_native | Genki | unavailable | The observed journey stopped at pricing promises and product detail, without a visible household quote result. | Run the questionnaire far enough to see whether the flow returns a family price before any account or contact gate. |
| wellaway_expat | WellAway | gated_behind_contact_details | The official portal reached Step 1 basics, but pricing was still hidden behind identity and family-input collection. | Continue the public quote portal until it becomes clear whether phone/email/contact submission is required before plan pricing is displayed. |
| passportcard_global | PassportCard | partially_shown | The official page exposed public from-prices for sample assumptions only; no personalized family pricing flow was verified. | Find a verified public quote path or zone-specific calculator that accepts household composition to test whether family pricing is available. |
