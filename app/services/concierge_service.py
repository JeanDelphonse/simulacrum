"""Founder Track concierge onboarding (FOUNDER_CONCIERGE_PLAN.md section 3).

An admin with the concierge permission onboards a venture for a founder:
uploads the founder's documents, runs the evaluation, reviews and confirms it,
and (steps 7-12, not built yet) shares it for the founder to accept and pay.

The venture is a normal FounderVenture with track and status 'concierge' and
no owner until acceptance, so it reuses the evaluation, team and plan code.
Its lifecycle lives on concierge_case.state and changes ONLY through
transition() below, which enforces TRANSITIONS and writes the audit log.

    can_concierge(user) -> bool
    transition(case, to_state, actor, note=None) -> case
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

PERMISSION_KEY = 'founder_concierge'

STATES = ('draft', 'documents_uploaded', 'evaluating', 'ready_for_review', 'confirmed', 'shared', 'viewed',
          'revision_requested', 'accepted_paid', 'active', 'declined', 'expired', 'deleted')

# Plan section 3.6. A failed evaluation returns to documents_uploaded; new
# documents after review re-run the evaluation (ready_for_review|confirmed ->
# evaluating). Nothing else moves a case.
TRANSITIONS = {
    'draft': ('documents_uploaded',),
    'documents_uploaded': ('evaluating',),
    'evaluating': ('ready_for_review', 'documents_uploaded'),
    'ready_for_review': ('confirmed', 'evaluating'),
    'confirmed': ('shared', 'evaluating'),
    'shared': ('viewed', 'expired'),
    'viewed': ('accepted_paid', 'revision_requested', 'declined', 'expired'),
    'revision_requested': ('ready_for_review', 'declined', 'expired'),
    'accepted_paid': ('active',),
    'active': (),
    'declined': ('deleted',),
    'expired': ('deleted',),
    'deleted': (),
}

STATE_LABELS = {
    'draft': 'Draft', 'documents_uploaded': 'Documents uploaded', 'evaluating': 'Evaluating',
    'ready_for_review': 'Ready for review', 'confirmed': 'Confirmed', 'shared': 'Shared', 'viewed': 'Viewed',
    'revision_requested': 'Revision requested', 'accepted_paid': 'Accepted and paid', 'active': 'Active',
    'declined': 'Declined', 'expired': 'Expired', 'deleted': 'Deleted',
}

# Timestamp/actor columns set when a case enters a state.
_STAMPS = {'confirmed': ('confirmed_at', 'confirmed_by'), 'viewed': ('viewed_at', None),
           'accepted_paid': ('accepted_at', None), 'declined': ('declined_at', None),
           'expired': ('expired_at', None)}


class ConciergeError(ValueError):
    """A refusal the admin route turns into a message. status is the HTTP code."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------

def can_concierge(user) -> bool:
    """Admins explicitly allowed for concierge onboarding (founder_feature key
    founder_concierge, a per-admin allowlist). Fails closed."""
    if user is None or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_admin', False):
        return False
    try:
        from app.services import founder_feature
        return founder_feature.is_enabled(user, founder_feature.CONCIERGE)
    except Exception as exc:
        logger.error('concierge permission unreadable for %s, refusing: %s', getattr(user, 'id', '?'), exc)
        return False


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def allowed(from_state: str, to_state: str) -> bool:
    return to_state in TRANSITIONS.get(from_state, ())


def transition(case, to_state: str, actor, note: str = None, commit: bool = True):
    """Move the case to to_state if TRANSITIONS allows it from its current
    state, and audit-log the change (action concierge_<to_state>; metadata
    from, to, venture, actor, case, note). The single place a case's state
    changes. Raises ConciergeError. Commits unless commit=False."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import ConciergeCase
    if to_state not in STATES:
        raise ConciergeError('Unknown state: %s.' % to_state, 400)
    # Lock the row and re-read the state, so two admins can't both move it.
    locked = (db.session.query(ConciergeCase).filter(ConciergeCase.id == case.id).with_for_update().first())
    if locked is None:
        raise ConciergeError('Case not found.', 404)
    case = locked
    from_state = case.state
    if not allowed(from_state, to_state):
        if commit:
            db.session.rollback()
        raise ConciergeError('A case that is %s cannot become %s.' % (
            STATE_LABELS.get(from_state, from_state).lower(), STATE_LABELS[to_state].lower()))
    now = datetime.utcnow()
    actor_id = getattr(actor, 'id', actor if isinstance(actor, str) else None)
    case.state = to_state
    case.updated_at = now
    at_col, by_col = _STAMPS.get(to_state, (None, None))
    if at_col:
        setattr(case, at_col, now)
    if by_col:
        setattr(case, by_col, actor_id)
    meta = {'from': from_state, 'to': to_state, 'venture': case.venture_id, 'actor': actor_id, 'case': case.id,
            'at': now.isoformat()}
    if note:
        meta['note'] = str(note)[:500]
    AuditLog.log('concierge_' + to_state, user_id=actor_id, resource_id=case.venture_id, metadata=meta)
    if commit:
        db.session.commit()
    return case
