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


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def case_for(venture_id: str):
    from app.models.founder import ConciergeCase
    return ConciergeCase.query.filter_by(venture_id=venture_id).first() if venture_id else None


def venture_and_case(venture_id: str):
    """(venture, case) for a concierge venture, or (None, None)."""
    from app.extensions import db
    from app.models.founder import FounderVenture
    v = db.session.get(FounderVenture, venture_id) if venture_id else None
    c = case_for(v.id) if v is not None else None
    if v is None or c is None:
        return None, None
    return v, c


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

MAX_COFOUNDERS = 10
NOTES_MAX = 5000


def _clean(value, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _email(raw, label: str) -> str:
    from utils.validators import ValidationError, validate_email
    try:
        return validate_email(raw, allow_disposable=True)
    except ValidationError as exc:
        raise ConciergeError('%s: %s' % (label, exc), 400)


def consent_version() -> str:
    """The version of the consent wording in force (Admin > Legal text)."""
    try:
        from app.models.compliance import LegalText
        row = LegalText.query.filter_by(key='concierge_consent').first()
        return 'concierge_consent@v%d' % (row.version if row else 1)
    except Exception:
        return 'concierge_consent@v1'


def create_case(admin, primary_name, primary_email, cofounder_emails=None, source='', referrer='', notes='',
                consent=False, consent_given_by='', nda=False, venture_name=''):
    """Create the unowned concierge venture and its case (state draft). The
    consent checkbox and who gave consent are required. Audited. Commits."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import ConciergeCase, FounderVenture
    from app.services import legal_text
    name = _clean(primary_name, 200)
    if not name:
        raise ConciergeError("Enter the primary founder's name.", 400)
    email = _email(primary_email, 'Primary founder email')
    if isinstance(cofounder_emails, str):
        import re
        cofounder_emails = [e for e in re.split(r'[\s,;]+', cofounder_emails) if e]
    cos = []
    for e in cofounder_emails or []:
        e = _email(e, 'Co-founder email')
        if e != email and e not in cos:
            cos.append(e)
    if len(cos) > MAX_COFOUNDERS:
        raise ConciergeError('%d co-founder emails at most.' % MAX_COFOUNDERS, 400)
    given_by = _clean(consent_given_by, 200)
    if not consent:
        raise ConciergeError('Record the consent: tick the box confirming SimiSI was authorized to receive and '
                             'evaluate these documents.', 400)
    if not given_by:
        raise ConciergeError('Say who gave consent (the founder, or the referrer acting for them).', 400)
    vname = _clean(venture_name, 200) or ('Venture of %s' % name)[:200]
    now = datetime.utcnow()
    v = FounderVenture(owner_user_id=None, name=vname, stage='idea', status='concierge', track='concierge',
                       rung=1, created_at=now, updated_at=now)
    db.session.add(v)
    db.session.flush()
    admin_id = getattr(admin, 'id', None)
    c = ConciergeCase(venture_id=v.id, state='draft', primary_name=name, primary_email=email,
                      cofounder_emails=cos, source=_clean(source, 120) or None,
                      referrer=_clean(referrer, 200) or None, notes=(str(notes or '').strip()[:NOTES_MAX] or None),
                      consent_given_by=given_by, consent_at=now, consent_version=consent_version(), nda=bool(nda),
                      created_by=admin_id, created_at=now, updated_at=now)
    db.session.add(c)
    db.session.flush()
    consent_text = legal_text.get('concierge_consent', consent_given_by=given_by, primary_name=name,
                                  admin_name=getattr(admin, 'full_name', None) or getattr(admin, 'email', ''),
                                  date=now.strftime('%Y-%m-%d'))
    AuditLog.log('concierge_draft', user_id=admin_id, resource_id=v.id,
                 metadata={'from': None, 'to': 'draft', 'venture': v.id, 'actor': admin_id, 'case': c.id,
                           'at': now.isoformat(), 'consent_version': c.consent_version,
                           'consent_text': consent_text[:1000], 'nda': bool(nda)})
    db.session.commit()
    return v, c


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

UPLOAD_STATES = ('draft', 'documents_uploaded', 'ready_for_review', 'confirmed')


def upload(venture, case, file_storage, kind: str, admin):
    """Store one document encrypted (founder_intake_service). The first
    document moves the case from draft to documents_uploaded. New documents
    after review are allowed and call for a re-run. Raises ConciergeError /
    founder_intake_service.IntakeError."""
    from app.services import founder_intake_service as fis
    if case.state not in UPLOAD_STATES:
        raise ConciergeError('Documents cannot be added while the case is %s.'
                             % STATE_LABELS.get(case.state, case.state).lower())
    art = fis.save_concierge_upload(venture, file_storage, kind, admin)      # commits
    if case.state == 'draft':
        transition(case, 'documents_uploaded', admin, note='%s v%d' % (kind, art.version))
    return art


def documents(venture) -> list:
    """One row per uploadable kind: the current version or None."""
    from app.services import founder_intake_service as fis
    rows = {k: {'kind': k, 'label': fis.KIND_LABELS[k], 'hint': fis.KIND_HINTS[k], 'current': None, 'versions': 0}
            for k in fis.UPLOAD_KINDS}
    for a in fis.artifacts(venture):
        r = rows.get(a.kind)
        if r is None:
            continue
        r['versions'] += 1
        if a.is_current:
            r['current'] = a
    return [rows[k] for k in fis.UPLOAD_KINDS]


# ---------------------------------------------------------------------------
# Fee
# ---------------------------------------------------------------------------

def base_fee_cents() -> int:
    from app.services import founder_config
    return int(founder_config.get('fnd_concierge_fee_cents') or 0)


def effective_fee_cents(case) -> int:
    """The per-venture override if set, else the Pricing setting (0 = not set)."""
    if case is not None and case.fee_override_cents is not None:
        return int(case.fee_override_cents)
    return base_fee_cents()


def set_fee_override(case, cents, reason: str, admin):
    """Set (cents) or clear (None) the venture's fee override. A reason is
    required either way. Audited. Commits."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.blueprints.founder.admin import CARD_MINIMUM_CENTS
    reason = _clean(reason, 500)
    if not reason:
        raise ConciergeError('Give a reason for the fee override.', 400)
    if case.state in ('accepted_paid', 'active', 'declined', 'expired', 'deleted'):
        raise ConciergeError('The fee can no longer change for this case.')
    if cents is not None:
        try:
            if isinstance(cents, bool):
                raise ValueError
            cents = int(cents)
        except (TypeError, ValueError):
            raise ConciergeError('Enter the fee as an amount.', 400)
        if cents < CARD_MINIMUM_CENTS or cents > 10000000:
            raise ConciergeError('The fee must be at least %.2f (the card minimum).' % (CARD_MINIMUM_CENTS / 100.0),
                                 400)
    before = case.fee_override_cents
    case.fee_override_cents = cents
    case.fee_override_reason = reason if cents is not None else None
    case.fee_override_by = getattr(admin, 'id', None) if cents is not None else None
    case.updated_at = datetime.utcnow()
    AuditLog.log('concierge_fee_override', user_id=getattr(admin, 'id', None), resource_id=case.venture_id,
                 metadata={'case': case.id, 'before': before, 'after': cents, 'reason': reason,
                           'base_fee': base_fee_cents()})
    db.session.commit()
    return case


def fee_history(case) -> list:
    from app.models.audit_log import AuditLog
    rows = (AuditLog.query.filter_by(action='concierge_fee_override', resource_id=case.venture_id)
            .order_by(AuditLog.created_at.desc()).limit(50).all())
    return [dict(r.extra or {}, by=r.user_id, at=r.created_at) for r in rows]


def money(cents, currency: str = None) -> str:
    from app.services import founder_config
    cur = (currency or founder_config.get('fnd_concierge_currency') or 'usd').upper()
    return '%s %s' % (cur, '{:,.2f}'.format((cents or 0) / 100.0))


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

QUEUE_LIMIT = 500


def queue(state: str = None) -> list:
    """Every concierge case (newest first), optionally one state, with the
    queue columns. Metadata only."""
    from app.models.founder import ConciergeCase, FounderVenture
    q = ConciergeCase.query
    if state:
        q = q.filter(ConciergeCase.state == state)
    cases = q.order_by(ConciergeCase.created_at.desc()).limit(QUEUE_LIMIT).all()
    ventures = {v.id: v for v in FounderVenture.query.filter(
        FounderVenture.id.in_([c.venture_id for c in cases] or ['-'])).all()}
    now = datetime.utcnow()
    out = []
    for c in cases:
        v = ventures.get(c.venture_id)
        out.append({
            'venture_id': c.venture_id, 'case_id': c.id, 'name': getattr(v, 'name', '') or '',
            'primary_name': c.primary_name or '', 'primary_email': c.primary_email or '',
            'state': c.state, 'state_label': STATE_LABELS.get(c.state, c.state),
            'created_at': c.created_at.strftime('%Y-%m-%d') if c.created_at else '',
            'days_since_shared': (now - c.shared_at).days if c.shared_at else None,
            'days_left': max(0, (c.expires_at - now).days) if c.expires_at else None,
            'fee_cents': effective_fee_cents(c), 'fee_override': c.fee_override_cents is not None,
            'fee': money(effective_fee_cents(c)) if effective_fee_cents(c) else '',
            'paid': bool(c.accepted_at or getattr(v, 'paid_at', None)),
            'amount': money(c.amount_cents) if c.amount_cents else '',
        })
    return out


def state_counts() -> dict:
    from sqlalchemy import func
    from app.extensions import db
    from app.models.founder import ConciergeCase
    return dict(db.session.query(ConciergeCase.state, func.count(ConciergeCase.id)).group_by(ConciergeCase.state).all())


# ---------------------------------------------------------------------------
# Admin venture page
# ---------------------------------------------------------------------------

def case_view(venture, case) -> dict:
    """Everything the admin venture page shows."""
    from app.extensions import db
    from app.models.user import User
    creator = db.session.get(User, case.created_by) if case.created_by else None
    fee = effective_fee_cents(case)
    return {
        'venture': venture, 'case': case, 'state_label': STATE_LABELS.get(case.state, case.state),
        'documents': documents(venture), 'can_upload': case.state in UPLOAD_STATES,
        'fee_cents': fee, 'fee': money(fee) if fee else '', 'base_fee_cents': base_fee_cents(),
        'base_fee': money(base_fee_cents()) if base_fee_cents() else '', 'fee_history': fee_history(case),
        'created_by': getattr(creator, 'full_name', None) or getattr(creator, 'email', '') or '',
        'cofounders': list(case.cofounder_emails or []),
    }
