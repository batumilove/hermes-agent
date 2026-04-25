# Insurance discovery evidence ledger data model

Discovery-only companion artifact for AC 70001 sub-AC 1. This document defines the evidence-ledger contract for candidate insurer/plan identity, source metadata, evidence classification, and cross-ledger traceability for the German family of four living in Georgia.

Machine-readable source: `data/insurance_discovery/evidence_ledger_data_model.discovery.json`

## Scope

- Discovery only: this model supports source-backed candidate capture, not final ranking or recommendation.
- Family context: German citizen family of four residing in Georgia; adults 42 and 37; two children.
- Preferred geography: Georgia and Germany, ideally worldwide excluding the US.
- Explicit uncertainty is required when pricing, eligibility, PDFs, or country fit are gated or not publicly verified.

## Required field groups

### 1. Candidate identity

Required fields:
- `candidate_id` — string — stable machine join key across all ledgers
- `normalized_insurer_name` — string — canonical insurer name
- `normalized_plan_name` — string — canonical plan or plan-family label
- `candidate_type` — enum: `primary`, `supplemental`

Traceability rules:
- `candidate_id` must be unique within the dataset.
- The same `candidate_id` must be reused across source, pricing, access, eligibility, and coverage ledgers.
- Plan naming can stay plan-family level if that is all the evidence supports; don’t invent narrower names.

### 2. Candidate status and discovery outcome

Required fields:
- `relevance_status` — enum: `likely_relevant`, `possibly_relevant`, `excluded`
- `relevance_status_reason` — string
- `uncertainty_notes` — array of strings
- `pricing_availability_status` — enum: `public_pricing_available`, `partially_visible`, `quote_flow_gated`, `unavailable_during_discovery`
- `pricing_availability_status_reason` — string

Optional normalized friction field already used in staging:
- `pricing_transparency_friction_primary` — enum:
  - `blocked_public_pricing`
  - `brochure_or_marketing_only_no_price`
  - `broker_or_intermediary_only_public_path`
  - `contact_or_account_gated_pricing`
  - `eligibility_gated_results`
  - `failed_or_abandoned_quote_retrieval`
  - `incomplete_quote_output`
  - `public_numeric_pricing_visible`

Traceability rules:
- `relevance_status` must always be paired with `relevance_status_reason`.
- Excluded candidates still stay in the ledger if the exclusion is source-backed.
- Pricing states must be tied to pricing evidence, not guessed.

### 3. Source metadata

Required candidate-level source field:
- `source_refs` — non-empty array of source reference objects

Required source-ref object fields:
- `dataset_path` — string
- `dataset_section` — string
- `record_bucket` — currently `records`
- `source_url` — string or null
- `source_title` — string
- `evidence_excerpt` — string

Optional source-ref fields:
- `quote_or_application_url` — string or null
- `source_kind` — normalized enum preferred in derivative ledgers:
  - `official insurer page`
  - `official insurer page via search result`
  - `official quote page`
  - `policy wording or brochure PDF`
  - `broker page`

Optional candidate-level normalized URL slots:
- `official_url`
- `quote_or_application_url`
- `broker_url`
- `policy_wording_or_pdf_url`
- `primary_evidence_url`

Traceability rules:
- Every candidate must have at least one source-backed ref.
- Null URL means not verified publicly in this run, not impossible.
- Broker URLs are allowed only when they materially supplement public evidence.
- Derived ledgers should normalize noisy upstream source kinds into the controlled set above.

### 4. Evidence classification

Normalized fields already in use:
- `source_origin` — enum:
  - `shared_candidate_staging.source_refs`
  - `derived_candidate_source_ledger_reference`
- `applicability_roles` — array of enums:
  - `access_path_evidence`
  - `benefit_detail_evidence`
  - `broker_substitute_evidence`
  - `candidate_discovery_entry`
  - `candidate_identification`
  - `geography_relevance_evidence`
  - `official_plan_positioning`
  - `plan_document_reference`
  - `pricing_or_quote_path_evidence`
  - `quote_or_application_path_reference`
- `source_directness_classification` — enum: `high`, `medium`, `low`
- `source_directness_justification` — string

