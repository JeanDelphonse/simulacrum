"""
sponsorship_outreach Agent — Layer 4, B15 use case
Two-pass: Pass 1 = web-augmented sponsor discovery (10–15 companies)
          Pass 2 = one personalized pitch email per company
          Pass 3 = sponsorship agreement template
"""
import json
import logging
import time

from flask import current_app

logger = logging.getLogger(__name__)

_SPONSOR_SYSTEM = (
    'You are a newsletter monetization specialist identifying ideal sponsors. '
    'Return valid JSON only — no markdown fences, no commentary outside the JSON object.'
)

_FIND_SPONSORS_PROMPT = """Find 10-15 companies that would be ideal sponsors for this newsletter:

Newsletter: {newsletter_name}
Niche / Topic: {niche}
Audience: {audience_description}
Subscribers: {list_size}
Open Rate: {open_rate}

{web_context}

For each company identify:
1. Company name and website
2. Why they are a strong fit (product/service aligns with this audience)
3. Most likely contact person title (e.g. "Head of Partnerships", "VP Marketing")
4. Realistic rate per issue and monthly rate given this list size

Industry benchmarks: ~$30-50 CPM for newsletters; premium open rates (>40%) command 1.5× rate.

Return ONLY this JSON (no extra keys):
{{"sponsors": [{{"company_name": "...", "website": "...", "contact_title": "...", "fit_reasoning": "...", "proposed_rate_per_issue": "$...", "proposed_monthly_rate": "$..."}}], "rate_card": {{"solo_placement": "$...", "dedicated_issue": "$...", "monthly_package_3x": "$..."}}}}"""

_EMAIL_SYSTEM = (
    'You are a newsletter publisher writing sponsorship pitch emails. '
    'Be direct, data-led, and specific to the sponsor. '
    'Return valid JSON only — no markdown fences.'
)

_EMAIL_PROMPT = """Write one sponsorship pitch email from {publisher_name} to {contact_title} at {company_name}.

=== NEWSLETTER PROFILE ===
Newsletter: {newsletter_name}
Niche: {niche}
Subscribers: {list_size}
Open Rate: {open_rate}
Audience: {audience_description}

=== WHY THIS COMPANY FITS ===
{fit_reasoning}

=== PROPOSED RATE ===
{proposed_rate} per issue placement

=== RULES ===
- Under 150 words
- Open with why THEIR product/service fits THIS audience — not list size
- Include one concrete placement format with rate
- One CTA: reply to discuss or schedule a brief call
- Subject line: direct and value-focused, no "Partnership Opportunity" or fake Re:/Fwd:

Return ONLY: {{"subject": "...", "body": "...", "placement_format": "solo|dedicated|monthly_package"}}"""

_AGREEMENT_PROMPT = """Write a professional newsletter sponsorship agreement template for:

Publisher: {publisher_name}
Newsletter: {newsletter_name}

Sections required:
1. Parties — [PUBLISHER_NAME], [PUBLISHER_ADDRESS] and [SPONSOR_NAME], [SPONSOR_ADDRESS]
2. Placement Specifications — issue date(s) [ISSUE_DATE], format, max word count
3. Content Approval — sponsor submits creative 5 business days before send; publisher has right to reject off-brand content
4. Payment Terms — [PLACEMENT_RATE] due net-15 from invoice date; late fees 1.5%/month
5. Exclusivity — category exclusivity within the same issue (no direct competitor placements)
6. Cancellation — 14-day written notice required; no refund on cancellations within 7 days of send
7. Performance — open rate and click data delivered within 48h of send
8. Governing Law — [GOVERNING_STATE]

Write as a clean legal document ready for delivery via PandaDoc. Use bracketed placeholders for all variable fields."""


def _web_search(query: str, api_key: str, model: str) -> str:
    """Web search via Claude's web_search tool. Returns text summary."""
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


