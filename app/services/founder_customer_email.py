"""Founder Track customer communication (CUSTOMER_EMAIL_PLAN.md, SIM-PRD-FND-001
decision of Nov 21, 2026).

Seven customer agents (config/founder_agents.json, external == 'customer') draft
emails. With fnd_customer_email on, SimiSI sends an approved draft in the
founder's name ("<Founder> via SimiSI", Reply-To the founder) to contacts in the
venture's contact book that the founders approved for that agent:

- contact book: venture_contact (proposed / approved / contacted / replied /
  opted_out / bounced), per-agent access in venture_contact_access, and the
  venture do-not-contact list venture_suppression;
- approval: each agent's `customer_send` block (first contact and follow-ups:
  'each' or 'campaign', the most a founder may raise to) and the founder's
  autonomy level (1 = copy only, 2 = approve each, 3+ = up to the rule);
- delivery: outreach_email_service.send_outreach_email (postal address,
  footer, global suppression, EmailLog), one venture_email_message per
  recipient, scheduled rows sent by cron_customer_email.py;
- feedback: SendGrid bounces and complaints and the founder unsubscribe link
  feed the do-not-contact list; a venture over its complaint or bounce
  threshold has customer sending paused until an admin resumes it.

With the setting off nothing here runs and customer drafts stay copy-only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'^[^@\s<>"\',;]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$')
URL_RE = re.compile(r'^https://[^\s<>"]{4,500}$')
# Amounts the Pricing Test lock compares: $49, $1,200.00, 49 USD, EUR 30, 29.99/mo is "$29.99".
PRICE_RE = re.compile(r'(?:[$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b)?'
                      r'|\b(?:USD|EUR|GBP)\s?\d[\d,]*(?:\.\d+)?'
                      r'|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|dollars|euros)\b)')
# An unfilled template slot left in a draft: "[Company]" (not a Markdown link "[text](url)").
PLACEHOLDER_RE = re.compile(r'\[([^\[\]\n]{2,40})\](?!\()')
MODES = ('each', 'campaign')
MODE_LABELS = {'each': 'Approve each', 'campaign': 'Approve the campaign'}
STATE_LABELS = {'proposed': 'Proposed', 'approved': 'Approved', 'contacted': 'Contacted', 'replied': 'Replied',
                'opted_out': 'Opted out', 'bounced': 'Bounced'}
SENDABLE_STATES = ('approved', 'contacted', 'replied')
MAX_SUGGESTIONS = 10
MAX_RECIPIENTS = 200
ONE_SEND_BATCH = 50          # recipients sent inline; the rest go to the worker


class CustomerEmailError(ValueError):
    def __init__(self, message: str = '', status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Settings and rules
# ---------------------------------------------------------------------------

def _cfg(key):
    from app.services import founder_config
    return founder_config.get(key)


def enabled() -> bool:
    try:
        return _cfg('fnd_customer_email') == 'on'
    except Exception:
        return False


def customer_agents() -> list:
    """[(key, label)] for the agents that email customers, in catalog order."""
    from app.services import founder_registry
    return [(a['action_type'], a['label']) for a in founder_registry.all_agents()
            if a.get('external') == 'customer']


def is_customer_agent(agent_key: str) -> bool:
    from app.services import founder_registry
    return (founder_registry.get(agent_key) or {}).get('external') == 'customer'


def rule(agent_key: str) -> dict:
    """The agent's customer_send block, with safe defaults (approve each)."""
    from app.services import founder_registry
    r = dict((founder_registry.get(agent_key) or {}).get('customer_send') or {})
    for k in ('first', 'followup'):
        if r.get(k) not in MODES:
            r[k] = 'each'
    return r


def agent_label(agent_key: str) -> str:
    from app.services import founder_registry
    return (founder_registry.get(agent_key) or {}).get('label') or agent_key or 'Agent'


def _norm(email) -> str:
    return (email or '').strip().lower()


def valid_email(email) -> bool:
    e = _norm(email)
    return bool(e) and len(e) <= 255 and bool(EMAIL_RE.match(e))


# ---------------------------------------------------------------------------
# Do-not-contact
# ---------------------------------------------------------------------------

def venture_suppressed(venture, email):
    from app.models.founder import VentureSuppression
    row = VentureSuppression.query.filter_by(venture_id=venture.id, email=_norm(email)).first()
    return row.reason if row is not None else None


def suppressed(venture, email):
    """Why this address can't be emailed by the venture, or None: the venture's
    do-not-contact list, then the owner's and the global opt-out lists."""
    r = venture_suppressed(venture, email)
    if r:
        return 'venture:' + r
    try:
        from app.services import suppression_service
        g = suppression_service.check(venture.owner_user_id, _norm(email))
        if g:
            return 'global:' + str(g)
    except Exception as exc:
        logger.debug('suppression check skipped: %s', exc)
    return None


def suppress(venture, email, reason: str, detail: str = '', user_id: str = None, commit: bool = True):
    """Add the address to the venture's do-not-contact list and mark the
    contact. Idempotent."""
    from app.extensions import db
    from app.models.founder import VentureContact, VentureSuppression
    e = _norm(email)
    if not e:
        return None
    reason = reason if reason in VentureSuppression.REASONS else 'manual'
    row = VentureSuppression.query.filter_by(venture_id=venture.id, email=e).first()
    if row is None:
        row = VentureSuppression(venture_id=venture.id, email=e[:255], reason=reason, detail=(detail or '')[:300],
                                 added_by=user_id)
        db.session.add(row)
    c = VentureContact.query.filter_by(venture_id=venture.id, email=e).first()
    if c is not None:
        c.state = 'bounced' if reason == 'bounce' else 'opted_out'
    from app.services import founder_ventures
    founder_ventures.log_event(venture.id, 'customer_suppressed', '%s (%s)' % (reason, detail[:100] if detail else ''),
                               user_id=user_id)
    if commit:
        db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Contact book
# ---------------------------------------------------------------------------

def _clean(value, n):
    v = re.sub(r'\s+', ' ', str(value or '')).strip()
    return v[:n] or None


def contacts(venture) -> list:
    from app.models.founder import VentureContact
    return (VentureContact.query.filter_by(venture_id=venture.id)
            .order_by(VentureContact.state, VentureContact.name, VentureContact.email).all())


def _get_contact(venture, contact_id):
    from app.models.founder import VentureContact
    c = VentureContact.query.filter_by(id=contact_id, venture_id=venture.id).first() if contact_id else None
    if c is None:
        raise CustomerEmailError('Contact not found.', 404)
    return c


def _member(venture, user):
    from app.services import founder_ventures
    if founder_ventures.get_member(venture, getattr(user, 'id', None)) is None:
        raise CustomerEmailError('Only the founders of this venture can change its contact book.', 403)


