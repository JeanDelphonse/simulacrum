import json
from flask import current_app
from app.extensions import db
from app.models.ai_interaction import AIInteraction
from utils.model_router import get_model, get_tier


def _client():
    import anthropic  # lazy — avoid slow import at startup
    return anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])


def _model():
    return current_app.config['CLAUDE_MODEL']


def _log_interaction(interaction_type, user_id, simulation_id, usage, model=None):
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    def _build():
        return AIInteraction(
            user_id=user_id,
            simulation_id=simulation_id,
            interaction_type=interaction_type,
            prompt_tokens=usage.input_tokens if usage else None,
            completion_tokens=usage.output_tokens if usage else None,
            model=model or _model(),
        )

    try:
        db.session.add(_build())
        db.session.commit()
    except Exception as exc:
        _logger.warning('_log_interaction first attempt failed (%s): %s', type(exc).__name__, exc)
        # Recover the session so the rest of the cycle can continue
        try:
            db.session.rollback()
        except Exception:
            pass
        # One retry — dispose the pool so the next acquire opens a fresh connection.
        # db.engine.dispose() does not touch the session or detach any loaded objects.
        try:
            try:
                db.engine.dispose()
            except Exception:
                pass
            db.session.add(_build())
            db.session.commit()
        except Exception as exc2:
            _logger.error('_log_interaction retry failed, skipping: %s', exc2)
            try:
                db.session.rollback()
            except Exception:
                pass


