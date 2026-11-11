"""The member's CRM, as briefing facts for the SME Console guide.

What the guide may see about a contact: name, job title, company, industry,
city, pipeline stage, score, last-contacted date, emails sent, whether they
replied, and Do Not Contact. Never email addresses, phone numbers, profile
links or notes.

Contacts imported from (or linked to) the member's email account are left out
entirely: Gmail Limited Use forbids passing that data to a third party such as
the AI model. Everything here is read-only; the guide can't send or change
anything. Switched by the guide setting guide_crm_access (Admin -> SME Console).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

TOP_CONTACTS = 12        # in the session briefing
LOOKUP_LIMIT = 8         # added for one answer when the member names someone
FOLLOWUP_DAYS = 7
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")
_STOP = {'the', 'and', 'for', 'with', 'who', 'what', 'which', 'when', 'how', 'should', 'could', 'would', 'about',
         'have', 'has', 'any', 'my', 'me', 'our', 'your', 'from', 'that', 'this', 'there', 'their', 'them', 'they',
         'contact', 'contacts', 'crm', 'people', 'person', 'follow', 'up', 'email', 'emails', 'list', 'show', 'tell',
         'many', 'much', 'are', 'was', 'were', 'did', 'does', 'not', 'yet', 'last', 'next', 'week', 'month', 'day',
         'can', 'you', 'all', 'get', 'got', 'want', 'need', 'help', 'goal', 'goals', 'layer', 'income', 'plan',
         'company', 'companies', 'pipeline', 'stage', 'client', 'clients', 'prospect', 'prospects', 'active'}


def enabled() -> bool:
    try:
        from app.services import guide_service
        return str(guide_service.setting('guide_crm_access')).lower() == 'on'
    except Exception:
        return False


def _eligible(user_id: str):
    """The member's contacts the guide may see: not archived, not placeholders,
    and never anything that came from their email account."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.mail_crm import ContactAccountLink
    linked = db.session.query(ContactAccountLink.contact_id)
    return (Contact.query.filter(Contact.user_id == user_id, Contact.is_archived == False,  # noqa: E712
                                 Contact.source != 'email_import', ~Contact.id.in_(linked)))


def _replied_ids(contact_ids) -> set:
    from app.models.outreach_email import EmailLog
    if not contact_ids:
        return set()
    return {cid for (cid,) in EmailLog.query.with_entities(EmailLog.contact_id).filter(
        EmailLog.contact_id.in_(list(contact_ids)), EmailLog.replied_at.isnot(None)).distinct().all()}


def _name(c) -> str:
    return ('%s %s' % (c.first_name or '', c.last_name or '')).strip() or 'Unnamed contact'


def contact_fact(c, replied: bool) -> dict:
    bits = [_name(c)]
    role = ' at '.join(x for x in ((c.job_title or '').strip(), (c.company_name or '').strip()) if x)
    if role:
        bits.append(role)
    if c.industry:
        bits.append('industry: %s' % c.industry)
    if c.city:
        bits.append(c.city)
    bits.append('stage: %s' % (c.pipeline_stage or 'prospect'))
    if c.qualifying_score is not None:
        bits.append('score %.2f' % float(c.qualifying_score))
    if c.last_contacted_at:
        bits.append('last contacted %s' % c.last_contacted_at.strftime('%b %d, %Y'))
    else:
        bits.append('not contacted yet')
    if c.outreach_count:
        bits.append('%d email%s sent' % (c.outreach_count, '' if c.outreach_count == 1 else 's'))
    bits.append('replied' if replied else 'no reply yet' if c.outreach_count else '')
    if c.do_not_contact:
        bits.append('DO NOT CONTACT')
    return {'id': 'contact:%s' % c.id, 'kind': 'contact', 'label': 'CRM · %s' % _name(c),
            'text': '; '.join(b for b in bits if b), 'layer': None}


def briefing_facts(user_id: str) -> list:
    """A summary of the CRM, follow-ups due, and the most active contacts."""
    if not enabled():
        return []
    from app.extensions import db
    from app.models.contact import Contact
    q = _eligible(user_id)
    total = q.count()
    if not total:
        return [{'id': 'crm', 'kind': 'crm', 'label': 'CRM summary', 'text': 'No contacts in the CRM yet.', 'layer': None}]
    stages = dict(q.with_entities(Contact.pipeline_stage, db.func.count(Contact.id)).group_by(Contact.pipeline_stage).all())
    dnc = q.filter(Contact.do_not_contact == True).count()  # noqa: E712
    month = datetime.utcnow() - timedelta(days=30)
    recent = q.filter(Contact.last_contacted_at >= month).count()
    facts = [{'id': 'crm', 'kind': 'crm', 'label': 'CRM summary', 'layer': None, 'text': (
        '%d contacts. By stage: %s. %d contacted in the last 30 days. %d marked Do Not Contact.'
        % (total, ', '.join('%s %d' % (k or 'prospect', v) for k, v in sorted(stages.items(), key=lambda x: -x[1])),
           recent, dnc))}]

    due_cut = datetime.utcnow() - timedelta(days=FOLLOWUP_DAYS)
    due = (q.filter(Contact.do_not_contact == False, Contact.last_contacted_at.isnot(None),  # noqa: E712
                    Contact.last_contacted_at <= due_cut).order_by(Contact.last_contacted_at.asc()).limit(40).all())
    replied = _replied_ids([c.id for c in due])
    due = [c for c in due if c.id not in replied][:8]
    if due:
        facts.append({'id': 'crm:followups', 'kind': 'crm', 'label': 'CRM · follow-ups due', 'layer': None,
                      'text': 'Contacted %d+ days ago with no reply: %s.' % (FOLLOWUP_DAYS, '; '.join(
                          '%s (%s, last %s)' % (_name(c), c.company_name or 'no company',
                                                c.last_contacted_at.strftime('%b %d')) for c in due))})

    top = (q.order_by(db.case((Contact.last_contacted_at.is_(None), 1), else_=0), Contact.last_contacted_at.desc(),
                      db.func.coalesce(Contact.qualifying_score, -1).desc()).limit(TOP_CONTACTS).all())
    replied = _replied_ids([c.id for c in top])
    facts += [contact_fact(c, c.id in replied) for c in top]
    return facts


def lookup_facts(user_id: str, text: str, have_ids=()) -> list:
    """Contacts the member names in this message (person, company or role words),
    for one answer. Read-only; at most LOOKUP_LIMIT."""
    if not enabled() or not text:
        return []
    from app.extensions import db
    from app.models.contact import Contact
    words = [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP and len(w) >= 3][:12]
    if not words:
        return []
    conds = []
    for w in words:
        like = '%' + w + '%'
        conds += [db.func.lower(Contact.first_name) == w, db.func.lower(Contact.last_name) == w,
                  db.func.lower(Contact.company_name).like(like)]
        if len(w) >= 5:
            conds.append(db.func.lower(Contact.job_title).like(like))
    have = set(have_ids)
    rows = [c for c in _eligible(user_id).filter(db.or_(*conds)).limit(LOOKUP_LIMIT * 3).all()
            if 'contact:%s' % c.id not in have][:LOOKUP_LIMIT]
    replied = _replied_ids([c.id for c in rows])
    return [contact_fact(c, c.id in replied) for c in rows]
