"""Contact lookup for agent dispatch — database-first, always-generate approach.

Flow:
1. Query DB for qualifying contacts (score >= min, stage in prospect/active, not DNC)
2. If N+ found, return DB contacts only
3. If < N found, supplement with agent-generated list and merge by email
"""
from __future__ import annotations
import logging
from typing import Optional

from app.services.contact_scoring import AGENT_MIN_SCORES

logger = logging.getLogger(__name__)

_DEFAULT_MIN_POOL = 10

OUTREACH_ACTION_TYPES = {
    'consulting_outreach', 'cold_email_campaign', 'corporate_training_pitch',
    'speaking_proposals', 'consulting_proposal', 'alumni_reactivation',
    'lapsed_buyer_reactivation', 'affiliate_partnerships',
}


def get_contacts_for_action(
    action_type: str,
    user_id: str,
    simulation_id: str,
    min_pool: int = _DEFAULT_MIN_POOL,
) -> dict:
    """Return qualified DB contacts and the count of additional prospects to generate.

    Returns:
        {
            'db_contacts': [Contact, ...],
            'generate_count': int,   # 0 if DB pool is sufficient
            'min_score': float,
        }
    """
    if action_type not in OUTREACH_ACTION_TYPES:
        return {'db_contacts': [], 'generate_count': min_pool, 'min_score': 0.0}

    min_score = AGENT_MIN_SCORES.get(action_type, 0.50)

    try:
        from app.models.contact import Contact
        from datetime import datetime, timedelta

        query = Contact.query.filter(
            Contact.user_id == user_id,
            Contact.do_not_contact == False,
            Contact.is_archived == False,
            Contact.pipeline_stage.in_(['prospect', 'active']),
        )

        if min_score > 0:
            query = query.filter(
                (Contact.qualifying_score >= min_score) |
                (Contact.qualifying_score.is_(None))
            )

        # alumni_reactivation and lapsed_buyer_reactivation bypass closed_lost 180-day rule
        if action_type in ('alumni_reactivation', 'lapsed_buyer_reactivation'):
            cutoff = datetime.utcnow() - timedelta(days=180)
            closed_lost_ok = Contact.query.filter(
                Contact.user_id == user_id,
                Contact.do_not_contact == False,
                Contact.is_archived == False,
                Contact.pipeline_stage == 'closed_lost',
                Contact.updated_at <= cutoff,
            ).all()
        else:
            closed_lost_ok = []

        db_contacts = query.order_by(Contact.qualifying_score.desc().nullslast()).limit(min_pool * 2).all()
        db_contacts = db_contacts + closed_lost_ok

        generate_count = max(0, min_pool - len(db_contacts))
        return {
            'db_contacts': db_contacts,
            'generate_count': generate_count,
            'min_score': min_score,
        }
    except Exception as e:
        logger.error('get_contacts_for_action failed: %s', e)
        return {'db_contacts': [], 'generate_count': min_pool, 'min_score': min_score}


def format_contacts_for_prompt(contacts: list) -> str:
    """Format DB contacts as prompt context string."""
    if not contacts:
        return ''
    lines = ['Existing qualified contacts from your database (use and reference these first):']
    for c in contacts[:20]:
        parts = [c.display_name, c.job_title or '', c.company_name or '']
        if c.email:
            parts.append(c.email)
        if c.pipeline_stage:
            parts.append(f'[{c.pipeline_stage}]')
        lines.append('  - ' + ', '.join(p for p in parts if p))
    return '\n'.join(lines)


def record_agent_contacts(
    contact_data_list: list[dict],
    user_id: str,
    simulation_id: str,
    action_id: str,
    action_type: str,
) -> dict:
    """Upsert agent-generated contacts into DB, advance stages, record activities.

    contact_data_list items: {first_name, last_name, email, job_title, company_name, ...}
    Returns: {'created': int, 'updated': int, 'skipped': int}
    """
    from app.models.contact import Contact, ContactActivity
    from app.extensions import db
    from utils.id_gen import generate_id

    created = updated = skipped = 0

    for data in contact_data_list:
        email = (data.get('email') or '').strip().lower()
        if not email or not data.get('first_name') or not data.get('last_name'):
            skipped += 1
            continue

        existing = Contact.query.filter_by(user_id=user_id, email=email).first()
        if existing:
            existing.last_contacted_at = __import__('datetime').datetime.utcnow()
            changed = existing.advance_stage(
                'active', created_by='agent',
                simulation_id=simulation_id, action_id=action_id,
            )
            if not changed:
                activity = ContactActivity(
                    id=generate_id(),
                    contact_id=existing.id,
                    simulation_id=simulation_id,
                    action_id=action_id,
                    activity_type='email_sent',
                    created_by='agent',
                )
                db.session.add(activity)
            updated += 1
        else:
            contact = Contact(
                id=generate_id(),
                user_id=user_id,
                first_name=data['first_name'],
                last_name=data['last_name'],
                email=email,
                job_title=data.get('job_title'),
                company_name=data.get('company_name'),
                company_size=data.get('company_size'),
                industry=data.get('industry'),
                seniority=data.get('seniority'),
                linkedin_url=data.get('linkedin_url'),
                city=data.get('city'),
                country=data.get('country', 'United States'),
                source='agent_generated',
                source_action_id=action_id,
                pipeline_stage='prospect',
            )
            db.session.add(contact)
            db.session.flush()
            created += 1

            activity = ContactActivity(
                id=generate_id(),
                contact_id=contact.id,
                simulation_id=simulation_id,
                action_id=action_id,
                activity_type='email_sent',
                created_by='agent',
            )
            db.session.add(activity)

    try:
        db.session.commit()
    except Exception as e:
        logger.error('record_agent_contacts commit failed: %s', e)
        db.session.rollback()

    return {'created': created, 'updated': updated, 'skipped': skipped}
