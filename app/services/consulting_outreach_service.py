"""
consulting_outreach Agent — SIM-PRD-L1AGENTS-001 addendum
Two-pass research: Pass 1 = standard ProspectResearchEngine (10 prospects, score >= 0.65)
                   Pass 2 = deep personalization per prospect (6 signal categories)
Email generation: Sonnet 4.6, one call per prospect (10 total).
CRM: save on research (Pass 1 via engine), advance on send (via Send API).
"""
import json
import logging
import time

from flask import current_app

from app.extensions import db

logger = logging.getLogger(__name__)

SIGNAL_CATEGORIES = [
    'linkedin_activity',
    'company_news',
    'mutual_connections',
    'conferences_events',
    'hiring_signals',
    'public_pain_points',
]

_EMAIL_SYSTEM = (
    'You are a B2B consultant writing highly personalized outreach emails. '
    'Every email must reference a specific, verifiable detail about this prospect. '
    'Return valid JSON only — no markdown fences, no commentary outside the JSON object.'
)

_EMAIL_PROMPT = """Write one personalized consulting outreach email from {user_first_name} to {prospect_first_name} {prospect_last_name}, {prospect_title} at {prospect_company}.

=== YOUR POSITIONING ===
{user_positioning}

=== CALL TO ACTION ===
Book a discovery call: {booking_url}

=== PERSONALIZATION CONTEXT ===

LinkedIn activity:
{linkedin_activity}

Company news:
{company_news}

Mutual connections:
{mutual_connections}

Conference / events:
{conferences}

Hiring signals:
{hiring_signals}

Public pain points (do NOT mention Glassdoor, Reddit, or any negative source — use insight to frame value):
{pain_points}

=== RULES ===
- Under 200 words
- Open with a SPECIFIC reference to one personalization signal — not "I came across your profile"
- If mutual connections exist, ALWAYS lead with that signal (highest converting opener)
- Connect that signal to {user_first_name}'s expertise naturally
- Exactly one CTA (the booking link)
- Subject line: personal, non-deceptive, no "Re:", "Fwd:", or false urgency
- Tone: {tone}

Return ONLY this JSON (no extra keys):
{{"subject": "...", "body": "...", "personalization_signal_used": "one sentence", "signal_category": "linkedin_activity|company_news|mutual_connections|conferences_events|hiring_signals|public_pain_points"}}"""


# ── Web search helper ─────────────────────────────────────────────────────────

