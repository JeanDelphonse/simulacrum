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


# ---------------------------------------------------------------------------
# Phase 3: sending (FOUNDER_PLAN.md WP8). New functions only; everything above
# keeps its behaviour. Behind the founder_investor switch and the counsel
# sign-off, gated on the recorded offering exemption.
# ---------------------------------------------------------------------------

SEND_TYPES = ('intro_request', 'followup', 'first_contact', 'financial_message', 'application')
# Under 506(b), or before an exemption is decided, only existing relationships
# and published application forms may be reached (PRD, Compliance guardrails).
CONSERVATIVE_EXEMPTIONS = ('506b', 'undecided')


class SendBlocked(AutonomyError):
    """A send refusal. above_level marks an attempt above the autonomy allowed
    for the action type (it raises an observability flag at once)."""

    def __init__(self, message, status=409, above_level=False):
        super().__init__(message, status)
        self.above_level = above_level


def sent_today(venture) -> int:
    """Outreach actions sent for this venture in the last 24 hours."""
    from datetime import timedelta
    from app.models.founder import VentureOutreachAction
    since = datetime.utcnow() - timedelta(days=1)
    return VentureOutreachAction.query.filter(VentureOutreachAction.venture_id == venture.id,
                                              VentureOutreachAction.status == 'sent',
                                              VentureOutreachAction.sent_at >= since).count()


def send_cap() -> int:
    from app.services import founder_config
    return max(0, int(founder_config.get('fnd_action_cap_per_day')))


def requested_level(venture, action) -> int:
    """The level the action was drafted and approved under."""
    if action.level_required is not None:
        return int(action.level_required)
    return level_for(venture, action.action_type)


def _send_gate(action, user, venture) -> dict:
    """Every check before a send, without side effects. Raises SendBlocked;
    returns {'cls', 'level', 'rec', 'relationship', 'exemption'}."""
    from app.services import founder_compliance, founder_config, founder_feature, founder_investors
    from app.services import founder_ventures
    if founder_ventures.get_member(venture, getattr(user, 'id', None)) is None:
        raise SendBlocked('Only the founders of this venture can send its drafts.', 403)
    if not founder_feature.is_enabled(user, founder_feature.INVESTOR) or not founder_config.counsel_signed_off():
        raise SendBlocked('Investor connection is not switched on for your account.', 403)
    if not founder_investors.exemption_recorded(venture):
        raise SendBlocked('Record which offering exemption your lawyer chose before anything is sent to '
                          'investors.', 409)
    rec = founder_investors.resolve(venture, action)
    if rec['kind'] == 'linkedin':
        from app.services import legal_text
        raise SendBlocked(legal_text.get('linkedin_manual'), 409)
    if action.status == 'sent':
        raise SendBlocked('This draft was already sent.', 409)
    if action.status == 'blocked':
        raise SendBlocked('This draft is held: %s.' % (action.block_reason or 'declined'), 409)
    cls = action_class(action.action_type)
    lim = limits(cls)
    level = requested_level(venture, action)
    if lim['max'] <= 0:
        raise SendBlocked('SimiSI never sends this kind of message (negotiating or accepting terms is not '
                          'permitted at any level).', 403, above_level=True)
    if action.action_type not in SEND_TYPES:
        raise SendBlocked('SimiSI sends only investor messages for the Founder Track. Copy this draft and send it '
                          'yourself.', 409)
    if level > lim['max']:
        raise SendBlocked('This draft is set to level %d (%s), above the most allowed for %s (level %d). It was '
                          'not sent.' % (level, LEVEL_LABELS.get(level, ''), cls.replace('_', ' '), lim['max']),
                          403, above_level=True)
    if level <= MIN_LEVEL:
        raise SendBlocked('This agent is advisor only: copy the draft and send it yourself.', 409)
    if action.status != 'approved' or not action.approved_by:
        raise SendBlocked('Approve the draft first. %s has the final say.' % approver_name(action), 409)
    ex = founder_investors.exemption(venture)
    issues = founder_compliance.check('%s\n%s' % (action.subject or '', action.body or ''), 5, exemption=ex)
    if issues:
        raise SendBlocked('The draft breaks a SimiSI rule (%s). Edit or redraft it.'
                          % ', '.join(sorted({i['label'] for i in issues})), 422)
    if rec['kind'] in (None, 'text'):
        raise SendBlocked('Choose who this goes to first (one of your contacts or an email address).', 409)
    rel = founder_investors.relationship(venture, action, rec)
    if ex in CONSERVATIVE_EXEMPTIONS and rel is None:
        raise SendBlocked('Under %s, SimiSI reaches only people you already know, investors who replied to you, '
                          'and published application forms. This recipient is none of those, so it was not sent.'
                          % ('Rule 506(b)' if ex == '506b' else 'an undecided exemption'), 409)
    if action.action_type == 'application':
        if rec.get('profile') is None or not rec['profile'].apply_url:
            raise SendBlocked('There is no published application form on file for this draft.', 409)
    elif not rec.get('email'):
        raise SendBlocked('There is no email address for this recipient yet.', 409)
    if action.action_type != 'application' and sent_today(venture) >= send_cap():
        raise SendBlocked('Your daily limit of %d investor messages is reached. Try again tomorrow.' % send_cap(),
                          429)
    return {'cls': cls, 'level': level, 'rec': rec, 'relationship': rel, 'exemption': ex}


