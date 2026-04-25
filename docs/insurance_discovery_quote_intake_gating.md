# Insurance discovery: quote-intake gating requirements

Discovery-only companion ledger for AC 50003 sub-AC 3. This does not rank plans; it records the visible quote-intake requirements for each staged candidate, including eligibility prechecks, required quote-form fields, and whether contact details or household data appear to be required before pricing or deeper plan output.

Machine-readable source: `data/insurance_discovery/quote_intake_gating_requirements.discovery.json`

Summary counts:
- verified_total_unique_candidates: 27
- eligibility_precheck_observed_count: 18
- contact_submission_required_before_pricing_true_count: 4
- contact_submission_required_before_pricing_conditional_count: 1
- contact_submission_required_before_pricing_unknown_count: 13
- household_submission_required_before_pricing_true_count: 5
- household_submission_required_before_pricing_unknown_count: 13
- email_required_before_quote_output_observed_count: 5
- phone_required_before_quote_output_observed_count: 1
- quote_intake_path_type_counts:
  - broker_led_entry_no_insurer_quote_captured: 1
  - broker_led_plan_page: 1
  - challenge_wall_before_quote: 1
  - legacy_brand_redirect: 1
  - mandatory_signup_before_quote: 1
  - marketing_page_to_member_or_b2b_paths: 1
  - multi_step_quote_request_with_advisor_followup: 1
  - multi_step_quote_requires_email_before_proceeding: 1
  - multi_step_quote_with_contact_and_family_basics_before_plan_step: 1
  - public_country_selector: 1
  - public_information_and_sample_prices_only: 1
  - public_information_page_only: 1
  - public_marketing_and_brochure_only: 1
  - public_multi_step_quote_and_application: 1
  - public_plan_cards_with_price_links: 1
  - public_plan_comparison_no_quote_entry_captured: 1
  - public_plan_comparison_with_quote_links: 1
  - public_plan_finder: 1
  - public_plan_navigation_only: 1
  - public_plan_page_with_quote_link_but_downstream_not_captured: 1
  - public_profile_and_region_selector: 1
  - public_questionnaire_recommendation_flow: 1
  - public_quick_quote_with_manual_fallback_contact_capture: 1
  - public_quote_country_selector: 1
  - public_quote_link_visible_but_fields_not_captured: 1
  - public_router_with_duration_precheck: 1
  - technical_barrier_on_external_quote_tool: 1