def add_contact(venture, user, email, name='', company='', role='', segment='', note='', source='manual',
                state='approved', agent_key=None, commit=True):
    """Add one contact. Raises CustomerEmailError for a bad, duplicate or
    do-not-contact address."""
    from app.extensions import db
    from app.models.founder import VentureContact
    if user is not None:
        _member(venture, user)
    e = _norm(email)
    if not valid_email(e):
        raise CustomerEmailError('"%s" is not a valid email address.' % (email or '')[:80])
    if VentureContact.query.filter_by(venture_id=venture.id, email=e).first() is not None:
        raise CustomerEmailError('%s is already in your contact book.' % e, 409)
    why = suppressed(venture, e)
    if why:
        raise CustomerEmailError('%s asked not to be emailed (or their address bounced), so it cannot be added.' % e,
                                 409)
    uid = getattr(user, 'id', None)
    now = datetime.utcnow()
    state = state if state in ('proposed', 'approved') else 'approved'
    c = VentureContact(venture_id=venture.id, email=e, name=_clean(name, 200), company=_clean(company, 200),
                       role=_clean(role, 120), segment=_clean(segment, 120), note=_clean(note, 500),
                       source=source if source in VentureContact.SOURCES else 'manual', state=state,
                       proposed_by_agent=(agent_key or None) if state == 'proposed' else None, added_by=uid,
                       approved_by=uid if state == 'approved' else None,
                       approved_at=now if state == 'approved' else None)
    db.session.add(c)
    db.session.flush()
    if commit:
        from app.services import founder_ventures
        founder_ventures.log_event(venture.id, 'contact_added', '%s (%s)' % (c.id, c.source), user_id=uid)
        db.session.commit()
    return c


def update_contact(venture, user, contact_id, data: dict):
    from app.extensions import db
    _member(venture, user)
    c = _get_contact(venture, contact_id)
    for key, n in (('name', 200), ('company', 200), ('role', 120), ('segment', 120), ('note', 500)):
        if key in data:
            setattr(c, key, _clean(data.get(key), n))
    if 'poc_customer' in data:
        c.poc_customer = bool(data.get('poc_customer')) and str(data.get('poc_customer')).lower() not in ('0', 'false')
    db.session.commit()
    return c


def decide_contact(venture, user, contact_id, decision: str):
    """approve | reject (a proposed contact) | remove | suppress | restore."""
    from app.extensions import db
    from app.models.founder import VentureContactAccess, VentureSuppression
    from app.services import founder_ventures
    _member(venture, user)
    c = _get_contact(venture, contact_id)
    uid = user.id
    if decision == 'approve':
        if c.state != 'proposed':
            raise CustomerEmailError('Only a proposed contact can be approved.', 409)
        why = suppressed(venture, c.email)
        if why:
            raise CustomerEmailError('%s asked not to be emailed, so it cannot be approved.' % c.email, 409)
        c.state, c.approved_by, c.approved_at = 'approved', uid, datetime.utcnow()
    elif decision in ('reject', 'remove'):
        if decision == 'reject' and c.state != 'proposed':
            raise CustomerEmailError('Only a proposed contact can be rejected.', 409)
        VentureContactAccess.query.filter_by(venture_contact_id=c.id).delete(synchronize_session=False)
        db.session.delete(c)
    elif decision == 'suppress':
        suppress(venture, c.email, 'manual', 'added by a founder', user_id=uid, commit=False)
    elif decision == 'restore':
        row = VentureSuppression.query.filter_by(venture_id=venture.id, email=c.email).first()
        if row is not None and row.reason != 'manual':
            raise CustomerEmailError('This person opted out, bounced or complained. Only they can opt back in, '
                                     'so they stay on the do-not-contact list.', 409)
        if row is not None:
            db.session.delete(row)
        c.state = 'approved'
    else:
        raise CustomerEmailError('Unknown decision.')
    founder_ventures.log_event(venture.id, 'contact_' + decision, contact_id, user_id=uid)
    db.session.commit()
    return None if decision in ('reject', 'remove') else c


def set_access(venture, user, contact_id, agent_key, allowed: bool):
    from app.extensions import db
    from app.models.founder import VentureContactAccess
    _member(venture, user)
    if not is_customer_agent(agent_key):
        raise CustomerEmailError('That agent does not email customers.')
    c = _get_contact(venture, contact_id)
    row = VentureContactAccess.query.filter_by(venture_contact_id=c.id, agent_key=agent_key).first()
    if allowed and row is None:
        if c.state not in SENDABLE_STATES:
            raise CustomerEmailError('Approve the contact first; only approved contacts can be given to an agent.',
                                     409)
        db.session.add(VentureContactAccess(venture_id=venture.id, venture_contact_id=c.id, agent_key=agent_key,
                                            granted_by=user.id))
    elif not allowed and row is not None:
        db.session.delete(row)
    db.session.commit()
    return bool(allowed)


def access_map(venture) -> dict:
    """{contact_id: set(agent_keys)}"""
    from app.models.founder import VentureContactAccess
    out = {}
    for r in VentureContactAccess.query.filter_by(venture_id=venture.id).all():
        out.setdefault(r.venture_contact_id, set()).add(r.agent_key)
    return out


CSV_HEADERS = {'email': ('email', 'e-mail', 'email address', 'mail'),
               'name': ('name', 'full name', 'contact', 'contact name'),
               'first': ('first name', 'first', 'firstname', 'given name'),
               'last': ('last name', 'last', 'lastname', 'surname', 'family name'),
               'company': ('company', 'organization', 'organisation', 'firm', 'company name'),
               'role': ('role', 'title', 'job title', 'position'),
               'segment': ('segment', 'group', 'list', 'tag')}


def import_csv(venture, user, text: str, own_list_confirmed: bool, agent_keys=None) -> dict:
    """Add contacts from a CSV the founders own. Rows are added as Approved
    (a founder uploaded them). Duplicates, bad addresses and anyone on a
    do-not-contact list are skipped and reported. agent_keys (optional) are
    ticked for every added contact."""
    from app.extensions import db
    _member(venture, user)
    if not own_list_confirmed:
        raise CustomerEmailError('Confirm these are people you know or who asked to hear from you. Purchased or '
                                 'rented lists are not accepted.')
    text = (text or '').lstrip('﻿')
    if not text.strip():
        raise CustomerEmailError('The file is empty.')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        raise CustomerEmailError('The file is empty.')
    head = [re.sub(r'\s+', ' ', h or '').strip().lower() for h in rows[0]]
    col = {}
    for key, names in CSV_HEADERS.items():
        for i, h in enumerate(head):
            if h in names and key not in col:
                col[key] = i
    body = rows[1:]
    if 'email' not in col:
        # No header row: take the first column that looks like an address.
        for i, v in enumerate(rows[0]):
            if valid_email(v):
                col = {'email': i}
                body = rows
                break
    if 'email' not in col:
        raise CustomerEmailError('No email column found. Add a header row with an "email" column.')
    cap = int(_cfg('fnd_csv_max_rows'))
    if len(body) > cap:
        raise CustomerEmailError('The file has %d rows; the most in one upload is %d.' % (len(body), cap))
    keys = [k for k in (agent_keys or []) if is_customer_agent(k)]

    def cell(r, key):
        i = col.get(key)
        return r[i].strip() if i is not None and i < len(r) and r[i] else ''

    added, skipped = [], []
    for n, r in enumerate(body, start=2 if body is not rows else 1):
        if not any((x or '').strip() for x in r):
            continue
        name = cell(r, 'name') or ' '.join(x for x in (cell(r, 'first'), cell(r, 'last')) if x)
        try:
            c = add_contact(venture, None, cell(r, 'email'), name=name, company=cell(r, 'company'),
                            role=cell(r, 'role'), segment=cell(r, 'segment'), source='csv', commit=False)
            c.added_by = c.approved_by = user.id
            added.append(c)
        except CustomerEmailError as exc:
            skipped.append({'row': n, 'reason': str(exc)})
    db.session.flush()
    from app.models.founder import VentureContactAccess
    for c in added:
        for k in keys:
            db.session.add(VentureContactAccess(venture_id=venture.id, venture_contact_id=c.id, agent_key=k,
                                                granted_by=user.id))
    from app.services import founder_ventures
    founder_ventures.log_event(venture.id, 'contacts_imported', 'csv: %d added, %d skipped' % (len(added), len(skipped)),
                               user_id=user.id)
    db.session.commit()
    return {'added': len(added), 'skipped': skipped[:200], 'skipped_count': len(skipped)}