def extract_expertise_zones(parsed_text: str, user_id: str) -> list:
    """Extract expertise zones from resume/LinkedIn text. Returns list of zone dicts."""
    prompt = f"""Analyze this professional resume/profile and extract 3-6 distinct Expertise Zones.

Resume/Profile Text:
{parsed_text}

Return ONLY a JSON array with no markdown fences. Each element must have:
- "zone_name": string (concise, e.g. "Enterprise Data Pipeline Architecture")
- "summary": string (2-3 sentence description)
- "deliverables": array of strings (specific named outputs from their history)
- "monetization_potential": string ("High" | "Medium" | "Low")
- "estimated_hourly_rate": string (e.g. "$150-250/hr")

Example format:
[
  {{
    "zone_name": "B2B SaaS Go-to-Market Strategy",
    "summary": "10 years launching enterprise SaaS products...",
    "deliverables": ["Led Series B GTM for Acme Corp ($2M ARR in 6 months)", "Built SDR playbook used by 40-person team"],
    "monetization_potential": "High",
    "estimated_hourly_rate": "$200-350/hr"
  }}
]"""

    model = get_model('expertise_zone_extraction')
    response = _client().messages.create(
        model=model,
        max_tokens=2000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    _log_interaction(AIInteraction.TYPE_ZONE_EXTRACT, user_id, None, response.usage, model=model)

    raw = response.content[0].text.strip()
    # Strip any accidental markdown fences
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(raw)


def normalize_linkedin_text(raw_html_or_text: str, user_id: str) -> str:
    """Normalize crawled LinkedIn data into standard resume-like prose."""
    prompt = f"""You are given raw crawled data from a LinkedIn profile. Convert it into a clean, structured professional resume format as plain text. Include: Professional Summary, Work Experience (with specific deliverables/achievements for each role), Skills, and Education.

Raw LinkedIn Data:
{raw_html_or_text[:8000]}

Return ONLY the normalized resume text. No JSON, no markdown headers with #, just clean structured text."""

    response = _client().messages.create(
        model=_model(),
        max_tokens=3000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    _log_interaction(AIInteraction.TYPE_LINKEDIN_NORMALIZE, user_id, None, response.usage)
    return response.content[0].text.strip()


LAYER_DEFINITIONS = {
    1: ('Active Income — 1:1 Time-for-Money', 'active', 'Consulting, freelance engagements, speaking, fractional CXO roles. Immediately actionable, directly validated by resume Deliverables.'),
    2: ('Leveraged Income — One-to-Many', 'leveraged', 'Same expertise delivered to multiple people simultaneously: group coaching cohorts, workshops, corporate training, bootcamps.'),
    3: ('Productized Income — Sell Once, Deliver Many', 'productized', 'IP packaged into products delivered without the person present: online courses, e-books, templates, membership communities.'),
    4: ('Automated Residual Systems — Running Without You', 'automated', 'Automated funnels, SEO content engines, SaaS tools, licensing: SaaS product, IP licensing, affiliate revenue, email funnels.'),
    5: ('Wealth Deployment — Money Working for You', 'passive_wealth', 'Revenue from Layers 1-4 deployed into compounding wealth vehicles: index funds/ETFs, real estate equity, angel investing, dividend portfolios.'),
}


def generate_simulation_layer(
    layer_number: int,
    expertise_zone: str,
    focus_hint: str,
    parsed_text: str,
    user_id: str,
    simulation_id: str,
    fintech_enabled: bool = False,
) -> dict:
    """Generate a single layer for a simulation. Returns layer dict with income_streams."""
    layer_name, income_type, layer_desc = LAYER_DEFINITIONS[layer_number]

    fintech_note = ''
    if layer_number == 5 and fintech_enabled:
        fintech_note = 'Live fintech API integration is enabled. Include specific fund tickers, brokerage platform names (e.g. Fidelity, Vanguard, Alpaca), and estimated yields where relevant.'
    elif layer_number == 5:
        fintech_note = 'Provide general wealth strategy guidance only. Do not reference specific live fund tickers or brokerage APIs.'

    prompt = f"""You are a career wealth strategist. Generate Layer {layer_number} of a 5-layer wealth simulation for a professional.

EXPERTISE ZONE: {expertise_zone}
FOCUS HINT: {focus_hint or 'None provided'}
LAYER: {layer_number} — {layer_name}
LAYER TYPE: {layer_desc}
{f'FINTECH NOTE: {fintech_note}' if fintech_note else ''}

PROFESSIONAL BACKGROUND (excerpt):
{parsed_text[:4000]}

Return ONLY a JSON object with no markdown fences:
{{
  "layer_number": {layer_number},
  "layer_name": "{layer_name}",
  "income_type": "{income_type}",
  "ai_narrative": "2-3 sentence strategic overview of why this layer matters for this specific person",
  "priority_score": 0.0-1.0 float indicating urgency/impact,
  "income_streams": [
    {{
      "name": "specific stream name",
      "description": "what it is and how it works for this person",
      "platform": "recommended platform(s) to use",
      "est_monthly_low": integer dollar amount,
      "est_monthly_high": integer dollar amount,
      "ai_reasoning": "2-3 sentences: which specific Deliverable from their background justifies this recommendation, why this stream fits their Expertise Zone, and why it's viable at this wealth-building stage",
      "deliverable_refs": ["list of specific resume/LinkedIn items that justify this"],
      "automation_level": "low|medium|high|full",
      "launch_timeline_weeks": integer
    }}
  ]
}}

Generate 3-5 income streams. Be specific — reference actual job titles, companies, and deliverables from their background."""

    response = _client().messages.create(
        model=_model(),
        max_tokens=3000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    _log_interaction(AIInteraction.TYPE_LAYER_GENERATE, user_id, simulation_id, response.usage)

    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(raw)


AGENT_ACTION_TYPES = {
    1: {
        'outreach_email': {
            'label': 'Draft Consulting Outreach Emails (×10)',
            'description': 'Generate 10 personalized outreach emails to target prospects.',
            'prompt_form': [
                {'key': 'target_industries', 'label': 'Target industries', 'type': 'text', 'required': True},
                {'key': 'engagement_model', 'label': 'Preferred engagement model', 'type': 'select',
                 'options': ['project', 'retainer', 'hourly', 'fractional'], 'required': True},
                {'key': 'tone', 'label': 'Tone', 'type': 'select',
                 'options': ['professional', 'warm', 'direct', 'concise'], 'required': False},
            ],
        },
        'cold_email_campaign': {
            'label': 'Execute Cold Email Campaign',
            'description': 'Research and build a 25-company prospect list with a 3-step email sequence per company.',
            'prompt_form': [
                {'key': 'target_company_size', 'label': 'Target company size', 'type': 'select',
                 'options': ['startup', 'SMB', 'mid-market', 'enterprise'], 'required': True},
                {'key': 'pain_point', 'label': 'Pain point or business outcome you address',
                 'type': 'textarea', 'required': True},
                {'key': 'daily_send_limit', 'label': 'Daily send limit', 'type': 'text', 'required': False},
            ],
        },
        'rate_card': {
            'label': 'Generate Rate Card & Capability One-Pager',
            'description': 'Create a formatted rate card with service tiers and a capability one-pager.',
            'prompt_form': [
                {'key': 'target_rate', 'label': 'Target hourly or day rate (USD)', 'type': 'text', 'required': True},
                {'key': 'engagement_preference', 'label': 'Preferred engagement type', 'type': 'select',
                 'options': ['fixed-project', 'retainer', 'hourly', 'both'], 'required': True},
                {'key': 'client_outcomes', 'label': 'Three outcomes clients achieve working with you',
                 'type': 'textarea', 'required': False},
            ],
        },
        'role_search': {
            'label': 'Search Active Fractional & Contract Roles',
            'description': 'Surface 15–20 active fractional and contract postings matched to your Expertise Zone.',
            'prompt_form': [
                {'key': 'location_preference', 'label': 'Location preference', 'type': 'select',
                 'options': ['remote', 'hybrid', 'on-site', 'any'], 'required': True},
                {'key': 'minimum_monthly_rate', 'label': 'Minimum acceptable monthly rate (USD)',
                 'type': 'text', 'required': True},
                {'key': 'industries_to_exclude', 'label': 'Industries or company types to exclude',
                 'type': 'text', 'required': False},
            ],
        },
        'linkedin_optimize': {
            'label': 'Optimize LinkedIn Headline, About & Featured',
            'description': 'Rewrite LinkedIn headline, About section, and Featured block for inbound consulting leads.',
            'prompt_form': [
                {'key': 'open_to_engagements', 'label': 'Currently open to new engagements?', 'type': 'select',
                 'options': ['yes', 'no', 'selectively'], 'required': True},
                {'key': 'profile_focus', 'label': 'Profile focus', 'type': 'select',
                 'options': ['consulting availability', 'specific industry focus', 'both'], 'required': False},
            ],
        },
        'booking_page': {
            'label': 'Create Booking Page Configuration',
            'description': 'Generate a complete booking page spec ready for Calendly or Cal.com.',
            'prompt_form': [
                {'key': 'meeting_types', 'label': 'Meeting types to offer', 'type': 'text',
                 'required': True},
                {'key': 'available_days', 'label': 'Available days and hours', 'type': 'text', 'required': True},
                {'key': 'paid_session_rate', 'label': 'Paid session rate (USD)', 'type': 'text', 'required': False},
            ],
        },
        'consulting_proposal': {
            'label': 'Generate Consulting Proposal & SOW',
            'description': 'Create a fully structured consulting proposal and companion Statement of Work.',
            'prompt_form': [
                {'key': 'client_name', 'label': 'Client name or company', 'type': 'text', 'required': True},
                {'key': 'project_scope', 'label': 'Project scope', 'type': 'textarea', 'required': True},
                {'key': 'payment_milestones', 'label': 'Payment milestones', 'type': 'select',
                 'options': ['50% upfront / 50% delivery', '100% upfront', 'phased monthly'], 'required': False},
            ],
        },
        'consulting_agreement': {
            'label': 'Generate Consulting Agreement Template',
            'description': 'Create a plain-English consulting agreement with scope, IP, confidentiality, and payment terms.',
            'prompt_form': [
                {'key': 'payment_terms', 'label': 'Payment terms', 'type': 'select',
                 'options': ['net-15', 'net-30', '50% upfront'], 'required': True},
                {'key': 'clauses', 'label': 'Include clauses (comma-separated)',
                 'type': 'text', 'required': False},
            ],
        },
        'consulting_outreach': {
            'label': 'Execute Consulting Outreach Campaign (×10)',
            'description': '10 deeply personalized emails to senior decision-makers — each prospect researched individually across 6 signal categories.',
            'prompt_form': [
                {'key': 'target_industries', 'label': 'Target industries (comma-separated)',
                 'type': 'text', 'required': True},
                {'key': 'value_proposition', 'label': 'Your value proposition in 1-2 sentences',
                 'type': 'textarea', 'required': True},
                {'key': 'tone', 'label': 'Email tone', 'type': 'select',
                 'options': ['balanced', 'conservative', 'aggressive'], 'required': False},
            ],
        },
        'referral_network': {
            'label': 'Activate Referral Network (×15 Messages)',
            'description': 'Draft 15 personalized referral request messages to top contacts.',
            'prompt_form': [
                {'key': 'top_referrers', 'label': 'Top 3–5 people most likely to refer you',
                 'type': 'textarea', 'required': True},
                {'key': 'referral_type', 'label': 'What type of engagement should they refer?',
                 'type': 'text', 'required': True},
                {'key': 'referral_incentive', 'label': 'Referral incentive (optional)',
                 'type': 'text', 'required': False},
            ],
        },
        'social_proof': {
            'label': 'Build Social Proof & Testimonial System',
            'description': 'Create a testimonial request sequence, intake form, and display copy.',
            'prompt_form': [
                {'key': 'existing_clients', 'label': 'Do you have existing clients or colleagues who praised your work?',
                 'type': 'textarea', 'required': False},
                {'key': 'display_platform', 'label': 'Where to display testimonials', 'type': 'select',
                 'options': ['LinkedIn', 'personal website', 'both'], 'required': True},
                {'key': 'format', 'label': 'Testimonial format', 'type': 'select',
                 'options': ['short quotes', 'long case studies', 'video scripts'], 'required': False},
            ],
        },
        'rate_negotiation': {
            'label': 'Rate Negotiation Coaching Script',
            'description': 'Generate a scenario-specific negotiation script with opening, counter-offer, and walk-away tactics.',
            'prompt_form': [
                {'key': 'offered_rate', 'label': 'Rate offered or preparing to negotiate',
                 'type': 'text', 'required': True},
                {'key': 'client_size', 'label': 'Client company size and budget signals',
                 'type': 'text', 'required': True},
                {'key': 'walkaway_rate', 'label': 'Walk-away number', 'type': 'text', 'required': False},
            ],
        },
        'pitch_deck_outline': {
            'label': 'Create Pitch Deck Outline',
            'description': 'Generate a slide-by-slide pitch deck outline for a speaking or advisory engagement.',
            'prompt_form': [
                {'key': 'audience', 'label': 'Target audience', 'type': 'text', 'required': True},
                {'key': 'key_message', 'label': 'Key message or thesis', 'type': 'textarea', 'required': True},
            ],
        },
    },
    2: {
        'speaking_proposals': {
            'label': 'Research & Submit Speaking Proposals',
            'description': 'Find 20 matched conferences and podcasts with pre-drafted CFP proposals.',
            'prompt_form': [
                {'key': 'event_type', 'label': 'Event type', 'type': 'select',
                 'options': ['in-person conferences', 'virtual events', 'podcasts', 'all'], 'required': True},
                {'key': 'talk_format', 'label': 'Preferred talk format', 'type': 'select',
                 'options': ['keynote (45 min)', 'breakout (20 min)', 'panel', 'any'], 'required': True},
                {'key': 'geographic_regions', 'label': 'Geographic regions', 'type': 'text', 'required': False},
            ],
        },
        'speaker_fee_rider': {
            'label': 'Speaker Fee Structure & Rider',
            'description': 'Create a tiered speaker fee structure, rider document, and negotiation response template.',
            'prompt_form': [
                {'key': 'speaking_stage', 'label': 'Speaking for free or ready to charge?', 'type': 'select',
                 'options': ['building visibility (free)', 'ready to charge'], 'required': True},
                {'key': 'keynote_fee', 'label': 'Target keynote fee (USD)', 'type': 'text', 'required': False},
                {'key': 'logistical_requirements', 'label': 'Logistical requirements (travel, hotel, AV)',
                 'type': 'text', 'required': False},
            ],
        },
        'coaching_curriculum': {
            'label': 'Design Group Coaching Program & Curriculum',
            'description': 'Create a structured multi-week group coaching program with CRM setup.',
            'prompt_form': [
                {'key': 'duration_weeks', 'label': 'Program duration (weeks)', 'type': 'text', 'required': True},
                {'key': 'transformation_goal', 'label': 'What transformation will participants achieve?',
                 'type': 'textarea', 'required': True},
                {'key': 'cohort_size', 'label': 'Participants per cohort', 'type': 'text', 'required': False},
                {'key': 'price_per_participant', 'label': 'Price per participant (USD)', 'type': 'text', 'required': False},
            ],
        },
        'corporate_training_proposal': {
            'label': 'Corporate Training Buyer Outreach',
            'description': 'Identify 25 companies with L&D leads, training deck outline, and 5 outreach emails.',
            'prompt_form': [
                {'key': 'target_company_sizes', 'label': 'Target company sizes', 'type': 'select',
                 'options': ['SMB', 'mid-market', 'enterprise', 'all'], 'required': True},
                {'key': 'buyer_departments', 'label': 'Primary buyer departments', 'type': 'text', 'required': True},
                {'key': 'workshop_duration', 'label': 'Preferred workshop duration', 'type': 'select',
                 'options': ['half-day', 'full-day', 'multi-day'], 'required': False},
            ],
        },
        'workshop_content': {
            'label': 'Workshop Curriculum & Facilitator Guide',
            'description': 'Generate a full workshop curriculum with facilitator guide, exercises, and slide outline.',
            'prompt_form': [
                {'key': 'workshop_duration', 'label': 'Duration (e.g. half-day, full-day)', 'type': 'text', 'required': True},
                {'key': 'participant_outcome', 'label': 'What skill or outcome will participants leave with?',
                 'type': 'textarea', 'required': True},
                {'key': 'participant_level', 'label': 'Participant experience level', 'type': 'select',
                 'options': ['beginner', 'intermediate', 'advanced', 'mixed'], 'required': False},
            ],
        },
        'waitlist_landing_page': {
            'label': 'Waitlist Landing Page & Email Sequence',
            'description': 'Build landing page copy and a 3-email confirmation sequence for your first cohort.',
            'prompt_form': [
                {'key': 'program_name', 'label': 'Program name and one-sentence transformation',
                 'type': 'text', 'required': True},
                {'key': 'start_date_price', 'label': 'Anticipated start date and price',
                 'type': 'text', 'required': True},
                {'key': 'application_type', 'label': 'Collection type', 'type': 'select',
                 'options': ['email only', 'full application (name, role, company, goals)'], 'required': False},
            ],
        },
        'alumni_reactivation': {
            'label': 'Cohort Graduation & Alumni Reactivation',
            'description': 'Create a graduation ceremony guide, alumni onboarding sequence, and re-enrollment offer.',
            'prompt_form': [
                {'key': 'multiple_cohorts', 'label': 'Plan to run multiple cohorts?', 'type': 'select',
                 'options': ['yes', 'no', 'maybe'], 'required': True},
                {'key': 'alumni_offer', 'label': 'What would you offer alumni?', 'type': 'select',
                 'options': ['discounted repeat', 'advanced cohort', 'referral incentive'], 'required': False},
            ],
        },
        'workshop_roi': {
            'label': 'Workshop & Cohort ROI Calculator',
            'description': 'Calculate ROI and recommend optimal delivery cadence for workshops and cohorts.',
            'prompt_form': [
                {'key': 'prep_hours', 'label': 'Hours to prepare and deliver one workshop/cohort',
                 'type': 'text', 'required': True},
                {'key': 'current_rate', 'label': 'Current per-session rate or cohort price (USD)',
                 'type': 'text', 'required': True},
                {'key': 'quarterly_capacity', 'label': 'Workshops/cohorts you can run per quarter',
                 'type': 'text', 'required': False},
            ],
        },
    },
    3: {
        'course_framework': {
            'label': 'Design Full Online Course Curriculum',
            'description': 'Generate a complete course architecture with module outlines and platform setup checklist.',
            'prompt_form': [
                {'key': 'course_title', 'label': 'Course title (draft)', 'type': 'text', 'required': True},
                {'key': 'target_student', 'label': 'Who is this course for?', 'type': 'textarea', 'required': True},
                {'key': 'hosting_platform', 'label': 'Hosting platform', 'type': 'select',
                 'options': ['Teachable', 'Maven', 'Kajabi', 'Udemy', 'other'], 'required': False},
                {'key': 'price_point', 'label': 'Target price point (USD)', 'type': 'text', 'required': False},
            ],
        },
        'competitive_pricing': {
            'label': 'Competing Course Research & Pricing Strategy',
            'description': 'Analyze 5–10 competitor courses and recommend positioning and pricing strategy.',
            'prompt_form': [
                {'key': 'competitor_courses', 'label': '2–3 courses you consider competitors or comparables',
                 'type': 'textarea', 'required': True},
                {'key': 'differentiation', 'label': 'Your differentiation', 'type': 'select',
                 'options': ['methodology', 'audience specificity', 'depth', 'format'], 'required': True},
            ],
        },
        'sales_page': {
            'label': 'Write Course or Product Sales Page',
            'description': 'Generate a complete 1,500–2,500 word sales page in HTML format.',
            'prompt_form': [
                {'key': 'product_name', 'label': 'Product name', 'type': 'text', 'required': True},
                {'key': 'transformation', 'label': 'Single most compelling before/after transformation',
                 'type': 'textarea', 'required': True},
                {'key': 'price', 'label': 'Product price (USD)', 'type': 'text', 'required': True},
                {'key': 'early_testimonials', 'label': 'Early student testimonials or beta feedback',
                 'type': 'textarea', 'required': False},
            ],
        },
        'ebook_guide': {
            'label': 'E-Book / Digital Guide Outline & Listing',
            'description': 'Draft a full e-book outline with introduction, chapters, conclusion, and Gumroad listing.',
            'prompt_form': [
                {'key': 'guide_problem', 'label': 'Problem this guide solves and for whom',
                 'type': 'textarea', 'required': True},
                {'key': 'guide_depth', 'label': 'Depth', 'type': 'select',
                 'options': ['quick guide (5,000 words)', 'definitive guide (15,000+ words)'], 'required': True},
                {'key': 'guide_type', 'label': 'Free lead magnet or paid product?', 'type': 'select',
                 'options': ['free lead magnet', 'paid product'], 'required': True},
            ],
        },
        'ab_test_plan': {
            'label': 'Product A/B Test & Pricing Experiment Plan',
            'description': 'Design an A/B test plan with sample size calculation, success metric, and decision rules.',
            'prompt_form': [
                {'key': 'current_price', 'label': 'Current product price (USD)', 'type': 'text', 'required': True},
                {'key': 'alternative_prices', 'label': 'Alternative price points to test',
                 'type': 'text', 'required': True},
                {'key': 'conversion_metric', 'label': 'Conversion metric that defines success',
                 'type': 'text', 'required': True},
            ],
        },
        'membership_structure': {
            'label': 'Design Membership Community Structure',
            'description': 'Create tier structure, 12-week content calendar, and churn-prevention sequence.',
            'prompt_form': [
                {'key': 'platform', 'label': 'Preferred platform', 'type': 'select',
                 'options': ['Circle', 'Skool', 'Mighty Networks', 'Discord', 'other'], 'required': True},
                {'key': 'monthly_price', 'label': 'Target monthly membership price (USD)',
                 'type': 'text', 'required': True},
                {'key': 'community_name', 'label': 'Community name (draft)', 'type': 'text', 'required': False},
            ],
        },
        'launch_email_sequence': {
            'label': 'Generate 7-Email Product Launch Sequence',
            'description': 'Write a full 7-email launch sequence with subject lines, body copy, and send schedule.',
            'prompt_form': [
                {'key': 'product_name_price', 'label': 'Product name and price',
                 'type': 'text', 'required': True},
                {'key': 'days_before_launch', 'label': 'Days before launch to start sequence',
                 'type': 'text', 'required': True},
                {'key': 'early_bird', 'label': 'Early-bird discount or cart-close deadline?',
                 'type': 'text', 'required': False},
            ],
        },
        'affiliate_program': {
            'label': 'Build Affiliate Recruitment Program',
            'description': 'Define commission structure, identify 20 potential affiliates, and write recruitment emails.',
            'prompt_form': [
                {'key': 'commission_rate', 'label': 'Commission rate', 'type': 'select',
                 'options': ['10%', '20%', '30%', 'other'], 'required': True},
                {'key': 'commission_type', 'label': 'Commission type', 'type': 'select',
                 'options': ['one-time', 'recurring on renewals'], 'required': True},
                {'key': 'program_type', 'label': 'Program type', 'type': 'select',
                 'options': ['open (public)', 'curated (invite-only)'], 'required': False},
            ],
        },
        'testimonial_system': {
            'label': 'Student Testimonial & Case Study System',
            'description': 'Create a testimonial collection sequence, intake form, and formatted display copy.',
            'prompt_form': [
                {'key': 'student_count', 'label': 'How many students or customers do you have?',
                 'type': 'text', 'required': False},
                {'key': 'testimonial_format', 'label': 'Preferred testimonial format', 'type': 'select',
                 'options': ['short quotes', 'long case studies', 'video scripts'], 'required': True},
                {'key': 'request_timing', 'label': 'When to request testimonials', 'type': 'select',
                 'options': ['mid-course', 'at completion', '30 days post-completion'], 'required': False},
            ],
        },
        'lapsed_buyer_reactivation': {
            'label': 'Reactivation Campaign for Lapsed Buyers',
            'description': 'Write a 3-email reactivation sequence with segmentation logic and projected revenue.',
            'prompt_form': [
                {'key': 'lapsed_threshold', 'label': 'Days since engagement to qualify as lapsed', 'type': 'select',
                 'options': ['30 days', '60 days', '90 days'], 'required': True},
                {'key': 'reengagement_offer', 'label': 'Re-engagement offer', 'type': 'select',
                 'options': ['discount', 'bonus module', 'check-in call'], 'required': True},
            ],
        },
        'template_pack_spec': {
            'label': 'Package Templates into a Sellable Pack',
            'description': 'Define and describe a template pack product based on your deliverables.',
            'prompt_form': [
                {'key': 'pack_focus', 'label': 'Template pack focus area', 'type': 'text', 'required': True},
                {'key': 'target_buyer', 'label': 'Who buys this?', 'type': 'text', 'required': True},
            ],
        },
    },
    4: {
        'seo_content_calendar': {
            'label': 'Create 90-Day SEO Content Calendar',
            'description': 'Generate a 90-day SEO content calendar with 36 post titles and 5 drafted articles.',
            'prompt_form': [
                {'key': 'content_domain', 'label': 'Primary content domain', 'type': 'text', 'required': True},
                {'key': 'target_reader', 'label': 'Target reader', 'type': 'select',
                 'options': ['practitioner', 'executive', 'general professional'], 'required': True},
                {'key': 'publishing_frequency', 'label': 'Publishing frequency (e.g. 2x/week)',
                 'type': 'text', 'required': False},
            ],
        },
        'funnel_design': {
            'label': 'Build Automated Lead Magnet & Email Funnel',
            'description': 'Create a complete funnel: lead magnet, opt-in page, thank-you page, and 5-email nurture sequence.',
            'prompt_form': [
                {'key': 'lead_magnet_type', 'label': 'Lead magnet type', 'type': 'select',
                 'options': ['checklist', 'mini-course', 'toolkit', 'assessment', 'generate for me'],
                 'required': True},
                {'key': 'funnel_goal', 'label': 'End offer the funnel leads toward', 'type': 'text', 'required': True},
                {'key': 'email_platform', 'label': 'Email platform', 'type': 'text', 'required': False},
            ],
        },
        'newsletter_monetization': {
            'label': 'Newsletter Monetization Strategy',
            'description': 'Build a sponsorship rate card, paid tier structure, and 4-week editorial calendar.',
            'prompt_form': [
                {'key': 'list_size', 'label': 'Current email list size', 'type': 'text', 'required': True},
                {'key': 'open_rate', 'label': 'Current open rate (%)', 'type': 'text', 'required': False},
                {'key': 'monetization_model', 'label': 'Monetization model', 'type': 'select',
                 'options': ['sponsorships', 'paid tiers', 'both'], 'required': True},
            ],
        },
        'saas_product_spec': {
            'label': 'SaaS Product Specification',
            'description': 'Write a 10–15 page product spec for a micro-SaaS built on your methodology.',
            'prompt_form': [
                {'key': 'problem_to_solve', 'label': 'Problem this SaaS solves', 'type': 'textarea', 'required': True},
                {'key': 'target_customer', 'label': 'Target customer', 'type': 'text', 'required': True},
                {'key': 'pricing_model', 'label': 'Pricing model', 'type': 'select',
                 'options': ['per-seat', 'usage-based', 'flat monthly', 'other'], 'required': False},
            ],
        },
        'ip_licensing': {
            'label': 'IP Licensing Outreach & Agreement Template',
            'description': 'Draft a licensing one-pager, identify 15 licensee targets, and write a licensing agreement.',
            'prompt_form': [
                {'key': 'ip_to_license', 'label': 'Framework, methodology, or curriculum to license',
                 'type': 'textarea', 'required': True},
                {'key': 'target_licensees', 'label': 'Target licensee type', 'type': 'select',
                 'options': ['companies', 'training firms', 'educational institutions', 'individual coaches'],
                 'required': True},
                {'key': 'licensing_model', 'label': 'Licensing model', 'type': 'select',
                 'options': ['per-seat', 'annual flat fee', 'revenue share'], 'required': False},
            ],
        },
        'affiliate_partnerships': {
            'label': 'Affiliate & Referral Partnership Opportunities',
            'description': 'Curate 20 affiliate programs with fit scores and draft a recruitment program.',
            'prompt_form': [
                {'key': 'tools_already_recommend', 'label': 'Tools or platforms you already recommend',
                 'type': 'text', 'required': False},
                {'key': 'affiliate_direction', 'label': 'Inbound (others promote you) or outbound (you promote others)?',
                 'type': 'select', 'options': ['inbound', 'outbound', 'both'], 'required': True},
            ],
        },
        'youtube_podcast': {
            'label': 'YouTube / Podcast Content Strategy & 3 Episode Scripts',
            'description': 'Create a show strategy, 24-episode content plan, and 3 fully scripted episodes.',
            'prompt_form': [
                {'key': 'format', 'label': 'Preferred format', 'type': 'select',
                 'options': ['long-form YouTube', 'short-form YouTube', 'audio podcast', 'video podcast'],
                 'required': True},
                {'key': 'production_frequency', 'label': 'How often can you produce content?',
                 'type': 'text', 'required': True},
                {'key': 'end_cta', 'label': 'End call-to-action', 'type': 'select',
                 'options': ['product', 'mailing list', 'consultation'], 'required': False},
            ],
        },
        'community_flywheel': {
            'label': 'Community Flywheel Activation Plan',
            'description': 'Build a content-to-community strategy with engagement calendar and paid tier upgrade pathway.',
            'prompt_form': [
                {'key': 'audience_size', 'label': 'Current audience size across all platforms',
                 'type': 'text', 'required': True},
                {'key': 'highest_engagement_platform', 'label': 'Platform with highest engagement',
                 'type': 'text', 'required': True},
                {'key': 'follow_reason', 'label': 'Why people follow you', 'type': 'select',
                 'options': ['entertainment', 'education', 'career advancement'], 'required': False},
            ],
        },
        'programmatic_ads': {
            'label': 'Programmatic Ad Strategy & Copy Set',
            'description': 'Create a full ad strategy brief with 10 copy variations across three creative angles.',
            'prompt_form': [
                {'key': 'product_to_advertise', 'label': 'Product to advertise',
                 'type': 'text', 'required': True},
                {'key': 'monthly_ad_budget', 'label': 'Monthly ad budget (USD)', 'type': 'text', 'required': True},
                {'key': 'ad_platforms', 'label': 'Ad platforms', 'type': 'text', 'required': True},
            ],
        },
        'client_winback': {
            'label': 'Client Win-Back Campaign (Lapsed L1/L2)',
            'description': 'Write a 3-email win-back sequence for lapsed consulting or coaching clients.',
            'prompt_form': [
                {'key': 'lapsed_threshold', 'label': 'Months since engagement to qualify as lapsed', 'type': 'select',
                 'options': ['3 months', '6 months', '1 year'], 'required': True},
                {'key': 'reengagement_offer', 'label': 'Re-engagement offer', 'type': 'select',
                 'options': ['reduced-rate', 'new service tier', 'check-in only'], 'required': True},
                {'key': 'estimated_lapsed_clients', 'label': 'Estimated number of lapsed clients',
                 'type': 'text', 'required': False},
            ],
        },
        'sponsorship_outreach': {
            'label': 'Newsletter Sponsorship Outreach (10–15 Sponsors)',
            'description': 'Find 10–15 companies that sponsor newsletters in your niche, generate personalized pitch emails, and produce a ready-to-sign sponsorship agreement template.',
            'prompt_form': [
                {'key': 'newsletter_name', 'label': 'Newsletter name', 'type': 'text', 'required': True},
                {'key': 'niche', 'label': 'Newsletter niche / topic (e.g. "B2B SaaS founders")',
                 'type': 'text', 'required': True},
                {'key': 'audience_description', 'label': 'Describe your audience (role, seniority, industry)',
                 'type': 'textarea', 'required': True},
                {'key': 'list_size', 'label': 'Subscriber count', 'type': 'text', 'required': True},
                {'key': 'open_rate', 'label': 'Open rate (%)', 'type': 'text', 'required': False},
            ],
        },
    },
    5: {
        'portfolio_analysis': {
            'label': 'Personalized Income Allocation Strategy',
            'description': 'Generate a monthly income allocation breakdown across all asset classes.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'monthly_income', 'label': 'Estimated monthly income from Layers 1–4 (USD)',
                 'type': 'text', 'required': True},
                {'key': 'monthly_expenses', 'label': 'Fixed monthly personal expenses (USD)',
                 'type': 'text', 'required': True},
                {'key': 'risk_tolerance', 'label': 'Risk tolerance', 'type': 'select',
                 'options': ['conservative', 'moderate', 'aggressive'], 'required': True},
                {'key': 'investment_horizon', 'label': 'Investment time horizon (years)',
                 'type': 'text', 'required': False},
            ],
        },
        'compound_growth': {
            'label': 'Compound Growth Projections',
            'description': 'Model compound growth at Years 1, 3, 5, 10, 15, and 20 under three return scenarios.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'monthly_deploy', 'label': 'Monthly amount to deploy into investments (USD)',
                 'type': 'text', 'required': True},
                {'key': 'years_to_model', 'label': 'Years to model', 'type': 'text', 'required': True},
                {'key': 'return_assumption', 'label': 'Annual return assumption', 'type': 'select',
                 'options': ['conservative (6%)', 'moderate (8%)', 'aggressive (10%)'], 'required': True},
            ],
        },
        'fund_recommendations': {
            'label': 'Index Fund & ETF Recommendations',
            'description': 'Curate 8–12 recommended funds with a model portfolio allocation.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'diversification', 'label': 'Diversification preference', 'type': 'select',
                 'options': ['US-only', 'international', 'global'], 'required': True},
                {'key': 'esg_preference', 'label': 'Want ESG (socially responsible) options?', 'type': 'select',
                 'options': ['yes', 'no', "don't mind"], 'required': False},
                {'key': 'account_type', 'label': 'Account type', 'type': 'select',
                 'options': ['taxable account', 'IRA', '401(k)', 'mix'], 'required': True},
            ],
        },
        'investment_policy_statement': {
            'label': 'Personal Investment Policy Statement (IPS)',
            'description': 'Draft a complete 2-page IPS with objectives, allocation, rebalancing triggers, and review cadence.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'financial_goals', 'label': 'Top three financial goals and target dates',
                 'type': 'textarea', 'required': True},
                {'key': 'min_return', 'label': 'Minimum annual return you need (%)', 'type': 'text', 'required': True},
                {'key': 'rebalance_triggers', 'label': 'Events that would trigger portfolio rebalance',
                 'type': 'text', 'required': False},
            ],
        },
        'real_estate_strategy': {
            'label': 'Real Estate Entry Strategy',
            'description': 'Build a real estate strategy brief with market comparisons and acquisition checklist.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'investable_capital', 'label': 'Investable capital available for real estate (USD)',
                 'type': 'text', 'required': True},
                {'key': 'investment_vehicle', 'label': 'Preferred vehicle', 'type': 'select',
                 'options': ['direct property ownership', 'REITs', 'real estate syndications', 'compare all'],
                 'required': True},
                {'key': 'hold_period', 'label': 'Preferred hold period', 'type': 'select',
                 'options': ['short-term flip', 'medium-term rental', 'long-term appreciation'], 'required': False},
            ],
        },
        'tax_optimization': {
            'label': 'Tax Optimization Strategy',
            'description': 'Create a tax optimization brief with entity structure, retirement accounts, and deduction list.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'business_entity', 'label': 'Current business entity', 'type': 'select',
                 'options': ['none / sole proprietor', 'LLC', 'S-Corp', 'C-Corp'], 'required': True},
                {'key': 'annual_gross_income', 'label': 'Estimated annual gross income from all Layers (USD)',
                 'type': 'text', 'required': True},
                {'key': 'has_retirement_account', 'label': 'Do you have a retirement account?', 'type': 'select',
                 'options': ['SEP-IRA', 'Solo 401k', 'none'], 'required': False},
            ],
        },
        'entity_structure': {
            'label': 'Business Entity Structure Recommendation',
            'description': 'Compare sole proprietor, LLC, S-Corp, and C-Corp with a transition checklist.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'current_entity', 'label': 'Current business entity', 'type': 'select',
                 'options': ['none', 'sole proprietor', 'LLC', 'S-Corp', 'C-Corp'], 'required': True},
                {'key': 'annual_revenue', 'label': 'Current annual revenue from all sources (USD)',
                 'type': 'text', 'required': True},
                {'key': 'raise_investment', 'label': 'Plan to raise outside investment or sell equity?', 'type': 'select',
                 'options': ['yes', 'no', 'maybe'], 'required': False},
            ],
        },
        'dca_schedule': {
            'label': 'Dollar-Cost Averaging Schedule & Contribution Plan',
            'description': 'Build a DCA schedule table and brokerage-specific setup guide.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'brokerage', 'label': 'Brokerage or investment platform', 'type': 'select',
                 'options': ['Fidelity', 'Schwab', 'Vanguard', 'Robinhood', 'Alpaca', 'other'], 'required': True},
                {'key': 'contribution_amount', 'label': 'Fixed contribution amount per cycle (USD)',
                 'type': 'text', 'required': True},
                {'key': 'contribution_frequency', 'label': 'Contribution frequency', 'type': 'select',
                 'options': ['weekly', 'bi-weekly', 'monthly'], 'required': True},
            ],
        },
        'insurance_gap_analysis': {
            'label': 'Insurance Gap Analysis & Coverage Recommendation',
            'description': 'Identify coverage gaps and recommend E&O, disability, life, and health policy parameters.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'current_coverage', 'label': 'Current coverage (check all that apply)',
                 'type': 'text', 'required': False},
                {'key': 'annual_income', 'label': 'Annual income to replace if unable to work (USD)',
                 'type': 'text', 'required': True},
                {'key': 'l1_consulting_volume', 'label': 'Estimated annual Layer 1 consulting revenue (USD)',
                 'type': 'text', 'required': False},
            ],
        },
        'estate_planning': {
            'label': 'Estate Planning Trigger Checklist & Beneficiary Review',
            'description': 'Build a documents checklist, beneficiary audit, and business succession brief.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'has_will', 'label': 'Do you have a will or living trust?', 'type': 'select',
                 'options': ['yes', 'no', 'in progress'], 'required': True},
                {'key': 'has_dependents', 'label': 'Do you have dependents or a spouse?', 'type': 'select',
                 'options': ['yes', 'no'], 'required': True},
                {'key': 'business_assets', 'label': 'Do you have business assets needing succession planning?',
                 'type': 'select', 'options': ['yes', 'no'], 'required': False},
            ],
        },
        'investment_thesis': {
            'label': 'Personal Investment Thesis',
            'description': 'Articulate a personal investment thesis aligned with your expertise and income sources.',
            'disclaimer': True,
            'prompt_form': [
                {'key': 'industries_to_invest', 'label': 'Industries you want exposure to',
                 'type': 'text', 'required': False},
                {'key': 'angel_investing_interest', 'label': 'Interested in angel investing?', 'type': 'select',
                 'options': ['yes', 'no', 'maybe'], 'required': False},
            ],
        },
    },
}