| Candidate | Intake path | Eligibility precheck | Required fields observed | Contact required before pricing/details? | Household data before pricing/details? | Evidence snapshot |
|---|---|---|---|---|---|---|
| cigna_global | public_quote_country_selector | Policy residence / living country must be selected before continuing. | Where will you be living for the duration of the policy? | False | False | Where will you be living for the duration of the policy?* |
| allianz_care | challenge_wall_before_quote | Challenge validation blocks quote contents before any customer fields became visible. | none observed / not captured | unknown | unknown | The captured quote URL returned a page titled “Challenge Validation” instead of quote fields. |
| bupa_global | public_country_selector | Residence country selection is required before seeing plan availability in region. | Tell us where you'll be living when you want your plan to start.; Select country | False | False | Tell us where you'll be living when you want your plan to start. |
| axa_global_healthcare | public_router_with_duration_precheck | AXA first screens by intended cover length and whether the user wants travel insurance or international healthcare insurance.; Less than 3 months is screened out from this product. | How long do you need cover for? (<3 months / 3-11 months / 12 months or over); What type of insurance are you looking for? (Travel Insurance / International Healthcare Insurance) | False | False | Less than 3 months / 3-11 months / 12 months or over |
| april_international | public_plan_finder | Public entry asks the user to choose cover type, country, and language before deeper quote output. | Cover type; Country; Language | False | False | The page is navigable without signup and lets users choose cover type, country, and language before any observed account gate. |
| william_russell | public_quote_link_visible_but_fields_not_captured | none observed / not captured | none observed / not captured | unknown | unknown | William Russell says users can “Use the online quote tool” and that prices are shown within minutes. |
| msh_international | public_profile_and_region_selector | User must confirm location region and select profile type before deeper comparison/offer flow. | Confirm your location (Europe / America / Mena / Asia); Tell us about yourself. You are ...; An individual / A company / NGO / TPA insurer / Broker / Medical provider | False | False | Confirm your location |
| now_health_international | public_plan_comparison_no_quote_entry_captured | none observed / not captured | none observed / not captured | unknown | unknown | Now Health’s plan-comparison page is publicly readable and shows product families, benefit tables, and document-library references. |
| img_global | public_plan_cards_with_price_links | none observed / not captured | none observed / not captured | False | False | Global Medical Gold — Buy Now / See Price; Global Medical Platinum — Buy Now / See Price; Global Medical Silver — Buy Now / See Price. |
| aetna_international | marketing_page_to_member_or_b2b_paths | No public individual-family quote intake was verified; observed path is oriented around members, brokers, employers, or portal handoff. | none observed / not captured | unknown | unknown | The Aetna page is structured around members, brokers, and employers rather than a public individual-family quote form. |
| foyer_global_health | public_plan_comparison_with_quote_links | none observed / not captured | none observed / not captured | unknown | unknown | Receive a quote / Get a quick quote / Apply now |
| henner | public_information_page_only | none observed / not captured | none observed / not captured | unknown | unknown | We have developed solutions to cover your health costs and your access to quality healthcare abroad. |
| globality_health | public_multi_step_quote_and_application | Direct-business-only notice is shown.; User must waive personal consultation or contact support before proceeding.; Applicant nationality, future residency, and signing-country location are part of the entry steps. | Date of birth; Nationality; Policy start date; Country of future residency; Currency; Would you like to add another member before calculating the quote?; Are you currently located in ... ?; Country where the application is signed; Consent to waive a personal consultation | False | True | Get a quotation in seconds. Get your health insurance in minutes. |
| morgan_price | public_quick_quote_with_manual_fallback_contact_capture | Online quote asks residence, currency, age, nationality, and number of dependents.; If online quote is unavailable for the circumstances, the flow falls back to manual quote request. | Country Of Residence; Currency; Policy Holder Age; Nationality; Dependents | conditional | True | Country Of Residence / Currency / Policy Holder Age / Nationality / Dependents |
| passportcard_global | public_information_and_sample_prices_only | none observed / not captured | none observed / not captured | False | False | PassportCard’s official page publicly shows plan tiers, benefit highlights, and example monthly EUR prices. |
| vumi | public_plan_navigation_only | none observed / not captured | none observed / not captured | unknown | unknown | VUMI’s homepage publicly lists health plans and regional selection paths without requiring login to read plan positioning. |
| integra_global | broker_led_entry_no_insurer_quote_captured | Only broker-led discovery evidence was captured, so insurer intake requirements remain unresolved. | none observed / not captured | unknown | unknown | The staged public evidence is a broker page, not an official insurer quote flow. |
| medihelp_international | public_plan_page_with_quote_link_but_downstream_not_captured | none observed / not captured | none observed / not captured | unknown | unknown | MediHelp’s individual-plans page is publicly accessible and shows both “Get a quote” and “BUY ONLINE.” |
| alc_health | legacy_brand_redirect | ALC appears to route new business away from the legacy brand, so direct new-business intake requirements were not captured on ALC itself. | none observed / not captured | unknown | unknown | Public evidence shows a legacy insurer-branded page, but new-business contact is redirected to IMG rather than retained on the original brand path. |
| geoblue_xplorer_bcbs_global_solutions | broker_led_plan_page | Discovery is broker-led, not insurer-led, so direct quote-intake fields were not verified from an official insurer quote flow. | none observed / not captured | unknown | unknown | The captured discovery evidence is a broker page summarizing BCBS Global Solutions / former GeoBlue Xplorer. |
| safetywing_nomad_complete | mandatory_signup_before_quote | Account creation or login is the first gate before quote contents become visible. | Sign in with Google OR Email + Password to continue | True | False | Your global coverage starts here |
| genki_native | public_questionnaire_recommendation_flow | Genki screens for travel duration and excludes trips shorter than one month.; Questionnaire asks whether user is planning travel or already abroad, main destination, home-country healthcare access, and long-term illness preference before recommendation. | Planning to travel or already abroad; How long will you be abroad? (1-12 months / 1 year+); Main destination; Do you have access to healthcare in your home country?; What if you get a serious injury or illness and need long-term treatment and recovery? | False | False | We don't cover trips shorter than one month. |
| acs_expat | multi_step_quote_request_with_advisor_followup | ACS first asks destination country and intended length/stay category.; Coverage need, currency, and beneficiary composition are captured before advisor follow-up. | Destination country; I am living abroad... (up to 12 months / Working Holiday Program / more than one year); What are your coverage needs?; I would like... (contract currency); I am the only beneficiary; Add my spouse; Add my children; Number of children under 18; Full name; Date of birth; Country of nationality | True | True | Request your free, no-obligation expat health insurance quote |
| hci_group_health_protect | technical_barrier_on_external_quote_tool | A technical/availability barrier prevented capture of quote questions. | none observed / not captured | unknown | unknown | Internet is unstable. Please check your connection. |
| expatriate_group_global_health | multi_step_quote_requires_email_before_proceeding | Quote requires people-to-insure count, area of cover, and dates of birth before proceeding.; Supports explicit worldwide excluding USA area selection. | Number of people to insure; Area of Cover (Area 1 / Area 2 worldwide excluding the USA / Area 3 worldwide); Date of birth for each person; Email | True | True | Please enter your email address below to receive a copy of your quotation. |
| wellaway_expat | multi_step_quote_with_contact_and_family_basics_before_plan_step | Step 1 requires coverage start, destination, state, identity, and consent fields before the Plans step.; Family/dependent composition is collected at the basics stage. | Coverage Start Date; Destination; State; First Name; Last Name; Applicant's Email; Date of Birth; Sex; Nationality; French Social Security Number; Origin Phone; Destination Phone; Would you like to combine your CFE Coverage with your WellAway?; Add your spouse/domestic partner to the policy; Number of dependents; Date of birth for spouse/dependents; Privacy consent | True | True | 1 Basics / 2 Plans / 3 Specifics / 4 Extras / 5 Locations / 6 Health / 7 Payment |
| securus_global_health_cover | public_marketing_and_brochure_only | none observed / not captured | none observed / not captured | unknown | unknown | Securus’s page publicly exposes marketing copy and brochure PDFs without requiring login. |