Completeness fields:
- `evidence_entry_completeness_summary.classification` — enum: `complete`, `partial`, `insufficiently_sourced`
- `evidence_entry_completeness_summary.classification_source_value` — enum:
  - `source_backed_complete_for_discovery`
  - `source_backed_partial_but_usable`
  - `insufficient_for_discovery`

Coverage classification fields:
- `coverage_ambiguity_annotations.<dimension>.coverage_state`
- `coverage_ambiguity_annotations.<dimension>.explicit_uncertainty_flag`

Allowed coverage states across current ledgers:
- Country assessment states: `confirmed`, `inferred`, `unknown`
- Worldwide ex-US states: `explicitly_indicated`, `indirectly_indicated`, `worldwide_positioned_but_ex_us_not_verified`, `not_verified_from_reviewed_sources`

Observed explicit uncertainty flags:
- `ambiguous_because_country_fit_is_inferred_not_directly_named`
- `ambiguous_because_global_positioning_does_not_verify_ex_us_configuration`
- `ambiguous_because_non_us_global_configuration_is_only_indirectly_supported`
- `ambiguous_because_reviewed_public_sources_did_not_verify_dimension`
- `ambiguous_because_reviewed_sources_did_not_verify_ex_us_configuration`
- `partial_because_configuration_exists_but_family_specific_terms_remain_unverified`
- `partial_because_country_presence_does_not_equal_final_family_fit`

Traceability rules:
- Every directness class must have a justification.
- Coverage states must be dimension-appropriate.
- Partial/unknown states are allowed, but omission is not.
- Every claim-bearing nested evidence row in derivative ledgers should carry a `candidate_linkage` object instead of depending purely on the parent record for joins.

Evidence-row linkage object:
- `candidate_linkage.linkage_basis` — currently `parent_record_candidate_id`
- `candidate_linkage.candidate_record_dataset_path` — dataset path of the parent candidate record that owns the evidence item
- `candidate_linkage.candidate_record_section` — section name of that parent dataset
- `candidate_linkage.candidate_ids` — array of associated candidate IDs
- `candidate_linkage.primary_candidate_id` — canonical candidate ID for this evidence row

### 5. Cross-ledger traceability

Required link fields in the shared candidate record:
- `pricing_evidence_ref`
- `access_and_intake_evidence_refs`
- `eligibility_ambiguity_evidence_ref`

Optional but important traceability fields:
- `medical_underwriting_preexisting_conditions.evidence_ledger_ref`
- `coverage_ambiguity_annotations.<dimension>.source_ledger_ref`
- `evidence_entry_source_map`

`evidence_entry_source_map` should preserve these source-id buckets:
- `candidate_source_entry`
- `official_positioning_source_ids`
- `quote_or_application_source_ids`
- `broker_source_ids`
- `plan_document_source_ids`
- `geography_relevance_source_ids`
- `pricing_or_quote_source_ids`
- `access_path_source_ids`

Traceability rules:
- Every source-id in `evidence_entry_source_map` must resolve to a source inside that candidate’s `source_set`.
- Every ledger ref must include enough information to reopen the exact JSON artifact and locate the candidate or record.
- Human-readable markdown is not enough by itself; machine-readable JSON references are required.

## Controlled rubrics

### Source directness rubric
- `high` — official insurer-controlled quote/application paths and official policy wording, brochures, benefit tables, or similar plan documents
- `medium` — official insurer-controlled landing/marketing pages
- `low` — broker/intermediary or otherwise indirect public sources

### URL capture semantics
- `verified_public_url` — URL was publicly verified and stored
- `not_verified_publicly_in_this_run` — represented as null

### Coverage-state semantics
Country dimensions (`georgia`, `germany`):
- `confirmed`
- `inferred`
- `unknown`

Worldwide ex-US dimension:
- `explicitly_indicated`
- `indirectly_indicated`
- `worldwide_positioned_but_ex_us_not_verified`
- `not_verified_from_reviewed_sources`

## Minimum traceability contract

