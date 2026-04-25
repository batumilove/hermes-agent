# Insurance discovery priority completion report

Sources used only from the local worktree:
- `data/insurance_discovery/completion_evidence_hardening_ledger.discovery.json`
- `data/insurance_discovery/priority_candidate_completion_audit.discovery.json`
- `data/insurance_discovery/shared_candidate_staging.discovery.json`
- `docs/insurance_discovery_priority_candidate_completion_audit.md`

Scope guardrails:
- This is a packaging report from existing local artifacts only.
- It does not add live research, new quote runs, ranking, purchase advice, value scoring, or a final shortlist order.
- It preserves the uncertainty, gates, and evidence limits already recorded in the source artifacts.
- The target set remains exactly the 14 audited priority candidates.

## Completion-pass status

- Priority candidates in scope: 14
- Completion audit result: passed
- Candidates with exactly one complete consolidated record: 14
- Completeness failures found in the priority set: 0

This means the priority set is packaged cleanly enough for the next shortlist stage, but not that the ambiguity gates are resolved.

## The 14 priority candidates in scope

1. Cigna Global
2. Bupa Global
3. APRIL International
4. Allianz Care
5. AXA Global Healthcare
6. William Russell
7. Now Health International
8. Foyer Global Health
9. SafetyWing
10. Expatriate Group
11. IMG (International Medical Group)
12. Genki
13. WellAway
14. PassportCard

## Candidates with the strongest current evidence package for the next shortlist stage

These are the candidates that already have comparatively stronger local evidence packaging for the next stage because they combine more of the following than the rest of the set: confirmed or partially confirmed geography fit, some pricing visibility, and/or a publicly found plan document. This is not a ranking.

### IMG (International Medical Group)
- Georgia: confirmed in the consolidated audit/staging package
- Germany: confirmed
- Pricing: partially visible
- Policy documents: public document found
- Remaining gate preserved from source artifacts: suitability still needs checking because the local notes say some IMG products may be travel-medical rather than full expat medical, and worldwide excluding U.S. still was not verified from the reviewed sources

### PassportCard
- Georgia: inferred
- Germany: confirmed
- Pricing: public pricing available
- Policy documents: public document found
- Remaining gate preserved from source artifacts: public prices were illustrative rather than family-specific, and Georgia-residence acceptance still needs confirmation

### SafetyWing
- Georgia: inferred
- Germany: inferred
- Pricing: public pricing available
- Policy documents: public document found
- Remaining gate preserved from source artifacts: residence-country acceptance for a German family living in Georgia still needs verification, and Germany/Georgia treatment plus underwriting details remain unresolved

### Expatriate Group
- Georgia: inferred
- Germany: inferred
- Worldwide excluding U.S.: explicitly indicated
- Pricing: partially visible, but family pricing is gated behind contact details
- Policy documents: public document found
- Remaining gate preserved from source artifacts: Georgia-resident acceptance, exact plan availability, underwriting, and family pricing still need confirmation

### Cigna Global, Bupa Global, and APRIL International
- Each has confirmed Georgia and confirmed Germany states in the consolidated priority audit package
- All three remain pricing-gated in the current local evidence package
- All three still lack a publicly found decisive plan document in the reviewed sources
- Practical read from the local artifacts: they remain very much in the next-stage conversation, but the evidence package is still quote-flow-heavy rather than document-backed

## Candidates blocked primarily by Georgia-residence ambiguity

The local artifacts still preserve a Georgia-residence gate for these candidates, even when other signals look promising.

### Explicit or partly confirmed Georgia presence, but not final family-fit proof
- Cigna Global: Georgia appears in the official selector path, but the artifacts explicitly say selector presence does not equal final underwriting acceptance for this Georgia-resident German family
- Bupa Global: Georgia is confirmed in the consolidated package, but actual Georgia-resident family plan availability still needs quote or brochure confirmation
- APRIL International: Georgia is confirmed in the consolidated package, but the next local step still requires quote-flow or benefits-guide confirmation
- IMG (International Medical Group): Georgia is confirmed, but product-fit ambiguity remains
- Genki: Georgia is confirmed, but the local notes still require proof that the product is a full expat-medical fit for this Georgia/Germany family profile rather than a lighter nomad-oriented product
- WellAway: Georgia is marked confirmed in the priority audit package, but the record still says Georgia acceptance and non-U.S. geography structure need follow-up

### Georgia not yet settled beyond inference or unknown status
- Allianz Care: Georgia remains unknown and the next step in the hardened ledger is to solve the challenge or use a clean browser session
- AXA Global Healthcare: Georgia remains inferred rather than confirmed
- William Russell: Georgia remains inferred rather than confirmed
- Now Health International: Georgia remains unknown and the hardened ledger says the family quote flow was gated before a family-specific result, with the next step recorded as broker or sales contact
- Foyer Global Health: Georgia remains inferred and the supporting notes say stronger evidence is still needed
- SafetyWing: Georgia remains inferred
- Expatriate Group: Georgia remains inferred
- PassportCard: Georgia remains inferred

