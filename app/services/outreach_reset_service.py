"""Admin reset of a user's outreach history — contacts back to "never emailed".

What a reset does, for every contact of the user that may still be emailed:
  * contacts            stage -> prospect, last_contacted_at -> NULL,
                        outreach_count -> 0
  * email_logs          action_id -> NULL. The record of what was sent stays (it
                        is the evidence if a recipient asks why they were
                        contacted); it just stops counting as "this agent
                        already emailed them" in the recipient ledger.
  * agent_recipients    the user's simulations' claims are deleted
  * action_steps        still-scheduled follow-ups in the user's simulations
                        are cancelled, so an old campaign's Step 2/3 cannot go
                        out after the reset
  * contact_activities  optional: outreach entries removed from the timeline

Never touched: contacts marked do_not_contact, archived contacts, and anyone
on the email suppression list. Those people asked not to be emailed (or were
removed deliberately); a reset must not put them back in front of an agent.

After a reset the next Cold Email / Consulting Outreach cycle can email these
contacts again, including people who already received a message. The admin
console shows a dry run first and requires an explicit confirmation.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

OUTREACH_ACTIVITY_TYPES = ('outreach_sent', 'email_sent', 'email_replied')


def _chunks(items, size=500):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _scope(user_id: str) -> dict:
    """Which rows a reset would touch. Read-only."""
    from app.models.action_step import ActionStep
    from app.models.agent_recipient import AgentRecipient
    from app.models.contact import Contact, ContactActivity
    from app.models.outreach_email import EmailLog, EmailSuppression
    from app.models.simulation import Simulation

    contacts = Contact.query.filter(Contact.user_id == user_id).all()
    suppressed = set()
    emails = [c.email.lower() for c in contacts if c.email]
    for batch in _chunks(emails):
        suppressed |= {r.email.lower() for r in EmailSuppression.query.filter(
            EmailSuppression.email.in_(batch)).all() if r.email}

    protected, resettable = [], []
    for c in contacts:
        if c.is_archived or c.do_not_contact or (c.email and c.email.lower() in suppressed):
            protected.append(c)
        else:
            resettable.append(c)
    ids = [c.id for c in resettable]
    sim_ids = [s.id for s in Simulation.query.with_entities(Simulation.id)
               .filter(Simulation.user_id == user_id).all()]

    log_ids, activity_ids = [], []
    for batch in _chunks(ids):
        log_ids += [r.id for r in EmailLog.query.with_entities(EmailLog.id).filter(
            EmailLog.contact_id.in_(batch), EmailLog.action_id.isnot(None)).all()]
        activity_ids += [r.id for r in ContactActivity.query.with_entities(ContactActivity.id).filter(
            ContactActivity.contact_id.in_(batch),
            ContactActivity.activity_type.in_(OUTREACH_ACTIVITY_TYPES)).all()]
    claim_ids, step_ids = [], []
    for batch in _chunks(sim_ids):
        claim_ids += [r.id for r in AgentRecipient.query.with_entities(AgentRecipient.id).filter(
            AgentRecipient.simulation_id.in_(batch)).all()]
        step_ids += [r.id for r in ActionStep.query.with_entities(ActionStep.id).filter(
            ActionStep.simulation_id.in_(batch),
            ActionStep.status == ActionStep.STATUS_SCHEDULED).all()]

    changed = [c for c in resettable
               if c.pipeline_stage != 'prospect' or c.last_contacted_at is not None
               or (c.outreach_count or 0) > 0]
    return {
        'resettable': resettable, 'changed': changed, 'protected': protected,
        'log_ids': log_ids, 'claim_ids': claim_ids, 'step_ids': step_ids,
        'activity_ids': activity_ids,
    }


def preview(user_id: str) -> dict:
    """Dry run: what a reset would change, for the admin to confirm."""
    sc = _scope(user_id)
    return {
        'contacts_reset': len(sc['changed']),
        'contacts_total': len(sc['resettable']),
        'contacts_protected': len(sc['protected']),
        'emails_detached': len(sc['log_ids']),
        'claims_cleared': len(sc['claim_ids']),
        'steps_cancelled': len(sc['step_ids']),
        'activities_removable': len(sc['activity_ids']),
        'sample': [{'name': ('%s %s' % (c.first_name or '', c.last_name or '')).strip(),
                    'email': c.email, 'stage': c.pipeline_stage,
                    'outreach_count': c.outreach_count or 0}
                   for c in sc['changed'][:10]],
    }


def reset(user_id: str, admin_user_id: str = None, clear_activities: bool = False) -> dict:
    """Run the reset in one transaction. Returns the counts actually changed."""
    from app.extensions import db
    from app.models.action_step import ActionStep
    from app.models.agent_recipient import AgentRecipient
    from app.models.audit_log import AuditLog
    from app.models.contact import Contact, ContactActivity
    from app.models.outreach_email import EmailLog

    sc = _scope(user_id)
    try:
        for c in sc['changed']:
            c.pipeline_stage = 'prospect'
            c.last_contacted_at = None
            c.outreach_count = 0
        for batch in _chunks(sc['log_ids']):
            EmailLog.query.filter(EmailLog.id.in_(batch)).update(
                {'action_id': None}, synchronize_session=False)
        for batch in _chunks(sc['claim_ids']):
            AgentRecipient.query.filter(AgentRecipient.id.in_(batch)).delete(
                synchronize_session=False)
        for batch in _chunks(sc['step_ids']):
            ActionStep.query.filter(ActionStep.id.in_(batch)).update(
                {'status': ActionStep.STATUS_CANCELLED,
                 'skip_reason': 'Cancelled by admin outreach reset'},
                synchronize_session=False)
        removed = 0
        if clear_activities:
            for batch in _chunks(sc['activity_ids']):
                removed += ContactActivity.query.filter(ContactActivity.id.in_(batch)).delete(
                    synchronize_session=False)
        result = {
            'contacts_reset': len(sc['changed']),
            'contacts_protected': len(sc['protected']),
            'emails_detached': len(sc['log_ids']),
            'claims_cleared': len(sc['claim_ids']),
            'steps_cancelled': len(sc['step_ids']),
            'activities_removed': removed,
        }
        AuditLog.log('admin_outreach_reset', user_id=admin_user_id, resource_id=user_id,
                     metadata=result)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Outreach reset failed for user %s: %s', user_id, exc, exc_info=True)
        raise
    logger.info('Admin %s reset outreach for user %s: %s', admin_user_id, user_id, result)
    return result
