import json
import pathlib

base = pathlib.Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
shared = json.loads((base / 'data/insurance_discovery/shared_candidate_staging.discovery.json').read_text())
source = json.loads((base / 'data/insurance_discovery/candidate_source_ledger.discovery.json').read_text())
source_map = {r['candidate_id']: r for r in source['records']}

custom = {
    'cigna_global': {
        'status': 'georgia_explicitly_listed',
        'summary': 'Official quote country selector explicitly lists both Georgia and Germany as selectable countries.',
        'evidence': [
            "Cigna's quote country selector HTML includes Georgia and Germany as adjacent options, giving direct public evidence that Georgia can at least be entered in the residence/selection flow.",
            'The main site also exposes a Where We Cover destination list with a Germany page and broader global positioning, but Georgia evidence in this run comes from the quote selector rather than a dedicated country page.'
        ],
        'refs': [
            {
                'source_url': 'https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html',
                'source_title': 'Cigna Global quote country selector',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The HTML country list includes “Georgia” and “Germany” as selectable options.'
            },
            {
                'source_url': 'https://www.cignaglobal.com/',
                'source_title': 'International Health Insurance & Global Medical Cover | Cigna',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Cigna markets international health insurance for individuals and families and says it supports customers worldwide through a large medical network.'
            }
        ],
        'uncertainty': [
            'Selector presence does not by itself prove final underwriting acceptance or pricing for a German family resident in Georgia.'
        ]
    },
    'allianz_care': {
        'status': 'georgia_not_named_global_positioning_only',
        'summary': 'Official public page confirms long-stay international cover for people living abroad, but this run did not verify Georgia in the quote flow because the quote page was challenge-protected.',
        'evidence': [
            'Allianz says its Care plans are for people working, studying, or living abroad for long periods and provides an official long-term quote path.',
            'The public quote URL returned a challenge-validation page in this run, so Georgia-specific selector evidence could not be captured publicly.'
        ],
        'refs': [
            {
                'source_url': 'https://www.allianzcare.com/en/personal-international-health-insurance.html',
                'source_title': 'International Health Insurance for Individuals | Allianz',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Allianz says its Care plans are designed for people who spend long periods overseas and offers cover for people working, studying, or living abroad.'
            },
            {
                'source_url': 'https://my.allianzcare.com/myquote/5',
                'source_title': 'Allianz Care quote page',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'Public access in this run surfaced a challenge-validation page rather than a readable country selector.'
            }
        ],
        'uncertainty': [
            'Georgia-resident eligibility remains unverified because the public quote flow was challenge-gated during this run.'
        ]
    },
    'bupa_global': {
        'status': 'georgia_explicitly_listed',
        'summary': 'Existing official-source evidence already captured Georgia and Germany in Bupa Global’s individuals-and-families selector.',
        'evidence': [
            "Bupa Global's official individuals-and-families selector publicly lists Georgia and Germany in the residence-country dropdown.",
            'This is one of the clearest direct-public indicators that the family can at least test Georgia residence in an official flow.'
        ],
        'refs': [
            {
                'source_url': 'https://www.bupaglobal.com/en/private-health-insurance',
                'source_title': 'Private Health Insurance & Medical Insurance | Bupa Global',
                'source_kind': 'official insurer page / selector',
                'evidence_excerpt': 'The official individuals-and-families selector asks where the customer will be living and publicly lists both Georgia and Germany in the residence-country dropdown.'
            }
        ],
        'uncertainty': [
            'Need full quote output or policy wording to verify available plans and final acceptance for Georgian residents.'
        ]
    },
    'axa_global_healthcare': {
        'status': 'georgia_not_named_area_of_cover_structure',
        'summary': 'AXA publicly documents area-of-cover logic and optional exclusion of the USA, but Georgia itself was not named in the captured public text.',
        'evidence': [
            'AXA says customers can exclude the USA from their area of cover, which is relevant for worldwide-ex-US screening.',
            'AXA states treatment outside the selected area of cover is not covered, so Georgia fit depends on selecting an area that includes Georgia; this was not directly resolved from captured text.'
        ],
        'refs': [
            {
                'source_url': 'https://www.axaglobalhealthcare.com/en/international-health-insurance/',
                'source_title': 'International Health Insurance Plans: AXA Global Healthcare',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'AXA says it will give customers the option to exclude the USA from their area of cover, while treatment outside the chosen area of cover is not covered.'
            },
            {
                'source_url': 'https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default',
                'source_title': 'AXA Global Healthcare quote guide',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'AXA explains that local insurance is limited to the country of residence, while international plans provide private healthcare across the globe.'
            }
        ],
        'uncertainty': [
            'Need quote or policy wording to verify that Georgia falls within the relevant AXA area-of-cover choice for this family.'
        ]
    },
    'april_international': {
        'status': 'georgia_explicitly_listed',
        'summary': 'APRIL’s public page and entry flow explicitly expose Georgia in the country-of-coverage list.',
        'evidence': [
            "APRIL's public page text includes a country-of-coverage list containing Georgia and Germany.",
            'APRIL also states long-term plans provide worldwide cover depending on the chosen area of cover, making Georgia-relevance direct but still area-dependent.'
        ],
        'refs': [
            {
                'source_url': 'https://www.april-international.com/en/long-term-international-health-insurance',
                'source_title': 'Long-term expat international health insurance | APRIL International',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The public country-of-coverage list includes Georgia and Germany, and APRIL says worldwide cover depends on the chosen area of cover.'
            },
            {
                'source_url': 'https://www.april-international.com/en',
                'source_title': 'APRIL International home / entry page',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'APRIL states it covers 180+ countries and the visible country selector includes Georgia and Germany.'
            }
        ],
        'uncertainty': [
            'Need quote output or benefits guide to confirm which APRIL area-of-cover choice applies to Georgia-resident German citizens.'
        ]
    },
    'william_russell': {
        'status': 'georgia_not_named_zone_based_cover',
        'summary': 'William Russell publicly shows region-based and worldwide cover examples, but Georgia was not named directly in captured text.',
        'evidence': [
            'William Russell says customers can buy anything from worldwide cover to one-region cover.',
            'Public pricing examples show that country of residence and coverage outside country of residence are core underwriting variables, which makes Georgia relevant even though it was not directly named.'
        ],
        'refs': [
            {
                'source_url': 'https://www.william-russell.com/international-health-insurance/',
                'source_title': 'International Health Insurance | William Russell',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The page says customers have different coverage options ranging from worldwide cover to cover in just one region, and examples are priced by country of residence and coverage area.'
            }
        ],
        'uncertainty': [
            'Need quote or plan wording to test Georgia specifically.'
        ]
    },
    'msh_international': {
        'status': 'georgia_not_named_mobility_positioning_only',
        'summary': 'MSH positions First’Expat+ as international cover for mobility needs, but no Georgia-specific country evidence was visible in this run.',
        'evidence': [
            'MSH markets itself as a healthcare partner for mobility needs and offers a quote path for international individual plans.',
            'The public page showed region-selection context including an Asia site version but did not expose a country dropdown naming Georgia in the extracted text.'
        ],
        'refs': [
            {
                'source_url': 'https://www.msh-intl.com/en/individuals/',
                'source_title': 'International Health Insurance for Individuals - MSH',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'MSH describes First’Expat+ as a comprehensive, flexible international health insurance plan and frames itself as the healthcare partner for mobility needs.'
            },
            {
                'source_url': 'https://www.msh-intl.com/en/comparisons-offers-msh',
                'source_title': 'MSH find your plan / quote path',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'MSH offers a public quote/comparison path for individual international plans, but Georgia was not visible in captured country-selection text.'
            }
        ],
        'uncertainty': [
            'Georgia-resident eligibility still needs quote-flow or document confirmation.'
        ]
    },
    'now_health_international': {
        'status': 'georgia_not_named_country_of_residence_mechanics',
        'summary': 'Now Health exposes international-plan geography mechanics tied to country of nationality or residence, but Georgia was not named directly in the captured page text.',
        'evidence': [
            'The public comparison table repeatedly refers to repatriation or transportation to the country of nationality or residence.',
            'Now Health also lists plan-issuance restrictions for some UAE residence-visa holders, showing country-of-residence matters even though Georgia was not named.'
        ],
        'refs': [
            {
                'source_url': 'https://www.now-health.com/en/insurance-plans/',
                'source_title': 'Compare International Health Insurance Plans from Now Health',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The public comparison table references repatriation to the country of nationality or residence and includes residence-specific issuance notes for some markets.'
            }
        ],
        'uncertainty': [
            'Need member handbook or quote flow to verify whether Georgia is an accepted country of residence.'
        ]
    },
    'img_global': {
        'status': 'georgia_explicitly_listed',
        'summary': 'IMG’s public HTML includes Georgia and Germany in country-selection lists on both the main site and international health page.',
        'evidence': [
            'IMG country lists publicly include Georgia and Germany, providing direct evidence that Georgia can be selected in IMG’s public web flow.',
            'IMG also says international health plans provide medical coverage worldwide and are suitable for expats and their families.'
        ],
        'refs': [
            {
                'source_url': 'https://www.imglobal.com/international-health-insurance',
                'source_title': 'IMG international health insurance',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The page HTML contains country options including Georgia and Germany and says IMG international health plans provide medical coverage worldwide.'
            },
            {
                'source_url': 'https://www.imglobal.com/',
                'source_title': 'International Health & Travel Medical Insurance - IMG',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'IMG states its international health insurance is for expats and global citizens living or working internationally.'
            }
        ],
        'uncertainty': [
            'Need product-specific quote or plan wording to confirm Georgia-resident acceptance for full expat medical products, not just travel products.'
        ]
    },
    'aetna_international': {
        'status': 'georgia_not_named_global_network_only',
        'summary': 'Aetna International publicly shows worldwide telehealth or network reach, but no Georgia-specific country selector evidence was captured.',
        'evidence': [
            'Aetna says members can access care anywhere, anytime through global solutions and worldwide telehealth services.',
            'This is relevant but weaker than a country selector because Georgia was not named or surfaced in an individual quote flow during this run.'
        ],
        'refs': [
            {
                'source_url': 'https://www.aetnainternational.com/',
                'source_title': 'Home | Aetna International',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Aetna says members can access care anywhere, anytime through the member website and worldwide telehealth services.'
            },
            {
                'source_url': 'https://www.aetna.com/employers-organizations/aetna-international-insurance.html',
                'source_title': 'Aetna International insurance overview',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The related Aetna international page did not provide captured Georgia-specific country evidence in this run.'
            }
        ],
        'uncertainty': [
            'Need individual or family market evidence or quote path showing Georgia residence support.'
        ]
    },
    'foyer_global_health': {
        'status': 'georgia_destination_reference',
        'summary': 'Foyer publicly references Georgia in dedicated destination and health guides, which is useful country-specific discovery evidence even though it is not a formal eligibility table.',
        'evidence': [
            'Foyer’s official site highlights Georgia among popular destination guides, including Georgia living, health, and budget guides.',
            'Foyer also positions its plans as worldwide and suitable for expats and families going abroad.'
        ],
        'refs': [
            {
                'source_url': 'https://www.foyerglobalhealth.com/',
                'source_title': 'Expat Health Insurance | Foyer Global Health',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The official site lists destination guides for Georgia, including living, health, and budget content, and markets worldwide expat-family coverage.'
            },
            {
                'source_url': 'https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/',
                'source_title': 'Foyer plan comparison',
                'source_kind': 'official plan comparison page',
                'evidence_excerpt': 'The plan comparison includes benefits tied to return travel to the country of residence after evacuation.'
            }
        ],
        'uncertainty': [
            'Georgia guide presence does not prove plan eligibility or underwriting acceptance for Georgia residents.'
        ]
    },
    'henner': {
        'status': 'georgia_not_named_global_network_only',
        'summary': 'Henner shows individuals-and-families living-abroad positioning and a 240-country healthcare-professional network, but Georgia was not explicitly captured.',
        'evidence': [
            'Henner says it developed personal-insurance expertise for people living abroad and supports members wherever they are.',
            'Henner also states it works with healthcare professionals in 240 countries, which is geography-relevant but not a Georgia-specific eligibility proof.'
        ],
        'refs': [
            {
                'source_url': 'https://www.henner.com/en/customers/individuals-families/',
                'source_title': 'Individuals & Families - Henner',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Henner says it supports people living abroad and has a network of healthcare professionals in 240 countries.'
            }
        ],
        'uncertainty': [
            'Need product-specific quote or brochure evidence naming Georgia or clarifying country-of-residence eligibility.'
        ]
    },
    'globality_health': {
        'status': 'georgia_residency_dropdown_unresolved',
        'summary': 'Globality’s application flow uses a broad country list for future residency, but the extracted summary did not explicitly name Georgia even though the flow is country-driven.',
        'evidence': [
            'The application requires country of future residency and country where the application is signed, using a broad country or territory list.',
            'The page warns that some future-residency countries may not be supported for quotation, which is directly relevant to Georgia screening.'
        ],
        'refs': [
            {
                'source_url': 'https://application.globality-health.com/?locale=en',
                'source_title': 'Globality Health application / quote flow',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The application requires a country of future residency from a broad country list and warns: “Please choose another country of future residency for the quotation.”'
            },
            {
                'source_url': 'https://www.globality-health.com/',
                'source_title': 'International Health Insurance for Expats - Globality Health',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Globality positions itself as an international health insurer for expatriates, but the home page did not yield a Georgia-specific excerpt in this run.'
            }
        ],
        'uncertainty': [
            'Need a live test of Georgia as future residency to determine whether the quote flow accepts or rejects it.'
        ]
    },
    'morgan_price': {
        'status': 'georgia_residence_rules_relevant_not_verified',
        'summary': 'Morgan Price publishes unusually concrete residence rules and area-of-cover wording, but Georgia itself was not named in the captured public text.',
        'evidence': [
            'Morgan Price states some products are available only where applicants’ primary residence is in specified countries or regions, proving residence-country rules are material.',
            'The page also says Flexible Choices applicants must reside outside the EU or EEA and includes emergency treatment outside area of cover and home-country cover excluding USA, all directly relevant to a Germany-citizen and Georgia-resident family.'
        ],
        'refs': [
            {
                'source_url': 'https://www.morgan-price.com/individual/',
                'source_title': 'International Healthcare Insurance Plans for Expats | Morgan Price International Healthcare',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Morgan Price states some plans are only available to applicants whose primary residence is in named countries, and Flexible Choices applicants must reside outside the EU or EEA.'
            },
            {
                'source_url': 'https://morgan-price.com/quick-quote/',
                'source_title': 'Morgan Price quick quote',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The public quote flow makes Country of Residence a core input and says some circumstances cannot be quoted online.'
            }
        ],
        'uncertainty': [
            'Need Georgia-specific quote attempt or policy wording review to confirm whether Georgia residence fits available Morgan Price products.'
        ]
    },
    'passportcard_global': {
        'status': 'georgia_not_named_worldwide_ex_us_structure',
        'summary': 'PassportCard provides clear worldwide-ex-USA territory structure and Germany home-visit wording, but Georgia itself was not captured in public text.',
        'evidence': [
            'PassportCard states contract currency is EUR for coverage worldwide excluding USA territories and USD for policies including the USA.',
            'It also says insureds traveling to countries in zones 0–3 can receive treatment in Germany during home visits after relocation, which is highly relevant for Germany but does not itself verify Georgia as destination.'
        ],
        'refs': [
            {
                'source_url': 'https://www.passportcard.de/en/international-health-insurance/',
                'source_title': 'International health insurance for expats & nomads | PassportCard',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'PassportCard says the contract currency is EUR for coverage worldwide excluding USA territories, and that some zones include treatment in Germany during home visits.'
            }
        ],
        'uncertainty': [
            'Need zone table or quote flow confirming whether Georgia sits in a supported destination zone.'
        ]
    },
    'vumi': {
        'status': 'georgia_not_named_worldwide_only',
        'summary': 'VUMI markets worldwide coverage for individuals and families, but no Georgia-specific country evidence was captured.',
        'evidence': [
            'VUMI’s homepage repeatedly says its international health insurance provides worldwide coverage.',
            'The company frames its plans as protecting families anywhere, anytime, but that is still weaker than a country-specific selector.'
        ],
        'refs': [
            {
                'source_url': 'https://www.vumigroup.com/',
                'source_title': 'Home - VUMI',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'VUMI says it offers international health insurance with worldwide coverage and that families are protected anywhere, anytime.'
            }
        ],
        'uncertainty': [
            'Need plan brochure, application flow, or regional site to verify Georgia residence explicitly.'
        ]
    },
    'integra_global': {
        'status': 'georgia_not_named_area_of_cover_structure',
        'summary': 'Integra Global clearly exposes worldwide including or excluding US+Canada cover options, but Georgia itself was not named.',
        'evidence': [
            'Integra says members can choose worldwide coverage including the US and Canada or worldwide coverage excluding the US and Canada.',
            'It also states the plan will cover members worldwide depending on the chosen region and the country where they intend to stay longest.'
        ],
        'refs': [
            {
                'source_url': 'https://hcigroupglobal.com/integra-global/',
                'source_title': 'Integra Global | HCI Group Global',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Integra says Cover 1 is worldwide including the US and Canada, Cover 2 is worldwide excluding the US and Canada, and coverage depends on the chosen region.'
            }
        ],
        'uncertainty': [
            'Need quote or eligibility wording to verify Georgia-resident acceptance.'
        ]
    },
    'medihelp_international': {
        'status': 'georgia_not_named_worldwide_only',
        'summary': 'MediHelp describes My Global Health as international coverage with worldwide treatment choice, but Georgia was not named directly.',
        'evidence': [
            'MediHelp says its individual plans provide international coverage and top medical services worldwide.',
            'The page emphasizes freedom to choose doctor and clinic worldwide, which is relevant but not Georgia-specific.'
        ],
        'refs': [
            {
                'source_url': 'https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans',
                'source_title': 'Individual plans | MediHelp International',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'MediHelp describes My Global Health as international coverage with top medical services worldwide and freedom to choose the doctor and clinic.'
            }
        ],
        'uncertainty': [
            'Need brochure or application evidence naming accepted residence countries including or excluding Georgia.'
        ]
    },
    'alc_health': {
        'status': 'georgia_not_named_country_pages_elsewhere',
        'summary': 'ALC Health shows strong global-country marketing and a Germany regional page, but Georgia itself was not surfaced in captured text.',
        'evidence': [
            'ALC says it provides global cover across the world and explicitly references multiple countries plus a Germany regional page.',
            'This suggests geography-specific sales structure, but Georgia was not named among the captured country references.'
        ],
        'refs': [
            {
                'source_url': 'https://www.alchealth.com/',
                'source_title': 'ALC Health home',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'ALC describes global cover across many named countries and regions and links to a Germany page and 2026 downloads.'
            }
        ],
        'uncertainty': [
            'Need Georgia-specific country page, quote result, or broker confirmation.'
        ]
    },
    'geoblue_xplorer_bcbs_global_solutions': {
        'status': 'georgia_not_named_outside_us_plan',
        'summary': 'GeoBlue’s living-abroad page gives a clear Outside-the-U.S. plan structure, but Georgia was not named directly in the captured text.',
        'evidence': [
            'The page offers an Outside the U.S. plan for people who do not need U.S. coverage, which is directly relevant to worldwide-ex-US screening.',
            'Germany appears in Schengen-insurance support text, but Georgia was not named or tied to residence eligibility.'
        ],
        'refs': [
            {
                'source_url': 'https://bcbsglobalsolutions.com/individuals-and-families/international-medical-insurance/living-abroad/worldwide-and-outside-us/',
                'source_title': 'Worldwide and Outside US | BCBS Global Solutions',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'BCBS Global Solutions says customers can choose Worldwide Premier for comprehensive coverage anywhere in the world or Outside the U.S. if they do not need U.S. coverage.'
            }
        ],
        'uncertainty': [
            'Need eligibility wording clarifying whether Georgia residents can buy the individual or family plan.'
        ]
    },
    'safetywing_nomad_complete': {
        'status': 'georgia_not_named_global_only',
        'summary': 'SafetyWing positions Nomad Insurance Complete for expats, digital nomads, and families with global provider access, but Georgia was not named.',
        'evidence': [
            'SafetyWing says Complete offers global coverage and lets members use any licensed provider at any clinic or hospital, private or public.',
            'The visible add-on logic references the U.S., Hong Kong, and Singapore rather than Georgia, so country-specific Georgia support remains unresolved.'
        ],
        'refs': [
            {
                'source_url': 'https://explore.safetywing.com/nomad-insurance-complete',
                'source_title': 'SafetyWing | Complete Health And Travel Insurance For Nomads',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'SafetyWing says Nomad Insurance Complete is made for digital nomads, expats, and families living abroad, with global coverage and any licensed provider worldwide.'
            },
            {
                'source_url': 'https://safetywing.com/signup?product=nomad-health',
                'source_title': 'SafetyWing signup',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The signup page exposes product enrollment but did not yield Georgia-specific country evidence in this run.'
            }
        ],
        'uncertainty': [
            'Need policy wording or quote-input testing to determine whether Georgia residence is accepted.'
        ]
    },
    'genki_native': {
        'status': 'georgia_explicitly_listed',
        'summary': 'Genki’s consultation flow explicitly lists Georgia and Germany and markets worldwide cover from any country.',
        'evidence': [
            'Genki’s public consultation flow contains Georgia and Germany in its country options.',
            'Genki also says it offers worldwide cover and that users can join from anywhere, which materially supports Georgia discovery.'
        ],
        'refs': [
            {
                'source_url': 'https://consultation.genki.world/v2',
                'source_title': 'Genki consultation flow',
                'source_kind': 'official quote / consultation page',
                'evidence_excerpt': 'The public options list includes Georgia and Germany, alongside messaging that users can join from anywhere.'
            },
            {
                'source_url': 'https://genki.world/',
                'source_title': 'Genki • Health Insurance for Digital Nomads',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Genki says it offers worldwide health insurance and covers treatment at every licensed healthcare provider worldwide.'
            }
        ],
        'uncertainty': [
            'Need product-specific terms to confirm family fit and whether long-term Georgia residence is acceptable for this exact plan.'
        ]
    },
    'acs_expat': {
        'status': 'georgia_not_named_worldwide_only',
        'summary': 'ACS Expat is publicly framed as worldwide expatriate health insurance with trips outside the selected coverage area, but Georgia was not named directly.',
        'evidence': [
            'ACS Expat says it covers international stays of 12+ months with worldwide healthcare access and support.',
            'ACS also says temporary stays worldwide are covered for up to 7 weeks outside the selected coverage area, which is geography-relevant but still not Georgia-specific.'
        ],
        'refs': [
            {
                'source_url': 'https://www.acs-ami.com/en/expat-health-insurance/acs-expat/',
                'source_title': 'ACS Expat',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'ACS Expat says the plan covers international stays of 12+ months, worldwide healthcare access and support, and temporary stays worldwide outside the selected coverage area.'
            },
            {
                'source_url': 'https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/',
                'source_title': 'ACS Expat quote request',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The quote path repeats ACS Expat’s worldwide expatriate-health positioning but did not expose Georgia-specific country input in this run.'
            }
        ],
        'uncertainty': [
            'Need policy wording or quote-flow testing to verify whether Georgia is within the selectable or accepted residence geography.'
        ]
    },
    'hci_group_health_protect': {
        'status': 'georgia_not_named_global_only',
        'summary': 'Health Protect is clearly an IPMI product for people living abroad, but Georgia was not named in the captured page text.',
        'evidence': [
            'HCI says Health Protect serves expatriates, digital nomads, international students, and holiday-home owners living abroad or traveling frequently.',
            'The page provides plan-benefits PDFs and quote entry points, but the captured content did not expose a Georgia country list or territorial exclusion table.'
        ],
        'refs': [
            {
                'source_url': 'https://hcigroupglobal.com/health-protect/',
                'source_title': 'International Healthcare | Healthcare International | HCI Global',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Health Protect is described as international healthcare for expatriates, digital nomads, international students, and others living abroad.'
            },
            {
                'source_url': 'https://hcigroup.outgrow.us/P21-2026',
                'source_title': 'HCI quote flow',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The quote path is public, but no Georgia-specific country-selection evidence was captured in this run.'
            }
        ],
        'uncertainty': [
            'Need quote-flow or PDF review to verify Georgia residence rules.'
        ]
    },
    'expatriate_group_global_health': {
        'status': 'georgia_regionally_implied_not_named',
        'summary': 'Expatriate Group’s public quote flow clearly defines Area 1, 2, and 3 geography, and Georgia is plausibly within Area 1’s Europe–Middle East–Africa–Asia–Oceania band, but Georgia was not named explicitly.',
        'evidence': [
            'The quote form lists Area 1 as Europe, Middle East, Africa, Asia and Oceania excluding China, Hong Kong and Singapore, Area 2 as Worldwide excluding the USA, and Area 3 as Worldwide.',
            'The page also states treatment outside the selected Area of Cover is limited, making the area definition directly relevant to Georgia screening.'
        ],
        'refs': [
            {
                'source_url': 'https://quote.expatriatehealthcare.com/healthcare/',
                'source_title': 'Expatriate Group healthcare quote',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The form offers Area 1 – Europe, Middle East, Africa, Asia and Oceania; Area 2 – Worldwide excluding the USA; and Area 3 – Worldwide.'
            },
            {
                'source_url': 'https://www.expatriatehealthcare.com/international-health-insurance/',
                'source_title': 'International health insurance | Expatriate Group',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'The public international health insurance page repeats the same area-of-cover structure and notes limited cover outside the selected area.'
            }
        ],
        'uncertainty': [
            'Georgia is inferred as falling within Area 1 geography but was not named directly; plan wording should confirm this.'
        ]
    },
    'wellaway_expat': {
        'status': 'georgia_explicitly_listed',
        'summary': 'Wellaway’s EXPAT quote flow explicitly lists Georgia and Germany in country selections.',
        'evidence': [
            'The public quote Step 1 HTML includes Georgia and Germany in the country list.',
            'The official EXPAT page positions the plan for individuals, families, and groups living abroad, making the selector evidence directly relevant to this family.'
        ],
        'refs': [
            {
                'source_url': 'https://portal.wellaway.com/Quote/EXPAT/Step1',
                'source_title': 'Wellaway EXPAT quote Step 1',
                'source_kind': 'official quote page',
                'evidence_excerpt': 'The country options include Georgia and Germany in the public quote form.'
            },
            {
                'source_url': 'https://www.wellaway.com/en/our-plans/expat/',
                'source_title': 'Expat health insurance plans for living abroad | Wellaway',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Wellaway markets EXPAT to individuals, families, and groups living abroad and links directly to the quote flow.'
            }
        ],
        'uncertainty': [
            'Need completed quote or brochure review to verify whether Georgia residence is fully accepted after underwriting.'
        ]
    },
    'securus_global_health_cover': {
        'status': 'georgia_not_named_worldwide_only',
        'summary': 'Securus publicly markets global health cover with treatment choice around the world, but Georgia-specific evidence was not captured.',
        'evidence': [
            'Securus says global health cover lets customers choose who, how, and where they are treated around the world.',
            'The page links benefit-plan PDFs, but no Georgia-specific country list or exclusion wording was captured in the HTML reviewed here.'
        ],
        'refs': [
            {
                'source_url': 'https://www.securus.co.uk/benefits/global-health-cover',
                'source_title': 'Securus global health cover',
                'source_kind': 'official insurer page',
                'evidence_excerpt': 'Securus says global health cover gives access to the best hospitals and doctors around the world and links its 2026 benefit-plan PDFs.'
            }
        ],
        'uncertainty': [
            'Need benefit-table or application review to verify whether Georgia is accepted as residence or treatment geography.'
        ]
    }
}