def send_check(action, user):
    """Why this draft can't be sent right now, or None. No side effects."""
    from app.extensions import db
    from app.models.founder import FounderVenture
    try:
        _send_gate(action, user, db.session.get(FounderVenture, action.venture_id))
    except SendBlocked as exc:
        return str(exc)
    except Exception as exc:
        logger.error('send check failed for %s: %s', getattr(action, 'id', '?'), exc)
        return 'SimiSI could not check this draft. Try again.'
    return None


def _flag_attempt(venture, action, user, message) -> None:
    """An attempt above the allowed autonomy: hold the draft and raise the
    above-level observability flag at once."""
    from app.extensions import db
    from app.services import founder_ventures
    action.status = 'blocked'
    action.block_reason = 'Not sent: above the allowed autonomy level'
    founder_ventures.log_event(venture.id, 'send_refused_above_level', '%s %s: %s' % (
        action.id, action.action_type, message), user_id=getattr(user, 'id', None))
    try:
        from app.services import founder_alerts
        if not founder_alerts._recent_flag('above_level_send', None, target_id=action.id):
            founder_alerts._flag('above_level_send', 'action', action.id,
                                 '%s attempted at level %s; allowed %s' % (
                                     action.action_type, action.level_required, limits(action.action_type)['max']),
                                 severity='critical', user_id=venture.owner_user_id)
    except Exception as exc:
        logger.error('above-level flag failed for %s: %s', action.id, exc)
    db.session.commit()


