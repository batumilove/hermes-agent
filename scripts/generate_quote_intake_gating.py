import json
from pathlib import Path

ROOT = Path('/home/ubuntu/.ouroboros/worktrees/hermes-agent/orch_63a162f0852d')
DATA_DIR = ROOT / 'data' / 'insurance_discovery'
DOCS_DIR = ROOT / 'docs'

friction = json.loads((DATA_DIR / 'account_access_friction.discovery.json').read_text())
access = json.loads((DATA_DIR / 'access_path_contact_models.discovery.json').read_text())

friction_by_id = {r['candidate_id']: r for r in friction['records']}
access_by_id = {r['candidate_id']: r for r in access['records']}

manual = {
    'cigna_global': {
        'quote_intake_path_type': 'public_quote_country_selector',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Policy residence / living country must be selected before continuing.'],
        'required_quote_form_fields_observed': ['Where will you be living for the duration of the policy?'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'partial_public_entry_only',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html', 'source_type': 'official quote page', 'excerpt': 'Where will you be living for the duration of the policy?*'},
            {'source_url': 'https://www.cignaglobal.com/quote/pages/quote/CountrySelector.html', 'source_type': 'official quote page', 'excerpt': 'Please enter where you will be living.'}
        ]
    },
    'allianz_care': {
        'quote_intake_path_type': 'challenge_wall_before_quote',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Challenge validation blocks quote contents before any customer fields became visible.'],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'not_visible_due_to_gate',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://my.allianzcare.com/myquote/5', 'source_type': 'official quote page', 'excerpt': 'The captured quote URL returned a page titled “Challenge Validation” instead of quote fields.'}
        ]
    },
    'bupa_global': {
        'quote_intake_path_type': 'public_country_selector',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Residence country selection is required before seeing plan availability in region.'],
        'required_quote_form_fields_observed': ['Tell us where you\'ll be living when you want your plan to start.', 'Select country'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'partial_public_entry_only',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.bupaglobal.com/en/private-health-insurance', 'source_type': 'official quote page', 'excerpt': 'Tell us where you\'ll be living when you want your plan to start.'},
            {'source_url': 'https://www.bupaglobal.com/en/private-health-insurance', 'source_type': 'official quote page', 'excerpt': 'See plans'}
        ]
    },
    'axa_global_healthcare': {
        'quote_intake_path_type': 'public_router_with_duration_precheck',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['AXA first screens by intended cover length and whether the user wants travel insurance or international healthcare insurance.', 'Less than 3 months is screened out from this product.'],
        'required_quote_form_fields_observed': ['How long do you need cover for? (<3 months / 3-11 months / 12 months or over)', 'What type of insurance are you looking for? (Travel Insurance / International Healthcare Insurance)'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_router_only',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default', 'source_type': 'official quote router', 'excerpt': 'Less than 3 months / 3-11 months / 12 months or over'},
            {'source_url': 'https://www.axaglobalhealthcare.com/en/international-health-insurance/get-a-quote/?camp=online-default', 'source_type': 'official quote router', 'excerpt': 'Travel Insurance / International Healthcare Insurance'}
        ]
    },
    'april_international': {
        'quote_intake_path_type': 'public_plan_finder',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Public entry asks the user to choose cover type, country, and language before deeper quote output.'],
        'required_quote_form_fields_observed': ['Cover type', 'Country', 'Language'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_entry_selector',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.april-international.com/en', 'source_type': 'official landing/plan finder', 'excerpt': 'The page is navigable without signup and lets users choose cover type, country, and language before any observed account gate.'}
        ]
    },
    'william_russell': {
        'quote_intake_path_type': 'public_quote_link_visible_but_fields_not_captured',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_marketing_and_examples',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.william-russell.com/international-health-insurance/', 'source_type': 'official plan page', 'excerpt': 'William Russell says users can “Use the online quote tool” and that prices are shown within minutes.'}
        ]
    },
    'msh_international': {
        'quote_intake_path_type': 'public_profile_and_region_selector',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['User must confirm location region and select profile type before deeper comparison/offer flow.'],
        'required_quote_form_fields_observed': ['Confirm your location (Europe / America / Mena / Asia)', 'Tell us about yourself. You are ...', 'An individual / A company / NGO / TPA insurer / Broker / Medical provider'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_profile_gate_only',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.msh-intl.com/en/comparisons-offers-msh', 'source_type': 'official quote entry', 'excerpt': 'Confirm your location'},
            {'source_url': 'https://www.msh-intl.com/en/comparisons-offers-msh', 'source_type': 'official quote entry', 'excerpt': 'Tell us about yourself. You are'}
        ]
    },
    'now_health_international': {
        'quote_intake_path_type': 'public_plan_comparison_no_quote_entry_captured',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_plan_details_visible',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.now-health.com/en/insurance-plans/', 'source_type': 'official plan comparison page', 'excerpt': 'Now Health’s plan-comparison page is publicly readable and shows product families, benefit tables, and document-library references.'}
        ]
    },
    'img_global': {
        'quote_intake_path_type': 'public_plan_cards_with_price_links',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_plan_details_visible',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.imglobal.com/international-health-insurance', 'source_type': 'official plan page', 'excerpt': 'Global Medical Gold — Buy Now / See Price; Global Medical Platinum — Buy Now / See Price; Global Medical Silver — Buy Now / See Price.'}
        ]
    },
    'aetna_international': {
        'quote_intake_path_type': 'marketing_page_to_member_or_b2b_paths',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['No public individual-family quote intake was verified; observed path is oriented around members, brokers, employers, or portal handoff.'],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_marketing_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.aetna.com/employers-organizations/aetna-international-insurance.html', 'source_type': 'official marketing page', 'excerpt': 'The Aetna page is structured around members, brokers, and employers rather than a public individual-family quote form.'}
        ]
    },
    'foyer_global_health': {
        'quote_intake_path_type': 'public_plan_comparison_with_quote_links',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_plan_details_visible',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/', 'source_type': 'official comparison page', 'excerpt': 'Receive a quote / Get a quick quote / Apply now'},
            {'source_url': 'https://www.foyerglobalhealth.com/plans-services/our-plans/international-health-insurance-comparison/', 'source_type': 'official comparison page', 'excerpt': 'Compare our International Health Insurance plans'}
        ]
    },
    'henner': {
        'quote_intake_path_type': 'public_information_page_only',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_information_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.henner.com/en/customers/individuals-families/', 'source_type': 'official information page', 'excerpt': 'We have developed solutions to cover your health costs and your access to quality healthcare abroad.'}
        ]
    },
    'globality_health': {
        'quote_intake_path_type': 'public_multi_step_quote_and_application',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Direct-business-only notice is shown.', 'User must waive personal consultation or contact support before proceeding.', 'Applicant nationality, future residency, and signing-country location are part of the entry steps.'],
        'required_quote_form_fields_observed': ['Date of birth', 'Nationality', 'Policy start date', 'Country of future residency', 'Currency', 'Would you like to add another member before calculating the quote?', 'Are you currently located in ... ?', 'Country where the application is signed', 'Consent to waive a personal consultation'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': ['Whether another member will be added before calculating the quote'],
        'pricing_or_plan_details_visible_before_contact_submission': 'quote_calculation_presteps_public',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': True,
        'evidence': [
            {'source_url': 'https://application.globality-health.com/?locale=en', 'source_type': 'official application/quote flow', 'excerpt': 'Get a quotation in seconds. Get your health insurance in minutes.'},
            {'source_url': 'https://application.globality-health.com/?locale=en', 'source_type': 'official application/quote flow', 'excerpt': 'Would you like to add another member before calculating the quote?'},
            {'source_url': 'https://application.globality-health.com/?locale=en', 'source_type': 'official application/quote flow', 'excerpt': 'Please note that this tool is only foreseen for direct business for which there is no involvement of an intermediary.'}
        ]
    },
    'morgan_price': {
        'quote_intake_path_type': 'public_quick_quote_with_manual_fallback_contact_capture',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Online quote asks residence, currency, age, nationality, and number of dependents.', 'If online quote is unavailable for the circumstances, the flow falls back to manual quote request.'],
        'required_quote_form_fields_observed': ['Country Of Residence', 'Currency', 'Policy Holder Age', 'Nationality', 'Dependents'],
        'required_contact_details_before_pricing_or_plan_details': ['Email for manual fallback quote', 'Name for manual fallback quote', 'Phone number optional if requesting a call'],
        'required_household_data_before_pricing_or_plan_details': ['Dependents count'],
        'pricing_or_plan_details_visible_before_contact_submission': 'instant_quote_claimed_but_fallback_requires_contact',
        'contact_submission_required_before_pricing_observed': 'conditional',
        'household_submission_required_before_pricing_observed': True,
        'evidence': [
            {'source_url': 'https://morgan-price.com/quick-quote/', 'source_type': 'official quick quote page', 'excerpt': 'Country Of Residence / Currency / Policy Holder Age / Nationality / Dependents'},
            {'source_url': 'https://morgan-price.com/quick-quote/', 'source_type': 'official quick quote page', 'excerpt': 'Unfortunately we are unable to quote for your particular circumstances online. Please contact us by email for a quotation'}
        ]
    },
    'passportcard_global': {
        'quote_intake_path_type': 'public_information_and_sample_prices_only',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'sample_prices_public',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://www.passportcard.de/en/international-health-insurance/', 'source_type': 'official information page', 'excerpt': 'PassportCard’s official page publicly shows plan tiers, benefit highlights, and example monthly EUR prices.'}
        ]
    },
    'vumi': {
        'quote_intake_path_type': 'public_plan_navigation_only',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_navigation_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.vumigroup.com/', 'source_type': 'official website', 'excerpt': 'VUMI’s homepage publicly lists health plans and regional selection paths without requiring login to read plan positioning.'}
        ]
    },
    'integra_global': {
        'quote_intake_path_type': 'broker_led_entry_no_insurer_quote_captured',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Only broker-led discovery evidence was captured, so insurer intake requirements remain unresolved.'],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'broker_summary_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.internationalinsurance.com/integra-global/', 'source_type': 'broker page', 'excerpt': 'The staged public evidence is a broker page, not an official insurer quote flow.'}
        ]
    },
    'medihelp_international': {
        'quote_intake_path_type': 'public_plan_page_with_quote_link_but_downstream_not_captured',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_plan_page_visible',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.medihelp-assistance.com/en/medihelp-plans/individual-plans', 'source_type': 'official plan page', 'excerpt': 'MediHelp’s individual-plans page is publicly accessible and shows both “Get a quote” and “BUY ONLINE.”'}
        ]
    },
    'alc_health': {
        'quote_intake_path_type': 'legacy_brand_redirect',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['ALC appears to route new business away from the legacy brand, so direct new-business intake requirements were not captured on ALC itself.'],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'legacy_information_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.alchealth.com/', 'source_type': 'legacy official page', 'excerpt': 'Public evidence shows a legacy insurer-branded page, but new-business contact is redirected to IMG rather than retained on the original brand path.'}
        ]
    },
    'geoblue_xplorer_bcbs_global_solutions': {
        'quote_intake_path_type': 'broker_led_plan_page',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Discovery is broker-led, not insurer-led, so direct quote-intake fields were not verified from an official insurer quote flow.'],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'broker_summary_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.internationalinsurance.com/geoblue/xplorer/', 'source_type': 'broker page', 'excerpt': 'The captured discovery evidence is a broker page summarizing BCBS Global Solutions / former GeoBlue Xplorer.'}
        ]
    },
    'safetywing_nomad_complete': {
        'quote_intake_path_type': 'mandatory_signup_before_quote',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Account creation or login is the first gate before quote contents become visible.'],
        'required_quote_form_fields_observed': ['Sign in with Google OR Email + Password to continue'],
        'required_contact_details_before_pricing_or_plan_details': ['Email address'],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'not_visible_due_to_signup_gate',
        'contact_submission_required_before_pricing_observed': True,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://safetywing.com/signup?product=nomad-health', 'source_type': 'official signup page', 'excerpt': 'Your global coverage starts here'},
            {'source_url': 'https://safetywing.com/signup?product=nomad-health', 'source_type': 'official signup page', 'excerpt': 'Email / Password / Continue with email'}
        ]
    },
    'genki_native': {
        'quote_intake_path_type': 'public_questionnaire_recommendation_flow',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Genki screens for travel duration and excludes trips shorter than one month.', 'Questionnaire asks whether user is planning travel or already abroad, main destination, home-country healthcare access, and long-term illness preference before recommendation.'],
        'required_quote_form_fields_observed': ['Planning to travel or already abroad', 'How long will you be abroad? (1-12 months / 1 year+)', 'Main destination', 'Do you have access to healthcare in your home country?', 'What if you get a serious injury or illness and need long-term treatment and recovery?'],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'recommendation_flow_public',
        'contact_submission_required_before_pricing_observed': False,
        'household_submission_required_before_pricing_observed': False,
        'evidence': [
            {'source_url': 'https://consultation.genki.world/v2', 'source_type': 'official consultation flow', 'excerpt': 'We don\'t cover trips shorter than one month.'},
            {'source_url': 'https://consultation.genki.world/v2', 'source_type': 'official consultation flow', 'excerpt': 'Answer a few quick questions / Genki finds the right insurance / See the price and sign up in minutes'}
        ]
    },
    'acs_expat': {
        'quote_intake_path_type': 'multi_step_quote_request_with_advisor_followup',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['ACS first asks destination country and intended length/stay category.', 'Coverage need, currency, and beneficiary composition are captured before advisor follow-up.'],
        'required_quote_form_fields_observed': ['Destination country', 'I am living abroad... (up to 12 months / Working Holiday Program / more than one year)', 'What are your coverage needs?', 'I would like... (contract currency)', 'I am the only beneficiary', 'Add my spouse', 'Add my children', 'Number of children under 18', 'Full name', 'Date of birth', 'Country of nationality'],
        'required_contact_details_before_pricing_or_plan_details': ['Full name', 'Contact details for advisor follow-up'],
        'required_household_data_before_pricing_or_plan_details': ['Whether applicant is sole beneficiary', 'Whether spouse is added', 'Whether children are added', 'Number of children under 18'],
        'pricing_or_plan_details_visible_before_contact_submission': 'personalized_proposal_request_only',
        'contact_submission_required_before_pricing_observed': True,
        'household_submission_required_before_pricing_observed': True,
        'evidence': [
            {'source_url': 'https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/', 'source_type': 'official quote request page', 'excerpt': 'Request your free, no-obligation expat health insurance quote'},
            {'source_url': 'https://www.acs-ami.com/en/expat-health-insurance/acs-expat-quote-request/', 'source_type': 'official quote request page', 'excerpt': 'One of our advisors will get back to you promptly with a personalised proposal.'}
        ]
    },
    'hci_group_health_protect': {
        'quote_intake_path_type': 'technical_barrier_on_external_quote_tool',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['A technical/availability barrier prevented capture of quote questions.'],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'not_visible_due_to_technical_barrier',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://hcigroup.outgrow.us/P21-2026', 'source_type': 'external specialized quote flow', 'excerpt': 'Internet is unstable. Please check your connection.'}
        ]
    },
    'expatriate_group_global_health': {
        'quote_intake_path_type': 'multi_step_quote_requires_email_before_proceeding',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Quote requires people-to-insure count, area of cover, and dates of birth before proceeding.', 'Supports explicit worldwide excluding USA area selection.'],
        'required_quote_form_fields_observed': ['Number of people to insure', 'Area of Cover (Area 1 / Area 2 worldwide excluding the USA / Area 3 worldwide)', 'Date of birth for each person', 'Email'],
        'required_contact_details_before_pricing_or_plan_details': ['Email address to receive a copy of the quotation'],
        'required_household_data_before_pricing_or_plan_details': ['Number of people to insure', 'Date of birth for each insured person'],
        'pricing_or_plan_details_visible_before_contact_submission': 'premium_stage_exists_but_email_needed_to_proceed',
        'contact_submission_required_before_pricing_observed': True,
        'household_submission_required_before_pricing_observed': True,
        'evidence': [
            {'source_url': 'https://quote.expatriatehealthcare.com/healthcare/', 'source_type': 'official quick quote page', 'excerpt': 'Please enter your email address below to receive a copy of your quotation.'},
            {'source_url': 'https://quote.expatriatehealthcare.com/healthcare/', 'source_type': 'official quick quote page', 'excerpt': 'Area 2 - Worldwide excluding the USA.'}
        ]
    },
    'wellaway_expat': {
        'quote_intake_path_type': 'multi_step_quote_with_contact_and_family_basics_before_plan_step',
        'eligibility_precheck_observed': True,
        'eligibility_precheck_details': ['Step 1 requires coverage start, destination, state, identity, and consent fields before the Plans step.', 'Family/dependent composition is collected at the basics stage.'],
        'required_quote_form_fields_observed': ['Coverage Start Date', 'Destination', 'State', 'First Name', 'Last Name', 'Applicant\'s Email', 'Date of Birth', 'Sex', 'Nationality', 'French Social Security Number', 'Origin Phone', 'Destination Phone', 'Would you like to combine your CFE Coverage with your WellAway?', 'Add your spouse/domestic partner to the policy', 'Number of dependents', 'Date of birth for spouse/dependents', 'Privacy consent'],
        'required_contact_details_before_pricing_or_plan_details': ['Applicant\'s Email', 'Origin Phone', 'Destination Phone'],
        'required_household_data_before_pricing_or_plan_details': ['Spouse/domestic partner yes/no', 'Spouse DOB if added', 'Number of dependents', 'Dependent DOBs'],
        'pricing_or_plan_details_visible_before_contact_submission': 'not_yet_plans_step',
        'contact_submission_required_before_pricing_observed': True,
        'household_submission_required_before_pricing_observed': True,
        'evidence': [
            {'source_url': 'https://portal.wellaway.com/Quote/EXPAT/Step1', 'source_type': 'official quote step', 'excerpt': '1 Basics / 2 Plans / 3 Specifics / 4 Extras / 5 Locations / 6 Health / 7 Payment'},
            {'source_url': 'https://portal.wellaway.com/Quote/EXPAT/Step1', 'source_type': 'official quote step', 'excerpt': 'Applicant\'s Email / Origin Phone / Destination Phone / Add your spouse/domestic partner to the policy / number of dependents'}
        ]
    },
    'securus_global_health_cover': {
        'quote_intake_path_type': 'public_marketing_and_brochure_only',
        'eligibility_precheck_observed': False,
        'eligibility_precheck_details': [],
        'required_quote_form_fields_observed': [],
        'required_contact_details_before_pricing_or_plan_details': [],
        'required_household_data_before_pricing_or_plan_details': [],
        'pricing_or_plan_details_visible_before_contact_submission': 'public_marketing_only',
        'contact_submission_required_before_pricing_observed': 'unknown',
        'household_submission_required_before_pricing_observed': 'unknown',
        'evidence': [
            {'source_url': 'https://www.securus.co.uk/benefits/global-health-cover', 'source_type': 'official marketing page', 'excerpt': 'Securus’s page publicly exposes marketing copy and brochure PDFs without requiring login.'}
        ]
    },
}