A candidate evidence entry is valid for discovery only if:
1. It has a stable `candidate_id`.
2. It has at least one source-backed `source_ref`.
3. It preserves insurer and plan identity.
4. It records explicit relevance and uncertainty.
5. It links pricing/access/eligibility evidence through machine-readable refs.
6. It preserves controlled states instead of silently omitting unknowns.
7. Any normalized or synthetic source rows preserve provenance through `source_origin`.

## Evidence ledger serialization contract

Primary machine-readable serialization choice: `JSONL`

Why JSONL was chosen over CSV for the canonical evidence ledger:
- Evidence entries are nested and heterogeneous across ledgers. JSONL preserves arrays, nested citation objects, and ledger refs without lossy flattening.
- One JSON object per line supports append-only discovery workflows and record-level diffs.
- Cross-ledger references (`dataset_path`, `candidate_id`, `evidence_id`, nested citations) remain machine-readable without inventing delimiter conventions inside cells.
- CSV remains acceptable only as a derived export for narrow flat views, like the existing candidate source URL inventory.

### Canonical file naming

- Canonical JSON ledger files in this repo may remain wrapped as dataset objects with a top-level `records` array using the existing pattern:
  - `data/insurance_discovery/<ledger_name>.discovery.json`
- When the same ledger is emitted as a line-oriented evidence stream, the canonical filename should be:
  - `data/insurance_discovery/<ledger_name>.discovery.jsonl`
- If a CSV companion is generated for spreadsheet review, it must be treated as derivative only:
  - `data/insurance_discovery/<ledger_name>.discovery.csv`

### One-record-per-evidence-entry rule

- Each JSONL line represents exactly one evidence entry.
- Do not pack multiple unrelated evidence findings into one JSONL record just because they belong to the same candidate.
- Candidate-level summary rows and evidence-entry rows must not be mixed in the same JSONL stream unless the record type is explicit.
- If one candidate has multiple evidence findings in the same ledger, emit multiple JSONL lines with distinct `evidence_id` values.
- Nested citation arrays are allowed inside a single evidence entry when those citations support the same finding.
- For verification-ready citation capture, each citation should also carry `source_title`, `document_identifier`, `access_date`, `publication_date` when publicly visible, and an exact locator split across `page_reference`, `section_reference`, and `anchor_reference` where available.

Recommended uniqueness key per line:
- `ledger_name + candidate_id + evidence_id`

### Key naming conventions

- Use `snake_case` for every top-level and nested key.
- IDs and join keys should end with `_id`.
- Human-readable labels should end with `_name`, `_title`, `_summary`, `_reason`, `_statement`, or `_notes` as appropriate.
- URLs should end with `_url`; URL arrays should end with `_urls`.
- Controlled classification fields should end with `_status`, `_classification`, `_type`, `_level`, `_state`, or `_kind`.
- Boolean fields should read naturally as booleans, preferably with prefixes like `is_`, `has_`, `was_`, or with explicit yes/no semantics already established in the ledger.
- Refs to other artifacts should end with `_ref` for single objects and `_refs` for arrays.

### Required vs optional field rules

Required on every JSONL evidence-entry record:
- `dataset`
- `run_scope`
- `section`
- `ledger_name`
- `record_type`
- `candidate_id`
- `evidence_id`
- `insurer_name`
- `plan_name`
- `source_origin`
- `source_directness_classification`
- at least one of:
  - `finding_statement`
  - `evidence_excerpt`
  - `pricing_summary`
  - other ledger-specific primary evidence text field
- `citations` as an array; may be empty only if the evidence entry instead embeds a non-empty `supporting_source_refs` array and the omission is intentional and documented by the ledger design
- `uncertainty_notes` as an array, even when empty

Required when applicable:
- `candidate_type` when the source ledger distinguishes `primary` vs `supplemental`
- `record_index` when preserving position from an existing wrapped JSON artifact
- `dataset_path` or `ledger_dataset_path` inside any cross-ledger reference object
- `citation_id`, `source_url` or null, `source_type`, and `excerpt` inside each citation object

Optional fields:
- Any field that is ledger-specific and not universally meaningful across all evidence ledgers
- URL slots such as `quote_or_application_url`, `broker_url`, `policy_wording_or_pdf_url`
- Screenshot fields, page references, and access metadata
- Coverage-dimension subobjects when the ledger is not a geography ledger