def execute_agent_action(
    action_type: str,
    layer_number: int,
    expertise_zone: str,
    parsed_text: str,
    user_inputs: dict,
    user_id: str,
    simulation_id: str,
    dispatch_source: str = 'orchestrator',
    action_id: str = None,
) -> str:
    """Execute an agent action for a simulation layer. Returns the generated artifact text."""
    layer_name, _, layer_desc = LAYER_DEFINITIONS[layer_number]
    action_meta = AGENT_ACTION_TYPES.get(layer_number, {}).get(action_type)
    if not action_meta:
        raise ValueError(f'Unknown action_type "{action_type}" for layer {layer_number}')

    # Two-pass agents with custom execution paths
    if action_type == 'consulting_outreach':
        from app.services.consulting_outreach_service import execute_consulting_outreach
        return execute_consulting_outreach(
            user_id=user_id,
            simulation_id=simulation_id,
            action_id=action_id,
            expertise_zone=expertise_zone,
            parsed_text=parsed_text,
            user_inputs=user_inputs,
        )

    if action_type == 'sponsorship_outreach':
        from app.services.sponsorship_outreach_service import execute_sponsorship_outreach
        return execute_sponsorship_outreach(
            user_id=user_id,
            simulation_id=simulation_id,
            action_id=action_id,
            expertise_zone=expertise_zone,
            parsed_text=parsed_text,
            user_inputs=user_inputs,
        )

    inputs_formatted = '\n'.join(
        f'- {k}: {v}' for k, v in user_inputs.items() if v
    ) or 'None provided'

    # outreach_email / cold_email_campaign: pull verified prospects from CRM
    # contacts (have real emails). Fall back to research engine if CRM is empty.
    if action_type in ('outreach_email', 'cold_email_campaign'):
        prospect_section = _get_crm_prospect_section(user_id, simulation_id, user_inputs)
        if not prospect_section:
            prospect_section = _get_prospect_context(
                action_type, expertise_zone, user_inputs, user_id, simulation_id, action_id,
            )
    else:
        # Prospect research injection (FR-RESEARCH-01)
        prospect_section = _get_prospect_context(
            action_type, expertise_zone, user_inputs, user_id, simulation_id, action_id,
        )

    prompt = f"""You are a specialized career wealth agent executing a specific action for a professional.

LAYER {layer_number}: {layer_name}
EXPERTISE ZONE: {expertise_zone}
ACTION: {action_meta['label']}
ACTION DESCRIPTION: {action_meta['description']}

USER-PROVIDED INPUTS:
{inputs_formatted}

PROFESSIONAL BACKGROUND (excerpt):
{parsed_text[:3500]}
{prospect_section}
Generate the complete artifact for this action. Be specific and draw directly from the professional's background, expertise zone, and deliverables. Write in a professional, immediately usable format. Do not include meta-commentary or instructions — only the artifact itself."""

    # outreach_email: override to structured JSON so emails can be sent programmatically
    if action_type == 'outreach_email':
        prompt += (
            '\n\n---\nReturn ONLY a JSON object in this exact format (no markdown fences, '
            'no commentary outside the JSON):\n'
            '{"version":"1.0","agent":"outreach_email","prospects":['
            '{"first_name":"...","last_name":"...","email":"...","job_title":"...",'
            '"company_name":"...","crm_contact_id":null,"send_status":"draft",'
            '"email_draft":{"subject":"...","body":"..."}}]}\n'
            'One entry per prospect from the research list above. '
            'Use the prospect email addresses provided. '
            'Subject and body must be complete, personalised, and ready to send.'
        )

    # cold_email_campaign: structured JSON with 3-step sequence per prospect
    if action_type == 'cold_email_campaign':
        prompt += (
            '\n\n---\nReturn ONLY a JSON object in this exact format (no markdown fences, '
            'no commentary outside the JSON):\n'
            '{"version":"1.0","agent":"cold_email_campaign","prospects":['
            '{"first_name":"...","last_name":"...","email":"...","job_title":"...",'
            '"company_name":"...","crm_contact_id":null,'
            '"sequence":['
            '{"step":1,"subject":"...","body":"...","send_status":"draft","send_delay_days":0},'
            '{"step":2,"subject":"...","body":"...","send_status":"pending","send_delay_days":7},'
            '{"step":3,"subject":"...","body":"...","send_status":"pending","send_delay_days":14}'
            ']}]}\n'
            'One entry per prospect from the research list above. '
            'Use the prospect email addresses provided. '
            'Step 1 is the initial outreach. Step 2 is a follow-up (day 7). '
            'Step 3 is a final follow-up (day 14). All subjects and bodies must be complete and ready to send.'
        )

    # For contact-producing agents, append a structured contacts extraction instruction
    _contact_agents = {
        'cold_email_campaign', 'role_search', 'referral_network',
        'speaking_proposals', 'corporate_training_proposal',
        'affiliate_program', 'affiliate_partnerships', 'alumni_reactivation',
    }
    _update_only_agents = {'alumni_reactivation'}
    if action_type in _contact_agents:
        prompt += (
            '\n\n---\nIMPORTANT: After your main response, append this exact block '
            '(mandatory output):\n'
            '<!--CONTACTS\n'
            '[{"first_name":"...","last_name":"...","email":"...","job_title":"...",'
            '"company_name":"...","qualifying_notes":"..."}]\n'
            'CONTACTS-->\n'
            'Include one entry per prospect/contact you generated or referenced. '
            'Required per entry: first_name, last_name. Omit fields you don\'t have. '
            'If no specific contacts were produced, output <!--CONTACTS [] CONTACTS-->.'
        )

    model = get_model(action_type)
    _json_outreach = {'outreach_email', 'cold_email_campaign'}
    max_tokens = 8192 if action_type == 'cold_email_campaign' else (4096 if action_type == 'outreach_email' else 3000)
    response = _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{'role': 'user', 'content': prompt}],
    )
    _log_interaction(AIInteraction.TYPE_AGENT_ACTION, user_id, simulation_id, response.usage, model=model)
    raw_artifact = response.content[0].text.strip()

    # JSON outreach agents: parse, save contacts, return clean JSON
    if action_type in _json_outreach:
        raw_artifact = _finalize_outreach_email_artifact(
            raw_artifact, user_id, simulation_id, action_id,
        )
        return raw_artifact

    # Extract and save contacts[] if present (FR-CRM-03)
    if action_type in _contact_agents:
        raw_artifact = _extract_and_save_contacts(
            raw_artifact, user_id, simulation_id, action_type,
            update_only=(action_type in _update_only_agents),
        )

    return raw_artifact


