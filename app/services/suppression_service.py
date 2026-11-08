"""One suppression check for every send and every import (COMPLIANCE_PLAN C2).

A recipient is suppressed for a member when any of these holds:
  * the address is on the global list (email_suppressions: unsubscribe, bounce,
    complaint, reply-STOP), which every member's sends respect;
  * the address is on the member's own list (suppression_list, hashed, so an
    opt-out outlives a deleted contact);
  * the member's contact for it is Do Not Contact (contacts.do_not_contact,
    which the Email-to-CRM switch and the member's own choice set);
  * an unlifted Email-to-CRM Do-Not-Contact flag is on that contact.

Every commercial send path calls check(); contacts created by any path (agents,
imports, chats) pass through a before-insert hook that marks them Do Not
Contact when the address is suppressed, so no agent or import can bypass it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

REASON_LABELS = {
    'global': 'Unsubscribed, bounced or complained',
    'member': 'On your opt-out list',
    'do_not_contact': 'Do not contact',
    'dnc_flag': 'Do not contact (email import switched off)',
}


def _norm(email) -> str:
    return (email or '').strip().lower()


def check(user_id: str | None, email: str, contact=None) -> str | None:
    """The first reason this member may not email this address, or None."""
    from app.models.outreach_email import EmailSuppression
    from app.models.mail_crm import UserSuppression, ContactDncFlag, email_hash
    from app.models.contact import Contact
    from sqlalchemy import func
    e = _norm(email)
    if not e:
        return 'do_not_contact'
    if EmailSuppression.is_suppressed(e):
        return 'global'
    if user_id and UserSuppression.query.filter_by(user_id=user_id, email_hash=email_hash(e)).first():
        return 'member'
    contacts = [contact] if contact is not None else []
    if user_id:
        contacts += Contact.query.filter(Contact.user_id == user_id, func.lower(Contact.email) == e).all()
    if any(c is not None and c.do_not_contact for c in contacts):
        return 'do_not_contact'
    ids = [c.id for c in contacts if c is not None and c.id]
    if ids and ContactDncFlag.query.filter(ContactDncFlag.contact_id.in_(ids),
                                           ContactDncFlag.lifted_at.is_(None)).first():
        return 'dnc_flag'
    return None


def bulk(user_id: str, emails) -> dict:
    """{email: reason} for the suppressed ones among `emails` (picker lists)."""
    from app.models.outreach_email import EmailSuppression
    from app.models.mail_crm import UserSuppression, email_hash
    emails = sorted({_norm(e) for e in emails if _norm(e)})
    out = {}
    for i in range(0, len(emails), 500):
        chunk = emails[i:i + 500]
        for r in EmailSuppression.query.filter(EmailSuppression.email.in_(chunk)).all():
            out[_norm(r.email)] = 'global'
        if user_id:
            hashes = {email_hash(e): e for e in chunk}
            for r in UserSuppression.query.filter(UserSuppression.user_id == user_id,
                                                  UserSuppression.email_hash.in_(list(hashes))).all():
                out.setdefault(hashes[r.email_hash], 'member')
    return out


def install(app) -> None:
    """Mark new contacts Do Not Contact when their address is suppressed."""
    from sqlalchemy import event, select, func
    from app.models.contact import Contact
    from app.models.outreach_email import EmailSuppression
    from app.models.mail_crm import UserSuppression, email_hash

    @event.listens_for(Contact, 'before_insert')
    def _suppressed_on_insert(mapper, connection, target):   # noqa: ARG001
        e = _norm(target.email)
        if not e or target.do_not_contact:
            return
        try:
            hit = connection.execute(select(EmailSuppression.__table__.c.id).where(
                func.lower(EmailSuppression.__table__.c.email) == e).limit(1)).first()
            if not hit and target.user_id:
                t = UserSuppression.__table__
                hit = connection.execute(select(t.c.id).where(t.c.user_id == target.user_id,
                                                              t.c.email_hash == email_hash(e)).limit(1)).first()
        except Exception:
            logger.warning('suppression check on contact insert failed', exc_info=True)
            return
        if hit:
            target.do_not_contact = True


# ── Opting out ───────────────────────────────────────────────────────────────

_VIA_LABELS = {'unsubscribe_link': 'Unsubscribed (email link)', 'reply_stop': 'Replied STOP',
               'spam_report': 'Marked an email as spam'}


def opt_out(email: str, via: str, reason: str = 'unsubscribe', detail: str = '', commit: bool = True) -> int:
    """Someone asked not to be emailed again (unsubscribe link, reply STOP, spam
    report). Puts the address on the global list and marks every contact with
    that address Do Not Contact, for every member: the flag the CRM shows, the
    flag row Email-to-CRM uses, follow-ups cancelled and an audit row. Returns
    the number of contacts newly marked. Safe to call twice.
    """
    from app.extensions import db
    from app.models.action_step import ActionStep
    from app.models.audit_log import AuditLog
    from app.models.contact import Contact, ContactActivity
    from app.models.mail_crm import ContactDncFlag
    from app.models.outreach_email import EmailSuppression
    from utils.id_gen import generate_id
    e = _norm(email)
    if not e:
        return 0
    if not EmailSuppression.query.filter_by(email=e).first():
        db.session.add(EmailSuppression(id=generate_id(), email=e, reason=reason, detail=(detail or '')[:500]))
    marked = 0
    label = _VIA_LABELS.get(via, 'Opted out')
    for c in Contact.query.filter(db.func.lower(Contact.email) == e).all():
        if not ContactDncFlag.query.filter_by(contact_id=c.id, reason=reason).first():
            db.session.add(ContactDncFlag(contact_id=c.id, reason=reason, prior_dnc=bool(c.do_not_contact)))
        ActionStep.query.filter(ActionStep.condition_ref == c.id,
                                ActionStep.status.in_([ActionStep.STATUS_SCHEDULED, ActionStep.STATUS_SUSPENDED])).update(
            {'status': ActionStep.STATUS_CANCELLED, 'skip_reason': 'Recipient opted out: %s' % label.lower()},
            synchronize_session=False)
        if c.do_not_contact:
            continue
        c.do_not_contact = True
        marked += 1
        db.session.add(ContactActivity(contact_id=c.id, activity_type='opted_out', notes=label))
        AuditLog.log('outreach_opt_out', user_id=c.user_id, resource_id=c.id, metadata={'via': via})
    if commit:
        db.session.commit()
    return marked
