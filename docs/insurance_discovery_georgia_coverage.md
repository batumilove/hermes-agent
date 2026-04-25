# International / Expat Health Insurance Discovery: Georgia Coverage Evidence Ledger

Discovery-only companion note for AC 30201 sub-AC 1 and AC 30202 sub-AC 2.

Scope:
- German citizen family of four living in Georgia
- Adults age 42 and 37
- Two children
- Preferred geography: Georgia and Germany, ideally worldwide excluding the US

What this artifact does:
- records direct public source evidence relevant to Georgia coverage for each staged candidate
- keeps uncertainty explicit when quote flows, selectors, PDFs, or underwriting details were gated or incomplete
- records a country-coverage assessment for Georgia so non-confirmed candidates are split into inferred vs unknown rather than left as a flat unresolved bucket
- does not rank, recommend, or score plans

Primary machine-readable artifact:
- data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json

Status buckets used:
- georgia_explicitly_listed: Georgia directly appeared in an official country selector or similar public quote artifact
- georgia_destination_reference: Georgia appeared on an official destination / health guide, but not a formal eligibility selector
- georgia_regionally_implied_not_named: area-of-cover wording strongly suggests Georgia is inside the geography, but Georgia was not quoted verbatim
- remaining not_named_* buckets: public evidence was still useful for Georgia screening, but Georgia itself was not directly verified

Country coverage section for Georgia:
- confirmed: captured public evidence explicitly named Georgia in an insurer-controlled selector or equivalent quote artifact
- inferred: captured public evidence did not name Georgia directly, but did expose geography wording broad enough to place Georgia plausibly inside the covered territory
- unknown: captured public evidence stayed too generic, too gated, or too incomplete to infer Georgia responsibly

Georgia country-coverage classification logic used in this run:
- inferred when the reviewed source language exposed a workable geography rule, such as area-of-cover bands, worldwide or outside-US territory structure, or an official Georgia destination guide that suggests Georgia is within the insurer's intended footprint
- unknown when the reviewed source language was only broad expat marketing, network reach, or a gated quote flow that might answer the question but did not reveal Georgia in captured output
- explicit Georgia selector evidence remains the strongest discovery signal, but still does not prove final underwriting acceptance or pricing

Georgia assessment counts from the machine-readable ledger:
- confirmed: 6
- inferred: 12
- unknown: 9

Examples of inferred Georgia classification logic:
- AXA, Integra, PassportCard, GeoBlue, and Expatriate Group expose area-of-cover or outside-US territory structures that make Georgia geographically plausible without naming it directly
- VUMI, MediHelp, ACS, SafetyWing, and Securus use public worldwide wording strong enough for discovery-stage inference, while still leaving eligibility uncertainty open
- Foyer is inferred rather than confirmed because Georgia appears in official destination content, not in a captured eligibility selector

Examples of unknown Georgia classification logic:
- Allianz and Globality had quote or application flows that were relevant but did not yield a readable Georgia result in captured public evidence
- Aetna and Henner showed worldwide network or service language, which is weaker than plan-geography wording
- MSH, Now Health, Morgan Price, ALC Health, and HCI Group remained unknown because the reviewed text did not place Georgia inside a named territory or accepted residence list

High-level takeaways from this discovery pass:
- Strongest direct-public Georgia selector evidence: Cigna Global, Bupa Global, APRIL International, IMG, Genki, Wellaway
- Strong Georgia-adjacent country evidence: Foyer Global Health via official Georgia guides
- Strong geography-structure evidence but Georgia still not named: AXA, Integra, Expatriate Group, PassportCard, GeoBlue
- Important unresolved blocker: Allianz quote flow was publicly challenge-gated during this run

This run intentionally stops at evidence capture. Final weighting, recommendation logic, and dashboarding remain out of scope for this AC.
