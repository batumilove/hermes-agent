# Insurance discovery: account-access friction

Discovery-only companion ledger for AC 50002 sub-AC 2. This does not rank plans; it records what public friction was visible before plan details, quote entry, or application steps could be accessed for each staged candidate.

Machine-readable source: `data/insurance_discovery/account_access_friction.discovery.json`

Summary counts:
- friction_level.high: 9
- friction_level.low: 11
- friction_level.moderate: 7
- forced_signup_before_quote_or_plan_details: 1
- mandatory_account_creation_before_quote_or_plan_details: 1
- login_wall_before_quote_or_plan_details: 5
- email_required_before_quote_output_observed: 5
- phone_required_before_quote_output_observed: 1
- quote_stage_unknown_due_to_no_public_quote_path_or_incomplete_capture: 5

| Candidate | Friction classification | Level | Signup/account wall | Email/phone before quote | Evidence snapshot |
|---|---|---|---|---|---|
| cigna_global | public_quote_form_accessible_without_account_at_entry | low | none observed at entry | none observed / unknown | Quote page says “Get a quote in just 2 minutes” and immediately asks “Where will you be living for the duration of the policy?” on a public country selector page. |
| allianz_care | bot_or_challenge_validation_wall_before_quote_contents | high | login/challenge wall | none observed / unknown | The captured quote URL returned a page titled “Challenge Validation” instead of quote fields. |
| bupa_global | public_country_selector_accessible_without_account | low | none observed at entry | none observed / unknown | The official page publicly exposes a country selector with Georgia and Germany visible and a “See plans” action. |
| axa_global_healthcare | public_quote_router_accessible_without_account | low | none observed at entry | none observed / unknown | AXA’s public quote page asks “What length of cover are you looking for?” and offers online quote links plus callback and phone options. |
| april_international | public_plan_finder_accessible_without_account | low | none observed at entry | none observed / unknown | APRIL states “International health insurance for everyone, 100% online” and presents a public “Find a plan / Get a quote” selector on the landing page. |
| william_russell | public_quote_link_and_marketing_details_without_account | low | none observed at entry | none observed / unknown | William Russell says users can “Use the online quote tool” and that prices are shown within minutes. |
| msh_international | public_profile_selection_with_existing_member_login_option | moderate | none observed at entry | none observed / unknown | MSH’s page is publicly accessible and starts with “Tell us about yourself. You are” plus profile choices such as individual, company, broker, and NGO. |
| now_health_international | public_plan_comparison_without_public_quote_gate_verified | low | none observed at entry | none observed / unknown | Now Health’s plan-comparison page is publicly readable and shows product families, benefit tables, and document-library references. |
| img_global | public_plan_details_with_buy_now_and_price_links | low | none observed at entry | none observed / unknown | IMG publicly shows plan cards with “Buy Now” and “See Price” links for Global Medical plans. |
| aetna_international | member_login_and_b2b_paths_visible_no_public_retail_quote_verified | high | login/challenge wall | none observed / unknown | The Aetna page is structured around members, brokers, and employers rather than a public individual-family quote form. |
| foyer_global_health | public_plan_comparison_with_quote_and_apply_links | low | none observed at entry | none observed / unknown | Foyer publicly exposes “Receive a quote,” “Get a quick quote,” and “Apply now” from the plan-comparison page. |
| henner | public_information_page_without_public_quote_path_verified | moderate | none observed at entry | none observed / unknown | Henner’s individuals-and-families page is publicly readable and explains the offering without requiring login. |
| globality_health | public_quote_application_flow_with_waiver_consent | moderate | none observed at entry | none observed / unknown | The application starts publicly with “Get a quotation in seconds. Get your health insurance in minutes.” and shows steps from Quote through Confirmation. |
| morgan_price | public_quick_quote_with_email_capture_and_manual_fallback | moderate | none observed at entry | email | Morgan Price says it is “quick and easy to get an instant quote” via the quick-quote form. |
| passportcard_global | public_plan_details_and_sample_prices_without_quote_path_verified | low | none observed at entry | none observed / unknown | PassportCard’s official page publicly shows plan tiers, benefit highlights, and example monthly EUR prices. |
| vumi | public_plan_navigation_with_separate_member_and_agent_portals | moderate | none observed at entry | none observed / unknown | VUMI’s homepage publicly lists health plans and regional selection paths without requiring login to read plan positioning. |
| integra_global | broker_led_quote_handoff | high | none observed at entry | none observed / unknown | The staged public evidence is a broker page, not an official insurer quote flow. |
| medihelp_international | public_plan_page_with_quote_link_and_buy_online_login | moderate | login/challenge wall | none observed / unknown | MediHelp’s individual-plans page is publicly accessible and shows both “Get a quote” and “BUY ONLINE.” |
| alc_health | legacy_site_with_member_login_and_new_business_redirect | high | login/challenge wall | none observed / unknown | ALC’s site remains publicly viewable and includes a MyALC claims/member account link. |
| geoblue_xplorer_bcbs_global_solutions | broker_led_plan_page | high | none observed at entry | none observed / unknown | The captured discovery evidence is a broker page summarizing BCBS Global Solutions / former GeoBlue Xplorer. |
| safetywing_nomad_complete | mandatory_account_creation_or_login_before_quote | high | forced signup, account creation, login/challenge wall | email | The captured entry page is a signup screen headed “Your global coverage starts here.” |
| genki_native | public_consultation_flow_without_account_at_entry | low | none observed at entry | none observed / unknown | Genki’s consultation starts publicly with “Answer a few quick questions” and says users can “See the price and sign up in minutes.” |
| acs_expat | contact_details_required_for_personalized_quote | high | none observed at entry | email | ACS invites users to “Request your free, no-obligation expat health insurance quote” through a multi-step request flow. |
| hci_group_health_protect | technical_or_availability_barrier_on_quote_tool | high | none observed at entry | none observed / unknown | The captured Outgrow quote URL did not expose quote questions; it returned an “Internet is unstable. Please check your connection” message. |
| expatriate_group_global_health | public_quote_requires_email_before_proceeding | moderate | none observed at entry | email | The quick-quote form collects people to insure, area of cover, and dates of birth, then says “Please enter your email address below to receive a copy of your quotation.” |
| wellaway_expat | quote_step_requires_email_phone_and_privacy_consent | high | none observed at entry | email, phone | WellAway Step 1 requires Applicant’s Email, Origin Phone, Destination Phone, nationality, DOB, and other basics before moving on. |
| securus_global_health_cover | public_brochure_and_marketing_access_without_quote_path_verified | low | none observed at entry | none observed / unknown | Securus’s page publicly exposes marketing copy and brochure PDFs without requiring login. |
