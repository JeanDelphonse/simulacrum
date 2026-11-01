"""Imported senders -> CRM contacts (PRD-SIM-MAIL-001).

Contacts live in the existing `contacts` table. Every field value records its
source and confidence in contact_field_sources; a higher-confidence value wins
field by field, and a value the user set (or typed before import existed) is
locked and never overwritten by a sync.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# The PRD's 12 fields, as Contact columns.
FIELDS = ('first_name', 'last_name', 'email', 'phone', 'company_name', 'job_title', 'role_to_user',
          'expertise', 'industry', 'company_size', 'linkedin_url', 'department', 'city',
          'state_region', 'country')
FIELD_LABELS = {'first_name': 'First name', 'last_name': 'Last name', 'email': 'Email',
                'phone': 'Telephone', 'company_name': 'Company', 'job_title': 'Job title',
                'role_to_user': 'Role (to you)', 'expertise': 'Expertise', 'industry': 'Industry',
                'company_size': 'Company size', 'linkedin_url': 'LinkedIn', 'department': 'Department',
                'city': 'City', 'state_region': 'Region', 'country': 'Country'}
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
_MX_CACHE: dict = {}


def domain_has_mx(domain: str) -> bool | None:
    """True / False, or None if the lookup couldn't run. Cached per process."""
    domain = (domain or '').lower()
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    try:
        import dns.resolver
        try:
            dns.resolver.resolve(domain, 'MX', lifetime=3)
            ok = True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            ok = False
    except Exception:
        ok = None
    _MX_CACHE[domain] = ok
    return ok


def readiness(contact, role_prefixes: list) -> tuple:
    """(campaign_ready, reason). PRD: a real person, a valid deliverable
    address, not suppressed or DNC, with at least a first name and a company."""
    from app.models.mail_crm import UserSuppression, email_hash
    from app.models.outreach_email import EmailSuppression
    from app.services.mail_scoring import is_role_address
    email = (contact.email or '').strip().lower()
    if contact.tier == 'role' or is_role_address(email, role_prefixes):
        return False, 'Role address, not a person'
    if not _EMAIL_RE.match(email):
        return False, 'Email address is not valid'
    if contact.do_not_contact:
        return False, 'Do Not Contact'
    if EmailSuppression.is_suppressed(email) or UserSuppression.query.filter_by(
            user_id=contact.user_id, email_hash=email_hash(email)).first():
        return False, 'Unsubscribed, bounced or suppressed'
    if not (contact.first_name or '').strip() or not (contact.company_name or '').strip():
        return False, 'Needs a first name and a company'
    mx = domain_has_mx(email.rsplit('@', 1)[-1])
    if mx is False:
        return False, 'Email domain does not accept mail'
    if mx is None:
        return False, 'Email domain could not be checked yet'
    return True, None


def _current_sources(contact_id: str) -> dict:
    from app.models.mail_crm import ContactFieldSource
    return {s.field: s for s in ContactFieldSource.query.filter_by(contact_id=contact_id).all()}


def _seed_existing(contact, sources: dict) -> dict:
    """Values a contact already had before import get a source row: typed or
    CSV-imported values count as the user's and are locked."""
    from app.extensions import db
    from app.models.mail_crm import ContactFieldSource
    user_entered = contact.source in ('manual_entry', 'csv_import', 'linkedin_import', 'inbound_referral')
    for f in FIELDS:
        if f in sources or f == 'email':
            continue
        value = getattr(contact, f, None)
        if f == 'country' and value == 'United States' and not user_entered:
            continue                     # the column default, not a fact
        if value not in (None, ''):
            row = ContactFieldSource(contact_id=contact.id, field=f, value=str(value)[:500],
                                     source='user' if user_entered else 'enrichment',
                                     confidence=1.0 if user_entered else 0.6, locked=user_entered)
            db.session.add(row)
            sources[f] = row
    return sources


def apply_fields(contact, fields: dict, account_id: str | None, message_id: str | None) -> list:
    """fields: {field: (value, source, confidence)}. Returns the fields changed."""
    from app.extensions import db
    from app.models.mail_crm import ContactFieldSource
    sources = _seed_existing(contact, _current_sources(contact.id))
    changed, now = [], datetime.utcnow()
    for f, triple in (fields or {}).items():
        if f not in FIELDS or not triple:
            continue
        value, source, conf = triple
        if value in (None, ''):
            continue
        value = str(value).strip()[:500]
        cur = sources.get(f)
        if cur is not None and cur.locked:
            continue
        if cur is not None and (cur.value or '').lower() == value.lower():
            cur.seen_at, cur.confidence = now, max(cur.confidence, conf)
            continue
        if cur is not None and conf < cur.confidence:
            continue
        if cur is None:
            cur = ContactFieldSource(contact_id=contact.id, field=f)
            db.session.add(cur)
            sources[f] = cur
        cur.value, cur.source, cur.confidence = value, source, conf
        cur.account_id, cur.message_id, cur.seen_at = account_id, (message_id or '')[:300] or None, now
        setattr(contact, f, value[:_max_len(f)])
        changed.append(f)
    return changed


def _max_len(field: str) -> int:
    from app.models.contact import Contact
    col = Contact.__table__.columns.get(field)
    return getattr(col.type, 'length', None) or 500


def lock_user_edits(contact, data: dict) -> None:
    """The user edited these fields: record them as the user's and lock them."""
    from app.extensions import db
    from app.models.mail_crm import ContactFieldSource
    sources = _current_sources(contact.id)
    for f in FIELDS:
        if f not in data:
            continue
        row = sources.get(f)
        if row is None:
            row = ContactFieldSource(contact_id=contact.id, field=f)
            db.session.add(row)
        row.value = None if data[f] in (None, '') else str(data[f])[:500]
        row.source, row.confidence, row.locked, row.seen_at = 'user', 1.0, True, datetime.utcnow()