def _web_search(query, api_key, model):
    """Run a single web search via Claude's web_search tool. Returns text summary."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    messages = [{'role': 'user', 'content': query}]
    try:
        for _ in range(3):
            response = client.messages.create(
                model=model,
                max_tokens=1200,
                tools=[{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 2}],
                messages=messages,
            )
            if response.stop_reason == 'end_turn':
                return '\n'.join(b.text for b in response.content if hasattr(b, 'text'))
            if response.stop_reason == 'tool_use':
                messages.append({'role': 'assistant', 'content': response.content})
                tool_results = [
                    {'type': 'tool_result', 'tool_use_id': b.id, 'content': ''}
                    for b in response.content
                    if hasattr(b, 'type') and b.type == 'tool_use'
                ]
                if tool_results:
                    messages.append({'role': 'user', 'content': tool_results})
                continue
            break
    except Exception as exc:
        logger.warning('Web search error for "%s": %s', query[:60], exc)
    return ''


def _extract_signal(raw, category, prospect_name, api_key, haiku_model):
    """Extract one useful personalization signal from raw web text using Haiku."""
    if not raw or len(raw.strip()) < 40:
        return ''
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f'From this research about {prospect_name}, extract the single most useful '
        f'{category} signal for a personalized B2B consulting outreach email. '
        f'Be specific and concrete (max 2 sentences). '
        f'If nothing clearly useful, return an empty string.\n\n{raw[:2000]}'
    )
    try:
        resp = client.messages.create(
            model=haiku_model,
            max_tokens=150,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        logger.warning('Signal extraction failed (%s): %s', category, exc)
        return ''


# ── Pass 2 — deep research per prospect ──────────────────────────────────────

def _deep_research_one(prospect, user_id, expertise_keywords, api_key, haiku_model):
    """Run 6-category Pass 2 research for one prospect. Returns context dict."""
    name = prospect.display_name
    company = prospect.company_name or ''
    industry = prospect.industry or ''
    context = {}

    # 1. LinkedIn activity
    raw = _web_search(f'{name} linkedin post article 2025 2026', api_key, haiku_model)
    context['linkedin_activity'] = _extract_signal(raw, 'recent LinkedIn activity', name, api_key, haiku_model)

    # 2. Company news
    raw = _web_search(f'{company} news announcement funding 2025 2026', api_key, haiku_model)
    context['company_news'] = _extract_signal(raw, 'recent company news', name, api_key, haiku_model)

    # 3. Mutual connections — CRM query, no web search
    context['mutual_connections'] = _find_mutual_connections(user_id, company, industry)

    # 4. Conferences / speaking
    raw = _web_search(f'{name} speaker conference podcast panel 2025 2026', api_key, haiku_model)
    context['conferences_events'] = _extract_signal(raw, 'conference or podcast appearances', name, api_key, haiku_model)

    # 5. Hiring signals
    if company and expertise_keywords:
        raw = _web_search(f'{company} hiring {expertise_keywords} job opening', api_key, haiku_model)
        context['hiring_signals'] = _extract_signal(raw, 'hiring signals matching your expertise', name, api_key, haiku_model)
    else:
        context['hiring_signals'] = ''

    # 6. Public pain points
    raw = _web_search(f'{company} challenges growth problem 2025 2026', api_key, haiku_model)
    context['public_pain_points'] = _extract_signal(raw, 'challenges and business pain points', name, api_key, haiku_model)

    return context


def _find_mutual_connections(user_id, company, industry):
    """Query CRM for active/client contacts at the same company or industry."""
    try:
        from app.models.contact import Contact
        from sqlalchemy import or_
        q = Contact.query.filter(
            Contact.user_id == user_id,
            Contact.pipeline_stage.in_(['active', 'client']),
            Contact.is_archived == False,
        )
        filters = []
        if company:
            filters.append(Contact.company_name.ilike(f'%{company}%'))
        if industry:
            filters.append(Contact.industry.ilike(f'%{industry}%'))
        if filters:
            q = q.filter(or_(*filters))
        matches = q.limit(5).all()
        if not matches:
            return ''
        return '; '.join(
            f'{c.display_name} ({c.company_name or "unknown company"})' for c in matches
        )
    except Exception as exc:
        logger.warning('Mutual connections query failed: %s', exc)
        return ''


# ── Email generation ──────────────────────────────────────────────────────────

def _generate_one_email(prospect, ctx, user_info, api_key, sonnet_model):
    """Generate one personalized email for a prospect. Returns email dict."""
    import anthropic

    def _fmt(val):
        return val if val else 'No data found'

    tone_map = {
        'conservative': 'warm and curious, no hard ask — suggest a conversation',
        'balanced': 'confident, specific value proposition, soft ask to connect',
        'aggressive': 'bold, direct claim of value, clear ask to meet',
    }
    tone = tone_map.get(user_info.get('tone', 'balanced'), user_info.get('tone', 'balanced'))

    prompt = _EMAIL_PROMPT.format(
        user_first_name=user_info['first_name'],
        prospect_first_name=prospect.first_name,
        prospect_last_name=prospect.last_name,
        prospect_title=prospect.job_title or 'professional',
        prospect_company=prospect.company_name or 'their company',
        user_positioning=user_info['positioning'],
        booking_url=user_info['booking_url'] or 'your booking link',
        tone=tone,
        linkedin_activity=_fmt(ctx.get('linkedin_activity')),
        company_news=_fmt(ctx.get('company_news')),
        mutual_connections=_fmt(ctx.get('mutual_connections')),
        conferences=_fmt(ctx.get('conferences_events')),
        hiring_signals=_fmt(ctx.get('hiring_signals')),
        pain_points=_fmt(ctx.get('public_pain_points')),
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=sonnet_model,
            max_tokens=700,
            system=_EMAIL_SYSTEM,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning('Email generation failed for %s: %s', prospect.display_name, exc)
        return {
            'subject': f'Quick question, {prospect.first_name}',
            'body': '',
            'personalization_signal_used': 'Fallback — generation error',
            'signal_category': 'none',
        }


# ── CRM helpers ───────────────────────────────────────────────────────────────

def _lookup_crm_id(user_id, email):
    """Return existing CRM contact_id for this user+email, or None."""
    try:
        from app.models.contact import Contact
        c = Contact.query.filter_by(user_id=user_id, email=email.lower().strip()).first()
        return c.id if c else None
    except Exception:
        return None


def send_prospect_email(artifact_id, prospect_idx, user_id, simulation_id):
    """
    Mark one prospect as sent, advance CRM to active, log activity, attempt Apollo send.
    Updates ArtifactVersion.content in place.
    Returns (updated_prospect_dict, error_str).
    """
    from app.models.agent_action import AgentAction
    from app.models.artifact import ArtifactVersion
    from app.models.contact import Contact, ContactActivity
    from utils.id_gen import generate_id
    import datetime

    action = AgentAction.query.get(artifact_id)
    if not action:
        return None, 'Action not found'

    current = ArtifactVersion.current_for(artifact_id)
    if not current or not current.content:
        return None, 'No artifact content'

    try:
        data = json.loads(current.content)
    except Exception:
        return None, 'Invalid artifact JSON'

    prospects = data.get('prospects', [])
    if prospect_idx < 0 or prospect_idx >= len(prospects):
        return None, 'Invalid prospect index'

    p = prospects[prospect_idx]
    if p.get('send_status') == 'sent':
        return p, None  # already sent, no-op

    p['send_status'] = 'sent'
    p['sent_at'] = datetime.datetime.utcnow().isoformat()

    # Advance CRM contact to 'active' + log activity
    crm_id = p.get('crm_contact_id')
    if crm_id:
        contact = Contact.query.get(crm_id)
        if contact:
            contact.advance_stage(
                'active', created_by='user',
                simulation_id=simulation_id, action_id=artifact_id,
            )
            contact.last_contacted_at = datetime.datetime.utcnow()
            activity = ContactActivity(
                id=generate_id(),
                contact_id=contact.id,
                simulation_id=simulation_id,
                action_id=artifact_id,
                activity_type='outreach_sent',
                notes='Consulting outreach email sent',
                created_by='user',
            )
            db.session.add(activity)

    # Attempt Apollo individual send (best-effort)
    _try_apollo_send(p, user_id, action)

    # Persist updated JSON in place (not a new version — status change only)
    current.content = json.dumps(data, ensure_ascii=False)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('send_prospect_email commit failed: %s', exc)
        return None, 'Database error'

    return p, None


def skip_prospect_email(artifact_id, prospect_idx, user_id, simulation_id):
    """
    Mark one prospect as skipped, log CRM activity. Contact stays at 'prospect'.
    Updates ArtifactVersion.content in place.
    Returns (updated_prospect_dict, error_str).
    """
    from app.models.agent_action import AgentAction
    from app.models.artifact import ArtifactVersion
    from app.models.contact import Contact, ContactActivity
    from utils.id_gen import generate_id
    import datetime

    action = AgentAction.query.get(artifact_id)
    if not action:
        return None, 'Action not found'

    current = ArtifactVersion.current_for(artifact_id)
    if not current or not current.content:
        return None, 'No artifact content'

    try:
        data = json.loads(current.content)
    except Exception:
        return None, 'Invalid artifact JSON'

    prospects = data.get('prospects', [])
    if prospect_idx < 0 or prospect_idx >= len(prospects):
        return None, 'Invalid prospect index'

    p = prospects[prospect_idx]
    p['send_status'] = 'skipped'

    crm_id = p.get('crm_contact_id')
    if crm_id:
        contact = Contact.query.get(crm_id)
        if contact:
            activity = ContactActivity(
                id=generate_id(),
                contact_id=contact.id,
                simulation_id=simulation_id,
                action_id=artifact_id,
                activity_type='outreach_skipped',
                notes='Consulting outreach email skipped — contact stays at prospect stage',
                created_by='user',
            )
            db.session.add(activity)

    current.content = json.dumps(data, ensure_ascii=False)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('skip_prospect_email commit failed: %s', exc)
        return None, 'Database error'

    return p, None


def _try_apollo_send(prospect_dict, user_id, action):
    """Attempt to send one email via Apollo. Silent failure — non-blocking."""
    try:
        from app.models.integration import UserIntegration
        rec = UserIntegration.query.filter_by(user_id=user_id, provider='apollo').first()
        if not rec or not rec.access_token_enc:
            return
        from app.services.token_crypto import decrypt_token
        from app.services.apollo_client import ApolloClient
        token = decrypt_token(rec.access_token_enc)
        client = ApolloClient(token)
        email_draft = prospect_dict.get('email_draft', {})
        client.send_email(
            to_email=prospect_dict.get('email', ''),
            subject=email_draft.get('subject', ''),
            body=email_draft.get('body', ''),
            from_name='',
        )
    except Exception as exc:
        logger.warning('Apollo send attempt failed (non-fatal): %s', exc)


# ── Main entry point ──────────────────────────────────────────────────────────

def execute_consulting_outreach(
    user_id,
    simulation_id,
    action_id,
    expertise_zone,
    parsed_text,
    user_inputs,
):
    """
    Execute the two-pass consulting_outreach agent.
    Returns a JSON string stored as ArtifactVersion.content.
    """
    from app.services.prospect_research_engine import (
        build_targeting_criteria, ProspectResearchEngine,
        _extract_expertise_tags,
    )
    from app.models.user import User
    from app.models.profile import UserProfile
    from utils.model_router import get_model, MODELS, ModelTier

    api_key = current_app.config['CLAUDE_API_KEY']
    haiku_model = MODELS[ModelTier.HAIKU]
    sonnet_model = MODELS[ModelTier.SONNET]

    t_start = time.time()

    # ── Pass 1: standard research ─────────────────────────────────────────────
    targeting = build_targeting_criteria('consulting_outreach', expertise_zone, user_inputs)
    engine = ProspectResearchEngine()
    result = engine.research(
        user_id=user_id,
        simulation_id=simulation_id,
        action_id=action_id,
        targeting=targeting,
        target_count=10,
    )
    prospects = result.prospects[:10]
    pass1_duration = round(time.time() - t_start, 2)

    # Populate crm_contact_id for any prospects the engine didn't tag
    for p in prospects:
        if not p.crm_contact_id and p.email:
            p.crm_contact_id = _lookup_crm_id(user_id, p.email)

    # ── Build user context for email generation ───────────────────────────────
    user = User.query.get(user_id)
    full_name = (user.full_name or '') if user else ''
    user_first_name = full_name.split()[0] if full_name else 'There'

    # Booking URL from UserProfile
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    booking_url = (profile.booking_url or '') if profile else ''

    # Expertise keywords for hiring signal searches
    expertise_keywords = ' '.join(_extract_expertise_tags(expertise_zone)[:5])

    # Positioning from expertise zone + user inputs
    value_prop = user_inputs.get('value_proposition', '') or parsed_text[:400]
    positioning = f'{expertise_zone}. {value_prop}'[:600]

    user_info = {
        'first_name': user_first_name,
        'positioning': positioning,
        'booking_url': booking_url,
        'tone': user_inputs.get('tone', 'balanced'),
    }

    # ── Pass 2 + email generation ─────────────────────────────────────────────
    t_pass2 = time.time()
    prospect_records = []
    total_signals = 0

    for p in prospects:
        # Pass 2: deep personalization
        try:
            ctx = _deep_research_one(
                p, user_id, expertise_keywords, api_key, haiku_model,
            )
        except Exception as exc:
            logger.warning('Pass 2 deep research failed for %s: %s', p.display_name, exc)
            ctx = {k: '' for k in SIGNAL_CATEGORIES}

        signals_found = sum(1 for v in ctx.values() if v)
        total_signals += signals_found

        # Email generation
        try:
            email_draft = _generate_one_email(p, ctx, user_info, api_key, sonnet_model)
        except Exception as exc:
            logger.warning('Email generation failed for %s: %s', p.display_name, exc)
            email_draft = {
                'subject': f'Quick question, {p.first_name}',
                'body': '',
                'personalization_signal_used': '',
                'signal_category': 'none',
            }

        prospect_records.append({
            'first_name': p.first_name,
            'last_name': p.last_name,
            'email': p.email or '',
            'job_title': p.job_title or '',
            'company_name': p.company_name or '',
            'linkedin_url': p.linkedin_url or '',
            'qualifying_score': float(p.qualifying_score_preview or 0),
            'crm_contact_id': p.crm_contact_id,
            'pipeline_stage': 'prospect',
            'send_status': 'draft',
            'personalization_context': ctx,
            'signals_found': signals_found,
            'email_draft': email_draft,
        })

    pass2_duration = round(time.time() - t_pass2, 2)

    artifact = {
        'version': '1.0',
        'agent': 'consulting_outreach',
        'prospects': prospect_records,
        'research_summary': {
            'total_researched': result.total_researched,
            'total_verified': len(prospects),
            'pass1_duration_seconds': pass1_duration,
            'pass2_duration_seconds': pass2_duration,
            'pass2_signals_found_per_prospect': round(
                total_signals / max(len(prospects), 1), 1
            ),
        },
    }

    return json.dumps(artifact, ensure_ascii=False)