def _finalize_outreach_email_artifact(
    raw: str,
    user_id: str,
    simulation_id: str,
    action_id: str,
) -> str:
    """
    Parse the outreach_email JSON artifact, save prospects to CRM,
    backfill crm_contact_id on each record, return the updated JSON string.
    Falls back to returning the raw string on parse failure.
    """
    import re as _re
    import logging as _log
    _logger = _log.getLogger(__name__)

    # Strip any accidental markdown fences
    clean = _re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=_re.IGNORECASE)
    clean = _re.sub(r'\s*```$', '', clean.strip())

    try:
        data = json.loads(clean)
    except Exception:
        _logger.warning('outreach_email artifact is not valid JSON — returning raw text')
        return raw

    prospects = data.get('prospects', [])
    if not prospects:
        return json.dumps(data, ensure_ascii=False)

    _FALLBACK_EMAIL = 'valuemanager.management@gmail.com'

    # Fill missing emails with fallback so every prospect is sendable
    for p in prospects:
        if not p.get('email'):
            p['email'] = _FALLBACK_EMAIL

    # Debug: log all prospects
    for i, p in enumerate(prospects):
        _logger.error(
            'outreach_email prospect[%d]: name="%s %s" email="%s" company="%s" title="%s"',
            i,
            p.get('first_name', ''),
            p.get('last_name', ''),
            p.get('email', ''),
            p.get('company_name', ''),
            p.get('job_title', ''),
        )

    try:
        from app.services.contact_lookup import save_contacts_to_crm
        from app.models.contact import Contact

        contacts_payload = [
            {
                'first_name': p.get('first_name', ''),
                'last_name':  p.get('last_name', ''),
                'email':      p.get('email', ''),
                'job_title':  p.get('job_title', ''),
                'company_name': p.get('company_name', ''),
            }
            for p in prospects if p.get('first_name') or p.get('email')
        ]
        if contacts_payload and user_id:
            save_contacts_to_crm(
                contacts=contacts_payload,
                user_id=user_id,
                simulation_id=simulation_id,
                action_type='outreach_email',
                cycle_id=None,
            )

        # Backfill crm_contact_id so send_prospect_email can advance CRM stage
        for p in prospects:
            if not p.get('crm_contact_id') and p.get('email'):
                c = Contact.query.filter_by(
                    user_id=user_id,
                    email=p['email'].lower().strip(),
                ).first()
                if c:
                    p['crm_contact_id'] = c.id

    except Exception as exc:
        _logger.warning('outreach_email CRM save failed: %s', exc)

    return json.dumps(data, ensure_ascii=False)


