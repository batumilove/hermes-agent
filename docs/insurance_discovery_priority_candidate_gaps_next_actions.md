# Insurance discovery priority candidate gaps and next actions

Sources used only from the local worktree:
- `data/insurance_discovery/priority_candidate_gaps_next_actions.discovery.json`
- `data/insurance_discovery/completion_evidence_hardening_ledger.discovery.json`
- `data/insurance_discovery/priority_candidate_completion_audit.discovery.json`
- `docs/insurance_discovery_priority_completion_report.md`

Scope guardrails:
- This document packages only existing local artifacts under `data/insurance_discovery/` and `docs/`.
- It does not add live web research, quote-flow reruns, account creation, personal-data submission, ranking, value scoring, dashboard output, or purchase advice.
- It preserves uncertainty, blocker states, and follow-up gates exactly as recorded in the existing completion ledger and audit.
- The scope remains exactly the 14 priority candidates already present in `completion_evidence_hardening_ledger.discovery.json`.

## Packaging summary

- Priority candidates covered: 14
- Source artifact status: packaged from the existing gaps-and-next-actions JSON only
- Shared unresolved themes across the full set: Georgia-residence confirmation, Germany/home-country treatment wording, worldwide-excluding-U.S. wording, family-specific quote visibility, pricing visibility, public policy-document access, and underwriting/pre-existing-condition clarity
- Shared friction signals across the full set:
  - `country_or_residence_gating`: 14 of 14
  - `policy_wording_not_public`: 14 of 14
  - `challenge_or_bot_protection`: 1 of 14, limited to Allianz Care

## Follow-up mode distribution from the packaged artifact

- `public_web_review_or_public_quote_flow_continuation`: 8 candidates
  - Cigna Global
  - Bupa Global
  - APRIL International
  - AXA Global Healthcare
  - William Russell
  - Foyer Global Health
  - IMG (International Medical Group)
  - Genki
- `public_web_review_retry_after_challenge_or_js_block`: 1 candidate
  - Allianz Care
- `broker_or_sales_follow_up_required`: 2 candidates
  - Now Health International
  - PassportCard
- `account_or_login_required_before_family_specific_follow_up`: 1 candidate
  - SafetyWing
- `contact_submission_required_before_pricing_or_plan_result`: 2 candidates
  - Expatriate Group
  - WellAway

## Candidate-by-candidate gaps and next actions

### 1. Cigna Global
- Audit snapshot: pricing `quote_flow_gated`; family pricing `unavailable`; Georgia `confirmed`; Germany `confirmed`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Georgia selector visibility does not prove final issuance for a German family resident in Georgia
  - Germany/home-country treatment still needs official post-issue wording
  - Worldwide-excluding-U.S. wording was not verified from reviewed public sources
  - Family quote flow stopped at entry/selector stage
  - No decisive public policy document, pricing visibility, or underwriting detail was captured
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: continue the official quote beyond the country selector to see whether the Georgia-resident German family can reach plan output and whether Germany remains selectable or covered
  - High: search public quote results, brochure links, and plan pages for official wording on Germany treatment or home-country care
  - Medium: if worldwide-excluding-U.S. options or premiums appear later, record the exact field or gate rather than infer availability

### 2. Bupa Global
- Audit snapshot: pricing `quote_flow_gated`; family pricing `unavailable`; Georgia `confirmed`; Germany `confirmed`; worldwide excluding U.S. `not_verified_from_reviewed_sources`
- Main unresolved gaps:
  - Georgia presence in the selector still does not close family-issueability
  - Germany/home-country treatment still needs official wording tied to a Georgia-resident policy
  - Worldwide-excluding-U.S. wording remains unverified
  - Family quote output was not reached beyond selector entry
  - No decisive public plan document, pricing output, or underwriting evidence was captured
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: continue past “See plans” to verify whether a family-specific plan display or quote result appears
  - High: check public plan pages and downstream result pages for brochures, membership guides, or benefits schedules
  - Medium: if geography or premium visibility appears only deeper in the flow, preserve the exact blocker or missing step

