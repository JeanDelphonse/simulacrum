"""
Event triggers for agents that declare `triggered_by` in config/agents.json.

Triggered agents (proposal, SOW, agreement, negotiation script, speaker rider)
used to be dispatched on the normal schedule as soon as their prerequisites were
done, so a "consulting proposal" was written for a client who did not exist yet.
The orchestrator now dispatches them only once one of their trigger events has
been observed for the simulation. Users can still run any of them manually.

Each detector maps a trigger name to evidence the platform already records
(email replies, Cal.com bookings, PandaDoc signatures) and returns context the
agent can use, e.g. the client who replied. Triggers with no detector
(price_objection_detected, speaking_engagement_confirmed) never fire on their
own — those agents stay manual-only until a detector exists.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _reply_hit(simulation_id: str) -> dict | None:
    from app.models.outreach_email import EmailLog
    from app.models.contact import Contact
    log = (EmailLog.query
           .filter(EmailLog.simulation_id == simulation_id, EmailLog.replied_at.isnot(None))
           .order_by(EmailLog.replied_at.desc())
           .first())
    if not log:
        from app.models.integration_signal import IntegrationSignal
        sig = IntegrationSignal.query.filter_by(
            simulation_id=simulation_id, signal_type=IntegrationSignal.SIGNAL_EMAIL_REPLY,
        ).order_by(IntegrationSignal.created_at.desc()).first()
        return {} if sig else None
    ctx = {}
    contact = Contact.query.get(log.contact_id) if log.contact_id else None
    if contact is not None:
        if contact.company_name:
            ctx['client_name'] = contact.company_name
        name = ('%s %s' % (contact.first_name or '', contact.last_name or '')).strip()
        if name:
            ctx['client_contact'] = name
    return ctx


def _booking_hit(simulation_id: str) -> dict | None:
    from app.models.integration_signal import IntegrationSignal
    sig = IntegrationSignal.query.filter_by(
        simulation_id=simulation_id, signal_type=IntegrationSignal.SIGNAL_BOOKING_CREATED,
    ).order_by(IntegrationSignal.created_at.desc()).first()
    if not sig:
        return None
    ctx = {}
    try:
        payload = json.loads(sig.payload) if sig.payload else {}
        attendee = payload.get('attendee_name') or payload.get('name')
        if attendee:
            ctx['client_contact'] = attendee
    except Exception:
        pass
    return ctx


def _document_hit(simulation_id: str, action_types: tuple, signed: bool) -> dict | None:
    from app.models.signing import SigningDocument
    from app.services.agent_registry import names_for
    names = [n for at in action_types for n in names_for(at)]
    q = SigningDocument.query.filter(SigningDocument.simulation_id == simulation_id,
                                     SigningDocument.action_type.in_(names))
    if signed:
        q = q.filter(SigningDocument.signed_at.isnot(None))
    doc = q.order_by(SigningDocument.created_at.desc()).first()
    if not doc:
        return None
    ctx = {}
    if doc.recipient_name:
        ctx['client_contact'] = doc.recipient_name
    if doc.document_title:
        ctx['source_document'] = doc.document_title
    return ctx


DETECTORS = {
    'email_replied_with_interest': _reply_hit,
    'booking_completed':           _booking_hit,
    'proposal_sent':     lambda sim: _document_hit(sim, ('consulting_proposal',), signed=False),
    'proposal_signed':   lambda sim: _document_hit(sim, ('consulting_proposal',), signed=True),
    'sow_signed':        lambda sim: _document_hit(sim, ('sow_template',), signed=True),
}


def triggers_for(action_type: str) -> list[str]:
    from app.services.agent_registry import get_agent_meta
    meta = get_agent_meta(action_type) or {}
    return list(meta.get('triggered_by') or [])


def check_trigger(simulation_id: str, action_type: str) -> tuple[bool, str | None, dict]:
    """(fired, event_name, context). Agents with no triggers always 'fire'."""
    events = triggers_for(action_type)
    if not events:
        return True, None, {}
    for event in events:
        detector = DETECTORS.get(event)
        if detector is None:
            continue
        try:
            ctx = detector(simulation_id)
        except Exception as exc:
            logger.warning('trigger %s check failed for %s: %s', event, simulation_id, exc)
            continue
        if ctx is not None:
            return True, event, ctx
    return False, None, {}