records = []
for idx, c in enumerate(shared['staging_candidates'], 1):
    cid = c['candidate_id']
    s = source_map[cid]
    extra = custom[cid]
    refs = list(extra['refs'])
    if s.get('policy_wording_or_pdf_url'):
        refs.append({
            'source_url': s['policy_wording_or_pdf_url'],
            'source_title': f"{c['normalized_insurer_name']} public brochure or policy PDF",
            'source_kind': 'public pdf link',
            'evidence_excerpt': 'Public PDF link captured in this discovery run; not fully parsed into this Georgia ledger unless quoted above.'
        })
    records.append({
        'index': idx,
        'candidate_id': cid,
        'insurer_name': c['normalized_insurer_name'],
        'plan_name': c['normalized_plan_name'],
        'candidate_type': c['candidate_type'],
        'georgia_coverage_status': extra['status'],
        'georgia_coverage_summary': extra['summary'],
        'direct_georgia_relevance_evidence': extra['evidence'],
        'supporting_source_refs': refs,
        'uncertainty_notes': extra['uncertainty']
    })

summary = {}
for r in records:
    summary[r['georgia_coverage_status']] = summary.get(r['georgia_coverage_status'], 0) + 1

out = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'georgia_coverage_evidence_ledger',
    'generated_from': 'data/insurance_discovery/shared_candidate_staging.discovery.json',
    'generated_by': 'gpt-5.4',
    'family_context': shared['family_context'],
    'status_legend': {
        'georgia_explicitly_listed': 'Georgia was directly observed in an official country selector or equivalent public plan or quote artifact.',
        'georgia_destination_reference': 'Georgia was directly referenced on an official destination or health guide, but not as a formal eligibility selector.',
        'georgia_regionally_implied_not_named': 'Public area-of-cover wording strongly suggests Georgia falls inside a named geography, but Georgia was not quoted verbatim.',
        'georgia_not_named_area_of_cover_structure': 'Public evidence gave relevant area-of-cover or territorial wording, but Georgia itself was not named.',
        'georgia_not_named_worldwide_ex_us_structure': 'Public evidence gave worldwide or worldwide-ex-US geographic structure relevant to Georgia, but Georgia was not named.',
        'georgia_not_named_country_of_residence_mechanics': 'Public evidence shows country-of-residence mechanics or restrictions, but Georgia was not named in captured text.',
        'georgia_not_named_global_positioning_only': 'Public evidence only confirmed broad international or living-abroad positioning; Georgia remains unverified.',
        'georgia_not_named_mobility_positioning_only': 'Public evidence confirmed international mobility positioning but no country-specific Georgia evidence was visible.',
        'georgia_not_named_global_network_only': 'Public evidence showed global provider networks or worldwide service access, but no Georgia-specific evidence.',
        'georgia_residency_dropdown_unresolved': 'A country-of-residency quote flow exists and appears country-driven, but Georgia acceptance was not directly confirmed in captured output.',
        'georgia_residence_rules_relevant_not_verified': 'Public sources show residence-country restrictions relevant to Georgia screening, but Georgia itself was not verified.',
        'georgia_not_named_country_pages_elsewhere': 'Public sources show geography-specific country pages or regional marketing, but not Georgia in captured evidence.',
        'georgia_not_named_outside_us_plan': 'Public evidence verifies a worldwide or outside-US structure but not Georgia specifically.',
        'georgia_not_named_global_only': 'Public evidence verifies worldwide or global positioning only, without Georgia-specific proof.',
        'georgia_not_named_zone_based_cover': 'Public evidence verifies region or zone-based geography logic, but Georgia was not named directly.'
    },
    'summary': {
        'verified_total_unique_candidates': len(records),
        'georgia_coverage_status_counts': summary,
        'notes': [
            'Discovery-only ledger: this artifact does not rank, recommend, or determine eligibility.',
            'For challenge-gated or JS-heavy quote flows, unresolved entries keep the uncertainty explicit rather than inferring acceptance.'
        ]
    },
    'records': records
}

path = base / 'data/insurance_discovery/georgia_coverage_evidence_ledger.discovery.json'
path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
print(path)