def upsert_sender(user_id: str, account, sender: dict, role_prefixes: list):
    """Create or update the contact for one imported sender. Returns the
    contact, or None if it must not be imported."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.mail_crm import ContactAccountLink, UserSuppression, email_hash
    from app.services.contact_lookup import is_placeholder_contact
    from sqlalchemy import func
    from utils.id_gen import generate_id

    email = (sender.get('email') or '').strip().lower()
    fields = sender.get('fields') or {}
    first = (fields.get('first_name') or ('', None, 0))[0] or ''
    last = (fields.get('last_name') or ('', None, 0))[0] or ''
    company = (fields.get('company_name') or ('', None, 0))[0] or ''
    if not _EMAIL_RE.match(email) or is_placeholder_contact(first, last, company, email):
        return None
    if UserSuppression.query.filter_by(user_id=user_id, email_hash=email_hash(email),
                                       reason='deleted').first():
        return None        # the user deleted this person; don't bring them back

    contact = Contact.query.filter(Contact.user_id == user_id, func.lower(Contact.email) == email).first()
    if contact is None:
        contact = Contact(id=generate_id(), user_id=user_id, first_name=first[:100] or '',
                          last_name=last[:100] or '', email=email, source='email_import',
                          source_notes=('Imported from %s' % account.address)[:500] if account else None,
                          country=None)
        db.session.add(contact)
        db.session.flush()
    apply_fields(contact, dict(fields, email=(email, 'header', 1.0)), getattr(account, 'id', None),
                 sender.get('message_id'))
    tier = sender.get('tier')
    order = {'role': 0, 'known': 1, 'warm': 2}
    if tier and (contact.tier is None or (contact.tier != 'role' and order.get(tier, 0) > order.get(contact.tier, 0))):
        contact.tier = tier
    if account is not None:
        link = ContactAccountLink.query.filter_by(contact_id=contact.id, account_id=account.id).first()
        if link is None:
            link = ContactAccountLink(contact_id=contact.id, account_id=account.id)
            db.session.add(link)
        link.last_seen = datetime.utcnow()
        link.sent_count = max(link.sent_count or 0, sender.get('sent_count') or 0)
        link.received_count = max(link.received_count or 0, sender.get('received_count') or 0)
    contact.campaign_ready, contact.not_ready_reason = readiness(contact, role_prefixes)
    return contact


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def decide(user_id: str, item_ids: list, decision: str) -> dict:
    """Approve (import) or reject (skip) review items. Returns counts plus a
    domain-exclusion hint when several rejections share one domain."""
    from app.extensions import db
    from app.models.mail_crm import EmailAccount, ReviewItem, SenderSignal
    from app.services import mail_feature
    if decision not in ('approve', 'reject'):
        raise ValueError('Unknown decision.')
    prefixes = mail_feature.setting_list('mail_role_prefixes')
    items = ReviewItem.query.filter(ReviewItem.user_id == user_id, ReviewItem.id.in_(item_ids or ['-']),
                                    ReviewItem.status == ReviewItem.STATUS_PENDING).all()
    imported, rejected_domains = 0, {}
    for item in items:
        p = item.payload
        sig = SenderSignal.query.filter_by(account_id=item.account_id, email_hash=item.email_hash).first()
        if decision == 'approve':
            account = EmailAccount.query.get(item.account_id) if item.account_id else None
            fields = {k: tuple(v) for k, v in (p.get('fields_full') or {}).items()}
            c = upsert_sender(user_id, account, {'email': p.get('email'), 'fields': fields,
                                                 'tier': p.get('tier'), 'message_id': p.get('message_id'),
                                                 'sent_count': 0, 'received_count': p.get('received_count', 0)},
                              prefixes)
            if c is not None:
                item.contact_id = c.id
                imported += 1
            item.status = ReviewItem.STATUS_APPROVED
            if sig:
                sig.decision = SenderSignal.DECISION_IMPORT
        else:
            item.status = ReviewItem.STATUS_REJECTED
            if sig:
                sig.decision = SenderSignal.DECISION_SKIP
            d = (p.get('email') or '').rsplit('@', 1)[-1].lower()
            rejected_domains[d] = rejected_domains.get(d, 0) + 1
        item.payload_json = _minimise(item)
        item.decided_at = datetime.utcnow()
    db.session.commit()
    hint = None
    if rejected_domains:
        from sqlalchemy import func
        dom, n = max(rejected_domains.items(), key=lambda kv: kv[1])
        total = ReviewItem.query.filter(ReviewItem.user_id == user_id,
                                        ReviewItem.status == ReviewItem.STATUS_REJECTED,
                                        ReviewItem.payload_json.like('%"domain": "' + dom + '"%')).count()
        if total >= 3:
            hint = {'domain': dom, 'rejected': total}
    return {'decided': len(items), 'imported': imported, 'exclude_domain_hint': hint}


def _minimise(item) -> str:
    """After a decision, keep only what explains it (no extracted fields)."""
    import json
    p = item.payload
    keep = {'email': p.get('email'), 'name': p.get('name'), 'score': p.get('score'),
            'reasons': p.get('reasons', [])}
    if item.status == 'rejected':
        keep['email'] = None
        keep['name'] = None
        keep['domain'] = (p.get('email') or '').rsplit('@', 1)[-1].lower()
    return json.dumps(keep)