def _extract_and_save_contacts(
    artifact: str,
    user_id: str,
    simulation_id: str,
    action_type: str,
    update_only: bool = False,
) -> str:
    """Extract <!--CONTACTS [...] CONTACTS--> block, save to CRM, return clean artifact."""
    import re as _re
    import logging as _log
    _logger = _log.getLogger(__name__)

    match = _re.search(r'<!--CONTACTS\s*(.*?)\s*CONTACTS-->', artifact, _re.DOTALL)
    if not match:
        return artifact

    clean = artifact[:match.start()].rstrip() + artifact[match.end():]

    try:
        contacts = json.loads(match.group(1).strip())
        if contacts and isinstance(contacts, list):
            from app.services.contact_lookup import save_contacts_to_crm
            # Resolve user_id from simulation if missing
            if not user_id and simulation_id:
                from app.models.simulation import Simulation as _Sim
                _sim = _Sim.query.get(simulation_id)
                user_id = _sim.user_id if _sim else None
            if user_id:
                save_contacts_to_crm(
                    contacts=contacts,
                    user_id=user_id,
                    simulation_id=simulation_id,
                    action_type=action_type,
                    update_only=update_only,
                )
    except Exception as exc:
        _logger.warning('Contacts extraction failed for %s: %s', action_type, exc)

    return clean.strip()