def send(action, user) -> dict:
    """Send one approved investor-facing draft (Phase 3).

    Refuses unless investor connection is on for the user with the counsel
    sign-off recorded and the venture's exemption recorded. Under 506(b) or an
    undecided exemption only existing relationships (the founder's own
    contacts, investors who replied) and published application forms. The
    level must be within the matrix (first contact never above
    fnd_first_contact_cap; negotiating terms never) and a founder must have
    approved it. fnd_action_cap_per_day caps sends per venture.

    Email goes through outreach_email_service.send_outreach_email (suppression,
    postal address, footer, logging): the founder's name as display name,
    reply-to the founder, labelled as sent on their behalf by SimiSI, with a
    member-scoped unsubscribe. Applications are never submitted by SimiSI: the
    approved answers are handed to the founder with the form link. LinkedIn is
    copy-only. Returns {'ok', 'delivery', ...}; raises SendBlocked."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import FounderVenture
    from app.services import founder_investors, founder_ventures, send_pipeline
    if action is None:
        raise SendBlocked('Draft not found.', 404)
    venture = db.session.get(FounderVenture, action.venture_id)
    try:
        gate = _send_gate(action, user, venture)
    except SendBlocked as exc:
        if exc.above_level and action.status != 'sent':
            _flag_attempt(venture, action, user, str(exc))
        raise
    now = datetime.utcnow()
    rec = gate['rec']
    if action.action_type == 'application':
        action.level_applied = gate['level']
        AuditLog.log('founder_application_handoff', user_id=user.id, resource_id=venture.id,
                     metadata={'action_id': action.id, 'apply_url': rec['profile'].apply_url,
                               'level': gate['level'], 'exemption': gate['exemption']})
        founder_ventures.log_event(venture.id, 'application_ready', '%s ready for the founder to submit' % action.id,
                                   user_id=user.id)
        db.session.commit()
        return {'ok': True, 'delivery': 'founder_submits', 'apply_url': rec['profile'].apply_url,
                'message': 'Approved. SimiSI does not submit applications: open the form and submit these answers '
                           'yourself.'}
    try:
        sender = send_pipeline.sender_for(user)
    except send_pipeline.SendRefused as exc:
        raise SendBlocked(str(exc), 409)
    contact = rec['contact']
    if contact is None:
        contact = founder_investors.contact_for(venture.owner_user_id, rec['email'])
        db.session.commit()
    founder = (getattr(user, 'full_name', '') or '').strip() or 'The founder'
    url = founder_investors.unsubscribe_url(venture, rec['email'])
    html = founder_investors._html(action.body) + founder_investors.on_behalf_html(founder)
    action_id = action.id
    from app.services.outreach_email_service import send_outreach_email
    res = send_outreach_email(simulation_id=venture.id, contact_id=contact.id, subject=action.subject or venture.name,
                              html_body=html, from_email=sender['from_email'], from_name=founder,
                              action_id=action_id, api_key=sender['api_key'], unsubscribe=True,
                              reply_to=(user.email or '').strip() or None, unsubscribe_url=url)
    action = db.session.get(type(action), action_id)
    if res.get('status') != 'sent':
        reason = str(res.get('reason') or res.get('status') or 'not sent')
        founder_ventures.log_event(venture.id, 'send_skipped', '%s: %s' % (action_id, reason), user_id=user.id)
        db.session.commit()
        labels = {'no_postal_address': 'Add your mailing address in Settings first; every email carries it.',
                  'do_not_contact': 'This person asked not to be emailed.'}
        msg = labels.get(reason) or ('Not sent: %s.' % (
            'they unsubscribed or are on an opt-out list' if reason.startswith('suppressed')
            else reason.replace('_', ' ')))
        raise SendBlocked(msg, 409)
    action.status, action.sent_at, action.level_applied = 'sent', now, gate['level']
    AuditLog.log('founder_outreach_sent', user_id=user.id, resource_id=venture.id,
                 metadata={'action_id': action.id, 'action_type': action.action_type, 'level': gate['level'],
                           'relationship': gate['relationship'], 'exemption': gate['exemption'],
                           'email_log_id': res.get('email_log_id')})
    try:
        from app.services import founder_stream
        founder_stream.publish(venture.id, 'outreach_sent', {
            'plan_step_id': action.plan_step_id, 'action_id': action.id, 'actor_user_id': user.id,
            'actor_kind': 'founder', 'note': 'Sent on your behalf by SimiSI: %s' % (action.subject or '')[:200]},
            commit=False)
    except Exception as exc:
        logger.debug('founder stream publish skipped: %s', exc)
    db.session.commit()
    try:
        from app.services import founder_alerts
        founder_alerts.check_action(action)
    except Exception as exc:
        logger.error('founder alert check failed for %s: %s', action.id, exc)
    return {'ok': True, 'delivery': 'email', 'email_log_id': res.get('email_log_id'),
            'message': 'Sent on your behalf by SimiSI. Replies go to %s.' % (user.email or 'you')}
