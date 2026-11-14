"""Founder Track autonomy levels and approvals (FOUNDER_PLAN.md section 5).

Levels (PRD, Investor connection): 1 advisor only, 2 approve each action,
3 approve the campaign, 4 act within limits. 0 means not permitted. The
matrix (default level and the most a founder may raise to, per action type)
is the admin setting fnd_autonomy_matrix; first contact with an investor or
program, including form submissions, is also held at fnd_first_contact_cap.

These levels are their own scale. They are never mapped onto the wealth
agent_plans levels (L0-L3).

    level_for(venture, 'fnd_pilot_offer')        -> 2
    can_raise('first_contact', 3)                -> True
    require_approval(action)                      -> True
    approve(action, user)                         routed to the decision-right holder

This wave drafts only: nothing here sends. WP8 adds send() behind the
founder_investor switch, extending this module with new functions only.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

LEVEL_LABELS = {
    0: 'Not permitted',
    1: 'Advisor only',
    2: 'Approve each action',
    3: 'Approve the campaign',
    4: 'Act within limits',
}
MIN_LEVEL = 1
# Action types the first-contact cap also holds (PRD: "First contact with an
# investor or program, including form submissions").
FIRST_CONTACT_TYPES = ('first_contact', 'application')
# Who has the final say on each action type (decision_right.decision_type).
DECISION_TYPES = {
    'research': 'product',
    'customer_message': 'customer_contact',
    'intro_request': 'investor_contact',
    'followup': 'investor_contact',
    'first_contact': 'investor_contact',
    'application': 'investor_contact',
    'financial_message': 'investor_contact',
}
PENDING = ('drafted', 'awaiting_approval')


class AutonomyError(ValueError):
    """status is the HTTP status a route should answer with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _matrix() -> dict:
    from app.services import founder_config
    return founder_config.get('fnd_autonomy_matrix') or {}


def action_class(action_type: str) -> str:
    """Matrix row for an agent key (fnd_*) or an action type."""
    if action_type and action_type.startswith('fnd_'):
        from app.services import founder_registry
        a = founder_registry.get(action_type)
        return (a or {}).get('action_type_class') or 'research'
    return action_type or 'research'


def limits(action_type: str) -> dict:
    """{'default', 'max'} for the action type after the first-contact cap.
    Unknown action types are not permitted (0, 0)."""
    from app.services import founder_config
    cls = action_class(action_type)
    row = _matrix().get(cls)
    if not row:
        return {'default': 0, 'max': 0}
    dflt, mx = int(row.get('default', 0) or 0), int(row.get('max', 0) or 0)
    if cls in FIRST_CONTACT_TYPES:
        cap = int(founder_config.get('fnd_first_contact_cap'))
        dflt, mx = min(dflt, cap), min(mx, cap)
    return {'default': min(dflt, mx), 'max': mx}


def default_level(action_type: str) -> int:
    return limits(action_type)['default']


