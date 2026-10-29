"""Contact lookup for agent dispatch — database-first, always-generate approach.

Flow:
1. Query DB for qualifying contacts (score >= min, stage in prospect/active, not DNC)
2. If N+ found, return DB contacts only
3. If < N found, supplement with agent-generated list and merge by email
"""
from __future__ import annotations
import logging
import re
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

        db_contacts = query.order_by(Contact.qualifying_score.desc()).limit(min_pool * 2).all()
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


# ── Placeholder detection ────────────────────────────────────────────────────
# Real surnames include Lead, Client and Test, so a generic word only marks a
# placeholder when it is numbered ("Prospect 3"), a name-field label ("First
# Name"), pure filler ("TBD"), or when both names are generic ("Contact Name").
_TEMPLATE_MARKER_RE = re.compile(r'[\[\]{}<>]')
_GENERIC_WORD = (r'(first|last|full|contact|prospect|person|client|lead|buyer|'
                 r'decision[\s-]*maker|hiring[\s-]*manager|recipient|name|firstname|'
                 r'lastname|sample|example|test|placeholder)')
_NUMBERED_GENERIC_RE = re.compile(r'^' + _GENERIC_WORD + r'(\s*name)?\s*#?\s*\d+$', re.I)
_GENERIC_ONLY_RE = re.compile(r'^' + _GENERIC_WORD + r'(\s*name)?$', re.I)
_FIELD_LABEL_RE = re.compile(r'^(first|last|full)\s*name$|^(firstname|lastname)$', re.I)
_FILLER_RE = re.compile(r'^(tbd|tba|n/?a|xxx+|placeholder|unknown\s*\d+)$', re.I)
_GENERIC_COMPANY_RE = re.compile(
    r'^((enterprise|target|sample|example|your|placeholder)\s+)?'
    r'(company|organi[sz]ation|firm|business|employer|client|corp(oration)?|inc)'
    r'(\s*name)?\s*(#?\s*\d+)?$', re.I)
_PLACEHOLDER_FULL_NAMES = {'john doe', 'jane doe', 'john smith', 'jane smith', 'lorem ipsum'}
_PLACEHOLDER_EMAIL_RE = re.compile(
    r'@(example|test|domain|company|companyname|yourcompany|yourdomain|'
    r'enterprisecompany\d*|placeholder)\.(com|org|net|io)$'
    r'|^(first|firstname|first\.last|firstname\.lastname|first\.lastname|flast)@', re.I)


def is_placeholder_contact(first_name='', last_name='', company_name='', email='') -> bool:
    """True for template rows an agent wrote instead of a real person, e.g.
    "[First Name 1] [Last Name 1] [Enterprise Company 1]", "Prospect 3",
    "John Doe", or name@example.com. These must never reach the CRM: they look
    like leads, get scheduled emails, and consume the recipient ledger."""
    first = (first_name or '').strip()
    last = (last_name or '').strip()
    company = (company_name or '').strip()
    mail = (email or '').strip()
    if any(_TEMPLATE_MARKER_RE.search(v) for v in (first, last, company, mail) if v):
        return True
    for name in (first, last):
        if name and (_NUMBERED_GENERIC_RE.match(name) or _FIELD_LABEL_RE.match(name)
                     or _FILLER_RE.match(name)):
            return True
    if first and _GENERIC_ONLY_RE.match(first) and (not last or _GENERIC_ONLY_RE.match(last)):
        return True
    if ('%s %s' % (first, last)).strip().lower() in _PLACEHOLDER_FULL_NAMES:
        return True
    if company and _GENERIC_COMPANY_RE.match(company):
        return True
    if mail and _PLACEHOLDER_EMAIL_RE.search(mail):
        return True
    return False


def is_placeholder_item(item) -> bool:
    """is_placeholder_contact for a dict or Prospect-like object."""
    get = item.get if isinstance(item, dict) else (lambda k, d='': getattr(item, k, d))
    return is_placeholder_contact(get('first_name', '') or '', get('last_name', '') or '',
                                  get('company_name', '') or '', get('email', '') or '')


def _fallback_email_parts() -> tuple[str, str]:
    """(local_part, domain) of the configured fallback contact address."""
    from flask import current_app
    base = current_app.config.get('FALLBACK_CONTACT_EMAIL', 'valuemanager.management@gmail.com')
    local, _, domain = base.partition('@')
    return local, domain


def is_fallback_email(email: str) -> bool:
    """True if the address is the configured fallback (base or numbered variant)."""
    if not email:
        return False
    import re
    local, domain = _fallback_email_parts()
    pattern = re.compile(
        r'^' + re.escape(local) + r'\d*@' + re.escape(domain) + r'$', re.IGNORECASE,
    )
    return bool(pattern.match(email.strip()))