def _find_sponsors(user_inputs: dict, api_key: str, sonnet_model: str) -> dict:
    """Pass 1: discover 10–15 sponsor companies via Claude + web search."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    niche = user_inputs.get('niche', '')
    web_context = ''
    if niche:
        raw = _web_search(
            f'companies that sponsor {niche} newsletters email list sponsorships 2025 2026',
            api_key, sonnet_model,
        )
        if raw:
            web_context = (
                'WEB RESEARCH — real companies known to sponsor newsletters in this niche:\n'
                + raw[:2000]
            )

    open_rate = user_inputs.get('open_rate', '')
    open_rate_str = (open_rate + '%') if open_rate else 'not provided'

    prompt = _FIND_SPONSORS_PROMPT.format(
        newsletter_name=user_inputs.get('newsletter_name', 'Newsletter'),
        niche=niche,
        audience_description=user_inputs.get('audience_description', ''),
        list_size=user_inputs.get('list_size', 'unknown'),
        open_rate=open_rate_str,
        web_context=web_context,
    )

    try:
        resp = client.messages.create(
            model=sonnet_model,
            max_tokens=2500,
            system=_SPONSOR_SYSTEM,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.error('Sponsor discovery failed: %s', exc)
        return {'sponsors': [], 'rate_card': {}}


def _generate_email(
    sponsor: dict, user_inputs: dict, publisher_name: str,
    api_key: str, sonnet_model: str,
) -> dict:
    """Generate one personalized pitch email for a sponsor."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    open_rate = user_inputs.get('open_rate', '')
    open_rate_str = (open_rate + '%') if open_rate else 'not provided'

    prompt = _EMAIL_PROMPT.format(
        publisher_name=publisher_name,
        contact_title=sponsor.get('contact_title', 'Marketing Team'),
        company_name=sponsor.get('company_name', ''),
        newsletter_name=user_inputs.get('newsletter_name', 'Newsletter'),
        niche=user_inputs.get('niche', ''),
        list_size=user_inputs.get('list_size', ''),
        open_rate=open_rate_str,
        audience_description=user_inputs.get('audience_description', ''),
        fit_reasoning=sponsor.get('fit_reasoning', ''),
        proposed_rate=sponsor.get('proposed_rate_per_issue', 'TBD'),
    )

    try:
        resp = client.messages.create(
            model=sonnet_model,
            max_tokens=500,
            system=_EMAIL_SYSTEM,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning('Email generation failed for %s: %s', sponsor.get('company_name', ''), exc)
        return {
            'subject': f'Sponsorship — {user_inputs.get("newsletter_name", "Newsletter")}',
            'body': '',
            'placement_format': 'solo',
        }


def _generate_agreement(user_inputs: dict, publisher_name: str, api_key: str, haiku_model: str) -> str:
    """Generate a ready-to-sign sponsorship agreement template."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    prompt = _AGREEMENT_PROMPT.format(
        publisher_name=publisher_name,
        newsletter_name=user_inputs.get('newsletter_name', 'Newsletter'),
    )

    try:
        resp = client.messages.create(
            model=haiku_model,
            max_tokens=1800,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        logger.warning('Agreement generation failed: %s', exc)
        return ''


def execute_sponsorship_outreach(
    user_id: str,
    simulation_id: str,
    action_id: str,
    expertise_zone: str,
    parsed_text: str,
    user_inputs: dict,
) -> str:
    """
    Execute the sponsorship_outreach agent.
    Returns a JSON string stored as ArtifactVersion.content.
    """
    from app.models.user import User
    from utils.model_router import MODELS, ModelTier

    api_key = current_app.config['CLAUDE_API_KEY']
    haiku_model = MODELS[ModelTier.HAIKU]
    sonnet_model = MODELS[ModelTier.SONNET]

    t_start = time.time()

    user = User.query.get(user_id)
    publisher_name = (user.full_name or 'The Publisher') if user else 'The Publisher'

    # Pass 1: discover sponsors + derive rate card
    discovery = _find_sponsors(user_inputs, api_key, sonnet_model)
    sponsors_raw = discovery.get('sponsors', [])[:15]
    rate_card = discovery.get('rate_card', {})
    discovery_duration = round(time.time() - t_start, 2)

    # Pass 2: generate outreach emails
    t_emails = time.time()
    sponsor_records = []
    for s in sponsors_raw:
        email_draft = _generate_email(s, user_inputs, publisher_name, api_key, sonnet_model)
        sponsor_records.append({
            'company_name': s.get('company_name', ''),
            'website': s.get('website', ''),
            'contact_title': s.get('contact_title', 'Marketing Team'),
            'fit_reasoning': s.get('fit_reasoning', ''),
            'proposed_rate_per_issue': s.get('proposed_rate_per_issue', ''),
            'proposed_monthly_rate': s.get('proposed_monthly_rate', ''),
            'email_draft': email_draft,
            'send_status': 'draft',
        })
    email_duration = round(time.time() - t_emails, 2)

    # Pass 3: agreement template
    agreement_template = _generate_agreement(user_inputs, publisher_name, api_key, haiku_model)

    artifact = {
        'version': '1.0',
        'agent': 'sponsorship_outreach',
        'newsletter_name': user_inputs.get('newsletter_name', ''),
        'niche': user_inputs.get('niche', ''),
        'list_size': user_inputs.get('list_size', ''),
        'open_rate': user_inputs.get('open_rate', ''),
        'sponsors': sponsor_records,
        'rate_card': rate_card,
        'agreement_template': agreement_template,
        'research_summary': {
            'total_found': len(sponsor_records),
            'discovery_duration_seconds': discovery_duration,
            'email_duration_seconds': email_duration,
        },
    }

    return json.dumps(artifact, ensure_ascii=False)