def _get_crm_prospect_section(user_id: str, simulation_id: str, user_inputs: dict) -> str:
    """
    Build a prospect injection section from existing CRM contacts.
    Returns formatted text for the prompt, or empty string if no eligible contacts.
    """
    if not user_id:
        return ''
    try:
        from app.models.contact import Contact
        contacts = Contact.query.filter(
            Contact.user_id == user_id,
            Contact.is_archived.is_(False),
            Contact.do_not_contact.is_(False),
            Contact.email.isnot(None),
            Contact.pipeline_stage.in_(['prospect', 'active', 'lead']),
        ).order_by(Contact.qualifying_score.desc()).limit(15).all()

        if not contacts:
            return ''

        lines = ['\n\nPROSPECT LIST (from your CRM — use these exact email addresses):']
        for c in contacts:
            parts = [f'{c.first_name or ""} {c.last_name or ""}'.strip()]
            if c.job_title:
                parts.append(c.job_title)
            if c.company_name:
                parts.append(c.company_name)
            parts.append(f'<{c.email}>')
            lines.append('  - ' + ' | '.join(p for p in parts if p))
        return '\n'.join(lines) + '\n'
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning('_get_crm_prospect_section failed: %s', exc)
        return ''


def _get_prospect_context(
    action_type: str,
    expertise_zone: str,
    user_inputs: dict,
    user_id: str,
    simulation_id: str,
    action_id: str,
) -> str:
    """
    Run the prospect research engine and return a formatted section for injection
    into the agent prompt. Returns empty string for non-outreach agents or on error.
    alumni_reactivation bypasses research per FR-RESEARCH-11.
    """
    from app.services.prospect_research_engine import (
        RESEARCH_ENABLED_AGENTS, build_targeting_criteria, ProspectResearchEngine,
    )

    if action_type not in RESEARCH_ENABLED_AGENTS:
        return ''
    if not action_id:
        return ''

    try:
        # Resolve user_id from the simulation when the caller couldn't supply it
        # (e.g. orchestrator-dispatched actions have created_by=None).
        # record_agent_contacts requires a valid user_id to save prospects.
        if not user_id and simulation_id:
            from app.models.simulation import Simulation as _Sim
            _sim = _Sim.query.get(simulation_id)
            user_id = _sim.user_id if _sim else None

        targeting = build_targeting_criteria(action_type, expertise_zone, user_inputs)
        engine    = ProspectResearchEngine()
        result    = engine.research(
            user_id=user_id,
            simulation_id=simulation_id,
            action_id=action_id,
            targeting=targeting,
            target_count=25,
        )
        if not result.prospects:
            return ''
        return f'\n\n{result.format_for_prompt()}\n'
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning(
            'Prospect research failed for action %s user %s: %s', action_type, user_id, exc
        )
        return ''


def refine_simulation_layer(
    layer_number: int,
    expertise_zone: str,
    parsed_text: str,
    constraint: str,
    existing_layer: dict,
    user_id: str,
    simulation_id: str,
) -> dict:
    """Regenerate a single layer with a new user constraint. Free operation."""
    layer_name, income_type, layer_desc = LAYER_DEFINITIONS[layer_number]

    prompt = f"""You are a career wealth strategist. Regenerate Layer {layer_number} of a wealth simulation with the user's new constraint applied.

EXPERTISE ZONE: {expertise_zone}
LAYER: {layer_number} — {layer_name}
USER CONSTRAINT: {constraint}

PROFESSIONAL BACKGROUND (excerpt):
{parsed_text[:3000]}

EXISTING LAYER (for reference — regenerate with constraint applied):
{json.dumps(existing_layer, indent=2)[:2000]}

Return ONLY a JSON object with the same structure as the existing layer but revised to honor the constraint. No markdown fences."""

    response = _client().messages.create(
        model=_model(),
        max_tokens=3000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    _log_interaction(AIInteraction.TYPE_LAYER_REFINE, user_id, simulation_id, response.usage)

    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(raw)
