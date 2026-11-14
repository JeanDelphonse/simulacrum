"""Founder Track ventures: lookup, access and creation (FOUNDER_PLAN.md section 5).

A user owns at most one venture (founder_venture.owner_user_id is UNIQUE) and
may be an active co-founder on others. Every founder route resolves its venture
through require_member(), which 404s for a missing venture and 403s for a user
who isn't an active member.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STAGES = ('idea', 'pre-seed', 'seed', 'series-a')
NAME_MAX = 200
ONE_LINER_MAX = 300


class VentureError(ValueError):
    pass


def venture_for_owner(user_id: str):
    from app.models.founder import FounderVenture
    if not user_id:
        return None
    return FounderVenture.query.filter_by(owner_user_id=user_id).first()


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
    if venture_for_owner(user.id) is not None:
        raise VentureError('You already have a venture.')

    now = datetime.utcnow()
    v = FounderVenture(owner_user_id=user.id, name=name, one_liner=one_liner or None, stage=stage,
                       status='active', rung=1, track='standalone', created_at=now, updated_at=now)
    db.session.add(v)
    from sqlalchemy.exc import IntegrityError
    try:
        db.session.flush()
    except IntegrityError:
        # A second request (a double-click on start) won the UNIQUE owner_user_id race.
        db.session.rollback()
        raise VentureError('You already have a venture.')
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