### Null and empty-value handling

- Use `null` for scalar values that were expected by the schema but were not publicly verified in this run.
- Do not use empty strings to mean unknown, gated, unavailable, or not captured.
- Use empty arrays `[]` only when the field is structurally present and the correct value is truly “no items observed/captured.”
- Use empty objects `{}` only when the field is structurally required but currently has no populated child members; otherwise omit optional objects.
- `null` means “not verified/publicly captured in this run,” not “does not exist in reality.”
- If a value is unknown because of gating, challenge walls, broker intermediation, or incomplete review, preserve that in a paired note or controlled status field rather than encoding meaning into null alone.

### Serialization rules for wrapped JSON vs JSONL

If the ledger is stored as wrapped JSON (`.discovery.json`):
- Top-level metadata belongs at the dataset root.
- Evidence entries live only inside `records`.
- `records` order may preserve discovery order, but consumers must join by IDs rather than array position.

If the ledger is stored as JSONL (`.discovery.jsonl`):
- Repeat the minimal routing metadata needed for each line: `dataset`, `run_scope`, `section`, `ledger_name`, `record_type`, `candidate_id`, `evidence_id`.
- Do not rely on file-level headers or sidecar assumptions.
- Each line must be valid standalone JSON.
- Newlines inside text values must be escaped within the JSON string, not emitted as physical multi-line records.

### CSV fallback rules

CSV is allowed only for derived flat exports, not as the canonical evidence ledger format.

When CSV is used:
- One row still equals one evidence entry.
- Flatten only stable scalar fields and counts.
- Keep nested arrays/objects out of CSV unless serialized intentionally into documented JSON-string columns.
- Any flattened multi-value column must document its delimiter or JSON-string encoding rules.
- The CSV must include a column that points back to the canonical JSON/JSONL source record, ideally `candidate_id` plus `evidence_id`.

### Minimum JSONL evidence-entry template

```json
{
  "dataset": "international_expat_health_insurance_discovery",
  "run_scope": "discovery_only",
  "section": "access_and_intake_evidence_ledger",
  "ledger_name": "access_and_intake_evidence_ledger",
  "record_type": "evidence_entry",
  "candidate_id": "cigna_global",
  "evidence_id": "cigna_global:intake",
  "insurer_name": "Cigna Global",
  "plan_name": "International Health Insurance / Global Medical Cover",
  "source_origin": "derived_candidate_source_ledger_reference",
  "source_directness_classification": "high",
  "finding_statement": "Quote page immediately asks for country of residence before continuing.",
  "citations": [
    {
      "citation_id": "cigna_global:intake:cite1",
      "source_url": "https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html",
      "source_type": "official quote page",
      "page_reference": "public_quote_country_selector",
      "excerpt": "Where will you be living for the duration of the policy?*",
      "screenshot_path": null,
      "screenshot_status": "not_captured_in_this_run"
    }
  ],
  "uncertainty_notes": [
    "Later steps may add account or eligibility gating that was not verified in this run."
  ]
}
```

### Validation expectations

- Every JSONL line must parse as UTF-8 JSON object.
- No duplicate `evidence_id` values may exist for the same `ledger_name` + `candidate_id` combination.
- Required keys must be present even when some values are `null` or arrays are empty by rule.
- Unknowns must be explicit through controlled states, `null`, or uncertainty notes; silent omission is not valid.

## Canonical schema artifact

Canonical record schema:
- `data/insurance_discovery/evidence_ledger_record.schema.json`

Schema scope:
- Defines the canonical minimum contract for one evidence-entry record.
- Applies directly to one JSONL line in canonical line-oriented ledgers.
- Also applies to one object inside a wrapped `.discovery.json` ledger’s `records` array when the ledger repeats routing metadata at record level or when a validator projects root metadata onto each record before validation.
- Allows ledger-specific extension fields via `additionalProperties: true`; the goal is to constrain the shared evidence-entry contract without breaking specialized ledgers.

### Canonical validation constraints encoded in the schema

