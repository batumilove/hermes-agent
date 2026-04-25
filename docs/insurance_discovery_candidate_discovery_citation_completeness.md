# Candidate discovery citation completeness audit

Machine-readable source: `data/insurance_discovery/candidate_discovery_citation_completeness_audit.discovery.json`

Discovery-only audit verifying that every candidate discovery evidence-row citation carries verification-ready citation details.

## Summary

- total-evidence-rows: 62
- total-citations: 62
- rows-with-citations: 62
- citations-with-source-title: 62
- citations-with-source-url: 62
- citations-with-document-identifier: 62
- citations-with-access-date: 62
- citations-with-publication-date-available: 0
- citations-with-page-reference: 62
- citations-with-section-reference: 62
- citations-with-anchor-reference-available: 0
- citations-with-exact-reference: 62
- missing-field-failures: 0
- audit-passed: true

## Required citation fields checked

- `citation_id`
- `source_title`
- `source_url`
- `document_identifier`
- `access_date`
- `publication_date`
- `page_reference`
- `section_reference`
- `anchor_reference`
- `exact_reference`
- `source_type`
- `excerpt`

## Notes

- access_date reflects the discovery-run retrieval date for this repository worktree update.
- publication_date is preserved as null when no public publication date was visible in the reviewed source metadata.
- section_reference points to the originating dataset section or ledger section used to reopen the source context.
- anchor_reference is null unless the reviewed public URL exposed an explicit fragment anchor.
- exact_reference is a normalized page/section/anchor locator string for verification-oriented reopening of the cited source.

## Source kind breakdown

- broker page: 2
- official insurer page: 30
- official insurer page via search result: 2
- official quote page: 16
- policy wording or brochure PDF: 12

## Failures

- None.