### 3. APRIL International
- Audit snapshot: pricing `quote_flow_gated`; family pricing `unavailable`; Georgia `confirmed`; Germany `confirmed`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Georgia and Germany are visible, but final family-fit proof still depends on deeper quote or document evidence
  - Worldwide-excluding-U.S. wording was not verified
  - Family flow stopped at cover-type / country / language stages rather than a family-specific result
  - No decisive public plan document, usable pricing, or underwriting detail was captured
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: continue the public plan finder until the Georgia-resident two-adult, two-child household reaches a quote, recommendation, or exclusion message
  - High: look for APRIL-controlled brochures, tables of benefits, or policy wording linked from the plan finder or downstream pages
  - Medium: if APRIL requires advisor handoff, account access, or contact capture before output, record that public gate explicitly and stop there

### 4. Allianz Care
- Audit snapshot: pricing `partially_visible`; family pricing `partially_shown`; Georgia `unknown`; Germany `unknown`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Georgia-resident eligibility remains unverified because the public flow was blocked before a usable family result
  - Germany/home-country treatment still needs brochure or policy wording support
  - Worldwide-excluding-U.S. wording remains unresolved
  - The family quote flow hit a challenge or JS block before result visibility
  - No decisive public policy document or underwriting detail was packaged from reviewed sources
- Required follow-up mode: `public_web_review_retry_after_challenge_or_js_block`
- Next actions from the packaged artifact:
  - High: retry the public quote entry in a clean session only to confirm whether the challenge wall is persistent and where quote access stops
  - High: search Allianz Care’s product pages and document library for public plan guides, tables of benefits, or policy wording
  - Medium: if the challenge keeps blocking quote details, preserve that as the unresolved dependency and stop rather than infer a result

### 5. AXA Global Healthcare
- Audit snapshot: pricing `partially_visible`; family pricing `partially_shown`; Georgia `inferred`; Germany `inferred`; worldwide excluding U.S. `indirectly_indicated`
- Main unresolved gaps:
  - Georgia and Germany evidence remain inferred rather than fully confirmed for the target family profile
  - Home-country treatment still needs official brochure or policy wording
  - Worldwide-excluding-U.S. is only indirectly indicated, not decisively verified
  - Family quote flow shows partial plan options but not a hardened family-specific endpoint
  - Public policy-document and underwriting details remain incomplete
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: proceed through AXA’s long-term international-healthcare routing to test Georgia residence, Germany treatment, and family-of-four support downstream
  - High: capture brochure, membership-guide, or benefits wording that names Germany or explains treatment outside the residence country
  - Medium: capture deeper quote or document evidence for the exact worldwide-excluding-U.S. option and note any gate before price output

### 6. William Russell
- Audit snapshot: pricing `quote_flow_gated`; family pricing `unavailable`; Georgia `inferred`; Germany `inferred`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Georgia and Germany remain inferred rather than confirmed in the hardened package
  - Worldwide-excluding-U.S. wording still needs explicit support
  - No quote result was visible in the reviewed public flow
  - Policy-document access, pricing visibility, and underwriting detail remain incomplete
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: follow the online quote tool until family inputs or quote output appear so Georgia residence can be tested in the actual journey
  - High: look for a brochure, table of benefits, or policy wording that names coverage zones and Germany/home-country use rules
  - Medium: if the tool redirects or withholds pricing behind contact details, record that blocker rather than treat the plan as anonymously quotable

### 7. Now Health International
- Audit snapshot: pricing `partially_visible`; family pricing `partially_shown`; Georgia `unknown`; Germany `unknown`; worldwide excluding U.S. `indirectly_indicated`
- Main unresolved gaps:
  - Georgia residence and Germany treatment remain unknown in the packaged audit snapshot
  - Worldwide-excluding-U.S. is only indirectly indicated
  - No public family quote result was verified in this pass
  - Policy-document access, final pricing clarity, and underwriting detail remain incomplete