def can_raise(action_type: str, level) -> bool:
    """True when a founder may set the action type to this level."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return False
    return MIN_LEVEL <= level <= limits(action_type)['max']


def level_for(venture, action_type: str) -> int:
    """The level in force for this venture: the agent's effective autonomy
    parameter (recommended or overridden, clamped to today's limits) when an
    agent key is given and it has a blueprint, else the matrix default."""
    if action_type and action_type.startswith('fnd_') and venture is not None:
        try:
            from app.services import founder_team_service
            return int(founder_team_service.effective_param(venture, action_type, 'autonomy_level'))
        except Exception as exc:
            logger.debug('autonomy level fallback for %s: %s', action_type, exc)
    return default_level(action_type)


def require_approval(action) -> bool:
    """True when a person must approve the action before anything happens.
    Everything addressed to a customer or investor needs approval at every
    level in this wave; only research (level 4, internal) does not."""
    cls = getattr(action, 'action_type', None) or 'research'
    if cls == 'research':
        return False
    return True


def decision_type_for(action_type: str) -> str:
    return DECISION_TYPES.get(action_class(action_type), 'product')


def approver(venture, decision_type: str, layer=None):
    """The active member who holds the decision right (a layer-specific right
    wins over a venture-wide one); the owner if no right is recorded or the
    holder has left."""
    from app.models.founder import DecisionRight, VentureMember
    rows = DecisionRight.query.filter_by(venture_id=venture.id, decision_type=decision_type).all()
    ordered = [r for r in rows if layer is not None and r.layer == layer] + [r for r in rows if r.layer is None]
    for r in ordered:
        m = VentureMember.query.filter_by(id=r.holder_member_id, venture_id=venture.id, status='active').first()
        if m is not None and m.user_id:
            return m
    return VentureMember.query.filter_by(venture_id=venture.id, role='owner', status='active').first()


def approver_for(action):
    from app.extensions import db
    from app.models.founder import FounderVenture, VenturePlanStep
    venture = db.session.get(FounderVenture, action.venture_id)
    step = db.session.get(VenturePlanStep, action.plan_step_id) if action.plan_step_id else None
    return approver(venture, decision_type_for(action.action_type), step.layer if step else None)


def _decide(action, user, decision: str, reason: str = None):
    from app.extensions import db
    from app.models.agent_plan import ActionApproval
    from app.models.founder import FounderVenture
    from app.services import founder_ventures
    if action is None:
        raise AutonomyError('Draft not found.', 404)
    venture = db.session.get(FounderVenture, action.venture_id)
    uid = getattr(user, 'id', None)
    if founder_ventures.get_member(venture, uid) is None:
        raise AutonomyError('Only the founders of this venture can decide on its drafts.', 403)
    if action.status not in PENDING:
        raise AutonomyError('This draft was already %s.' % action.status.replace('_', ' '), 409)
    holder = approver_for(action)
    if holder is None or holder.user_id != uid:
        name = _member_name(holder)
        raise AutonomyError('%s has the final say on %s, so the approval is routed to them.'
                            % (name, decision_type_for(action.action_type).replace('_', ' ')), 403)
    now = datetime.utcnow()
    if decision == 'approve':
        action.status = 'approved'
        action.approved_by = uid
        if action.level_required is None:
            action.level_required = level_for(venture, action.action_type)
    else:
        action.status = 'blocked'
        action.block_reason = (reason or 'Declined by the founder')[:300]
    db.session.add(ActionApproval(target_type='fnd_outreach', target_id=action.id,
                                  required_reason=decision_type_for(action.action_type)[:20],
                                  decided_by=uid, decision=decision, ts=now))
    founder_ventures.log_event(venture.id, 'draft_' + ('approved' if decision == 'approve' else 'declined'),
                               '%s %s' % (action.id, action.action_type), user_id=uid)
    try:
        from app.services import founder_stream
        founder_stream.publish(venture.id, 'draft_' + ('approved' if decision == 'approve' else 'declined'),
                               {'plan_step_id': action.plan_step_id, 'action_id': action.id,
                                'actor_user_id': uid, 'actor_kind': 'founder',
                                'note': ('Draft approved. Nothing is sent by SimiSI; send it yourself.'
                                         if decision == 'approve' else 'Draft declined.')}, commit=False)
    except Exception as exc:
        logger.debug('founder stream publish skipped: %s', exc)
    db.session.commit()
    try:
        _settle_step(action, user)
    except Exception as exc:
        # The decision is already saved; a step that can't move on stays put.
        logger.error('founder step for draft %s not settled: %s', action.id, exc)
        db.session.rollback()
    return action


def _settle_step(action, user) -> None:
    """When a step has no drafts left to decide, it moves on: done if any
    draft was approved (the founder sends it), otherwise back to ready."""
    from app.extensions import db
    from app.models.founder import VentureOutreachAction, VenturePlanStep
    if not action.plan_step_id:
        return
    step = db.session.get(VenturePlanStep, action.plan_step_id)
    if step is None or step.state != 'awaiting_approval':
        return
    rows = VentureOutreachAction.query.filter_by(plan_step_id=step.id).all()
    if any(r.status in PENDING for r in rows):
        return
    from app.services import founder_orchestrator as fo
    if any(r.status == 'approved' for r in rows):
        try:
            fo.complete(step, user, evidence_note='Drafts approved. The founders send them from their own accounts.',
                        output_id=step.output_id, actor_kind='founder')
        except fo.OrchestratorError as exc:
            # An L5 gate closed while the drafts waited (investor connection
            # switched off, or the evidence rung lowered). The step goes back
            # to ready and refresh() locks it behind the gate.
            db.session.rollback()
            step = db.session.get(VenturePlanStep, action.plan_step_id)
            if step is None or step.state != 'awaiting_approval':
                return
            fo.transition(step, 'ready', user=user, actor_kind='founder', commit=False,
                          note=('Drafts decided, but the step cannot close: %s' % exc)[:200])
            db.session.flush()
            fo.refresh(fo._venture(step), commit=False)
            db.session.commit()
    else:
        fo.transition(step, 'ready', user=user, actor_kind='founder', note='All drafts declined')


def approve(action, user):
    """Approve a pending draft. Only the decision-right holder for the
    action's decision type may approve. Approving does not send anything."""
    return _decide(action, user, 'approve')


def reject(action, user, reason: str = None):
    return _decide(action, user, 'reject', reason)


def _member_name(member) -> str:
    if member is None:
        return 'The venture owner'
    try:
        from app.extensions import db
        from app.models.user import User
        u = db.session.get(User, member.user_id) if member.user_id else None
        return (u.full_name or u.email) if u else (member.invite_email or 'A co-founder')
    except Exception:
        return 'A co-founder'


def approver_name(action) -> str:
    return _member_name(approver_for(action))