records = []
for idx, fr in enumerate(friction['records'], start=1):
    cid = fr['candidate_id']
    acc = access_by_id[cid]
    extra = manual[cid]
    record = {
        'index': idx,
        'candidate_id': cid,
        'insurer_name': fr['insurer_name'],
        'plan_name': fr['plan_name'],
        'candidate_type': fr['candidate_type'],
        'contact_model': acc['contact_model'],
        'initial_access_path_type': acc['initial_access_path_type'],
        'primary_source_url': fr['primary_source_url'],
        'quote_or_application_url': fr['quote_or_application_url'],
        'account_access_friction_classification': fr['account_access_friction_classification'],
        'account_access_friction_level': fr['account_access_friction_level'],
        'quote_intake_path_type': extra['quote_intake_path_type'],
        'eligibility_precheck_observed': extra['eligibility_precheck_observed'],
        'eligibility_precheck_details': extra['eligibility_precheck_details'],
        'required_quote_form_fields_observed': extra['required_quote_form_fields_observed'],
        'required_contact_details_before_pricing_or_plan_details': extra['required_contact_details_before_pricing_or_plan_details'],
        'required_household_data_before_pricing_or_plan_details': extra['required_household_data_before_pricing_or_plan_details'],
        'contact_submission_required_before_pricing_observed': extra['contact_submission_required_before_pricing_observed'],
        'household_submission_required_before_pricing_observed': extra['household_submission_required_before_pricing_observed'],
        'pricing_or_plan_details_visible_before_contact_submission': extra['pricing_or_plan_details_visible_before_contact_submission'],
        'public_plan_details_accessible_without_account_observed': fr['public_plan_details_accessible_without_account_observed'],
        'public_quote_entry_without_account_observed': fr['public_quote_entry_without_account_observed'],
        'verification_requirement_observed': fr['verification_requirement_observed'],
        'forced_signup_before_quote_or_plan_details': fr['forced_signup_before_quote_or_plan_details'],
        'mandatory_account_creation_before_quote_or_plan_details': fr['mandatory_account_creation_before_quote_or_plan_details'],
        'login_wall_before_quote_or_plan_details': fr['login_wall_before_quote_or_plan_details'],
        'email_required_before_quote_output_observed': fr['email_required_before_quote_output_observed'],
        'phone_required_before_quote_output_observed': fr['phone_required_before_quote_output_observed'],
        'evidence': extra['evidence'],
        'supporting_evidence_ledger_entries': fr['evidence_ledger_entries'],
        'uncertainty_notes': fr['uncertainty_notes'],
    }
    records.append(record)

