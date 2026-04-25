# Insurance discovery evidence ledger output template

Discovery-only starter artifact for AC 70004 sub-AC 4. This file provides a canonical output template that can be copied or appended to when creating new evidence-ledger entries for international/expat health insurance discovery relevant to a German family of four living in Georgia.

Canonical starter dataset:
- `data/insurance_discovery/evidence_ledger_output_template.discovery.jsonl`

Why this file exists:
- It gives future discovery runs a stable machine-readable starter artifact instead of forcing each run to reinvent the evidence-entry shape.
- It follows the canonical record contract in `data/insurance_discovery/evidence_ledger_record.schema.json`.
- It stays discovery-only: these starter records capture source-backed evidence structure, not recommendations or rankings.

Template characteristics:
- Format: JSONL
- One line = one evidence entry
- Each line is valid standalone JSON
- Routing metadata is repeated per line, matching the serialization contract in `docs/insurance_discovery_evidence_ledger_data_model.md`

Starter records included:
1. `template_candidate:access`
   - Demonstrates a minimum access/intake evidence entry with citations.
2. `template_candidate:pricing`
   - Demonstrates a pricing evidence entry with `pricing_availability_status` and `supporting_source_refs`.
3. `template_candidate:worldwide_ex_us`
   - Demonstrates a geography coverage evidence entry with `coverage_dimension` and `coverage_state`.

How to populate it:
- Replace `template_candidate` with the stable snake_case candidate id used elsewhere in discovery.
- Replace placeholder insurer/plan names and example.com URLs with verified public sources.
- Keep `dataset`, `run_scope`, and `record_type` unchanged.
- Change `section` and `ledger_name` to the destination ledger you are populating.
- Preserve explicit uncertainty notes instead of dropping unknowns.
- Add or replace citations/supporting source refs with public evidence from official insurer pages, quote/application paths, policy wording PDFs, or broker pages where needed.

Validation expectation:
- Each JSONL line should validate against `data/insurance_discovery/evidence_ledger_record.schema.json`.
- The starter file was created to satisfy that schema with placeholder-but-valid example values so downstream discovery runs can copy and modify records safely.
