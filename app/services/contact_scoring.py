"""Contact qualifying score — Haiku 4.5 async scoring (0.000–1.000).

Factors: seniority 30%, company_size 25%, industry 25%, profile_completeness 15%, source_quality 5%
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

AGENT_MIN_SCORES = {
    'consulting_outreach':       0.70,
    'cold_email_campaign':       0.50,
    'corporate_training_pitch':  0.75,
    'speaking_proposals':        0.55,
    'consulting_proposal':       0.80,
    'alumni_reactivation':       0.40,
    'lapsed_buyer_reactivation': 0.35,
    'affiliate_partnerships':    0.45,
}

_SENIORITY_WEIGHTS = {
    'c_suite':                0.30,
    'founder':                0.28,
    'partner':                0.26,
    'vp':                     0.22,
    'director':               0.18,
    'manager':                0.12,
    'individual_contributor': 0.06,
}

_COMPANY_SIZE_WEIGHTS = {
    '11-50':    0.25,
    '51-200':   0.22,
    '201-500':  0.18,
    '2-10':     0.15,
    '501-1000': 0.14,
    '1001-5000':0.10,
    '5000+':    0.08,
    'solo':     0.06,
}

_SOURCE_WEIGHTS = {
    'inbound_referral':       0.05,
    'manual_entry':           0.04,
    'agent_generated':        0.03,
    'csv_import':             0.02,
    'linkedin_import':        0.02,
}

_PROFILE_FIELDS = [
    'job_title', 'company_name', 'company_size', 'industry',
    'seniority', 'linkedin_url', 'city', 'country',
]


def _heuristic_score(contact) -> float:
    """Fast heuristic score — used as fallback when Claude unavailable."""
    seniority_score = _SENIORITY_WEIGHTS.get(contact.seniority or '', 0.10)
    size_score = _COMPANY_SIZE_WEIGHTS.get(contact.company_size or '', 0.12)
    filled = sum(1 for f in _PROFILE_FIELDS if getattr(contact, f, None))
    completeness_score = (filled / len(_PROFILE_FIELDS)) * 0.15
    source_score = _SOURCE_WEIGHTS.get(contact.source or 'manual_entry', 0.02)
    industry_score = 0.15 if contact.industry else 0.08
    total = seniority_score + size_score + industry_score + completeness_score + source_score
    return min(round(total, 3), 1.0)


def score_contact_sync(contact_id: str) -> float | None:
    """Score a contact via Haiku 4.5. Returns score float or None on failure."""
    try:
        import anthropic
        from app.models.contact import Contact
        from app.extensions import db
        from utils.model_router import get_model

        contact = Contact.query.get(contact_id)
        if not contact:
            return None

        profile_lines = []
        if contact.seniority:
            profile_lines.append(f'Seniority: {contact.seniority}')
        if contact.job_title:
            profile_lines.append(f'Job title: {contact.job_title}')
        if contact.company_name:
            profile_lines.append(f'Company: {contact.company_name}')
        if contact.company_size:
            profile_lines.append(f'Company size: {contact.company_size}')
        if contact.industry:
            profile_lines.append(f'Industry: {contact.industry}')
        if contact.department:
            profile_lines.append(f'Department: {contact.department}')
        if contact.city:
            profile_lines.append(f'Location: {contact.city}, {contact.country or ""}')
        filled_count = sum(1 for f in _PROFILE_FIELDS if getattr(contact, f, None))
        profile_lines.append(f'Profile completeness: {filled_count}/{len(_PROFILE_FIELDS)} fields filled')
        profile_lines.append(f'Source: {contact.source}')

        profile_text = '\n'.join(profile_lines) or 'No profile data'

        client = anthropic.Anthropic()
        model = get_model('contact_score')
        response = client.messages.create(
            model=model,
            max_tokens=20,
            messages=[{
                'role': 'user',
                'content': (
                    'Score this B2B consulting prospect on a 0.000–1.000 scale. '
                    'Factors: seniority 30%, company size 25%, industry fit 25%, '
                    'profile completeness 15%, source quality 5%. '
                    'Reply with ONLY the decimal number, nothing else.\n\n'
                    + profile_text
                ),
            }],
        )
        raw = response.content[0].text.strip()
        score = float(raw)
        return round(min(max(score, 0.0), 1.0), 3)
    except Exception as e:
        logger.debug('Claude scoring failed for %s: %s — using heuristic', contact_id, e)
        try:
            from app.models.contact import Contact
            contact = Contact.query.get(contact_id)
            return _heuristic_score(contact) if contact else None
        except Exception:
            return None