def get_next_fallback_email(user_id: str, used_emails: set[str] = None) -> str:
    """Find the next available incremented fallback email for the given user_id."""
    from app.models.contact import Contact
    import re

    user_id = user_id or '_system'
    local, domain = _fallback_email_parts()

    # Query all contacts for this user starting with the prefix
    contacts = Contact.query.filter(
        Contact.user_id == user_id,
        Contact.email.like(f'{local}%@{domain}')
    ).all()

    max_num = 0
    pattern = re.compile(
        r'^' + re.escape(local) + r'(\d+)@' + re.escape(domain) + r'$', re.IGNORECASE,
    )

    # Check DB contacts
    for c in contacts:
        match = pattern.match(c.email)
        if match:
            try:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
            except ValueError:
                pass

    # Check currently used fallback emails in this batch
    if used_emails:
        for email in used_emails:
            match = pattern.match(email)
            if match:
                try:
                    num = int(match.group(1))
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass

    return f'{local}{max_num + 1}@{domain}'


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
    used_fallbacks = set()

    for data in contact_data_list:
        if is_placeholder_item(data):
            logger.warning('record_agent_contacts: skipped placeholder contact %r',
                           (data.get('first_name'), data.get('last_name'), data.get('company_name')))
            skipped += 1
            continue
        email = (data.get('email') or '').strip().lower()
        if not email:
            skipped += 1
            continue
        if not data.get('first_name') or not data.get('last_name'):
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


def save_contacts_to_crm(
    contacts: list,
    user_id: str,
    simulation_id: str,
    action_type: str,
    cycle_id: str = None,
    update_only: bool = False,
) -> int:
    """
    Upsert a list of contacts into the CRM (FR-CRM-03, FR-CRM-04).

    Match priority: email → name+company → create new.
    If update_only=True (alumni_reactivation), never create new contacts.
    Returns count of contacts upserted/created.
    """
    if not contacts or not user_id:
        return 0

    from app.extensions import db
    from app.models.contact import Contact, ContactActivity
    from utils.id_gen import generate_id
    from datetime import datetime

    saved = 0
    used_fallbacks = set()
    for raw in contacts:
        if not raw.get('first_name') and not raw.get('last_name'):
            continue
        if is_placeholder_item(raw):
            logger.warning('save_contacts_to_crm: skipped placeholder contact %r (action_type=%s)',
                           (raw.get('first_name'), raw.get('last_name'), raw.get('company_name')),
                           action_type)
            continue

        first = (raw.get('first_name') or '').strip()
        last  = (raw.get('last_name') or '').strip()
        email = (raw.get('email') or '').strip().lower()
        if not email:
            continue

        # Try to find existing contact
        existing = None
        if email:
            existing = Contact.query.filter_by(user_id=user_id, email=email).first()
        if not existing and first and last and raw.get('company_name'):
            existing = Contact.query.filter_by(
                user_id=user_id,
                first_name=first,
                last_name=last,
                company_name=raw.get('company_name'),
            ).first()

        if existing:
            # Enrich null fields only
            for field, val in [
                ('email', email),
                ('phone', raw.get('phone')),
                ('job_title', raw.get('job_title')),
                ('company_name', raw.get('company_name')),
                ('company_size', raw.get('company_size')),
                ('industry', raw.get('industry')),
                ('linkedin_url', raw.get('linkedin_url')),
                ('notes', raw.get('qualifying_notes')),
            ]:
                if val and getattr(existing, field) is None:
                    setattr(existing, field, val)
            if cycle_id and not existing.source_cycle_id:
                existing.source_cycle_id = cycle_id
            saved += 1
        elif not update_only:
            contact = Contact(
                id=generate_id(),
                user_id=user_id,
                first_name=first or 'Unknown',
                last_name=last or '',
                email=email,
                phone=raw.get('phone'),
                job_title=raw.get('job_title'),
                company_name=raw.get('company_name'),
                company_size=raw.get('company_size'),
                industry=raw.get('industry'),
                linkedin_url=raw.get('linkedin_url'),
                notes=raw.get('qualifying_notes'),
                source='agent_generated',
                source_cycle_id=cycle_id,
                pipeline_stage='prospect',
            )
            db.session.add(contact)
            saved += 1

    if saved:
        try:
            db.session.commit()
        except Exception as exc:
            logger.error('CONTACTS_SAVE FAILED save_contacts_to_crm db commit error: %s', exc, exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass
            return 0

    return saved
