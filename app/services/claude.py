import json
import logging
from flask import current_app
from app.extensions import db
from app.models.ai_interaction import AIInteraction
from utils.model_router import get_model, get_tier

logger = logging.getLogger(__name__)


def _client():
    import anthropic  # lazy — avoid slow import at startup
    return anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])


def _model():
    return current_app.config['CLAUDE_MODEL']


def _log_interaction(interaction_type, user_id, simulation_id, usage, model=None):
    import logging as _logging
    from sqlalchemy import insert as _sa_insert
    _logger = _logging.getLogger(__name__)

    from utils.id_gen import generate_id as _gen_id
    from datetime import datetime as _dt
    row = {
        'id':                 _gen_id(),
        'user_id':            user_id,
        'simulation_id':      simulation_id,
        'interaction_type':   interaction_type,
        'prompt_tokens':      usage.input_tokens if usage else None,
        'completion_tokens':  usage.output_tokens if usage else None,
        'model':              model or _model(),
        'created_at':         _dt.utcnow(),
    }

    # This is always called after a long Claude API call. The scoped db.session
    # holds a checked-out connection that MySQL may have dropped. Dispose clears
    # the pool; engine.begin() then opens a fresh connection, bypassing the stale
    # session entirely.
    try:
        db.engine.dispose()
    except Exception:
        pass
    try:
        with db.engine.begin() as _conn:
            _conn.execute(_sa_insert(AIInteraction).values(**row))
    except Exception as exc:
        _logger.warning('_log_interaction failed, skipping: %s', exc)


def extract_expertise_zones(parsed_text: str, user_id: str, resume_id: str = None) -> list:
    """Extract expertise zones from resume/LinkedIn text. Returns list of zone dicts.

    The free tier's headline moment, so it is instrumented as an L0 run
    (PRD-SIM-OBS-001): without this the console could only see what happens
    after someone pays, which is the smaller half of the funnel.
    """
    from app.services import observability_service as _obs
    with _obs.free_tier_run('expertise_zone_extraction', user_id,
                            {'source_chars': len(parsed_text or ''),
                             'resume_id': resume_id}):
        return _extract_expertise_zones(parsed_text, user_id, resume_id)


def _extract_expertise_zones(parsed_text: str, user_id: str, resume_id: str = None) -> list:
    from app.services import observability_service as _obs
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
    with _obs.tool_span('claude_generate', {'model': model, 'max_tokens': 2000}) as _span:
        response = _client().messages.create(
            model=model,
            max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}],
        )
        _span['output'] = 'stop_reason=%s' % response.stop_reason
    _obs.record_usage(model=model,
                      prompt_tokens=getattr(response.usage, 'input_tokens', None),
                      completion_tokens=getattr(response.usage, 'output_tokens', None))
    _log_interaction(AIInteraction.TYPE_ZONE_EXTRACT, user_id, None, response.usage, model=model)

    raw = response.content[0].text.strip()
    # Strip any accidental markdown fences
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    zones = json.loads(raw)

    _obs.record_decision('Extracted %d expertise zone(s)' % len(zones),
                         zones=[z.get('zone_name') for z in zones][:6])
    _obs.record_artifact(
        'Expertise Zones', content=raw,
        summary={'count': len(zones),
                 'zones': [z.get('zone_name') for z in zones][:6]},
        content_ref=('resume:%s' % resume_id) if resume_id else None,
    )
    return zones


def classify_canonical_zones(
    professional_title: str,
    expertise_text: str,
    categories: list,
    user_id: str,
) -> list:
    """SIM-PRD-SME-001 FR-SME-04 — map a user's free-form expertise into canonical categories.

    `categories` is the list of active canonical slugs (e.g. ['technology','finance',...]).
    Returns a list of {"category": slug, "confidence": 0.0-1.0} ordered by confidence,
    for every category scoring above 0.3. Uses Haiku (cheap, fast). Never raises — returns
    [] on any failure so callers can fall back to the Unassigned queue.
    """
    slugs = ', '.join(categories)
    prompt = f"""Classify this professional into expertise categories.
Categories: {slugs}.
Title: {professional_title or 'Unknown'}
Expertise: {expertise_text or 'Unknown'}
Return JSON only (no markdown fences): [{{"category": str, "confidence": 0.0-1.0}}]
ordered by confidence descending, for every category scoring above 0.3.
Only use category slugs from the list above."""

    from app.services import observability_service as _obs
    try:
        with _obs.free_tier_run('zone_classification', user_id,
                                {'title': professional_title,
                                 'categories': len(categories)}):
            return _classify_canonical_zones(prompt, categories, user_id)
    except Exception as exc:
        logger.warning('classify_canonical_zones failed for user %s: %s', user_id, exc)
        return []