- `dataset` must equal `international_expat_health_insurance_discovery`.
- `run_scope` must equal `discovery_only`.
- `record_type` must equal `evidence_entry`.
- `candidate_id` must be a stable snake_case identifier matching `^[a-z0-9]+(?:_[a-z0-9]+)*$`.
- `evidence_id` must be a non-empty joinable identifier matching `^[A-Za-z0-9_:-]+$`.
- `ledger_name` must be non-empty snake_case.
- `source_origin` must be one of:
  - `shared_candidate_staging.source_refs`
  - `derived_candidate_source_ledger_reference`
- `source_directness_classification` must be one of `high`, `medium`, `low`.
- At least one primary evidence text field must be present:
  - `finding_statement`, or
  - `evidence_excerpt`, or
  - `pricing_summary`
- At least one source-proof structure must be present and non-empty:
  - `citations`, or
  - `supporting_source_refs`
- Citation objects must include `citation_id`, `source_type`, and `excerpt`; `source_url` may be `null` when not publicly verified in this run.
- Supporting source refs must include `source_title` and `evidence_excerpt`; `source_url` may be `null`.

### Compliant record examples

Example 1: citation-backed access/intake evidence entry

```json
{
  "dataset": "international_expat_health_insurance_discovery",
  "run_scope": "discovery_only",
  "section": "access_and_intake_evidence_ledger",
  "ledger_name": "access_and_intake_evidence_ledger",
  "record_type": "evidence_entry",
  "candidate_id": "cigna_global",
  "evidence_id": "cigna_global:intake",
  "candidate_type": "primary",
  "insurer_name": "Cigna Global",
  "plan_name": "International Health Insurance / Global Medical Cover",
  "source_origin": "derived_candidate_source_ledger_reference",
  "source_directness_classification": "high",
  "source_directness_justification": "Official insurer-controlled quote path directly shows the intake step used during discovery.",
  "finding_statement": "Quote page immediately asks for country of residence before continuing.",
  "citations": [
    {
      "citation_id": "cigna_global:intake:cite1",
      "source_url": "https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html",
      "source_type": "official quote page",
      "page_reference": "public_quote_country_selector",
      "excerpt": "Where will you be living for the duration of the policy?*",
      "screenshot_path": null,
      "screenshot_status": "not_captured_in_this_run"
    }
  ],
  "uncertainty_notes": [
    "Later steps may add account or eligibility gating that was not verified in this run."
  ]
}
```

Example 2: supporting-source-ref-backed pricing evidence entry

```json
{
  "dataset": "international_expat_health_insurance_discovery",
  "run_scope": "discovery_only",
  "section": "pricing_evidence_ledger",
  "ledger_name": "pricing_evidence_ledger",
  "record_type": "evidence_entry",
  "candidate_id": "allianz_care",
  "evidence_id": "allianz_care:pricing",
  "candidate_type": "primary",
  "insurer_name": "Allianz Care",
  "plan_name": "Care International Health Insurance Plans",
  "source_origin": "shared_candidate_staging.source_refs",
  "source_directness_classification": "medium",
  "source_directness_justification": "The reviewed pricing evidence came from an official public insurer page, but not from a completed quote result or policy wording.",
  "pricing_summary": "A 10% promotion was publicly visible, but the usable premium remained quote-gated.",
  "pricing_availability_status": "partially_visible",
  "supporting_source_refs": [
    {
      "dataset_path": "data/insurance_discovery/global_providers.discovery.json",
      "dataset_section": "global_providers",
      "source_url": "https://www.allianzcare.com/en/personal-international-health-insurance.html",
      "source_title": "International Health Insurance for Individuals | Allianz",
      "source_kind": "official insurer page",
      "evidence_excerpt": "10% off your international health insurance for life...",
      "quote_or_application_url": "https://my.allianzcare.com/myquote/5"
    }
  ],
  "uncertainty_notes": [
    "The quote path was challenge-gated in this run, so public pricing may exist deeper in the flow without being verified here."
  ]
}
```

## Artifact paths

- JSON contract: `data/insurance_discovery/evidence_ledger_data_model.discovery.json`
- Canonical JSON Schema: `data/insurance_discovery/evidence_ledger_record.schema.json`
- Markdown companion: `docs/insurance_discovery_evidence_ledger_data_model.md`