- Required follow-up mode: `broker_or_sales_follow_up_required`
- Next actions from the packaged artifact:
  - High: locate the official quote-start or sales-contact route needed to move from public comparison pages into insurer-controlled intake
  - High: use public plan-comparison pages and document-library references to obtain brochure or policy wording for Georgia and Germany/home-country treatment rules
  - Medium: if pricing or eligibility can only be clarified through sales, broker follow-up, login, or a non-public portal, record that dependency explicitly

### 8. Foyer Global Health
- Audit snapshot: pricing `unavailable_during_discovery`; family pricing `unavailable`; Georgia `inferred`; Germany `inferred`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Georgia and Germany remain inferred rather than confirmed
  - Worldwide-excluding-U.S. wording still was not verified
  - Family-specific quote output was not reached
  - Pricing was unavailable during discovery
  - The package still needs stronger document-backed evidence for Germany treatment, geography, and underwriting
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: use the public quote path to determine whether a Georgia-resident German family of four can reach plan output
  - High: review the public brochure or other accessible documents already found and capture sections that decide Germany treatment, home-country use, and worldwide geography
  - Medium: if deeper quote or application steps ask for contact details before price, preserve that gate as the unresolved next step

### 9. SafetyWing
- Audit snapshot: pricing `public_pricing_available`; family pricing `partially_shown`; Georgia `inferred`; Germany `inferred`; worldwide excluding U.S. `indirectly_indicated`
- Main unresolved gaps:
  - Public prices exist, but the family-specific path is blocked before anonymous household pricing
  - Georgia residence and Germany treatment still need policy-wording or deeper quote confirmation
  - Worldwide-excluding-U.S. mechanics remain indirect rather than decisively verified
  - The follow-up path is gated by login/account requirements before family-specific results
- Required follow-up mode: `account_or_login_required_before_family_specific_follow_up`
- Next actions from the packaged artifact:
  - High: use the already captured public policy PDF to test Georgia residence, Germany treatment, and USA-exclusion mechanics for the family product
  - High: preserve the signup/login wall as the blocking dependency for family-specific quote or premium output
  - Medium: verify whether any public calculator, sample-price page, or brokerless pricing path exists outside signup; if none exists, record that absence explicitly

### 10. Expatriate Group
- Audit snapshot: pricing `partially_visible`; family pricing `gated_behind_contact_details`; Georgia `inferred`; Germany `inferred`; worldwide excluding U.S. `explicitly_indicated`
- Main unresolved gaps:
  - Georgia and Germany still rely on inferred rather than confirmed family-fit evidence
  - Worldwide-excluding-U.S. is the strongest in the set here, but Germany treatment and Georgia-resident acceptance still need wording confirmation
  - Family pricing is gated behind contact details
  - Public policy wording remains insufficiently decisive for final family-fit proof
- Required follow-up mode: `contact_submission_required_before_pricing_or_plan_result`
- Next actions from the packaged artifact:
  - High: record that the quote flow reaches family inputs and area-of-cover choices but requires an email before a quotation can be delivered
  - High: use the public brochure or plan documents already captured to verify whether the Georgia-resident family can use the Area 2 worldwide-excluding-USA option while retaining Germany treatment access
  - Medium: if no anonymous premium is visible without email, preserve that as the pricing blocker instead of estimating a family quote

### 11. IMG (International Medical Group)
- Audit snapshot: pricing `null`; family pricing `null`; Georgia `null`; Germany `null`; worldwide excluding U.S. `null`
- Main unresolved gaps:
  - The packaged gaps artifact still carries the normal seven evidence dimensions, but its audit snapshot fields are unpopulated in this JSON packaging layer
  - Georgia residence, Germany/home-country treatment, worldwide-excluding-U.S. wording, family quote visibility, policy-document use, pricing visibility, and underwriting details all remain open in the packaged record
  - The completion report still notes stronger local evidence elsewhere in the package, but this gaps artifact preserves unresolved next steps rather than resolving those fields here
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: follow IMG’s public “See Price” path far enough to verify whether a Georgia-resident German family of four can obtain plan-specific pricing or whether later gates appear
  - High: review public IMG brochures or certificates already linked to confirm Germany treatment, home-country use, and worldwide-excluding-U.S. availability for the Global Medical scenario
  - Medium: if only partial prices or application redirects appear, record that exact blocker as the next action required for pricing validation