def _classify_canonical_zones(prompt: str, categories: list, user_id: str) -> list:
    from app.services import observability_service as _obs

    model = get_model('expertise_zone_extraction')  # HAIKU tier
    with _obs.tool_span('claude_generate', {'model': model, 'max_tokens': 500}):
        response = _client().messages.create(
            model=model,
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}],
        )
    _obs.record_usage(model=model,
                      prompt_tokens=getattr(response.usage, 'input_tokens', None),
                      completion_tokens=getattr(response.usage, 'output_tokens', None))
    _log_interaction(AIInteraction.TYPE_ZONE_EXTRACT, user_id, None, response.usage, model=model)
    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    parsed = json.loads(raw)
    valid = set(categories)
    out = []
    for z in parsed:
        cat = (z.get('category') or '').strip().lower()
        if cat in valid:
            try:
                conf = float(z.get('confidence', 0))
            except (TypeError, ValueError):
                conf = 0.0
            out.append({'category': cat, 'confidence': round(conf, 3)})
    out.sort(key=lambda z: z['confidence'], reverse=True)
    _obs.record_decision('Matched %d canonical category(ies)' % len(out),
                         top=[z['category'] for z in out][:3])
    return out


def normalize_linkedin_text(raw_html_or_text: str, user_id: str) -> str:
    """Normalize crawled LinkedIn data into standard resume-like prose."""
    prompt = f"""You are given raw crawled data from a LinkedIn profile. Convert it into a clean, structured professional resume format as plain text. Include: Professional Summary, Work Experience (with specific deliverables/achievements for each role), Skills, and Education.

Raw LinkedIn Data:
{raw_html_or_text[:8000]}

Return ONLY the normalized resume text. No JSON, no markdown headers with #, just clean structured text."""

    from app.services import observability_service as _obs
    with _obs.free_tier_run('linkedin_normalize', user_id,
                            {'source_chars': len(raw_html_or_text or '')}):
        model = _model()
        with _obs.tool_span('claude_generate', {'model': model, 'max_tokens': 3000}):
            response = _client().messages.create(
                model=model,
                max_tokens=3000,
                messages=[{'role': 'user', 'content': prompt}],
            )
        _obs.record_usage(model=model,
                          prompt_tokens=getattr(response.usage, 'input_tokens', None),
                          completion_tokens=getattr(response.usage, 'output_tokens', None))
        _log_interaction(AIInteraction.TYPE_LINKEDIN_NORMALIZE, user_id, None, response.usage)
        text = response.content[0].text.strip()
        _obs.record_artifact('Normalised LinkedIn Profile', content=text,
                             summary={'chars': len(text)})
        return text


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
        max_tokens=8000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    _log_interaction(AIInteraction.TYPE_LAYER_GENERATE, user_id, simulation_id, response.usage)

    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    if response.stop_reason == 'max_tokens':
        logger.warning('Layer %d response truncated at max_tokens for simulation %s', layer_number, simulation_id)
        # Salvage: drop the last (possibly partial) income stream and close the JSON.
        cut = raw.rfind('},')
        if cut != -1:
            raw = raw[:cut + 1] + ']}'
    return json.loads(raw)