def mailbox_candidates(venture, user, limit: int = 300) -> list:
    """People this founder's connected mailbox (Email-to-CRM, PRD-SIM-MAIL-001)
    already put in their CRM, not yet in the venture's book."""
    from app.models.contact import Contact
    from app.models.founder import VentureContact
    _member(venture, user)
    have = {e for (e,) in VentureContact.query.with_entities(VentureContact.email)
            .filter_by(venture_id=venture.id).all()}
    rows = (Contact.query.filter(Contact.user_id == user.id, Contact.source == 'email_import',
                                 Contact.email.isnot(None))
            .order_by(Contact.last_name, Contact.first_name).limit(limit * 2).all())
    out = []
    for c in rows:
        e = _norm(c.email)
        if not valid_email(e) or e in have or getattr(c, 'do_not_contact', False):
            continue
        out.append({'id': c.id, 'email': e, 'name': ' '.join(x for x in (c.first_name, c.last_name) if x).strip(),
                    'company': c.company_name or ''})
        if len(out) >= limit:
            break
    return out


def import_mailbox(venture, user, crm_contact_ids) -> dict:
    from app.extensions import db
    from app.models.contact import Contact
    _member(venture, user)
    ids = [str(i) for i in (crm_contact_ids or [])][:500]
    added, skipped = 0, []
    for c in Contact.query.filter(Contact.id.in_(ids), Contact.user_id == user.id,
                                  Contact.source == 'email_import').all() if ids else []:
        try:
            add_contact(venture, None, c.email, name=' '.join(x for x in (c.first_name, c.last_name) if x),
                        company=c.company_name or '', role=getattr(c, 'job_title', '') or '', source='imap',
                        commit=False).added_by = user.id
            added += 1
        except CustomerEmailError as exc:
            skipped.append({'email': c.email, 'reason': str(exc)})
    from app.services import founder_ventures
    founder_ventures.log_event(venture.id, 'contacts_imported', 'mailbox: %d added' % added, user_id=user.id)
    db.session.commit()
    return {'added': added, 'skipped': skipped}


def suggest_contacts(venture, agent_key: str, suggestions) -> int:
    """Contacts an agent suggests are Proposed until a founder approves them.
    Bad, duplicate and do-not-contact addresses are dropped. Doesn't commit."""
    if not enabled() or not suggestions:
        return 0
    n = 0
    for s in list(suggestions)[:MAX_SUGGESTIONS]:
        if not isinstance(s, dict):
            continue
        try:
            add_contact(venture, None, s.get('email'), name=s.get('name') or '', company=s.get('company') or '',
                        role=s.get('role') or '', note=s.get('reason') or '',
                        source='agent', state='proposed', agent_key=agent_key, commit=False)
            n += 1
        except CustomerEmailError:
            continue
    return n


# ---------------------------------------------------------------------------
# Drafts: who may receive them, at what approval
# ---------------------------------------------------------------------------

def action_agent(action):
    """The agent key behind a customer draft, or None if it isn't one."""
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    if action is None or action.action_type != 'customer_message' or not action.plan_step_id:
        return None
    step = db.session.get(VenturePlanStep, action.plan_step_id)
    key = getattr(step, 'agent_key', None)
    return key if key and is_customer_agent(key) else None


def level(venture, action, agent_key) -> int:
    from app.services import founder_autonomy
    if action.level_required is not None:
        return int(action.level_required)
    return int(founder_autonomy.level_for(venture, agent_key))


def mode_for(lvl: int, r: dict, kind: str):
    """'each', 'campaign' or None (copy only) for one recipient."""
    if lvl <= 1:
        return None
    if lvl == 2:
        return 'each'
    return r.get(kind) or 'each'


def _sent_kinds(venture) -> dict:
    """{contact_id: True} for contacts this venture already emailed."""
    from app.models.founder import VentureEmailMessage
    return {cid: True for (cid,) in VentureEmailMessage.query.with_entities(VentureEmailMessage.venture_contact_id)
            .filter(VentureEmailMessage.venture_id == venture.id, VentureEmailMessage.status == 'sent').distinct()}


def _already_for_action(action) -> set:
    from app.models.founder import VentureEmailMessage
    return {cid for (cid,) in VentureEmailMessage.query.with_entities(VentureEmailMessage.venture_contact_id)
            .filter(VentureEmailMessage.outreach_action_id == action.id,
                    VentureEmailMessage.status.in_(('scheduled', 'sending', 'sent'))).all()}


def recipients(venture, action) -> list:
    """Every contact the draft's agent may email, each with its kind, approval
    mode and, when it can't be picked, why."""
    agent_key = action_agent(action)
    if agent_key is None:
        return []
    r = rule(agent_key)
    lvl = level(venture, action, agent_key)
    access = access_map(venture)
    emailed = _sent_kinds(venture)
    done = _already_for_action(action)
    out = []
    for c in contacts(venture):
        if agent_key not in access.get(c.id, set()) or c.state == 'proposed':
            continue
        kind = 'followup' if emailed.get(c.id) else 'first'
        why = None
        if c.state not in SENDABLE_STATES:
            why = STATE_LABELS.get(c.state, c.state)
        elif suppressed(venture, c.email):
            why = 'On a do-not-contact list'
        elif c.id in done:
            why = 'Already sent this draft'
        elif r.get('poc_customer_only') and not c.poc_customer:
            why = 'Not your proof-of-concept customer'
        elif kind == 'followup' and r.get('followup_requires_reply') and c.state != 'replied':
            why = 'Follow-ups go only to prospects who replied'
        out.append({'contact': c, 'kind': kind, 'mode': mode_for(lvl, r, kind), 'blocked': why})
    return out


def _merge(text: str, contact, sender_name: str) -> str:
    first = contact.first_name or 'there'
    company = contact.company or 'your team'
    t = text or ''
    t = re.sub(r'\[(?:first[ _-]?name|name|recipient(?: name)?|contact(?: name)?)\]|\{\{?\s*(?:first_name|name)\s*\}?\}',
               first, t, flags=re.I)
    t = re.sub(r'\[(?:company|company name|organi[sz]ation)\]|\{\{?\s*company\s*\}?\}', company, t, flags=re.I)
    t = re.sub(r'\[(?:your name|founder(?:\'s)? name|my name|sender name)\]|\{\{?\s*(?:sender_name|your_name)\s*\}?\}',
               sender_name, t, flags=re.I)
    return t


class _Sample(object):
    """Stand-in recipient: merge fields count as filled when checking a draft
    for slots the founders still have to fill."""
    first_name, company = 'Sam', 'Example Co'


_SAMPLE = _Sample()


def render(action, contact, sender_name: str) -> tuple:
    """(subject, body) for one recipient: merge fields filled, the scheduling
    link added when the agent needs one and the draft doesn't carry it."""
    meta = action.send_meta or {}
    subject = _merge(action.subject or '', contact, sender_name).strip() or 'A note from %s' % sender_name
    body = _merge(action.body or '', contact, sender_name).strip()
    link = meta.get('scheduling_link')
    if link and link not in body:
        body += '\n\nPick a time that suits you: %s' % link
    return subject[:300], body


