# Insurance discovery completion evidence-hardening ledger schema

Machine-readable sources:
- `data/insurance_discovery/completion_evidence_hardening_ledger.schema.json`
- `data/insurance_discovery/completion_evidence_hardening_ledger_data_model.discovery.json`

## Purpose

This completion-pass schema deepens the existing discovery dataset for the German citizen family of four living in Georgia. It does not replace the discovery ledgers and it does not turn the project into a ranking, dashboard, or purchase recommendation.

## Record granularity

- One `candidate_completion_record` per shortlisted candidate.
- Shortlist restricted in this pass to:
  - `cigna_global`
  - `bupa_global`
  - `april_international`
  - `allianz_care`
  - `axa_global_healthcare`
  - `william_russell`
  - `now_health_international`
  - `foyer_global_health`
  - `safetywing`
  - `expatriate_group`
  - `img_global_medical`
  - `genki`
  - `wellaway`
  - `passportcard`

## Required top-level fields

- `dataset = international_expat_health_insurance_discovery`
- `run_scope = completion_evidence_hardening`
- `section = completion_evidence_hardening_ledger`
- `ledger_name = completion_evidence_hardening_ledger`
- `record_type = candidate_completion_record`
- `candidate_id`
- `candidate_type`
- `priority_candidate = true`
- `insurer_name`
- `plan_name`
- `family_context`
- `completion_status`
- `georgia_residence_eligibility`
- `germany_or_home_country_treatment`
- `worldwide_ex_us_area_of_cover`
- `family_quote_flow`
- `policy_document_access`
- `pricing_visibility`
- `medical_underwriting_preexisting_conditions`
- `source_urls`
- `confidence`
- `friction`
- `unresolved_next_actions`
- `uncertainty_notes`

## Standardized evidence sections

### 1. Georgia residence eligibility

Required standardized fields inside `georgia_residence_eligibility`:
- `status`
- `evidence_status`
- `summary`
- `evidence_strength`
- `source_refs`

Allowed status values:
- `confirmed`
- `partially_confirmed`
- `inferred`
- `contradicted`
- `gated`
- `unknown`
- `not_applicable_from_reviewed_public_sources`

This section is where the ledger records whether Georgia residence is explicitly selectable, inferable from public territory wording, or still gated behind challenge/broker/contact barriers.

### 2. Germany or home-country treatment

Same normalized contract as Georgia, but focused on:
- Germany in selector/coverage evidence
- home-country treatment wording
- home-country visit wording
- any explicit Germany-treatment public evidence

The schema includes `treatment_scope` so downstream consumers can distinguish `home_country_cover` from `home_country_visits_only` or generic country presence.

### 3. Worldwide-excluding-U.S. area of cover

Same normalized dimension contract again, but specifically for:
- direct worldwide excluding U.S. wording
- outside-U.S. wording
- region/area-of-cover selector evidence
- indirect include/exclude USA mechanics

This is deliberately separate from generic worldwide marketing, because those are not the same thing.

### 4. Family quote-flow status

`family_quote_flow` standardizes what happened in the public quote/application path without submitting personal contact info.

Required subfields:
- `status`
- `entrypoint_type`
- `family_specific_inputs_observed`
- `quote_result_visibility`
- `summary`
- `source_refs`

This is where the ledger captures whether the run only reached entry, advanced to family-specific inputs, hit a challenge wall, or surfaced an actual plan/price result.

### 5. Policy document status

`policy_document_access` records:
- whether a public policy wording, table of benefits, brochure, handbook, or similar document was found
- whether the link existed but failed in the environment
- whether the document was public but still incomplete for the exact question

### 6. Pricing visibility

`pricing_visibility` separates:
- true public numeric price visibility
- promo-only or partial pricing hints
- quote-gated pricing
- broker/contact-gated pricing
- no visible pricing at all

This avoids conflating “there is a quote button” with “there is usable pricing.”

### 7. Underwriting / pre-existing-condition evidence

`medical_underwriting_preexisting_conditions` records:
- whether explicit public terms were found
- whether only underwriting route language was visible
- whether evidence was snippet-only
- whether the details appear gated behind documents or quote flows

## Traceability and uncertainty

The schema requires:
- `source_urls` for reviewed or blocked URLs
- `source_refs` inside each major evidence section
- `confidence` with an overall score and optional dimension split
- `friction` with normalized barriers
- `unresolved_next_actions` with explicit next public-safe steps
- `uncertainty_notes` even when evidence is otherwise strong

That matters because the completion pass is supposed to harden evidence, not pretend missing public evidence doesn’t exist.

## Additive workflow rule

This schema is additive to the current discovery artifacts. It is designed to link back to:
- geography ledgers
- access/intake evidence
- plan-document access evidence
- pricing evidence
- medical underwriting evidence
- eligibility ambiguity evidence
- normalized candidate records

Instead of flattening everything into a brand-new dataset from scratch, each completion record can preserve `existing_discovery_refs` and nested `dataset_ref` pointers.

## Recommended canonical output path

- Wrapped JSON dataset: `data/insurance_discovery/completion_evidence_hardening_ledger.discovery.json`
- Optional JSONL variant: `data/insurance_discovery/completion_evidence_hardening_ledger.discovery.jsonl`

## Notes

- This schema intentionally preserves gated, blocked, unknown, and not-found states.
- It is completion-oriented, not recommendation-oriented.
- It is family-specific, not generic-market-wide.