### 12. Genki
- Audit snapshot: pricing `quote_flow_gated`; family pricing `unavailable`; Georgia `confirmed`; Germany `confirmed`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Georgia and Germany are confirmed in the packaged audit snapshot, but product-fit and family suitability still need hardening
  - Worldwide-excluding-U.S. wording remains unverified
  - Family-specific quote output and public pricing remain gated
  - Public plan wording still needs to clarify whether the relevant product is a full expat-medical fit rather than a lighter nomad-oriented product
- Required follow-up mode: `public_web_review_or_public_quote_flow_continuation`
- Next actions from the packaged artifact:
  - High: progress the questionnaire beyond recommendation screens to see whether the Georgia-resident family reaches a product recommendation and visible price, or later exclusion
  - High: obtain official plan wording or product terms clarifying long-term Georgia residence with Germany treatment needs for a family of four
  - Medium: if Genki shows only a recommendation teaser and withholds final price or terms until signup or purchase steps, record that gate explicitly

### 13. WellAway
- Audit snapshot: pricing `quote_flow_gated`; family pricing `gated_behind_contact_details`; Georgia `confirmed`; Germany `confirmed`; worldwide excluding U.S. `not_verified_from_reviewed_sources`
- Main unresolved gaps:
  - Georgia and Germany are confirmed in the audit snapshot, but final coexistence with a non-U.S. geography structure still needs proof
  - Worldwide-excluding-U.S. remains not verified from reviewed sources
  - Anonymous family pricing is blocked because contact submission is required before plans
  - Public brochure and comparison documents still need to settle the geography and treatment combination for the target family
- Required follow-up mode: `contact_submission_required_before_pricing_or_plan_result`
- Next actions from the packaged artifact:
  - High: record that the quote flow already collects family composition, ages, nationality, and destination but requires email, phone, and consent before the plans step
  - High: use public brochure and plan-comparison PDFs to determine whether Georgia residence, Germany treatment, and a worldwide-excluding-U.S.-style geography can coexist on the same family policy
  - Medium: if plan or premium output cannot be seen without submitting contact details, preserve that dependency as the unresolved step needed to secure pricing

### 14. PassportCard
- Audit snapshot: pricing `public_pricing_available`; family pricing `partially_shown`; Georgia `inferred`; Germany `confirmed`; worldwide excluding U.S. `worldwide_positioned_but_ex_us_not_verified`
- Main unresolved gaps:
  - Public/sample pricing exists, but a real family quote still appears to require broker or sales follow-up
  - Georgia remains inferred rather than confirmed
  - Germany is confirmed, but home-country treatment still needs brochure or policy wording support
  - Worldwide-excluding-U.S. remains positioned rather than decisively verified
- Required follow-up mode: `broker_or_sales_follow_up_required`
- Next actions from the packaged artifact:
  - High: locate the insurer-controlled quote or application entrypoint needed to test a Georgia-resident family case
  - High: review publicly accessible brochures or policy materials to confirm Germany treatment or home-country visits for a German family residing in Georgia
  - Medium: if only sample monthly prices are public and a real quote requires sales, broker, or contact follow-up, capture that dependency explicitly

## Friction-category packaging summary

### 1. Country or residence gating
Present on all 14 candidates.

What it means in the packaged artifact:
- Georgia selector visibility, regional marketing, or quote-entry availability is not treated as final issuance proof
- Even where Germany or Georgia are confirmed in the audit snapshot, the package still preserves uncertainty about final family acceptance, area-of-cover compatibility, and downstream underwriting

Candidates affected:
- Cigna Global
- Bupa Global
- APRIL International
- Allianz Care
- AXA Global Healthcare
- William Russell
- Now Health International
- Foyer Global Health
- SafetyWing
- Expatriate Group
- IMG (International Medical Group)
- Genki
- WellAway
- PassportCard

Typical next actions preserved in the source artifact:
- Continue or retry the public quote flow far enough to see whether a family-specific result or exclusion appears
- Capture the exact blocker when the journey stops before pricing or plan output
- Avoid inferring family eligibility from selector presence alone