summary = {
    'verified_total_unique_candidates': len(records),
    'eligibility_precheck_observed_count': sum(1 for r in records if r['eligibility_precheck_observed'] is True),
    'contact_submission_required_before_pricing_true_count': sum(1 for r in records if r['contact_submission_required_before_pricing_observed'] is True),
    'contact_submission_required_before_pricing_conditional_count': sum(1 for r in records if r['contact_submission_required_before_pricing_observed'] == 'conditional'),
    'contact_submission_required_before_pricing_unknown_count': sum(1 for r in records if r['contact_submission_required_before_pricing_observed'] == 'unknown'),
    'household_submission_required_before_pricing_true_count': sum(1 for r in records if r['household_submission_required_before_pricing_observed'] is True),
    'household_submission_required_before_pricing_unknown_count': sum(1 for r in records if r['household_submission_required_before_pricing_observed'] == 'unknown'),
    'email_required_before_quote_output_observed_count': sum(1 for r in records if r['email_required_before_quote_output_observed'] is True),
    'phone_required_before_quote_output_observed_count': sum(1 for r in records if r['phone_required_before_quote_output_observed'] is True),
    'quote_intake_path_type_counts': {},
}
for r in records:
    summary['quote_intake_path_type_counts'][r['quote_intake_path_type']] = summary['quote_intake_path_type_counts'].get(r['quote_intake_path_type'], 0) + 1