# DEPRECATED — retained for reference only; NOT used at runtime.  The live
# AGENT_ACTION_TYPES is rebuilt from config/agents.json below (the single
# source of truth per SIM-PRD-AGENTMAP-001).  Do not add agents here; add them
# to agents.json.  This dict still contains stale/legacy entries (outreach_email,
# social_proof, pitch_deck_outline, alias duplicates) which is exactly why it is
# no longer authoritative.
_LEGACY_HARDCODED_AGENTS = {
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
        'linkedin_optimization': {
            'label': 'Optimize LinkedIn Headline, About & Featured',
            'description': 'Rewrite LinkedIn headline, About section, and Featured block for inbound consulting leads.',
            'prompt_form': [
                {'key': 'open_to_engagements', 'label': 'Currently open to new engagements?', 'type': 'select',
                 'options': ['yes', 'no', 'selectively'], 'required': True},
                {'key': 'profile_focus', 'label': 'Profile focus', 'type': 'select',
                 'options': ['consulting availability', 'specific industry focus', 'both'], 'required': False},
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

# Build AGENT_ACTION_TYPES exclusively from config/agents.json via the registry
# — the single source of truth (SIM-PRD-AGENTMAP-001).  Only the canonical
# action_type of each agent appears here, so per-layer agent lists and progress
# rings reflect the configured agent count exactly (e.g. layer 1 == 11).  Legacy
# name variants are mapped to canonical via agent_registry.resolve_alias() at
# every lookup site, so they never inflate these lists.
from app.services.agent_registry import AGENT_ACTION_TYPES as _registry_types
AGENT_ACTION_TYPES = {_layer: dict(_agents) for _layer, _agents in _registry_types.items()}


# Token budgets. Canonical names only — action_type is alias-resolved at
# function entry. Agents not listed get 16384 if agents.json marks them
# large_output, else 8192.
ACTION_MAX_TOKENS = {
    # ── long structured output (15 messages / 7 emails / full guide) ─
    'referral_network':        6144,
    'speaking_proposals':      8192,
    'workshop_curriculum':     8192,
    'group_coaching':          8192,
    'launch_sequence':         6144,
    'lead_magnet_funnel':      6144,
    'corporate_training':      6144,
    'affiliate_program':       6144,
    'ip_licensing':            6144,
    'ebook_outline':           6144,
    # ── medium structured output ─────────────────────────────────────
    'role_search':             4096,
    'sow_template':            4096,
    'agreement_template':      4096,
    'waitlist_landing_page':   4096,
    'alumni_reactivation':     4096,
    'membership':              4096,
    'testimonials':            4096,
    'lapsed_buyer':            4096,
    'programmatic_ads':        4096,
    'projections':             4096,
    'income_allocation':       4096,
    # ── short structured output ───────────────────────────────────────
    'negotiation_script':      3072,
    'pricing_research':        3072,
    'newsletter':              3072,
    'community':               3072,
    'fund_recommendations':    3072,
    'real_estate_strategy':    3072,
    'insurance_review':        3072,
    # ── very short (headline / calculation / schedule) ────────────────
    'linkedin_optimization':   2048,
    'roi_calculator':          2048,
    'booking_page':            3072,   # 2048 truncated once sections were required
    'dca_schedule':            2048,
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
    prospect_count: int = None,
) -> str:
    """Execute an agent action for a simulation layer. Returns the artifact text.

    The governed entry point (PRD-SIM-OBS-001). Every agent run — user-triggered
    or orchestrator-dispatched — funnels through here, so this is where the
    single emit hook opens the run and where the guardrail engine gets its two
    bites: the dispatch gate before any tokens are spent, and the post-agent
    evaluation of what came back.

    Both are non-fatal by construction. Capture failures are swallowed inside
    the observability service; a blocked result comes back as the held-for-review
    notice rather than an exception, because the caller's job is to store an
    artifact, not to handle a governance outcome.
    """
    # Resolve legacy aliases up front so model routing, token budgets, custom
    # execution paths, and contact-agent sets all key off the canonical name —
    # otherwise the same logical agent gets a different model/budget depending
    # on which name variant was stored in the DB.
    from app.services.agent_registry import resolve_alias
    action_type = resolve_alias(action_type)

    from app.services import guardrail_service as _guardrails
    from app.services import observability_service as _obs

    _model = get_model(action_type)
    run = _obs.start_run(
        agent_key=action_type, layer=layer_number, user_id=user_id,
        simulation_id=simulation_id, action_id=action_id,
        params=user_inputs, model=_model, dispatch_source=dispatch_source,
    )

    # Carve-out one: rate limits gate here, not post-hoc, because a runaway
    # agent's tokens cannot be refunded after the fact.
    gate = _guardrails.check_dispatch(action_type, layer_number, user_id, _model, run)
    if gate.blocked:
        _obs.finish_run(status='blocked', error_code='dispatch_blocked')
        logger.warning('Agent %s blocked at dispatch: %s', action_type, gate.reason)
        return _guardrails.HELD_NOTICE
    if gate.model_override:
        _obs.record_decision('Model pinned to %s by guardrail' % gate.model_override,
                             requested=_model)
        _model = gate.model_override

    try:
        artifact = _execute_agent_action_body(
            action_type=action_type, layer_number=layer_number,
            expertise_zone=expertise_zone, parsed_text=parsed_text,
            user_inputs=user_inputs, user_id=user_id, simulation_id=simulation_id,
            action_id=action_id, prospect_count=prospect_count, model=_model,
        )
    except Exception as exc:
        _obs.finish_run(status='error', error_code=exc.__class__.__name__)
        raise

    _action_meta = AGENT_ACTION_TYPES.get(layer_number, {}).get(action_type) or {}
    _obs.record_artifact(
        _action_meta.get('label', action_type),
        content=artifact,
        summary={'agent': action_type, 'layer': layer_number},
        content_ref=('agent_action:%s' % action_id) if action_id else None,
    )

    verdict = _guardrails.evaluate_run(
        run, artifact=artifact, params=user_inputs, model=_model,
    )
    if verdict.blocked:
        # Withhold delivery and quarantine, rather than fail the run. The
        # content never reaches the product store, so the quarantine row holds
        # the only copy until an admin releases it (PRD §9).
        _obs.quarantine_artifacts(artifact, verdict.reason)
        _obs.finish_run(status='blocked', model=_model, error_code='output_withheld')
        logger.warning('Agent %s output withheld: %s', action_type, verdict.reason)
        return _guardrails.HELD_NOTICE

    _obs.finish_run(status='success', model=_model)
    return artifact


def _execute_agent_action_body(
    action_type: str,
    layer_number: int,
    expertise_zone: str,
    parsed_text: str,
    user_inputs: dict,
    user_id: str,
    simulation_id: str,
    action_id: str = None,
    prospect_count: int = None,
    model: str = None,
) -> str:
    """The agent itself. Always called through execute_agent_action, which owns
    the run record and the guardrail passes; action_type arrives alias-resolved."""
    from app.services import observability_service as _obs

    layer_name, _, layer_desc = LAYER_DEFINITIONS[layer_number]
    action_meta = AGENT_ACTION_TYPES.get(layer_number, {}).get(action_type)
    if not action_meta:
        raise ValueError(f'Unknown action_type "{action_type}" for layer {layer_number}')

    _obs.record_decision(
        'Parsed inputs for %s' % action_meta['label'],
        layer=layer_number, fields=sorted(k for k, v in (user_inputs or {}).items() if v),
    )

    from app.services import agent_briefing as _brief
    from app.services.agent_registry import (
        get_agent_meta, CONTACT_AGENTS, UPDATE_ONLY_CONTACT_AGENTS,
    )
    agent_meta = get_agent_meta(action_type) or {}

    inputs_formatted = '\n'.join(
        f'- {k}: {v}' for k, v in (user_inputs or {}).items() if v
    ) or 'None provided'

    # ── Briefing: everything the agent grounds its artifact in ────────────────
    # Upstream artifacts (agents.json prerequisites[].data_flow), the user's
    # profile, existing contacts for update-only agents, projected income for
    # Layer 5, and deterministic figures. All DB reads happen here, before any
    # model call, because _log_interaction() disposes the engine after each call.
    with _obs.tool_span('briefing') as _span:
        briefing = _brief.build_briefing(
            action_type, layer_number, simulation_id, user_id, user_inputs or {},
            prospect_count=prospect_count,
        )
        _span['output'] = 'upstream=%s missing=%s' % (
            briefing.upstream_used or '-', briefing.upstream_missing or '-')

    # Two-pass consulting_outreach runs its own research + per-prospect drafting;
    # it still gets a planning step, passed through as a campaign brief.
    if action_type == 'consulting_outreach':
        with _obs.tool_span('agent_planning') as _span:
            plan = _brief.plan_run(
                client=_client(), meta=agent_meta, layer_name=layer_name,
                expertise_zone=expertise_zone, inputs_formatted=inputs_formatted,
                briefing=briefing, prospect_note='researched per prospect in the next pass',
                parsed_text=parsed_text, user_id=user_id, simulation_id=simulation_id,
            )
            _span['output'] = 'planned' if plan else 'no plan'

        from app.extensions import db as _db
        _db.session.remove()   # _log_interaction disposed the pool
        _record_plan(_obs, action_meta['label'], plan)
        _obs.record_decision('Two-pass path — research then draft', path='consulting_outreach')
        from app.services.consulting_outreach_service import execute_consulting_outreach
        artifact = execute_consulting_outreach(
            user_id=user_id,
            simulation_id=simulation_id,
            action_id=action_id,
            expertise_zone=expertise_zone,
            parsed_text=parsed_text,
            user_inputs=user_inputs,
            prospect_count=prospect_count,
            campaign_brief=_campaign_brief(briefing, plan),
        )
        from app.services import recipient_ledger as _ledger
        _ledger.record_from_artifact(simulation_id, action_type, action_id, artifact)
        return artifact

    # Resolve the effective prospect count up front for cold_email_campaign so BOTH
    # the research target and the count constraint below are bounded. The
    # orchestrator dispatches with prospect_count=None; without this the agent
    # over-generates, exhausts the token budget, and gets truncated.
    if action_type == 'cold_email_campaign' and not prospect_count:
        try:
            prospect_count = int(user_inputs.get('prospect_count') or 5)
        except (TypeError, ValueError):
            prospect_count = 5

    # cold_email_campaign: pull verified prospects from CRM contacts (real
    # emails). Fall back to the research engine if the CRM pool is empty.
    prospect_section = ''
    if action_type == 'cold_email_campaign':
        with _obs.tool_span('crm_prospect_lookup', {'want': prospect_count}) as _span:
            prospect_section = _get_crm_prospect_section(user_id, simulation_id, user_inputs, prospect_count)
            _span['output'] = 'found verified contacts' if prospect_section else 'CRM empty'
    if not prospect_section:
        # Prospect research injection (FR-RESEARCH-01) — no-op unless the agent
        # has research_enabled in agents.json.
        with _obs.tool_span('prospect_research', {'want': prospect_count}) as _span:
            prospect_section = _get_prospect_context(
                action_type, expertise_zone, user_inputs, user_id, simulation_id, action_id,
                prospect_count=prospect_count,
            )
            _span['output'] = '%d chars of prospect context' % len(prospect_section or '')

    is_json_outreach = action_type == 'cold_email_campaign'
    produces_contacts = action_type in CONTACT_AGENTS and not is_json_outreach
    if prospect_section:
        prospect_note = 'a verified prospect list is supplied (use only those people)'
    elif action_type in UPDATE_ONLY_CONTACT_AGENTS:
        prospect_note = ('%d existing contacts supplied' % briefing.existing_contact_count
                         if briefing.existing_contact_count else 'no existing contacts on file')
    elif action_type in CONTACT_AGENTS:
        prospect_note = 'none supplied — describe target profiles, do not name individuals'
    else:
        prospect_note = 'not applicable'

    # ── Planning step (runs before every agent's generation call) ─────────────
    with _obs.tool_span('agent_planning') as _span:
        plan = _brief.plan_run(
            client=_client(), meta=agent_meta, layer_name=layer_name,
            expertise_zone=expertise_zone, inputs_formatted=inputs_formatted,
            briefing=briefing, prospect_note=prospect_note, parsed_text=parsed_text,
            user_id=user_id, simulation_id=simulation_id,
        )
        _span['output'] = 'planned' if plan else 'no plan'

    # The planning call's _log_interaction disposed the engine; drop the scoped
    # session so the queries below (calibration config, contact saves) check out
    # a live connection.
    from app.extensions import db as _db
    _db.session.remove()
    _record_plan(_obs, action_meta['label'], plan)

    sections = [
        f"LAYER {layer_number}: {layer_name}",
        f"EXPERTISE ZONE: {expertise_zone}",
        f"ACTION: {action_meta['label']}",
        f"ACTION DESCRIPTION: {action_meta['description']}",
        f"USER-PROVIDED INPUTS:\n{inputs_formatted}",
        briefing.sections(),
        f"PROFESSIONAL BACKGROUND (resume excerpt):\n{(parsed_text or '')[:3500]}",
        (prospect_section or '').strip(),
        _brief.format_plan(plan),
        _brief.output_spec_block(agent_meta),
        _brief.grounding_rules(agent_meta),
    ]
    prompt = '\n\n'.join(s for s in sections if s) + (
        '\n\nGenerate the complete artifact for this action. Be specific and draw directly '
        "from the professional's background, the upstream artifacts and the plan. Write in a "
        'professional, immediately usable format.'
    )

    # cold_email_campaign: structured JSON with 3-step sequence per prospect
    if is_json_outreach:
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
            'One entry per prospect from the prospect list above. '
            'Use the prospect email addresses provided. '
            'Step 1 is the initial outreach. Step 2 is a follow-up (day 7). '
            'Step 3 is a final follow-up (day 14). All subjects and bodies must be complete and ready to send. '
            'Keep step 1 focused (under ~150 words); step 2 and step 3 are short nudges '
            '(3-4 sentences each) — do not repeat step 1 at length.'
        )

    # Count constraint for contact-producing agents (FR-TIER-07). The plan's
    # prospect count overrides any number in the action description, and the
    # agent may never pad the list with invented people.
    if prospect_count and (is_json_outreach or produces_contacts):
        container = 'prospects[] array' if is_json_outreach else 'CONTACTS block'
        prompt += (
            f'\n\nCOUNT CONSTRAINT: this run covers at most {prospect_count} people — this '
            f'overrides any number in the action description. The {container} must contain '
            f'one entry per supplied person, up to {prospect_count}. If fewer were supplied, '
            'use fewer. Never invent people or email addresses to reach the number.'
        )

    # Structured contacts block for contact-producing markdown agents (FR-CRM-03)
    if produces_contacts:
        prompt += (
            '\n\n---\nIMPORTANT: After your main response, append this exact block '
            '(mandatory output):\n'
            '<!--CONTACTS\n'
            '[{"first_name":"...","last_name":"...","email":"...","job_title":"...",'
            '"company_name":"...","qualifying_notes":"..."}]\n'
            'CONTACTS-->\n'
            'Include only people who appear in the supplied prospect or existing-contact '
            'lists above, copying their details exactly. Required per entry: first_name, '
            "last_name. Omit fields you don't have. If no list was supplied, output "
            '<!--CONTACTS [] CONTACTS-->.'
        )

    # SIM-PRD-CAL-001 — ask calibrated agents for a trailing METRICS block so the
    # Calibration Layer has a numeric field to anchor. Driven by the live
    # calibration_configs rows, so this is a no-op for every agent that has no
    # bound config (and for all agents if calibration is switched off).
    try:
        from app.services import calibration_service as _cal
        prompt += _cal.metrics_prompt_block(action_type)
    except Exception as _cal_err:
        _log_ = __import__('logging').getLogger(__name__)
        _log_.warning('Could not append METRICS block for %s: %s', action_type, _cal_err)

    max_tokens = ACTION_MAX_TOKENS.get(
        action_type,
        16384 if agent_meta.get('large_output') else 8192,
    )

    # Scale cold_email_campaign tokens to the (already-resolved) prospect count so
    # we don't allocate — and let the model fill — 16k tokens of unused capacity.
    if action_type == 'cold_email_campaign':
        max_tokens = min(16384, max(3072, (prospect_count or 5) * 900 + 1024))

    import logging as _log
    _logger = _log.getLogger(__name__)

    # The caller resolved the model (and may have re-pinned it via a guardrail),
    # so only fall back to the routing table when called without one.
    model = model or get_model(action_type)
    with _obs.tool_span('claude_generate',
                        {'model': model, 'max_tokens': max_tokens,
                         'prompt_chars': len(prompt)}) as _span:
        response = _client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_brief.system_prompt(agent_meta),
            messages=[{'role': 'user', 'content': prompt}],
        )
        _span['output'] = 'stop_reason=%s out_tokens=%s' % (
            response.stop_reason, getattr(response.usage, 'output_tokens', '?'))
    _obs.record_usage(
        model=model,
        prompt_tokens=getattr(response.usage, 'input_tokens', None),
        completion_tokens=getattr(response.usage, 'output_tokens', None),
    )
    _log_interaction(AIInteraction.TYPE_AGENT_ACTION, user_id, simulation_id, response.usage, model=model)

    if response.stop_reason == 'max_tokens':
        _obs.record_warning('Artifact truncated at the token budget',
                            max_tokens=max_tokens)
        _logger.warning(
            'execute_agent_action: artifact truncated at max_tokens=%d '
            'action_type=%s sim=%s action=%s — consider raising the limit',
            max_tokens, action_type, simulation_id, action_id,
        )

    raw_artifact = response.content[0].text.strip()

    # SIM-PRD-CAL-001 — strip the METRICS block and calibrate, before any of the
    # per-agent return paths below. Wrapped whole: a calibration failure must
    # never cost the user the artifact they paid for (PRD §12.9 graceful degrade).
    try:
        from app.services import calibration_service as _cal
        raw_artifact, _metrics = _cal.extract_metrics_block(raw_artifact)
        if _metrics:
            # _log_interaction just called engine.dispose(), so the scoped session
            # is bound to a discarded pool. Drop it so calibration's first query
            # checks out a live connection instead of a dead one.
            from app.extensions import db as _db
            _db.session.remove()
            with _obs.tool_span('calibration', {'fields': sorted(_metrics)}) as _span:
                _cal.calibrate_metrics(
                    metrics=_metrics,
                    agent_key=action_type,
                    simulation_id=simulation_id,
                    action_id=action_id,
                )
                _span['output'] = '%d field(s) calibrated' % len(_metrics)
            # The headline number goes on the shared run header, so calibration
            # and the World can read a value without the detail tables.
            for _value in _metrics.values():
                if isinstance(_value, (int, float)):
                    _obs.set_projected_value(_value)
                    break
    except Exception as _cal_err:
        _logger.warning(
            'Calibration skipped for action_type=%s action=%s: %s',
            action_type, action_id, _cal_err,
        )

    # JSON outreach agents: parse, save contacts, return clean JSON
    if is_json_outreach:
        with _obs.tool_span('outreach_finalize') as _span:
            raw_artifact = _finalize_outreach_email_artifact(
                raw_artifact, user_id, simulation_id, action_id,
            )
            _span['output'] = '%d chars of structured outreach' % len(raw_artifact or '')
        # Enforce prospect count cap — keep highest qualifying_score entries (FR-TIER-07)
        if prospect_count:
            raw_artifact = _truncate_prospects_to_limit(raw_artifact, prospect_count)
        # Claim these recipients now, not at send time — sends may be held for
        # approval and the next cycle must not draft the same people again.
        from app.services import recipient_ledger as _ledger
        _ledger.record_from_artifact(simulation_id, action_type, action_id, raw_artifact)
        return raw_artifact

    # Extract and save contacts[] if present (FR-CRM-03)
    if produces_contacts:
        with _obs.tool_span('contact_extract') as _span:
            raw_artifact = _extract_and_save_contacts(
                raw_artifact, user_id, simulation_id, action_type,
                update_only=(action_type in UPDATE_ONLY_CONTACT_AGENTS),
                action_id=action_id,
            )
            _span['output'] = 'contacts block processed'

    return raw_artifact


def _record_plan(_obs, label: str, plan) -> None:
    """Put the planning step on the run trace (observability console)."""
    if plan:
        _obs.record_decision(
            'Planned %s' % label,
            objective=plan.get('objective'),
            sections=[o.get('section') for o in (plan.get('outline') or []) if isinstance(o, dict)],
            assumptions=plan.get('assumptions') or [],
            gaps=plan.get('gaps') or [],
        )
    else:
        _obs.record_warning('Planning step unavailable — generated without a plan')


def _campaign_brief(briefing, plan) -> str:
    """Condensed plan + upstream positioning for the per-prospect email drafts."""
    from app.services import agent_briefing as _brief
    parts = [_brief.format_plan(plan)]
    if briefing.upstream:
        parts.append(briefing.upstream[:2000])
    return '\n\n'.join(p for p in parts if p)[:3000]


def _truncate_prospects_to_limit(artifact_json: str, limit: int) -> str:
    """Enforce prospect count cap — keep highest qualifying_score entries (FR-TIER-07)."""
    import json as _json
    try:
        data = _json.loads(artifact_json)
        prospects = data.get('prospects', [])
        if len(prospects) > limit:
            prospects.sort(key=lambda p: p.get('qualifying_score', 0), reverse=True)
            data['prospects'] = prospects[:limit]
            return _json.dumps(data, ensure_ascii=False)
    except Exception:
        pass
    return artifact_json


def _finalize_outreach_email_artifact(
    raw: str,
    user_id: str,
    simulation_id: str,
    action_id: str,
) -> str:
    """
    Parse the outreach_email / cold_email_campaign JSON artifact, save prospects to CRM,
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
        # raw_decode parses the first complete JSON value and ignores trailing text
        # (handles the common case where the LLM appends commentary after the closing })
        data, _ = json.JSONDecoder().raw_decode(clean)
    except Exception as _je:
        _logger.error(
            'CONTACTS_SAVE FAILED action=%s sim=%s: artifact is not valid JSON — %s',
            action_id, simulation_id, _je,
        )
        return raw

    prospects = data.get('prospects', [])
    if not prospects:
        _logger.error(
            'CONTACTS_SAVE FAILED action=%s sim=%s: JSON parsed but prospects[] is empty',
            action_id, simulation_id,
        )
        return json.dumps(data, ensure_ascii=False)

    if not user_id:
        _logger.error(
            'CONTACTS_SAVE FAILED action=%s sim=%s: user_id is None — cannot save %d prospects',
            action_id, simulation_id, len(prospects),
        )
        return json.dumps(data, ensure_ascii=False)

    # Fill missing emails with fallback so every prospect is sendable
    from app.services.contact_lookup import get_next_fallback_email, is_fallback_email
    used_fallbacks = set()
    for p in prospects:
        email = (p.get('email') or '').strip().lower()
        if not email or is_fallback_email(email):
            fb_email = get_next_fallback_email(user_id, used_fallbacks)
            p['email'] = fb_email
            used_fallbacks.add(fb_email)

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
        if contacts_payload:
            saved = save_contacts_to_crm(
                contacts=contacts_payload,
                user_id=user_id,
                simulation_id=simulation_id,
                action_type='cold_email_campaign',
                cycle_id=None,
            )
            if saved:
                _logger.info(
                    'CONTACTS_SAVE OK action=%s sim=%s: %d/%d prospects saved to CRM',
                    action_id, simulation_id, saved, len(contacts_payload),
                )
            else:
                _logger.error(
                    'CONTACTS_SAVE FAILED action=%s sim=%s: save_contacts_to_crm returned 0 for %d prospects',
                    action_id, simulation_id, len(contacts_payload),
                )
        else:
            _logger.error(
                'CONTACTS_SAVE FAILED action=%s sim=%s: contacts_payload is empty after filtering %d prospects',
                action_id, simulation_id, len(prospects),
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
        _logger.error(
            'CONTACTS_SAVE FAILED action=%s sim=%s: exception — %s',
            action_id, simulation_id, exc, exc_info=True,
        )

    return json.dumps(data, ensure_ascii=False)


def _llm_extract_contacts(artifact: str) -> list:
    """
    Fallback: ask Haiku to pull named contacts from a markdown artifact.
    Returns a list of contact dicts, or [] on failure.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)
    try:
        import anthropic as _ant
        _c = _ant.Anthropic()
        resp = _c.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            messages=[{
                'role': 'user',
                'content': (
                    'Extract every named person or organisation from the text below as a JSON array.\n'
                    'Each element: {"first_name":"...","last_name":"...","email":"...","job_title":"...","company_name":"..."}\n'
                    'Rules: first_name and last_name are required. Omit keys you do not have. '
                    'For organisations with no named person use first_name="Contact", last_name=company_name. '
                    'Return ONLY the JSON array, no commentary, no fences. If none found return [].\n\n'
                    f'TEXT:\n{artifact[:3000]}'
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        contacts = json.loads(raw)
        if isinstance(contacts, list):
            return contacts
    except Exception as exc:
        _logger.error('CONTACTS_SAVE fallback LLM extraction failed: %s', exc)
    return []


def _save_contacts_list(
    contacts: list,
    user_id: str,
    simulation_id: str,
    action_type: str,
    update_only: bool,
    source: str,
    action_id: str = None,
) -> None:
    """Resolve user_id if missing, call save_contacts_to_crm, log result."""
    import logging as _log
    _logger = _log.getLogger(__name__)

    # Claim these recipients for the agent (unique across cycles), and drop any
    # it already targeted in an earlier run before they reach the CRM again.
    try:
        from app.services import recipient_ledger as _ledger
        contacts = _ledger.filter_unused(simulation_id, action_type, contacts)
        _ledger.record(simulation_id, action_type, action_id, contacts)
    except Exception as _le:
        _logger.warning('recipient ledger update failed for %s: %s', action_type, _le)
    if not contacts:
        return

    if not user_id and simulation_id:
        from app.models.simulation import Simulation as _Sim
        _sim = _Sim.query.get(simulation_id)
        user_id = _sim.user_id if _sim else None

    if not user_id:
        _logger.error(
            'CONTACTS_SAVE FAILED action_type=%s sim=%s [%s]: user_id is None — cannot save %d contacts',
            action_type, simulation_id, source, len(contacts),
        )
        return

    try:
        from app.services.contact_lookup import save_contacts_to_crm
        saved = save_contacts_to_crm(
            contacts=contacts,
            user_id=user_id,
            simulation_id=simulation_id,
            action_type=action_type,
            update_only=update_only,
        )
        if saved:
            _logger.info(
                'CONTACTS_SAVE OK action_type=%s sim=%s [%s]: %d/%d contacts saved to CRM',
                action_type, simulation_id, source, saved, len(contacts),
            )
        else:
            _logger.error(
                'CONTACTS_SAVE FAILED action_type=%s sim=%s [%s]: save_contacts_to_crm returned 0 for %d contacts',
                action_type, simulation_id, source, len(contacts),
            )
    except Exception as exc:
        _logger.error(
            'CONTACTS_SAVE FAILED action_type=%s sim=%s [%s]: exception — %s',
            action_type, simulation_id, source, exc, exc_info=True,
        )


def _extract_and_save_contacts(
    artifact: str,
    user_id: str,
    simulation_id: str,
    action_type: str,
    update_only: bool = False,
    action_id: str = None,
) -> str:
    """
    Extract <!--CONTACTS [...] CONTACTS--> block and save to CRM.
    Falls back to a Haiku extraction call if the LLM omitted the block.
    Returns the artifact with the CONTACTS block stripped.
    """
    import re as _re
    import logging as _log
    _logger = _log.getLogger(__name__)

    match = _re.search(r'<!--CONTACTS\s*(.*?)\s*CONTACTS-->', artifact, _re.DOTALL)

    if not match:
        # LLM skipped the block — extract via Haiku fallback
        _logger.warning(
            'CONTACTS_SAVE action_type=%s sim=%s: CONTACTS block missing, attempting LLM fallback extraction',
            action_type, simulation_id,
        )
        contacts = _llm_extract_contacts(artifact)
        if contacts:
            _save_contacts_list(contacts, user_id, simulation_id, action_type, update_only, source='llm_fallback', action_id=action_id)
        else:
            _logger.error(
                'CONTACTS_SAVE FAILED action_type=%s sim=%s: no CONTACTS block and LLM fallback returned nothing',
                action_type, simulation_id,
            )
        return artifact  # nothing to strip

    clean = artifact[:match.start()].rstrip() + artifact[match.end():]

    try:
        contacts = json.loads(match.group(1).strip())
    except Exception as _je:
        _logger.error(
            'CONTACTS_SAVE FAILED action_type=%s sim=%s: CONTACTS block is not valid JSON — %s',
            action_type, simulation_id, _je,
        )
        return clean.strip()

    if not contacts or not isinstance(contacts, list):
        _logger.error(
            'CONTACTS_SAVE FAILED action_type=%s sim=%s: CONTACTS block parsed to empty/non-list',
            action_type, simulation_id,
        )
        return clean.strip()

    _save_contacts_list(contacts, user_id, simulation_id, action_type, update_only, source='embedded_block', action_id=action_id)
    return clean.strip()


def _get_crm_prospect_section(user_id: str, simulation_id: str, user_inputs: dict, prospect_count: int = None) -> str:
    """
    Build a prospect injection section from existing CRM contacts.
    Returns formatted text for the prompt, or empty string if no eligible contacts.
    """
    if not user_id:
        return ''
    try:
        from app.models.contact import Contact
        from sqlalchemy import or_ as _or
        # Only NOT-yet-contacted prospects — otherwise the same top-scoring contacts
        # (advanced to 'active' after their first email) get re-selected and
        # re-emailed every cycle. When this pool is exhausted the caller falls back
        # to the research engine, which sources NEW prospects from Apollo.
        contacts = Contact.query.filter(
            Contact.user_id == user_id,
            Contact.is_archived.is_(False),
            Contact.do_not_contact.is_(False),
            Contact.email.isnot(None),
            Contact.pipeline_stage == 'prospect',
            Contact.last_contacted_at.is_(None),
            _or(Contact.outreach_count == 0, Contact.outreach_count.is_(None)),
        ).order_by(Contact.qualifying_score.desc()).all()
        # Recipients are unique per agent across every cycle (recipient_ledger):
        # drop anyone an earlier Cold Email run already targeted, including
        # drafts still waiting on email-channel approval.
        from app.services import recipient_ledger as _ledger
        used = _ledger.used_keys(simulation_id, 'cold_email_campaign')
        contacts = [c for c in contacts
                    if _ledger.recipient_key(c.email) not in used][:prospect_count or 5]

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
    prospect_count: int = None,
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
        from app.services import recipient_ledger as _ledger
        result    = engine.research(
            user_id=user_id,
            simulation_id=simulation_id,
            action_id=action_id,
            targeting=targeting,
            target_count=prospect_count or 5,
            # Search only for people new to this agent, within a time budget.
            exclude_keys=_ledger.used_keys(simulation_id, action_type),
        )
        # Never hand the agent someone it targeted in an earlier cycle.
        from app.services import recipient_ledger as _ledger
        result.prospects = _ledger.filter_unused(simulation_id, action_type, result.prospects)
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
