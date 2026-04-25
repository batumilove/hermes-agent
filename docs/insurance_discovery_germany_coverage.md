# International / Expat Health Insurance Discovery: Germany Coverage Evidence Ledger

Discovery-only companion note for AC 4 sub-AC 4.

Scope:
- German citizen family of four living in Georgia
- Adults age 42 and 37
- Two children
- Preferred geography: Georgia and Germany, ideally worldwide excluding the US

What this artifact does:
- records direct public source evidence relevant to Germany coverage for each staged candidate
- keeps uncertainty explicit when quote flows, selectors, PDFs, or underwriting details were gated or incomplete
- records a country-coverage assessment for Germany so non-confirmed candidates are split into inferred vs unknown rather than left as one unresolved bucket
- does not rank, recommend, or score plans

Primary machine-readable artifact:
- data/insurance_discovery/germany_coverage_evidence_ledger.discovery.json

Status buckets used:
- germany_explicitly_listed: Germany directly appeared in an official country selector, quote form, or similar public plan artifact
- germany_destination_reference: Germany appeared in official destination or country content, but not in a formal selector
- germany_home_country_reference: Germany was directly named in public wording about Germany treatment, home visits, or Germany-facing market positioning
- remaining germany_* not_named / implied buckets: public evidence was still useful for Germany screening, but Germany itself was not directly verified in a selector

Country coverage section for Germany:
- confirmed: captured public evidence explicitly named Germany in an insurer-controlled selector, quote artifact, or direct Germany treatment wording
- inferred: captured public evidence did not name Germany directly, but did expose geography wording, region structure, worldwide wording, home-country mechanics, or Germany-targeted market context strong enough for cautious discovery-stage inference
- unknown: captured public evidence stayed too generic, too gated, or too incomplete to infer Germany responsibly

Germany assessment counts from the machine-readable ledger:
- confirmed: 8
- inferred: 12
- unknown: 7

Examples of confirmed Germany logic:
- Cigna, Bupa, APRIL, IMG, Genki, and WellAway all surfaced Germany directly in public selector or quote-form evidence
- PassportCard explicitly references treatment in Germany during home visits after relocation
- ALC Health includes Germany-specific public country marketing / page structure in captured evidence

Examples of inferred Germany logic:
- AXA, William Russell, Integra, and Expatriate Group expose area-of-cover or regional structures broad enough to place Germany inside the relevant geography
- Morgan Price and ACS expose home-country or EU/EEA mechanics that are materially relevant to Germany for this family context
- GeoBlue, SafetyWing, VUMI, MediHelp, and Securus remain plausible Germany fits from outside-US or worldwide wording, but still need deeper verification

Examples of unknown Germany logic:
- Allianz remained unknown because the quote path was challenge-gated during this run
- MSH, Now Health, Aetna, Henner, Globality, and HCI Group did not expose Germany-specific evidence strong enough to move beyond generic international positioning or market mechanics

High-level takeaways from this discovery pass:
- Strongest direct-public Germany evidence: Cigna Global, Bupa Global, APRIL International, IMG, Genki, WellAway
- Strong Germany-specific non-selector evidence: PassportCard and ALC Health
- Strong Germany-inference via geography structure: AXA, William Russell, Morgan Price, Integra, Expatriate Group, GeoBlue
- Key unresolved blocker: Allianz quote flow was publicly challenge-gated during this run

This run intentionally stops at evidence capture. Final weighting, recommendation logic, and dashboarding remain out of scope for this AC.