out = {
    'dataset': 'international_expat_health_insurance_discovery',
    'run_scope': 'discovery_only',
    'section': 'quote_intake_gating_requirements',
    'generated_by': 'gpt-5.4',
    'generated_from': [
        'data/insurance_discovery/account_access_friction.discovery.json',
        'data/insurance_discovery/access_path_contact_models.discovery.json'
    ],
    'family_context': friction['family_context'],
    'method_notes': [
        'This artifact records what quote-intake gating was visible at the first public quote, application, comparison, signup, or broker-handoff step reached for each staged candidate.',
        'required_quote_form_fields_observed only lists fields or questions that were explicitly visible in reviewed public sources; it does not infer hidden later fields.',
        'required_contact_details_before_pricing_or_plan_details distinguishes hard-observed contact capture from cases where contact requirements remain unknown because the run did not traverse farther or the quote path was blocked.',
        'required_household_data_before_pricing_or_plan_details records visible family-composition questions such as spouse/dependent toggles, people-to-insure counts, or DOB collection before pricing or plan output.',
        'Where no public quote path was captured, broker pages were used, or anti-bot / technical barriers blocked capture, uncertainty is preserved explicitly rather than guessed away.'
    ],
    'summary': summary,
    'records': records
}

(DATA_DIR / 'quote_intake_gating_requirements.discovery.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')