def leftover_placeholders(text: str) -> list:
    return sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(text or '')
                   if not m.group(1).strip().lower().startswith(('x]', ' '))})[:5]


def prices_in(text: str) -> list:
    return [norm_price(m.group(0)) for m in PRICE_RE.finditer(text or '')]


def norm_price(p: str) -> str:
    return re.sub(r'[\s,]', '', p or '').lower()


def content_hash(action, contact_ids) -> str:
    meta = action.send_meta or {}
    raw = json.dumps({'s': action.subject or '', 'b': action.body or '', 'r': sorted(contact_ids or []),
                      'l': meta.get('scheduling_link') or '', 'p': sorted(meta.get('locked_prices') or [])},
                     sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def sender_name(user) -> str:
    return (getattr(user, 'full_name', '') or '').strip() or 'The founder'


def from_name(user) -> str:
    return '%s via SimiSI' % sender_name(user)


def from_address(user) -> str:
    v = _cfg('fnd_customer_from_address')
    if v:
        return v
    from app.services import send_pipeline
    try:
        return send_pipeline.sender_for(user)['from_email']
    except Exception:
        from flask import current_app
        return current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simisi.io')


def _owner(venture):
    from app.extensions import db
    from app.models.user import User
    return db.session.get(User, venture.owner_user_id) if venture.owner_user_id else None


def postal_address(venture):
    from app.services import sender_identity
    owner = _owner(venture)
    return sender_identity.postal_address(owner) if owner is not None else None


def copy_only_reason(venture, action, user=None):
    """Why this customer draft can't be sent through SimiSI (so it stays
    copy-only), or None."""
    from app.services import founder_autonomy, founder_obs_service, founder_ventures
    if not enabled():
        return 'Customer email is not switched on.'
    agent_key = action_agent(action)
    if agent_key is None:
        return 'Only customer drafts are sent through SimiSI.'
    if not founder_ventures.is_paid(venture):
        return 'Customer email unlocks with the Founder Track.'
    if venture.customer_paused_at:
        return 'Customer email is paused for this venture: %s' % (venture.customer_paused_reason or 'see Contacts')
    if venture.paused_at:
        return 'SimiSI paused this venture.'
    if founder_obs_service.agent_paused(agent_key):
        return 'SimiSI paused this agent.'
    if action.status == 'blocked':
        return 'This draft was declined.'
    if level(venture, action, agent_key) <= founder_autonomy.MIN_LEVEL:
        return 'This agent is set to advisor only: copy the draft and send it yourself.'
    return None


def approver_ok(action, user) -> bool:
    from app.services import founder_autonomy
    holder = founder_autonomy.approver_for(action)
    return holder is not None and holder.user_id == getattr(user, 'id', None)


def sent_today(venture, agent_key) -> int:
    from app.models.founder import VentureEmailMessage
    since = datetime.utcnow() - timedelta(days=1)
    return VentureEmailMessage.query.filter(
        VentureEmailMessage.venture_id == venture.id, VentureEmailMessage.agent_key == agent_key,
        VentureEmailMessage.status.in_(('sent', 'sending')), VentureEmailMessage.sent_at >= since).count()


def scheduled_count(venture, agent_key) -> int:
    from app.models.founder import VentureEmailMessage
    return VentureEmailMessage.query.filter_by(venture_id=venture.id, agent_key=agent_key, status='scheduled').count()


# ---------------------------------------------------------------------------
# Edit, lock prices, scheduling link (before approval)
# ---------------------------------------------------------------------------

def _editable(venture, action, user):
    if copy_only_reason(venture, action, user):
        raise CustomerEmailError(copy_only_reason(venture, action, user), 409)
    if not approver_ok(action, user):
        from app.services import founder_autonomy
        raise CustomerEmailError('%s has the final say on customer contact and approves and sends these drafts.'
                                 % founder_autonomy.approver_name(action), 403)


def edit_draft(venture, action, user, subject, body):
    """The founder edits the draft before sending. Any edit invalidates an
    earlier preview (the content hash changes)."""
    from app.extensions import db
    from app.services import founder_ventures
    _editable(venture, action, user)
    body = str(body or '').strip()
    if not body:
        raise CustomerEmailError('The email needs a body.')
    if len(body) > 20000:
        raise CustomerEmailError('The email is too long.')
    r = rule(action_agent(action))
    meta = action.send_meta or {}
    if r.get('locked_prices') and meta.get('locked_prices'):
        bad = sorted(set(prices_in(body) + prices_in(subject)) - set(meta['locked_prices']))
        if bad:
            raise CustomerEmailError('The prices are locked (%s). Unlock them first to change a price.'
                                     % ', '.join(meta['locked_prices']), 409)
    action.subject = (str(subject or '').strip()[:300] or None)
    action.body = body
    founder_ventures.log_event(venture.id, 'customer_draft_edited', action.id, user_id=user.id)
    db.session.commit()
    return action


def set_meta(venture, action, user, scheduling_link=None, locked_prices=None):
    """Scheduling link (Customer Interview) and locked price versions (Pricing
    Test). locked_prices=[] unlocks."""
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    from app.services import founder_ventures
    _editable(venture, action, user)
    r = rule(action_agent(action))
    meta = dict(action.send_meta or {})
    if scheduling_link is not None:
        link = str(scheduling_link or '').strip()
        if link and not URL_RE.match(link):
            raise CustomerEmailError('The scheduling link must be a full https:// address.')
        meta['scheduling_link'] = link or None
    if locked_prices is not None:
        if not r.get('locked_prices'):
            raise CustomerEmailError('Only the Pricing Test locks prices.')
        vals = locked_prices if isinstance(locked_prices, list) else re.split(r'[\n;]+', str(locked_prices))
        vals = sorted({norm_price(v) for v in vals if str(v).strip()})[:10]
        for v in vals:
            if not PRICE_RE.fullmatch(v) and not prices_in(v):
                raise CustomerEmailError('"%s" is not a price. Use amounts like $49 or 49 USD.' % v)
        if vals:
            missing = sorted(set(prices_in(action.body) + prices_in(action.subject)) - set(vals))
            if missing:
                raise CustomerEmailError('The draft states %s, which is not in the locked set. Lock every price the '
                                         'email states.' % ', '.join(missing), 409)
            meta['locked_prices'], meta['locked_by'] = vals, user.id
            meta['locked_at'] = datetime.utcnow().isoformat()
        else:
            meta.pop('locked_prices', None)
    action.send_meta = meta
    flag_modified(action, 'send_meta')
    founder_ventures.log_event(venture.id, 'customer_draft_settings', action.id, user_id=user.id)
    db.session.commit()
    return meta


# ---------------------------------------------------------------------------
# Preview and send
# ---------------------------------------------------------------------------

def _pick(venture, action, contact_ids) -> list:
    ids = [str(i) for i in (contact_ids or []) if i]
    if not ids:
        raise CustomerEmailError('Pick at least one recipient.')
    if len(ids) != len(set(ids)):
        raise CustomerEmailError('A recipient is listed twice.')
    if len(ids) > MAX_RECIPIENTS:
        raise CustomerEmailError('Send to %d people at most at once.' % MAX_RECIPIENTS)
    by_id = {x['contact'].id: x for x in recipients(venture, action)}
    out = []
    for i in ids:
        x = by_id.get(i)
        if x is None:
            raise CustomerEmailError('A recipient is not one this agent may email. Tick them for this agent in your '
                                     'contact book first.', 409)
        if x['blocked']:
            raise CustomerEmailError('%s cannot receive this: %s.' % (x['contact'].email, x['blocked']), 409)
        out.append(x)
    return out


def check(venture, action, user, contact_ids) -> list:
    """Every rule before a preview or send. Returns the picked recipients."""
    from app.services import founder_compliance
    reason = copy_only_reason(venture, action, user)
    if reason:
        raise CustomerEmailError(reason, 409)
    from app.services import founder_ventures
    if founder_ventures.get_member(venture, getattr(user, 'id', None)) is None:
        raise CustomerEmailError('Only the founders of this venture can send its drafts.', 403)
    if not approver_ok(action, user):
        from app.services import founder_autonomy
        raise CustomerEmailError('%s has the final say on customer contact, so they approve and send this.'
                                 % founder_autonomy.approver_name(action), 403)
    agent_key = action_agent(action)
    r = rule(agent_key)
    picked = _pick(venture, action, contact_ids)
    firsts = [x for x in picked if x['kind'] == 'first']
    if r.get('max_recipients') and len(picked) > int(r['max_recipients']):
        raise CustomerEmailError('%s goes to one named decision-maker at a time.' % agent_label(agent_key), 409)
    if r.get('first_contact_max_recipients') and len(firsts) > int(r['first_contact_max_recipients']):
        raise CustomerEmailError('%s sends a first contact to one prospect at a time, each approved on its own.'
                                 % agent_label(agent_key), 409)
    meta = action.send_meta or {}
    if r.get('needs_scheduling_link') and not meta.get('scheduling_link'):
        raise CustomerEmailError('Add your scheduling link first; interview invites carry one.', 409)
    text = '%s\n%s' % (action.subject or '', action.body or '')
    if r.get('locked_prices'):
        locked = set(meta.get('locked_prices') or [])
        if not locked:
            raise CustomerEmailError('Lock the price versions first. The Pricing Test sends only prices you locked.',
                                     409)
        stray = sorted(set(prices_in(text)) - locked)
        if stray:
            raise CustomerEmailError('The draft states %s, which is not a locked price.' % ', '.join(stray), 409)
    left = leftover_placeholders(_merge(text, picked[0]['contact'], sender_name(user)))
    if left:
        raise CustomerEmailError('Fill in %s in the draft first (Edit draft).' % ', '.join(left), 409)
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    step = db.session.get(VenturePlanStep, action.plan_step_id) if action.plan_step_id else None
    issues = founder_compliance.check(text, getattr(step, 'layer', None))
    if issues:
        raise CustomerEmailError('The draft breaks a SimiSI rule (%s). Edit it first.'
                                 % ', '.join(sorted({i['label'] for i in issues})), 422)
    if not postal_address(venture):
        raise CustomerEmailError('Add the mailing address in Settings -> Notification first (the venture owner\'s); '
                                 'every customer email carries it.', 409)
    cap = int(_cfg('fnd_action_cap_per_day'))
    if sent_today(venture, agent_key) + scheduled_count(venture, agent_key) + len(picked) > cap:
        raise CustomerEmailError('This would pass the limit of %d emails per agent per day (%d sent or scheduled '
                                 'in the last 24 hours).' % (cap, sent_today(venture, agent_key)
                                                              + scheduled_count(venture, agent_key)), 429)
    return picked


def preview(venture, action, user, contact_ids) -> dict:
    from html import escape
    picked = check(venture, action, user, contact_ids)
    from app.services import founder_investors as fi
    name = sender_name(user)
    postal = postal_address(venture)
    out = []
    for x in picked:
        c = x['contact']
        subject, body = render(action, c, name)
        html = (fi._html(body) + fi.on_behalf_html(name)
                + '<p style="font-size:12px;color:#68738C">%s<br>%s · <a href="#">Unsubscribe</a></p>'
                % (escape(name), escape(postal or '')))
        out.append({'contact_id': c.id, 'email': c.email, 'name': c.name, 'kind': x['kind'], 'mode': x['mode'],
                    'mode_label': MODE_LABELS.get(x['mode'], ''), 'subject': subject, 'html': html})
    return {'recipients': out, 'from_name': from_name(user), 'from_email': from_address(user),
            'reply_to': (getattr(user, 'email', '') or '').strip(), 'hash': content_hash(action, contact_ids),
            'needs_each': [o['contact_id'] for o in out if o['mode'] == 'each'],
            'campaign': [o['contact_id'] for o in out if o['mode'] == 'campaign']}


def send(venture, action, user, contact_ids, approved_each=None, approve_campaign=False, confirm_hash='',
         schedule_at=None) -> dict:
    """Approve and send (or schedule) the draft to the picked recipients."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import VentureEmailMessage
    from app.services import founder_autonomy, founder_ventures
    picked = check(venture, action, user, contact_ids)
    if confirm_hash != content_hash(action, contact_ids):
        raise CustomerEmailError('The draft or the recipients changed since the preview. Preview again.', 409)
    each_ok = {str(i) for i in (approved_each or [])}
    for x in picked:
        if x['mode'] == 'each' and x['contact'].id not in each_ok:
            raise CustomerEmailError('Approve the email to %s on its own first.' % x['contact'].email, 409)
        if x['mode'] == 'campaign' and not approve_campaign:
            raise CustomerEmailError('Approve the campaign first.', 409)
        if x['mode'] is None:
            raise CustomerEmailError('This agent is advisor only.', 409)
    when = None
    if schedule_at:
        try:
            when = datetime.fromisoformat(str(schedule_at).replace('Z', '+00:00'))
        except ValueError:
            raise CustomerEmailError('The scheduled time is not a valid date and time.')
        if when.tzinfo is not None:
            from datetime import timezone
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        if when <= datetime.utcnow() + timedelta(minutes=1):
            when = None                     # in the past or now: send now
        elif when > datetime.utcnow() + timedelta(days=60):
            raise CustomerEmailError('Schedule within the next 60 days.')
    agent_key = action_agent(action)
    lvl = level(venture, action, agent_key)
    if action.status in founder_autonomy.PENDING:
        try:
            founder_autonomy.approve(action, user)
        except founder_autonomy.AutonomyError as exc:
            raise CustomerEmailError(str(exc), exc.status)
        action = db.session.get(type(action), action.id)
    name = sender_name(user)
    rows = []
    for x in picked:
        c = x['contact']
        subject, body = render(action, c, name)
        m = VentureEmailMessage(venture_id=venture.id, outreach_action_id=action.id, venture_contact_id=c.id,
                                plan_step_id=action.plan_step_id, agent_key=agent_key, to_email=c.email,
                                subject=subject, body=body, kind=x['kind'], approval_mode=x['mode'],
                                approved_level=lvl, approved_by=user.id, sender_user_id=user.id,
                                status='scheduled', scheduled_for=when or datetime.utcnow())
        db.session.add(m)
        rows.append(m)
    db.session.flush()
    AuditLog.log('founder_customer_send_approved', user_id=user.id, resource_id=venture.id,
                 metadata={'action_id': action.id, 'agent': agent_key, 'level': lvl, 'recipients': len(rows),
                           'each': sum(1 for x in picked if x['mode'] == 'each'),
                           'campaign': sum(1 for x in picked if x['mode'] == 'campaign'),
                           'scheduled_for': when.isoformat() if when else None})
    founder_ventures.log_event(venture.id, 'customer_send_approved', '%s: %d recipient%s%s' % (
        action.id, len(rows), '' if len(rows) == 1 else 's', ' scheduled' if when else ''), user_id=user.id)
    db.session.commit()
    ids = [m.id for m in rows]
    results = {'sent': 0, 'skipped': 0, 'failed': 0, 'scheduled': 0, 'reasons': []}
    if when:
        results['scheduled'] = len(ids)
    else:
        for mid in ids[:ONE_SEND_BATCH]:
            st, why = deliver(mid)
            results[st if st in results else 'failed'] += 1
            if why:
                results['reasons'].append(why)
        results['scheduled'] = max(0, len(ids) - ONE_SEND_BATCH)
    results['reasons'] = sorted(set(results['reasons']))[:5]
    return results


SKIP_LABELS = {'no_postal_address': 'no mailing address in Settings',
               'do_not_contact': 'marked do not contact',
               'deceptive_subject': 'the subject looks like a reply or forward',
               'no_api_key': 'sending is not configured on this server',
               'placeholder_contact': 'the contact looks like a template row'}


def deliver(message_id: str) -> tuple:
    """Send one scheduled venture_email_message. Re-checks everything that can
    change after approval. Returns (status, reason)."""
    from app.extensions import db
    from app.models.founder import FounderVenture, VentureContact, VentureEmailMessage, VentureOutreachAction
    from app.models.user import User
    m = db.session.get(VentureEmailMessage, message_id)
    if m is None or m.status != 'scheduled':
        return 'skipped', None
    venture = db.session.get(FounderVenture, m.venture_id)
    action = db.session.get(VentureOutreachAction, m.outreach_action_id)
    contact = db.session.get(VentureContact, m.venture_contact_id)
    user = db.session.get(User, m.sender_user_id) if m.sender_user_id else None

    def stop(reason, status='skipped'):
        m.status, m.reason = status, reason[:300]
        db.session.commit()
        return status, reason

    if venture is None or action is None or contact is None or user is None:
        return stop('the venture, draft, contact or sender is gone')
    why = copy_only_reason(venture, action, user)
    if why:
        return stop(why)
    if contact.state not in SENDABLE_STATES or suppressed(venture, contact.email):
        return stop('%s asked not to be emailed or bounced' % contact.email)
    from app.services import founder_ventures
    if founder_ventures.get_member(venture, user.id) is None:
        return stop('the sender is no longer on the team')
    from app.services import founder_investors as fi
    from app.services import send_pipeline
    try:
        sender = send_pipeline.sender_for(user)
    except send_pipeline.SendRefused as exc:
        return stop(str(exc), 'failed')
    crm = fi.contact_for(venture.owner_user_id, contact.email, contact.name or '', contact.company or '')
    m.status = 'sending'
    db.session.commit()
    name = sender_name(user)
    html = fi._html(m.body or '') + fi.on_behalf_html(name)
    from app.services.outreach_email_service import send_outreach_email
    res = send_outreach_email(simulation_id=venture.id, contact_id=crm.id, subject=m.subject or venture.name,
                              html_body=html, from_email=from_address(user), from_name=from_name(user),
                              action_id=action.id, api_key=sender.get('api_key'), unsubscribe=True,
                              reply_to=(user.email or '').strip() or None,
                              unsubscribe_url=fi.unsubscribe_url(venture, contact.email))
    m = db.session.get(VentureEmailMessage, message_id)
    contact = db.session.get(VentureContact, m.venture_contact_id)
    status = res.get('status')
    if status == 'deferred':
        m.status, m.reason = 'scheduled', 'deferred: %s' % res.get('reason')
        m.scheduled_for = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        return 'scheduled', 'deferred by a send limit; retried in an hour'
    if status != 'sent':
        reason = str(res.get('reason') or status or 'not sent')
        label = SKIP_LABELS.get(reason) or ('they are on an opt-out list' if reason.startswith('suppressed')
                                            else reason.replace('_', ' '))
        return stop(label, 'failed' if status == 'error' else 'skipped')
    now = datetime.utcnow()
    m.status, m.sent_at, m.email_log_id, m.reason = 'sent', now, res.get('email_log_id'), None
    if contact.state == 'approved':
        contact.state = 'contacted'
    contact.contacted_at = now
    r = rule(m.agent_key)
    if r.get('marks_poc_customer'):
        contact.poc_customer = True
    action = db.session.get(VentureOutreachAction, m.outreach_action_id)
    if action.status != 'sent':
        action.status, action.sent_at = 'sent', now
    action.level_applied = m.approved_level
    from app.models.audit_log import AuditLog
    AuditLog.log('founder_customer_email_sent', user_id=user.id, resource_id=venture.id,
                 metadata={'action_id': action.id, 'message_id': m.id, 'agent': m.agent_key,
                           'level': m.approved_level, 'mode': m.approval_mode, 'approver': m.approved_by,
                           'email_log_id': m.email_log_id})
    try:
        from app.services import founder_stream
        founder_stream.publish(venture.id, 'outreach_sent', {
            'plan_step_id': m.plan_step_id, 'action_id': action.id, 'actor_user_id': user.id,
            'actor_kind': 'founder', 'note': 'Emailed %s on your behalf: %s' % (
                contact.name or contact.email, (m.subject or '')[:150])}, commit=False)
    except Exception as exc:
        logger.debug('founder stream publish skipped: %s', exc)
    db.session.commit()
    try:
        from app.services import founder_alerts
        founder_alerts.check_action(action)
    except Exception as exc:
        logger.error('founder alert check failed for %s: %s', action.id, exc)
    return 'sent', None


def cancel(venture, user, message_id):
    from app.extensions import db
    from app.models.founder import VentureEmailMessage
    _member(venture, user)
    m = VentureEmailMessage.query.filter_by(id=message_id, venture_id=venture.id).first()
    if m is None:
        raise CustomerEmailError('Not found.', 404)
    if m.status != 'scheduled':
        raise CustomerEmailError('Only a scheduled email can be cancelled.', 409)
    m.status, m.reason = 'cancelled', 'cancelled by %s' % sender_name(user)
    db.session.commit()
    return m


def process_scheduled(now=None, limit: int = 200) -> dict:
    """Send every scheduled message that is due (cron_customer_email.py)."""
    from app.extensions import db
    from app.models.founder import VentureEmailMessage
    now = now or datetime.utcnow()
    ids = [i for (i,) in db.session.query(VentureEmailMessage.id)
           .filter(VentureEmailMessage.status == 'scheduled', VentureEmailMessage.scheduled_for <= now)
           .order_by(VentureEmailMessage.scheduled_for).limit(limit).all()]
    db.session.commit()
    out = {'due': len(ids), 'sent': 0, 'skipped': 0, 'failed': 0, 'scheduled': 0}
    if ids and not enabled():
        return dict(out, note='customer email is off; due messages wait')
    for mid in ids:
        try:
            st, _why = deliver(mid)
        except Exception as exc:
            logger.exception('customer email %s failed: %s', mid, exc)
            db.session.rollback()
            st = 'failed'
            m = db.session.get(VentureEmailMessage, mid)
            if m is not None and m.status in ('scheduled', 'sending'):
                m.status, m.reason = 'failed', str(exc)[:300]
                db.session.commit()
        out[st if st in out else 'failed'] += 1
    # A message left 'sending' by a process that died: the send may or may not
    # have gone out, so it is marked failed (never re-sent automatically).
    stale = now - timedelta(minutes=30)
    for m in VentureEmailMessage.query.filter(VentureEmailMessage.status == 'sending',
                                              VentureEmailMessage.created_at < stale).limit(100).all():
        m.status, m.reason = 'failed', 'the sending process stopped; check before sending again'
    db.session.commit()
    return out


# ---------------------------------------------------------------------------
# Replies, delivery events, opt-outs
# ---------------------------------------------------------------------------

def mark_replied(venture, user, contact_id, when=None, via='founder'):
    from app.extensions import db
    from app.models.founder import VentureEmailMessage
    from app.services import founder_ventures
    if user is not None:
        _member(venture, user)
    c = _get_contact(venture, contact_id)
    if c.state in ('opted_out', 'bounced'):
        raise CustomerEmailError('This contact opted out or bounced.', 409)
    when = when or datetime.utcnow()
    c.state, c.replied_at = 'replied', when
    last = (VentureEmailMessage.query.filter_by(venture_id=venture.id, venture_contact_id=c.id, status='sent')
            .order_by(VentureEmailMessage.sent_at.desc()).first())
    if last is not None and last.replied_at is None:
        last.replied_at = when
    founder_ventures.log_event(venture.id, 'customer_replied', '%s via %s' % (c.id, via),
                               user_id=getattr(user, 'id', None))
    try:
        from app.services import founder_stream
        founder_stream.publish(venture.id, 'update', {
            'plan_step_id': last.plan_step_id if last is not None else None,
            'actor_user_id': getattr(user, 'id', None), 'actor_kind': 'founder' if user is not None else 'system',
            'note': '%s replied%s' % (c.name or c.email, ' to %s' % agent_label(last.agent_key) if last else '')},
            commit=False)
    except Exception as exc:
        logger.debug('founder stream publish skipped: %s', exc)
    db.session.commit()
    return c


def record_inbound(user_id: str, sender_email: str, received_at=None) -> int:
    """A message from sender_email reached a founder's connected mailbox
    (mail_sync). Every venture of theirs that emailed that person before it
    arrived marks them Replied. Returns how many contacts were marked."""
    from app.models.founder import VentureContact, VentureEmailMessage, VentureMember, FounderVenture
    from app.extensions import db
    e = _norm(sender_email)
    if not e or not user_id:
        return 0
    try:
        vids = [v for (v,) in db.session.query(VentureMember.venture_id)
                .filter_by(user_id=user_id, status='active').all()]
        if not vids:
            return 0
        rows = VentureContact.query.filter(VentureContact.venture_id.in_(vids), VentureContact.email == e,
                                           VentureContact.state == 'contacted').all()
    except Exception as exc:
        db.session.rollback()
        logger.debug('reply lookup skipped: %s', exc)
        return 0
    n = 0
    for c in rows:
        first = (VentureEmailMessage.query.filter_by(venture_contact_id=c.id, status='sent')
                 .order_by(VentureEmailMessage.sent_at).first())
        if first is None or received_at is None or received_at <= first.sent_at:
            continue
        v = db.session.get(FounderVenture, c.venture_id)
        try:
            mark_replied(v, None, c.id, when=received_at or datetime.utcnow(), via='mailbox')
            n += 1
        except CustomerEmailError:
            continue
    return n


def on_email_event(log, event_type: str, event: dict = None) -> bool:
    """SendGrid event for an EmailLog row. Only rows behind a venture email
    are touched. Doesn't commit (the webhook does)."""
    from app.extensions import db
    from app.models.founder import FounderVenture, VentureEmailMessage
    if log is None or not getattr(log, 'id', None):
        return False
    m = VentureEmailMessage.query.filter_by(email_log_id=log.id).first()
    if m is None:
        return False
    venture = db.session.get(FounderVenture, m.venture_id)
    if venture is None:
        return False
    if event_type in ('bounce', 'dropped'):
        m.status = 'bounced'
        suppress(venture, m.to_email, 'bounce', ((event or {}).get('reason') or event_type)[:300], commit=False)
    elif event_type == 'spamreport':
        m.status = 'complained'
        suppress(venture, m.to_email, 'complaint', 'spam report', commit=False)
    else:
        return True
    db.session.flush()
    review_venture(venture, commit=False)
    return True


def on_unsubscribe(venture, email) -> None:
    """The founder unsubscribe link (founder_investors.apply_unsubscribe):
    the venture's do-not-contact list too. Doesn't commit."""
    if venture is None:
        return
    try:
        suppress(venture, email, 'opt_out', 'unsubscribe link', commit=False)
    except Exception as exc:
        logger.error('venture suppression on unsubscribe failed: %s', exc)


def rates(venture, now=None) -> dict:
    from app.models.founder import VentureEmailMessage
    now = now or datetime.utcnow()
    since = now - timedelta(days=int(_cfg('fnd_customer_rate_window_days')))
    q = VentureEmailMessage.query.filter(VentureEmailMessage.venture_id == venture.id,
                                         VentureEmailMessage.sent_at >= since)
    sent = q.filter(VentureEmailMessage.status.in_(('sent', 'bounced', 'complained'))).count()
    bounced = q.filter(VentureEmailMessage.status == 'bounced').count()
    complained = q.filter(VentureEmailMessage.status == 'complained').count()
    pct = (lambda n: round(100.0 * n / sent, 2) if sent else 0.0)
    return {'sent': sent, 'bounced': bounced, 'complained': complained, 'bounce_pct': pct(bounced),
            'complaint_pct': pct(complained)}


def review_venture(venture, commit: bool = True) -> bool:
    """Pause the venture's customer email once its complaint or bounce rate
    passes the admin threshold. True when it paused now."""
    from app.extensions import db
    if venture.customer_paused_at:
        return False
    r = rates(venture)
    if r['sent'] < int(_cfg('fnd_customer_rate_min_sends')):
        return False
    why = None
    if r['complaint_pct'] > float(_cfg('fnd_alert_customer_complaint_pct')):
        why = 'complaint rate %.2f%% passed %.2f%%' % (r['complaint_pct'], float(_cfg('fnd_alert_customer_complaint_pct')))
    elif r['bounce_pct'] > float(_cfg('fnd_alert_customer_bounce_pct')):
        why = 'bounce rate %.2f%% passed %.2f%%' % (r['bounce_pct'], float(_cfg('fnd_alert_customer_bounce_pct')))
    if why is None:
        return False
    venture.customer_paused_at = datetime.utcnow()
    venture.customer_paused_reason = ('Paused automatically: %s over %d emails' % (why, r['sent']))[:300]
    from app.models.founder import VentureEmailMessage
    for m in VentureEmailMessage.query.filter_by(venture_id=venture.id, status='scheduled').all():
        m.status, m.reason = 'cancelled', 'customer email paused'
    from app.services import founder_ventures
    founder_ventures.log_event(venture.id, 'customer_email_paused', venture.customer_paused_reason)
    try:
        from app.services import founder_alerts
        founder_alerts._flag('customer_email_paused', 'venture', venture.id, venture.customer_paused_reason,
                             severity='critical', user_id=venture.owner_user_id)
    except Exception as exc:
        logger.error('customer pause flag failed for %s: %s', venture.id, exc)
    if commit:
        db.session.commit()
    return True


def set_paused(venture, paused: bool, admin_id: str, reason: str = ''):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    if paused:
        venture.customer_paused_at = datetime.utcnow()
        venture.customer_paused_reason = (reason or 'Paused by SimiSI')[:300]
    else:
        venture.customer_paused_at, venture.customer_paused_reason = None, None
    AuditLog.log('founder_customer_email_' + ('paused' if paused else 'resumed'), user_id=admin_id,
                 resource_id=venture.id, metadata={'reason': (reason or '')[:300]})
    db.session.commit()
    return venture


# ---------------------------------------------------------------------------
# Page data and metrics
# ---------------------------------------------------------------------------

def book_view(venture, user) -> dict:
    """Everything the contact book page shows."""
    from app.models.founder import VentureEmailMessage, VentureOutreachAction, VentureSuppression
    from app.services import founder_autonomy
    agents = customer_agents()
    access = access_map(venture)
    rows = []
    for c in contacts(venture):
        d = c.to_dict()
        d['state_label'] = STATE_LABELS.get(c.state, c.state)
        d['access'] = sorted(access.get(c.id, set()))
        d['agent_label'] = agent_label(c.proposed_by_agent) if c.proposed_by_agent else None
        rows.append(d)
    outbox = []
    for m in (VentureEmailMessage.query.filter_by(venture_id=venture.id)
              .order_by(VentureEmailMessage.created_at.desc()).limit(100).all()):
        d = m.to_dict()
        d['agent_label'] = agent_label(m.agent_key)
        outbox.append(d)
    ready = []
    if enabled():
        for a in (VentureOutreachAction.query.filter(VentureOutreachAction.venture_id == venture.id,
                                                     VentureOutreachAction.action_type == 'customer_message',
                                                     VentureOutreachAction.status.in_(
                                                         founder_autonomy.PENDING + ('approved', 'sent')))
                  .order_by(VentureOutreachAction.created_at.desc()).limit(50).all()):
            key = action_agent(a)
            if key:
                ready.append({'id': a.id, 'subject': a.subject, 'agent_label': agent_label(key),
                              'status': a.status})
    dnc = [{'email': s.email, 'reason': s.reason, 'created_at': s.created_at.isoformat() if s.created_at else None}
           for s in VentureSuppression.query.filter_by(venture_id=venture.id)
           .order_by(VentureSuppression.created_at.desc()).limit(200).all()]
    counts = {st: sum(1 for r in rows if r['state'] == st) for st in STATE_LABELS}
    return {'contacts': rows, 'agents': agents, 'outbox': outbox, 'drafts': ready, 'dnc': dnc, 'counts': counts,
            'enabled': enabled(), 'paused': venture.customer_paused_at is not None,
            'paused_reason': venture.customer_paused_reason, 'csv_max': int(_cfg('fnd_csv_max_rows')),
            'rules': {k: dict(rule(k), label=lbl) for k, lbl in agents}, 'state_labels': STATE_LABELS,
            'postal_set': bool(postal_address(venture))}


def send_view(venture, action, user) -> dict:
    agent_key = action_agent(action)
    r = rule(agent_key) if agent_key else {}
    lvl = level(venture, action, agent_key) if agent_key else 1
    recs = []
    for x in recipients(venture, action):
        c = x['contact']
        recs.append({'id': c.id, 'email': c.email, 'name': c.name, 'company': c.company, 'state': c.state,
                     'kind': x['kind'], 'mode': x['mode'], 'mode_label': MODE_LABELS.get(x['mode'], 'Copy only'),
                     'blocked': x['blocked'], 'poc': bool(c.poc_customer)})
    from app.services import founder_autonomy
    return {'agent_key': agent_key, 'agent_label': agent_label(agent_key), 'rule': r, 'level': lvl,
            'level_label': founder_autonomy.LEVEL_LABELS.get(lvl, ''), 'recipients': recs,
            'reason': copy_only_reason(venture, action, user), 'approver_ok': approver_ok(action, user),
            'approver': founder_autonomy.approver_name(action), 'meta': action.send_meta or {},
            'from_name': from_name(user), 'reply_to': getattr(user, 'email', ''),
            'postal_set': bool(postal_address(venture)),
            'cap': int(_cfg('fnd_action_cap_per_day')),
            'used': sent_today(venture, agent_key) + scheduled_count(venture, agent_key) if agent_key else 0,
            'placeholders': leftover_placeholders(_merge('%s\n%s' % (action.subject or '', action.body or ''),
                                                         _SAMPLE, sender_name(user)))}


def metrics(hours: int = 24 * 30) -> dict:
    """Customer email per venture and per agent for /admin/observability."""
    from app.extensions import db
    from app.models.founder import FounderVenture, VentureEmailMessage, VentureSuppression
    from sqlalchemy import func
    since = datetime.utcnow() - timedelta(hours=int(hours))
    try:
        rows = (db.session.query(VentureEmailMessage.venture_id, VentureEmailMessage.agent_key,
                                 VentureEmailMessage.status, func.count(VentureEmailMessage.id),
                                 func.sum(db.case((VentureEmailMessage.replied_at.isnot(None), 1), else_=0)))
                .filter(VentureEmailMessage.created_at >= since)
                .group_by(VentureEmailMessage.venture_id, VentureEmailMessage.agent_key,
                          VentureEmailMessage.status).all())
        optouts = dict(db.session.query(VentureSuppression.venture_id, func.count(VentureSuppression.id))
                       .filter(VentureSuppression.reason == 'opt_out', VentureSuppression.created_at >= since)
                       .group_by(VentureSuppression.venture_id).all())
    except Exception as exc:
        db.session.rollback()
        logger.debug('customer email metrics unavailable: %s', exc)
        return {'available': False, 'ventures': [], 'agents': [], 'totals': {}}
    SENT = ('sent', 'bounced', 'complained')
    by_v, by_a = {}, {}
    for vid, agent, status, n, replied in rows:
        n, replied = int(n), int(replied or 0)
        for bucket, key in ((by_v, vid), (by_a, agent or '?')):
            b = bucket.setdefault(key, {'sent': 0, 'bounced': 0, 'complained': 0, 'skipped': 0, 'failed': 0,
                                        'scheduled': 0, 'replied': 0})
            if status in SENT:
                b['sent'] += n
            if status in b and status != 'sent':
                b[status] += n
            b['replied'] += replied
    names = {v.id: (v.name, v.customer_paused_at is not None) for v in
             FounderVenture.query.filter(FounderVenture.id.in_(list(by_v) or ['-'])).all()}

    def pct(a, b):
        return round(100.0 * a / b, 2) if b else None
    ventures = []
    for vid, b in by_v.items():
        oo = int(optouts.get(vid, 0))
        ventures.append(dict(b, venture_id=vid, name=names.get(vid, ('?', False))[0], paused=names.get(vid, ('', False))[1],
                             opted_out=oo, complaint_pct=pct(b['complained'], b['sent']),
                             bounce_pct=pct(b['bounced'], b['sent']), optout_pct=pct(oo, b['sent']),
                             reply_pct=pct(b['replied'], b['sent'])))
    agents = [dict(b, agent=k, label=agent_label(k), reply_pct=pct(b['replied'], b['sent']))
              for k, b in by_a.items()]
    tot = {k: sum(v[k] for v in ventures) for k in ('sent', 'bounced', 'complained', 'replied', 'opted_out')}
    tot.update(complaint_pct=pct(tot['complained'], tot['sent']), optout_pct=pct(tot['opted_out'], tot['sent']),
               reply_pct=pct(tot['replied'], tot['sent']))
    return {'available': True, 'ventures': sorted(ventures, key=lambda v: -(v['complained'] + v['bounced'])),
            'agents': sorted(agents, key=lambda a: -a['sent']), 'totals': tot}