### 2. Policy wording not public
Present on all 14 candidates.

What it means in the packaged artifact:
- Germany treatment, home-country use, worldwide-excluding-U.S. structure, and underwriting rules were not settled decisively by public wording in the reviewed package
- Even candidates with some public documents still need more decisive passages for the target family configuration

Candidates affected:
- Cigna Global
- Bupa Global
- APRIL International
- Allianz Care
- AXA Global Healthcare
- William Russell
- Now Health International
- Foyer Global Health
- SafetyWing
- Expatriate Group
- IMG (International Medical Group)
- Genki
- WellAway
- PassportCard

Typical next actions preserved in the source artifact:
- Find public brochures, tables of benefits, membership guides, certificates, or policy wording
- Capture wording for Germany treatment, home-country use, and worldwide-excluding-U.S. geography rather than infer it from marketing pages
- Preserve missing-document status when only partial or non-decisive documents are available

### 3. Challenge or bot protection
Present on 1 candidate: Allianz Care.

What it means in the packaged artifact:
- The public quote path was blocked before a usable family-specific outcome could be captured
- The blocker itself is part of the evidence package and should remain preserved as unresolved until a clean-session retry confirms otherwise

Typical next actions preserved in the source artifact:
- Retry only far enough to confirm whether the block is persistent and where it occurs
- If it persists, record the gate and stop instead of inferring pricing, eligibility, or geography output

### 4. Broker or sales follow-up required
Present as the primary mode for 2 candidates: Now Health International and PassportCard.

What it means in the packaged artifact:
- The public artifact set does not close the case anonymously; the next recorded step is broker or sales follow-up
- This does not resolve eligibility, pricing, or geography. It preserves the fact that public discovery alone stopped short

Typical next actions preserved in the source artifact:
- Locate the official broker/sales-controlled intake route
- Capture public brochure or policy wording before any non-public step
- Record the dependency explicitly if real pricing or family eligibility requires sales involvement

### 5. Account or login required before family-specific follow-up
Present as the primary mode for 1 candidate: SafetyWing.

What it means in the packaged artifact:
- Public pricing exists at some level, but family-specific progression is blocked by signup/login before anonymous quote output
- The login wall itself is treated as evidence, not as permission to infer the missing family result

Typical next actions preserved in the source artifact:
- Use already captured public documents for geography and treatment verification
- Preserve the login wall as the pricing/quote blocker
- Check whether any separate public calculator or sample-price path exists outside the gated flow

### 6. Contact submission required before pricing or plan result
Present as the primary mode for 2 candidates: Expatriate Group and WellAway.

What it means in the packaged artifact:
- The flow reaches meaningful intake steps, but family-specific quote or plan results are blocked behind email, phone, or consent capture
- The package preserves that dependency without crossing it

Typical next actions preserved in the source artifact:
- Record the contact-capture gate and stop
- Use already captured public documents to verify geography, Germany treatment, and ex-U.S. positioning
- Do not estimate anonymous premiums where contact submission is required

## Cross-candidate action clustering

The unresolved next actions in the packaged artifact cluster into a few repeatable patterns:
- `find_public_policy_document`: 11 candidates
- `record_gate_and_stop`: 11 candidates
- `continue_public_quote_flow`: 7 candidates
- `capture_family_quote_result`: 5 candidates
- `capture_worldwide_ex_us_wording`: 4 candidates
- `capture_home_country_wording`: 4 candidates

Practical read from the existing local package:
- Most of the work left is still evidence-hardening, not shortlist judgment
- The dominant missing pieces are document-backed geography/treatment wording and faithful recording of quote-flow gates
- The packaged artifact repeatedly prefers “record the gate and stop” over inference when pricing or family-specific output is blocked

## Packaging conclusion

The gaps-and-next-actions packaging is now documented for the exact 14 priority candidates already present in the completion evidence hardening ledger. The document keeps the candidate-level gaps and the friction-category grouping aligned to the local JSON artifact without adding new research, new decisions, or new certainty.
