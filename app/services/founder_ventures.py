"""Founder Track ventures: lookup, access and creation (FOUNDER_PLAN.md section 5).

A user may own several ventures (up to MAX_VENTURES_PER_OWNER, each with a
distinct name; sql/078 dropped the one-per-owner key) and may be an active
co-founder on others. Each venture has its own documents, team, report, page
and one-time payment. Every founder route resolves its venture
through require_member(), which 404s for a missing venture and 403s for a user
who isn't an active member.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STAGES = ('idea', 'pre-seed', 'seed', 'series-a')
# Every venture gets a free evaluation preview (model cost), so ownership is
# capped. Raise it here if founders need more.
MAX_VENTURES_PER_OWNER = 5
NAME_MAX = 200
ONE_LINER_MAX = 300


class VentureError(ValueError):
    pass


def venture_for_owner(user_id: str):
    """The owner's most recent venture, or None (a founder may own several)."""
    from app.models.founder import FounderVenture
    if not user_id:
        return None
    return (FounderVenture.query.filter_by(owner_user_id=user_id)
            .order_by(FounderVenture.created_at.desc()).first())


def owned_ventures(user_id: str) -> list:
    from app.models.founder import FounderVenture
    if not user_id:
        return []
    return (FounderVenture.query.filter_by(owner_user_id=user_id)
            .order_by(FounderVenture.created_at).all())


def can_start_another(user_id: str) -> bool:
    return len(owned_ventures(user_id)) < MAX_VENTURES_PER_OWNER


def ventures_for_user(user_id: str) -> list:
    """Ventures the user owns or is an active member of, newest first."""
    from app.models.founder import FounderVenture, VentureMember
    if not user_id:
        return []
    member_ids = [m.venture_id for m in VentureMember.query.filter_by(user_id=user_id, status='active').all()]
    q = FounderVenture.query.filter(
        (FounderVenture.owner_user_id == user_id) | (FounderVenture.id.in_(member_ids or ['-']))
    )
    return q.order_by(FounderVenture.created_at.desc()).all()


def get_member(venture, user_id: str):
    """The user's active membership row on this venture, or None."""
    from app.models.founder import VentureMember
    if venture is None or not user_id:
        return None
    return VentureMember.query.filter_by(venture_id=venture.id, user_id=user_id, status='active').first()


def require_member(venture_id: str, user):
    """The venture if the user is an active member; otherwise abort 404 / 403."""
    from flask import abort
    from app.models.founder import FounderVenture
    v = FounderVenture.query.get(venture_id) if venture_id else None
    if v is None:
        abort(404)
    uid = getattr(user, 'id', None)
    if not getattr(user, 'is_authenticated', False) or get_member(v, uid) is None:
        abort(403)
    return v


def is_paid(venture) -> bool:
    return bool(venture is not None and venture.paid_at is not None)


def create_venture(user, name: str, one_liner: str = '', stage: str = 'idea'):
    """Create the user's venture, its owner member and the default decision
    rights (the owner holds every decision type). Raises VentureError."""
    from app.extensions import db
    from app.models.founder import DecisionRight, FounderVenture, VentureMember
    from datetime import datetime

    name = ' '.join((name or '').split())
    one_liner = ' '.join((one_liner or '').split())
    stage = (stage or 'idea').strip().lower()
    if not name:
        raise VentureError('Give the venture a name.')
    if len(name) > NAME_MAX:
        raise VentureError('The name is too long (%d characters at most).' % NAME_MAX)
    if len(one_liner) > ONE_LINER_MAX:
        raise VentureError('The one-liner is too long (%d characters at most).' % ONE_LINER_MAX)
    if stage not in STAGES:
        raise VentureError('Unknown stage.')
    owned = owned_ventures(user.id)
    if any((x.name or '').strip().lower() == name.lower() for x in owned):
        # Also stops a double-click on Start from creating the venture twice.
        raise VentureError('You already have a venture called %s. Open it from the Founder Track page, '
                           'or give the new one a different name.' % name)
    if len(owned) >= MAX_VENTURES_PER_OWNER:
        raise VentureError('You can run up to %d ventures on the Founder Track. Contact SimiSI if you need more.'
                           % MAX_VENTURES_PER_OWNER)

    now = datetime.utcnow()
    v = FounderVenture(owner_user_id=user.id, name=name, one_liner=one_liner or None, stage=stage,
                       status='active', rung=1, track='standalone', created_at=now, updated_at=now)
    db.session.add(v)
    from sqlalchemy.exc import IntegrityError
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise VentureError('That venture could not be created. Try again.')
    owner = VentureMember(venture_id=v.id, user_id=user.id, role='owner', status='active', joined_at=now,
                          title='Founder')
    db.session.add(owner)
    db.session.flush()
    for dt in DecisionRight.DECISION_TYPES:
        db.session.add(DecisionRight(venture_id=v.id, decision_type=dt, holder_member_id=owner.id))
    log_event(v.id, 'venture_created', 'Venture created', user_id=user.id)
    db.session.commit()
    return v


def log_event(venture_id: str, kind: str, note: str = '', user_id: str = None) -> None:
    """Audit-log a venture event. Doesn't commit (the caller's transaction does)."""
    from app.models.audit_log import AuditLog
    try:
        AuditLog.log('founder_' + (kind or 'event')[:80], user_id=user_id, resource_id=venture_id,
                     metadata={'note': (note or '')[:500]} if note else None)
    except Exception as exc:
        logger.error('founder audit log failed for %s: %s', venture_id, exc)