lines = []
lines.append('# Insurance discovery: quote-intake gating requirements')
lines.append('')
lines.append('Discovery-only companion ledger for AC 50003 sub-AC 3. This does not rank plans; it records the visible quote-intake requirements for each staged candidate, including eligibility prechecks, required quote-form fields, and whether contact details or household data appear to be required before pricing or deeper plan output.')
lines.append('')
lines.append('Machine-readable source: `data/insurance_discovery/quote_intake_gating_requirements.discovery.json`')
lines.append('')
lines.append('Summary counts:')
for k, v in summary.items():
    if k == 'quote_intake_path_type_counts':
        continue
    lines.append(f'- {k}: {v}')
lines.append('- quote_intake_path_type_counts:')
for k, v in sorted(summary['quote_intake_path_type_counts'].items()):
    lines.append(f'  - {k}: {v}')
lines.append('')
lines.append('| Candidate | Intake path | Eligibility precheck | Required fields observed | Contact required before pricing/details? | Household data before pricing/details? | Evidence snapshot |')
lines.append('|---|---|---|---|---|---|---|')
for r in records:
    fields = '; '.join(r['required_quote_form_fields_observed']) if r['required_quote_form_fields_observed'] else 'none observed / not captured'
    contact = r['contact_submission_required_before_pricing_observed']
    household = r['household_submission_required_before_pricing_observed']
    snapshot = r['evidence'][0]['excerpt'] if r['evidence'] else (r['supporting_evidence_ledger_entries'][0] if r['supporting_evidence_ledger_entries'] else '')
    precheck = '; '.join(r['eligibility_precheck_details']) if r['eligibility_precheck_details'] else 'none observed / not captured'
    lines.append(f"| {r['candidate_id']} | {r['quote_intake_path_type']} | {precheck} | {fields} | {contact} | {household} | {snapshot} |")

(DOCS_DIR / 'insurance_discovery_quote_intake_gating.md').write_text('\n'.join(lines) + '\n')
print('Wrote', DATA_DIR / 'quote_intake_gating_requirements.discovery.json')
print('Wrote', DOCS_DIR / 'insurance_discovery_quote_intake_gating.md')