## Candidates blocked primarily by worldwide excluding U.S. ambiguity

This remains one of the biggest unresolved gates across the whole priority set.

### Explicit or strongest current ex-U.S. signal
- Expatriate Group: the local artifacts mark worldwide excluding U.S. as explicitly indicated

### Indirectly indicated, but not fully verified
- AXA Global Healthcare
- Now Health International
- SafetyWing

For these three, the current local package suggests a non-U.S. global configuration may exist, but the reviewed evidence did not verify the exact worldwide-excluding-U.S. configuration decisively.

### Worldwide-positioned, but ex-U.S. not verified
- Cigna Global
- APRIL International
- Allianz Care
- William Russell
- Foyer Global Health
- IMG (International Medical Group)
- Genki
- PassportCard

For this group, the source artifacts support broad worldwide or global positioning, but they do not yet establish the exact ex-U.S. configuration required for the target family.

### Not verified from reviewed sources
- Bupa Global
- WellAway

These two remain the weakest on the exact ex-U.S. requirement inside the current local artifact set.

## Candidates blocked primarily by pricing gates

The pricing package is still incomplete for most of the 14.

### Public pricing available, but still not family-specific enough to close the case
- PassportCard
- SafetyWing

Both show public pricing signals in the local artifacts, but the records still preserve family-specific limitations and other eligibility ambiguity.

### Partially visible pricing only
- Allianz Care
- AXA Global Healthcare
- Now Health International
- Expatriate Group
- IMG (International Medical Group)

These have some pricing signal, but not a usable family-specific premium from the reviewed public evidence.

### Quote-flow gated pricing
- Cigna Global
- Bupa Global
- APRIL International
- William Russell
- Genki
- WellAway

These remain dependent on a deeper quote flow to expose meaningful pricing.

### No usable pricing during discovery
- Foyer Global Health

## Candidates blocked primarily by missing policy documents

The local completion ledger records no publicly found decisive plan document for these candidates in the reviewed public sources:
- Cigna Global
- Bupa Global
- APRIL International
- Allianz Care
- AXA Global Healthcare
- William Russell
- Now Health International
- Genki

Public plan-relevant documents were found for these candidates, although those documents still did not remove all eligibility or geography ambiguity:
- Foyer Global Health
- SafetyWing
- Expatriate Group
- IMG (International Medical Group)
- WellAway
- PassportCard

## Candidates that the current local artifact set already supports deprioritizing

This section is still not a recommendation or purchase judgment. It only identifies names that the existing local evidence package itself places behind the cleaner next-stage evidence set.

### WellAway
- The staging artifact marks WellAway as `excluded`
- Recorded reason: public plan messaging leaned heavily U.S.-inclusive and the non-U.S. configuration was not established for the target profile
- It also remains quote-flow gated on pricing and not verified from reviewed sources on the ex-U.S. requirement

### Foyer Global Health
- Still usable as a backup name, but the local notes explicitly say it was added as an eleventh backup provider to keep the shortlist above the required minimum of 10
- Pricing was unavailable during discovery
- Georgia and Germany remain inferred rather than confirmed

### Allianz Care
- Pricing signals exist, but both Georgia and Germany remain unknown in the consolidated package
- The hardened ledger records a blocked-or-failed family quote flow with the next step `solve_challenge_or_use_clean_browser_session`
- No public policy document was found in the reviewed sources

### Now Health International
- Georgia and Germany both remain unknown
- The hardened ledger records the family quote flow as `gated_before_family_specific_result` with next step `use_broker_or_sales_contact`
- No public policy document was found in the reviewed sources

### Genki and IMG (International Medical Group)
These are not excluded, but both are held back by product-fit ambiguity already recorded in the local artifacts.
- Genki: the notes say the specific Native/Resident product still needs verification that it is a full expat-medical fit rather than a lighter nomad-oriented product
- IMG: the notes say some IMG products may be travel-medical rather than full expat medical

## Packaging conclusion

The completion pass is now coherent for the exact 14 priority candidates already in scope. The strongest locally packaged names for the next shortlist stage are the ones with the best combination of geography evidence, pricing visibility, and/or public document access, but nearly every candidate still carries at least one unresolved gate in Georgia-residence fit, worldwide-excluding-U.S. confirmation, family-specific pricing, or policy-document support.

Nothing in the local completion evidence removes the need to carry those uncertainty labels forward exactly as recorded.