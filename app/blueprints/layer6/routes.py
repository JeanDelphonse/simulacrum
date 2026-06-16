"""
Layer 6 API — Autonomous Growth Orchestrator endpoints.
All routes are mounted under /api/simulations/<sim_id>/layer6/
"""
from __future__ import annotations
from datetime import datetime, timedelta
from flask import request, jsonify, Response, stream_with_context
from flask_login import login_required, current_user
from app.blueprints.layer6 import layer6_bp
from app.extensions import db
from app.models.simulation import Simulation
from app.models.layer6 import (
    Layer6Config, Layer6Cycle, Layer6ActionQueue,
    Layer6Outcome, Layer6Momentum, Layer6ExecutionLog, Layer6ShareToken,
    CyclePosteriorSnapshot,
)
from app.models.audit_log import AuditLog


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_sim_or_404(sim_id: str):
    sim = Simulation.query.get(sim_id)
    if not sim:
        return None, jsonify({'error': 'Simulation not found'}), 404
    if sim.user_id == current_user.id:
        return sim, None, None
    # Allow active advisors read-only access on GET requests
    if request.method == 'GET':
        from app.models.partner import ReferralPartner, AdvisorAccess
        partner = ReferralPartner.query.filter_by(
            user_id=current_user.id, status='active',
        ).first()
        if partner:
            access = AdvisorAccess.query.filter_by(
                partner_id=partner.id, simulation_id=sim_id, revoked_at=None,
            ).first()
            if access:
                return sim, None, None
    return None, jsonify({'error': 'Forbidden'}), 403


def _get_config_or_404(sim_id: str):
    cfg = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    if not cfg:
        return None, jsonify({'error': 'Layer 6 not set up for this simulation'}), 404
    return cfg, None, None


# ---------------------------------------------------------------------------
# Setup & Config
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/setup', methods=['POST'])
@login_required
def setup(sim_id):
    """Complete autonomy boundary setup — creates layer6_configs record."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    if Layer6Config.query.filter_by(simulation_id=sim_id).first():
        return jsonify({'error': 'Layer 6 already set up. Use PUT /config to update.'}), 409

    data = request.get_json(force=True) or {}

    cfg = Layer6Config(simulation_id=sim_id)
    _apply_config(cfg, data)
    db.session.add(cfg)

    AuditLog.log('layer6_setup', user_id=current_user.id, resource_id=sim_id, metadata={'cadence': cfg.cadence})
    db.session.commit()
    return jsonify(cfg.to_dict()), 201


@layer6_bp.route('/<sim_id>/layer6/config', methods=['GET'])
@login_required
def get_config(sim_id):
    """Get current autonomy boundary configuration."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    cfg, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code
    return jsonify(cfg.to_dict()), 200


@layer6_bp.route('/<sim_id>/layer6/config', methods=['PUT'])
@login_required
def update_config(sim_id):
    """Update any autonomy boundary setting — takes effect next cycle."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    cfg, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}
    _apply_config(cfg, data)
    AuditLog.log('layer6_config_update', user_id=current_user.id, resource_id=sim_id, metadata=data)
    db.session.commit()
    return jsonify(cfg.to_dict()), 200


def _apply_config(cfg: Layer6Config, data: dict) -> None:
    valid_cadences = {Layer6Config.CADENCE_DAILY, Layer6Config.CADENCE_THREE_DAYS,
                      Layer6Config.CADENCE_WEEKLY}
    valid_scopes = {Layer6Config.CONTACT_SCOPE_UPLOADED, Layer6Config.CONTACT_SCOPE_LINKEDIN,
                    Layer6Config.CONTACT_SCOPE_ANY}

    if 'channel_approvals' in data:
        cfg.channel_approvals = data['channel_approvals']
    if 'spend_ceiling' in data:
        cfg.spend_ceiling = max(0, float(data['spend_ceiling']))
    if 'contact_scope' in data and data['contact_scope'] in valid_scopes:
        cfg.contact_scope = data['contact_scope']
    if 'blocked_actions' in data and isinstance(data['blocked_actions'], list):
        cfg.blocked_actions = data['blocked_actions']
    if 'cadence' in data and data['cadence'] in valid_cadences:
        cfg.cadence = data['cadence']
    if 'actions_per_cycle' in data:
        cfg.actions_per_cycle = max(1, min(10, int(data['actions_per_cycle'])))
    if 'quiet_hours' in data:
        cfg.quiet_hours = data['quiet_hours']
    if 'explore_phase_end_month' in data:
        cfg.explore_phase_end_month = max(1, int(data['explore_phase_end_month']))
    if 'trust_level' in data and data['trust_level'] in {'full_auto', 'balanced', 'review_all'}:
        cfg.trust_level = data['trust_level']


# ---------------------------------------------------------------------------
# Trust Controls (ENH-09)
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/trust-level', methods=['PUT'])
@login_required
def set_trust_level(sim_id):
    """Set trust_level and bulk-update channel_approvals from preset."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    if sim.user_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403

    cfg, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}
    level = data.get('trust_level', '').strip()
    valid = {'full_auto', 'balanced', 'review_all'}
    if level not in valid:
        return jsonify({'error': f'trust_level must be one of: {", ".join(valid)}'}), 400

    from app.services.layer6 import TRUST_PRESETS
    cfg.trust_level = level
    cfg.channel_approvals = TRUST_PRESETS[level]
    db.session.commit()
    return jsonify(cfg.to_dict()), 200


# ---------------------------------------------------------------------------
# ROI Card (ENH-06)
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/roi-card', methods=['GET'])
@login_required
def roi_card(sim_id):
    """Return ROI metrics for the share card."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.models.layer6 import Layer6Outcome
    from app.extensions import db as _db
    from sqlalchemy import func

    total_income = float(
        _db.session.query(func.sum(Layer6Outcome.actual_income))
        .filter_by(simulation_id=sim_id).scalar() or 0
    )
    cost_usd = (sim.amount_charged_cents or 0) / 100
    roi_ratio = round(total_income / cost_usd, 1) if cost_usd > 0 else 0.0

    return jsonify({
        'simulation_id': sim_id,
        'simulation_name': sim.name,
        'total_income': total_income,
        'simulation_cost_usd': cost_usd,
        'roi_ratio': roi_ratio,
    }), 200


# ---------------------------------------------------------------------------
# Cycle execution
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/run', methods=['POST'])
@login_required
def run_cycle(sim_id):
    """Manually trigger an orchestrator cycle outside the cadence schedule."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    _, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code

    from app.services.layer6 import run_orchestrator_cycle
    from app.models.layer6 import Layer6ActionQueue
    try:
        cycle_data = run_orchestrator_cycle(sim_id, force_rerun=True)
        if cycle_data.get('skipped'):
            return jsonify(cycle_data), 200
        actions = Layer6ActionQueue.query.filter_by(
            cycle_id=cycle_data['id']
        ).order_by(Layer6ActionQueue.priority_score.desc()).all()
        cycle_data['action_queue'] = [a.to_dict() for a in actions]
        AuditLog.log('layer6_manual_run', user_id=current_user.id, resource_id=sim_id,
                     metadata={'cycle_number': cycle_data.get('cycle_number')})
        return jsonify(cycle_data), 200
    except Exception as exc:
        logger.exception('run_cycle failed for %s: %s', sim_id, exc)
        return jsonify({'error': str(exc)}), 500


@layer6_bp.route('/<sim_id>/layer6/pause', methods=['POST'])
@login_required
def pause(sim_id):
    """Pause the orchestrator — no cycles run until resumed."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    cfg, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code

    cfg.is_active = False
    log = Layer6ExecutionLog(
        simulation_id=sim_id,
        event_type=Layer6ExecutionLog.EVENT_PAUSED,
        actor=Layer6ExecutionLog.ACTOR_USER,
        reasoning='User paused the orchestrator.',
    )
    db.session.add(log)
    AuditLog.log('layer6_pause', user_id=current_user.id, resource_id=sim_id)
    db.session.commit()
    return jsonify({'status': 'paused'}), 200


@layer6_bp.route('/<sim_id>/layer6/resume', methods=['POST'])
@login_required
def resume(sim_id):
    """Resume a paused orchestrator."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    cfg, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code

    cfg.is_active = True
    log = Layer6ExecutionLog(
        simulation_id=sim_id,
        event_type=Layer6ExecutionLog.EVENT_RESUMED,
        actor=Layer6ExecutionLog.ACTOR_USER,
        reasoning='User resumed the orchestrator.',
    )
    db.session.add(log)
    AuditLog.log('layer6_resume', user_id=current_user.id, resource_id=sim_id)
    db.session.commit()
    return jsonify({'status': 'active'}), 200


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/cycles', methods=['GET'])
@login_required
def list_cycles(sim_id):
    """List all orchestrator cycles with reasoning and action counts."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()
    return jsonify([c.to_dict() for c in cycles]), 200


@layer6_bp.route('/<sim_id>/layer6/cycles/summary', methods=['GET'])
@login_required
def list_cycles_summary(sim_id):
    """Paginated cycle list with per-cycle activity counts for the Cycle accordion (FR-CYC-14)."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    page = max(0, int(request.args.get('page', 0)))
    per_page = 10

    cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).offset(page * per_page).limit(per_page + 1).all()

    has_more = len(cycles) > per_page
    cycles = cycles[:per_page]

    from app.models.outreach_email import EmailLog
    from app.models.contact import Contact

    result = []
    for c in cycles:
        agent_action_ids = [
            r.agent_action_id
            for r in Layer6ActionQueue.query.filter_by(cycle_id=c.id)
            .with_entities(Layer6ActionQueue.agent_action_id).all()
            if r.agent_action_id
        ]
        emails_sent = 0
        replies = 0
        if agent_action_ids:
            emails_sent = EmailLog.query.filter(
                EmailLog.action_id.in_(agent_action_ids)
            ).count()
            replies = EmailLog.query.filter(
                EmailLog.action_id.in_(agent_action_ids),
                EmailLog.replied_at.isnot(None),
            ).count()
        contacts_added = Contact.query.filter_by(source_cycle_id=c.id).count()

        row = c.to_dict()
        row['emails_sent'] = emails_sent
        row['replies'] = replies
        row['contacts_added'] = contacts_added
        result.append(row)

    return jsonify({'cycles': result, 'has_more': has_more}), 200


@layer6_bp.route('/<sim_id>/layer6/cycles/<cycle_id>/activity', methods=['GET'])
@login_required
def cycle_activity(sim_id, cycle_id):
    """Full activity detail for one cycle: agents, emails, contacts, steps, bookings."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    if not cycle:
        return jsonify({'error': 'Cycle not found'}), 404

    from app.models.outreach_email import EmailLog
    from app.models.contact import Contact
    from app.models.action_step import ActionStep
    from app.models.artifact import ArtifactVersion

    queue_items = Layer6ActionQueue.query.filter_by(cycle_id=cycle_id).order_by(
        Layer6ActionQueue.created_at
    ).all()
    agent_action_ids = [q.agent_action_id for q in queue_items if q.agent_action_id]

    artifact_action_ids = set()
    if agent_action_ids:
        for av in ArtifactVersion.query.filter(
            ArtifactVersion.action_id.in_(agent_action_ids),
            ArtifactVersion.is_current == True,
        ).with_entities(ArtifactVersion.action_id).all():
            artifact_action_ids.add(av.action_id)

    agents_data = []
    for q in queue_items:
        d = q.to_dict()
        d['has_artifact'] = q.agent_action_id in artifact_action_ids
        agents_data.append(d)

    # Emails sent via direct agent dispatch or executed follow-up steps
    emails_data = []
    executed_step_ids = []
    if queue_items:
        executed_step_ids = [
            s.id for s in ActionStep.query.filter(
                ActionStep.parent_action_id.in_([q.id for q in queue_items]),
                ActionStep.status == ActionStep.STATUS_EXECUTED,
            ).with_entities(ActionStep.id).all()
        ]

    email_filters = []
    if agent_action_ids:
        email_filters.append(EmailLog.action_id.in_(agent_action_ids))
    if executed_step_ids:
        email_filters.append(EmailLog.step_id.in_(executed_step_ids))

    if email_filters:
        emails = EmailLog.query.filter(db.or_(*email_filters)).order_by(EmailLog.sent_at).all()
        contact_ids = list({e.contact_id for e in emails})
        contacts_map = {}
        if contact_ids:
            for c in Contact.query.filter(Contact.id.in_(contact_ids)).all():
                contacts_map[c.id] = c
        for e in emails:
            ct = contacts_map.get(e.contact_id)
            row = e.to_dict()
            row['contact_name'] = ct.display_name if ct else 'Unknown'
            row['company'] = ct.company_name or '' if ct else ''
            if e.replied_at:
                row['display_status'] = 'replied'
            elif e.bounced_at:
                row['display_status'] = 'bounced'
            elif e.opened_at:
                row['display_status'] = 'opened'
            else:
                row['display_status'] = 'sent'
            emails_data.append(row)

    # Contacts added to CRM during this cycle
    contacts_added_data = []
    for c in Contact.query.filter_by(source_cycle_id=cycle_id).order_by(
        Contact.pipeline_stage.desc()
    ).all():
        initials = ((c.first_name or '')[:1] + (c.last_name or '')[:1]).upper() or '?'
        source_agent = ''
        if c.source_action_id:
            q = next((q for q in queue_items if q.agent_action_id == c.source_action_id), None)
            if q:
                source_agent = q.action_type.replace('_', ' ').title()
        contacts_added_data.append({
            'id': c.id,
            'name': c.display_name,
            'company': c.company_name or '',
            'initials': initials,
            'pipeline_stage': c.pipeline_stage,
            'source_agent': source_agent or 'Agent',
        })

    # Action steps: processed (executed/skipped) and upcoming (scheduled)
    steps_processed = []
    steps_upcoming = []
    if queue_items:
        queue_label_map = {q.id: q.action_type.replace('_', ' ').title() for q in queue_items}
        for s in ActionStep.query.filter(
            ActionStep.parent_action_id.in_([q.id for q in queue_items])
        ).order_by(ActionStep.scheduled_for).all():
            row = s.to_dict()
            row['agent_label'] = queue_label_map.get(s.parent_action_id, '')
            if s.status in ('executed', 'skipped'):
                steps_processed.append(row)
            elif s.status == 'scheduled':
                days_left = max(0, (s.scheduled_for - datetime.utcnow()).days)
                if days_left == 0:
                    urgency, countdown = 'high', 'Today'
                elif days_left == 1:
                    urgency, countdown = 'high', 'Tomorrow'
                elif days_left == 2:
                    urgency, countdown = 'high', 'in 2 days'
                elif days_left <= 5:
                    urgency, countdown = 'medium', f'in {days_left} days'
                else:
                    urgency, countdown = 'low', f'in {days_left} days'
                row['urgency'] = urgency
                row['urgency_label'] = {'high': 'Urgent', 'medium': 'Soon', 'low': 'Upcoming'}[urgency]
                row['countdown'] = countdown
                row['suggested_date'] = s.scheduled_for.strftime('%b %d')
                steps_upcoming.append(row)

    return jsonify({
        'agents': agents_data,
        'emails': emails_data,
        'contacts': contacts_added_data,
        'steps_processed': steps_processed,
        'steps_upcoming': steps_upcoming,
        'bookings': [],
    }), 200


@layer6_bp.route('/<sim_id>/layer6/cycles/<cycle_id>', methods=['GET'])
@login_required
def get_cycle(sim_id, cycle_id):
    """Get full detail for one cycle including all actions scored and dispatched."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    if not cycle:
        return jsonify({'error': 'Cycle not found'}), 404

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle_id).order_by(
        Layer6ActionQueue.priority_score.desc()
    ).all()

    data = cycle.to_dict()
    data['actions'] = [a.to_dict() for a in actions]
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# Action queue
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/queue', methods=['GET'])
@login_required
def get_queue(sim_id):
    """Get current action queue — queued, dispatched, escalated, complete."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    status_filter = request.args.get('status')
    q = Layer6ActionQueue.query.filter_by(simulation_id=sim_id)
    if status_filter:
        q = q.filter_by(status=status_filter)
    items = q.order_by(Layer6ActionQueue.created_at.desc()).limit(100).all()
    return jsonify([i.to_dict() for i in items]), 200


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/escalations', methods=['GET'])
@login_required
def list_escalations(sim_id):
    """Get all actions currently awaiting user approval."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    items = Layer6ActionQueue.query.filter_by(
        simulation_id=sim_id, status=Layer6ActionQueue.STATUS_ESCALATED
    ).order_by(Layer6ActionQueue.priority_score.desc()).all()
    return jsonify([i.to_dict() for i in items]), 200


@layer6_bp.route('/<sim_id>/layer6/escalations/<escalation_id>/approve', methods=['POST'])
@login_required
def approve_escalation(sim_id, escalation_id):
    """Approve an escalated action — dispatched in next cycle."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    item = Layer6ActionQueue.query.filter_by(id=escalation_id, simulation_id=sim_id).first()
    if not item or item.status != Layer6ActionQueue.STATUS_ESCALATED:
        return jsonify({'error': 'Escalated action not found'}), 404

    item.status = Layer6ActionQueue.STATUS_DISPATCHED
    item.dispatched_at = datetime.utcnow()

    log = Layer6ExecutionLog(
        simulation_id=sim_id,
        cycle_id=item.cycle_id,
        action_id=item.id,
        event_type=Layer6ExecutionLog.EVENT_APPROVED,
        actor=Layer6ExecutionLog.ACTOR_USER,
        reasoning='User approved escalated action for dispatch.',
    )
    db.session.add(log)
    AuditLog.log('layer6_escalation_approved', user_id=current_user.id, resource_id=sim_id,
                 metadata={'action_type': item.action_type})
    db.session.commit()

    from flask import current_app as _ca
    if _ca.config.get('REDIS_URL'):
        from app.tasks.layer6 import dispatch_layer6_action
        dispatch_layer6_action.delay(item.id)
    else:
        import threading
        from app.services.layer6 import _execute_action_sync
        _app = _ca._get_current_object()
        _item_id = item.id

        def _run():
            with _app.app_context():
                from app.models.layer6 import Layer6ActionQueue as _Q
                _execute_action_sync(_Q.query.get(_item_id))

        threading.Thread(target=_run, daemon=True).start()

    return jsonify(item.to_dict()), 200


@layer6_bp.route('/<sim_id>/layer6/escalations/<escalation_id>/reject', methods=['POST'])
@login_required
def reject_escalation(sim_id, escalation_id):
    """Reject an escalated action — adds to blocked list for this cycle."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    item = Layer6ActionQueue.query.filter_by(id=escalation_id, simulation_id=sim_id).first()
    if not item or item.status != Layer6ActionQueue.STATUS_ESCALATED:
        return jsonify({'error': 'Escalated action not found'}), 404

    item.status = Layer6ActionQueue.STATUS_REJECTED

    log = Layer6ExecutionLog(
        simulation_id=sim_id,
        cycle_id=item.cycle_id,
        action_id=item.id,
        event_type=Layer6ExecutionLog.EVENT_REJECTED,
        actor=Layer6ExecutionLog.ACTOR_USER,
        reasoning='User rejected escalated action.',
    )
    db.session.add(log)
    AuditLog.log('layer6_escalation_rejected', user_id=current_user.id, resource_id=sim_id,
                 metadata={'action_type': item.action_type})
    db.session.commit()
    return jsonify(item.to_dict()), 200


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/outcomes', methods=['GET'])
@login_required
def list_outcomes(sim_id):
    """Get all income outcome records across all layers."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    outcomes = Layer6Outcome.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Outcome.reporting_month.desc()
    ).all()
    return jsonify([o.to_dict() for o in outcomes]), 200


@layer6_bp.route('/<sim_id>/layer6/outcomes', methods=['POST'])
@login_required
def report_outcome(sim_id):
    """User reports actual income for a layer/stream/month."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}
    required = ('layer_number', 'reporting_month', 'actual_income')
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({'error': f'Missing fields: {missing}'}), 400

    # Validate reporting_month format YYYY-MM
    try:
        datetime.strptime(data['reporting_month'], '%Y-%m')
    except ValueError:
        return jsonify({'error': 'reporting_month must be YYYY-MM'}), 400

    # Look up projected income from income_streams for this layer
    from app.models.simulation import SimulationLayer, IncomeStream
    layer = SimulationLayer.query.filter_by(
        simulation_id=sim_id, layer_number=data['layer_number']
    ).first()
    projected = 0.0
    if layer:
        streams = IncomeStream.query.filter_by(layer_id=layer.id).all()
        projected = sum((s.est_monthly_low + s.est_monthly_high) / 2 for s in streams)

    actual = float(data['actual_income'])
    outcome = Layer6Outcome(
        simulation_id=sim_id,
        layer_number=int(data['layer_number']),
        income_stream_id=data.get('income_stream_id'),
        reporting_month=data['reporting_month'],
        actual_income=actual,
        projected_income=data.get('projected_income', projected),
        variance=actual - projected,
        reported_by=Layer6Outcome.REPORTED_BY_USER,
    )
    db.session.add(outcome)
    AuditLog.log('layer6_outcome_reported', user_id=current_user.id, resource_id=sim_id,
                 metadata={'layer': data['layer_number'], 'month': data['reporting_month'], 'actual': actual})
    db.session.commit()
    return jsonify(outcome.to_dict()), 201


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/momentum', methods=['GET'])
@login_required
def get_momentum(sim_id):
    """Get latest momentum snapshot — all leading indicators."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    snapshot = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()
    ).first()
    history = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()
    ).limit(30).all()
    return jsonify({
        'latest': snapshot.to_dict() if snapshot else None,
        'history': [s.to_dict() for s in history],
    }), 200


@layer6_bp.route('/<sim_id>/layer6/momentum', methods=['POST'])
@login_required
def update_momentum(sim_id):
    """Manually update today's momentum snapshot with reported leading indicators."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}
    today = datetime.utcnow().date()

    snapshot = Layer6Momentum.query.filter_by(
        simulation_id=sim_id, snapshot_date=today
    ).first()
    if not snapshot:
        snapshot = Layer6Momentum(simulation_id=sim_id, snapshot_date=today)
        db.session.add(snapshot)

    for field in ('email_list_size', 'linkedin_connections', 'course_enrollments',
                  'seo_organic_sessions', 'newsletter_subscribers', 'consulting_bookings_mo'):
        if field in data:
            setattr(snapshot, field, int(data[field]))
    for field in ('funnel_opt_in_rate', 'pipeline_value', 'investment_balance'):
        if field in data:
            setattr(snapshot, field, float(data[field]))

    db.session.commit()
    return jsonify(snapshot.to_dict()), 200


# ---------------------------------------------------------------------------
# Execution log
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/log', methods=['GET'])
@login_required
def get_log(sim_id):
    """Get full immutable audit log of all orchestrator decisions and user overrides."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 50)))
    offset = (page - 1) * per_page

    total = Layer6ExecutionLog.query.filter_by(simulation_id=sim_id).count()
    entries = Layer6ExecutionLog.query.filter_by(simulation_id=sim_id).order_by(
        Layer6ExecutionLog.created_at.desc()
    ).offset(offset).limit(per_page).all()

    return jsonify({
        'total': total,
        'page': page,
        'per_page': per_page,
        'entries': [e.to_dict() for e in entries],
    }), 200


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/dashboard', methods=['GET'])
@login_required
def get_dashboard(sim_id):
    """Single endpoint returning all Growth Command Center data — numbers, actions, momentum."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.services.layer6 import build_dashboard
    return jsonify(build_dashboard(sim_id)), 200


# ---------------------------------------------------------------------------
# Phase override
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/phase', methods=['PUT'])
@login_required
def override_phase(sim_id):
    """Manually override the explore/exploit phase transition."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code
    cfg, err, code = _get_config_or_404(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}
    phase = data.get('phase')
    if phase not in ('explore', 'exploit'):
        return jsonify({'error': "phase must be 'explore' or 'exploit'"}), 400

    if phase == 'exploit':
        # Force end of explore phase immediately
        cfg.explore_phase_end_month = 0
    else:
        # Reset back to exploration
        cfg.explore_phase_end_month = 99

    log = Layer6ExecutionLog(
        simulation_id=sim_id,
        event_type=Layer6ExecutionLog.EVENT_OVERRIDDEN,
        actor=Layer6ExecutionLog.ACTOR_USER,
        reasoning=f'User manually set phase to {phase}.',
    )
    db.session.add(log)
    AuditLog.log('layer6_phase_override', user_id=current_user.id, resource_id=sim_id, metadata={'phase': phase})
    db.session.commit()
    return jsonify({'phase': phase, 'explore_phase_end_month': cfg.explore_phase_end_month}), 200


# ---------------------------------------------------------------------------
# Cycle detail (with action_queue + execution_log)
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/cycles/<cycle_id>/detail', methods=['GET'])
@login_required
def cycle_detail(sim_id, cycle_id):
    """Full cycle data including action queue and execution log — used by diagram."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    if not cycle:
        return jsonify({'error': 'Cycle not found'}), 404

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle_id).order_by(
        Layer6ActionQueue.priority_score.desc()
    ).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle_id).order_by(
        Layer6ExecutionLog.created_at.asc()
    ).all()

    data = cycle.to_dict()
    data['action_queue'] = [a.to_dict() for a in actions]
    data['execution_log'] = [e.to_dict() for e in log_entries]
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# DAG — all action types with current statuses
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/dag', methods=['GET'])
@login_required
def get_dag(sim_id):
    """All 49 action types with their statuses for the DAG/swimlane visualization."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.services.claude import AGENT_ACTION_TYPES
    from app.models.agent_action import AgentAction
    from app.services.layer6 import ACTION_PREREQUISITES

    completed = {
        a.action_type for a in AgentAction.query.filter_by(
            simulation_id=sim_id, status=AgentAction.STATUS_COMPLETE,
        ).all()
    }
    in_flight = {
        q.action_type: q.status
        for q in Layer6ActionQueue.query.filter(
            Layer6ActionQueue.simulation_id == sim_id,
            Layer6ActionQueue.status.in_([
                Layer6ActionQueue.STATUS_DISPATCHED,
                Layer6ActionQueue.STATUS_ESCALATED,
                Layer6ActionQueue.STATUS_QUEUED,
            ])
        ).order_by(Layer6ActionQueue.created_at.desc()).all()
    }
    # Latest AgentAction record per action_type for editor pre-fill
    action_records = {}
    for a in AgentAction.query.filter_by(simulation_id=sim_id).order_by(AgentAction.created_at.desc()).all():
        if a.action_type not in action_records:
            action_records[a.action_type] = a

    nodes = []
    for layer_num, actions in AGENT_ACTION_TYPES.items():
        for action_type, action_def in actions.items():
            prereqs = ACTION_PREREQUISITES.get(action_type, [])
            if action_type in completed:
                status = 'complete'
            elif action_type in in_flight:
                status = in_flight[action_type]
            elif all(p in completed for p in prereqs):
                status = 'queued'
            else:
                status = 'blocked'
            rec = action_records.get(action_type)
            form = [
                {'key': f['key'], 'label': f['label'], 'required': f.get('required', False)}
                for f in action_def.get('prompt_form', [])
            ]
            nodes.append({
                'id': action_type,
                'label': action_def.get('label', action_type),
                'layer': layer_num,
                'status': status,
                'prerequisites': prereqs,
                'action_id': rec.id if rec else None,
                'user_inputs': rec.user_inputs if rec else {},
                'has_artifact': bool(rec and rec.artifact),
                'form': form,
            })

    return jsonify({'nodes': nodes}), 200


# ---------------------------------------------------------------------------
# Live Node Editor — rerun an action with updated inputs
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/actions/<action_id>/rerun', methods=['POST'])
@login_required
def rerun_agent_action(sim_id, action_id):
    """Re-run an agent action from the Live Node Editor; archives previous artifact."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.models.agent_action import AgentAction
    action = AgentAction.query.filter_by(id=action_id, simulation_id=sim_id).first()
    if not action:
        return jsonify({'error': 'Action not found'}), 404

    if action.status == AgentAction.STATUS_IN_PROGRESS:
        return jsonify({'error': 'Action is already running'}), 409

    data = request.get_json(force=True, silent=True) or {}

    if action.artifact:
        action.archived_artifact = action.artifact
        action.archived_at = datetime.utcnow()

    if data.get('user_inputs'):
        action.user_inputs = data['user_inputs']

    action.artifact = None
    action.status = AgentAction.STATUS_PENDING
    action.error_message = None
    action.completed_at = None
    db.session.commit()

    import threading
    from app.tasks.agent import execute_agent_action_task
    from flask import current_app
    app = current_app._get_current_object()
    action_id_str = action.id

    def _run():
        with app.app_context():
            execute_agent_action_task.apply(args=[action_id_str])

    threading.Thread(target=_run, daemon=True).start()

    AuditLog.log('layer6_action_rerun', user_id=current_user.id, resource_id=sim_id,
                 metadata={'action_id': action_id_str, 'action_type': action.action_type})
    return jsonify(action.to_dict()), 202


# ---------------------------------------------------------------------------
# Share tokens
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/share', methods=['POST'])
@login_required
def create_share_token(sim_id):
    """Generate a 30-day read-only share link for the orchestrator diagram."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}
    cycle_id = data.get('cycle_id') or None

    token = Layer6ShareToken(
        simulation_id=sim_id,
        cycle_id=cycle_id,
        created_by=current_user.id,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    db.session.add(token)
    db.session.commit()
    return jsonify(token.to_dict()), 201


# ---------------------------------------------------------------------------
# SSE stream — polling-based, no Redis required
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Agent Network Visualization — Node/Edge catalog + state computation
# ---------------------------------------------------------------------------

_NODE_CATALOG = [
    {'id':'L1','label':'Active Income Agent','tier':1,'color':'#0D1B3E','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'L2','label':'Leveraged Income Agent','tier':1,'color':'#1B3A6B','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'L3','label':'Productized Agent','tier':1,'color':'#0F7B72','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'L4','label':'Automated Residual Agent','tier':1,'color':'#0F7B72','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'L5','label':'Wealth Deployment Agent','tier':1,'color':'#C9952A','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'ORC','label':'L6 Orchestrator','tier':2,'color':'#FFFFFF','hub':True,'sub':False,'parent':None,'conditional':False},
    {'id':'DAG','label':'Dependency Graph','tier':3,'color':'#BA7517','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'BAY','label':'Bayesian Outcome Model','tier':3,'color':'#BA7517','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'EXP','label':'Explore/Exploit Engine','tier':3,'color':'#534AB7','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'ESC','label':'Escalation Queue','tier':3,'color':'#993C1D','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'GCC','label':'Growth Command Center','tier':4,'color':'#13A89E','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'NUM','label':'Numbers Panel','tier':4,'color':'#13A89E','hub':False,'sub':True,'parent':'GCC','conditional':False},
    {'id':'ACT','label':'Actions Panel','tier':4,'color':'#13A89E','hub':False,'sub':True,'parent':'GCC','conditional':False},
    {'id':'MOM','label':'Momentum Panel','tier':4,'color':'#13A89E','hub':False,'sub':True,'parent':'GCC','conditional':False},
    {'id':'APO','label':'Apollo','tier':5,'color':'#888888','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'LIN','label':'LinkedIn OAuth','tier':5,'color':'#888888','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'CON','label':'ConvertKit','tier':5,'color':'#888888','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'CAL','label':'Cal.com','tier':5,'color':'#888888','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'STR','label':'Stripe','tier':5,'color':'#888888','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'FIN','label':'Plaid / Alpaca','tier':5,'color':'#888888','hub':False,'sub':False,'parent':None,'conditional':True},
    {'id':'AUT','label':'Autonomy Boundary','tier':6,'color':'#1B3A6B','hub':False,'sub':False,'parent':None,'conditional':False},
    {'id':'CHN','label':'Channels','tier':6,'color':'#1B3A6B','hub':False,'sub':True,'parent':'AUT','conditional':False},
    {'id':'SPD','label':'Spend Ceiling','tier':6,'color':'#1B3A6B','hub':False,'sub':True,'parent':'AUT','conditional':False},
    {'id':'QHR','label':'Quiet Hours','tier':6,'color':'#1B3A6B','hub':False,'sub':True,'parent':'AUT','conditional':False},
    {'id':'LOG','label':'Execution Audit Log','tier':7,'color':'#444444','hub':False,'sub':False,'parent':None,'conditional':False},

    # ── L1 child agent nodes ──────────────────────────────────────────────────
    {'id':'CO','label':'Consulting Outreach','tier':1,'color':'#1a3a6b','hub':False,'sub':True,'parent':'L1','conditional':False,'action_type':'consulting_outreach'},
    {'id':'CEC','label':'Cold Email Campaign','tier':1,'color':'#1a3a6b','hub':False,'sub':True,'parent':'L1','conditional':False,'action_type':'cold_email_campaign'},
    {'id':'OEM','label':'Outreach Email','tier':1,'color':'#1a3a6b','hub':False,'sub':True,'parent':'L1','conditional':False,'action_type':'outreach_email'},
    {'id':'RC','label':'Rate Card','tier':1,'color':'#1a3a6b','hub':False,'sub':True,'parent':'L1','conditional':False,'action_type':'rate_card'},
    {'id':'BP','label':'Booking Page','tier':1,'color':'#1a3a6b','hub':False,'sub':True,'parent':'L1','conditional':False,'action_type':'booking_page'},
    {'id':'CPP','label':'Consulting Proposal','tier':1,'color':'#1a3a6b','hub':False,'sub':True,'parent':'L1','conditional':False,'action_type':'consulting_proposal'},

    # ── L2 child agent nodes ──────────────────────────────────────────────────
    {'id':'SPK','label':'Speaking Proposals','tier':1,'color':'#2a4a8b','hub':False,'sub':True,'parent':'L2','conditional':False,'action_type':'speaking_proposals'},
    {'id':'CCC','label':'Coaching Curriculum','tier':1,'color':'#2a4a8b','hub':False,'sub':True,'parent':'L2','conditional':False,'action_type':'coaching_curriculum'},
    {'id':'WKC','label':'Workshop Content','tier':1,'color':'#2a4a8b','hub':False,'sub':True,'parent':'L2','conditional':False,'action_type':'workshop_content'},
    {'id':'CTP','label':'Corp Training Proposal','tier':1,'color':'#2a4a8b','hub':False,'sub':True,'parent':'L2','conditional':False,'action_type':'corporate_training_proposal'},

    # ── L3 child agent nodes ──────────────────────────────────────────────────
    {'id':'CRF','label':'Course Framework','tier':1,'color':'#1a9e94','hub':False,'sub':True,'parent':'L3','conditional':False,'action_type':'course_framework'},
    {'id':'SAP','label':'Sales Page','tier':1,'color':'#1a9e94','hub':False,'sub':True,'parent':'L3','conditional':False,'action_type':'sales_page'},
    {'id':'LES','label':'Launch Email Seq.','tier':1,'color':'#1a9e94','hub':False,'sub':True,'parent':'L3','conditional':False,'action_type':'launch_email_sequence'},
    {'id':'MST','label':'Membership Structure','tier':1,'color':'#1a9e94','hub':False,'sub':True,'parent':'L3','conditional':False,'action_type':'membership_structure'},

    # ── L4 child agent nodes ──────────────────────────────────────────────────
    {'id':'SEO','label':'SEO Content Calendar','tier':1,'color':'#0ea89e','hub':False,'sub':True,'parent':'L4','conditional':False,'action_type':'seo_content_calendar'},
    {'id':'FDN','label':'Funnel Design','tier':1,'color':'#0ea89e','hub':False,'sub':True,'parent':'L4','conditional':False,'action_type':'funnel_design'},
    {'id':'NMO','label':'Newsletter Monetiz.','tier':1,'color':'#0ea89e','hub':False,'sub':True,'parent':'L4','conditional':False,'action_type':'newsletter_monetization'},
    {'id':'SPS','label':'SaaS Product Spec','tier':1,'color':'#0ea89e','hub':False,'sub':True,'parent':'L4','conditional':False,'action_type':'saas_product_spec'},

    # ── L5 child agent nodes ──────────────────────────────────────────────────
    {'id':'PAN','label':'Portfolio Analysis','tier':1,'color':'#d9a830','hub':False,'sub':True,'parent':'L5','conditional':False,'action_type':'portfolio_analysis'},
    {'id':'CGW','label':'Compound Growth','tier':1,'color':'#d9a830','hub':False,'sub':True,'parent':'L5','conditional':False,'action_type':'compound_growth'},
    {'id':'DCS','label':'DCA Schedule','tier':1,'color':'#d9a830','hub':False,'sub':True,'parent':'L5','conditional':False,'action_type':'dca_schedule'},
]

# Quick lookup: action_type → node id (for state computation)
_AGENT_NODE_MAP = {n['action_type']: n['id'] for n in _NODE_CATALOG if n.get('action_type')}
# Layer membership for each agent node id
_AGENT_PARENT_MAP = {n['id']: n['parent'] for n in _NODE_CATALOG if n.get('action_type')}

_EDGE_CATALOG = [
    {'id':'APO-ORC','source':'APO','target':'ORC','step':'harvest','bidirectional':False,'conditional':False,
     'fields':['open_rate','reply_rate','prospects_contacted','sequences_active','last_pulled_at']},
    {'id':'LIN-ORC','source':'LIN','target':'ORC','step':'harvest','bidirectional':False,'conditional':False,
     'fields':['connection_count','post_engagement_rate','profile_views_7d','last_pulled_at']},
    {'id':'CON-ORC','source':'CON','target':'ORC','step':'harvest','bidirectional':False,'conditional':False,
     'fields':['subscriber_count','funnel_conversion_rate','emails_sent_cycle','open_rate','last_pulled_at']},
    {'id':'CAL-ORC','source':'CAL','target':'ORC','step':'harvest','bidirectional':False,'conditional':False,
     'fields':['bookings_confirmed_cycle','bookings_pending','next_available_slot','last_pulled_at']},
    {'id':'STR-ORC','source':'STR','target':'ORC','step':'harvest','bidirectional':False,'conditional':False,
     'fields':['revenue_confirmed_cycle','refunds_cycle','active_subscriptions','last_pulled_at']},
    {'id':'FIN-ORC','source':'FIN','target':'ORC','step':'harvest','bidirectional':False,'conditional':True,
     'fields':['investment_balance','portfolio_return_ytd','dca_contributions_cycle','last_pulled_at']},
    {'id':'ORC-BAY','source':'ORC','target':'BAY','step':'score','bidirectional':True,'conditional':False,
     'fields':['layer6_momentum_snapshot','layer6_outcomes_array']},
    {'id':'BAY-ORC','source':'BAY','target':'ORC','step':'score','bidirectional':True,'conditional':False,
     'fields':['ranked_streams','phase_recommendation']},
    {'id':'ORC-DAG','source':'ORC','target':'DAG','step':'score','bidirectional':True,'conditional':False,
     'fields':['action_queue']},
    {'id':'DAG-ORC','source':'DAG','target':'ORC','step':'score','bidirectional':True,'conditional':False,
     'fields':['eligible_actions','critical_path_ids']},
    {'id':'ORC-EXP','source':'ORC','target':'EXP','step':'score','bidirectional':True,'conditional':False,
     'fields':['cycle_number','explore_phase_end_month']},
    {'id':'EXP-ORC','source':'EXP','target':'ORC','step':'score','bidirectional':True,'conditional':False,
     'fields':['phase','diversity_required','min_per_layer','transition_countdown']},
    {'id':'AUT-ORC','source':'AUT','target':'ORC','step':'schedule','bidirectional':False,'conditional':False,
     'fields':['approved_channels','spend_ceiling','contact_scope','blocked_action_types','quiet_hours']},
    {'id':'ORC-L1','source':'ORC','target':'L1','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['actions_dispatched']},
    {'id':'L1-ORC','source':'L1','target':'ORC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['action_id','status','outcome_summary','artifact_ids','completed_at']},
    {'id':'ORC-L2','source':'ORC','target':'L2','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['actions_dispatched']},
    {'id':'L2-ORC','source':'L2','target':'ORC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['action_id','status','outcome_summary','artifact_ids','completed_at']},
    {'id':'ORC-L3','source':'ORC','target':'L3','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['actions_dispatched']},
    {'id':'L3-ORC','source':'L3','target':'ORC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['action_id','status','outcome_summary','artifact_ids','completed_at']},
    {'id':'ORC-L4','source':'ORC','target':'L4','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['actions_dispatched']},
    {'id':'L4-ORC','source':'L4','target':'ORC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['action_id','status','outcome_summary','artifact_ids','completed_at']},
    {'id':'ORC-L5','source':'ORC','target':'L5','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['actions_dispatched']},
    {'id':'L5-ORC','source':'L5','target':'ORC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['action_id','status','outcome_summary','artifact_ids','completed_at']},
    {'id':'ORC-ESC','source':'ORC','target':'ESC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['escalated_actions']},
    {'id':'ESC-ORC','source':'ESC','target':'ORC','step':'schedule','bidirectional':True,'conditional':False,
     'fields':['approved_ids','rejected_ids','decision_timestamp']},
    {'id':'ORC-GCC','source':'ORC','target':'GCC','step':'report','bidirectional':False,'conditional':False,
     'fields':['cycle_summary','momentum_snapshot','income_outcomes']},
    {'id':'GCC-NUM','source':'GCC','target':'NUM','step':'report','bidirectional':False,'conditional':False,
     'fields':['income_per_layer','velocity_rate','cumulative_cash']},
    {'id':'GCC-ACT','source':'GCC','target':'ACT','step':'report','bidirectional':False,'conditional':False,
     'fields':['execution_log_entries','escalation_queue_items']},
    {'id':'GCC-MOM','source':'GCC','target':'MOM','step':'report','bidirectional':False,'conditional':False,
     'fields':['email_list_size','linkedin_connections','course_enrollments','funnel_opt_in_rate',
               'seo_organic_sessions','newsletter_subscribers','investment_balance','consulting_bookings_mo']},
    {'id':'ORC-LOG','source':'ORC','target':'LOG','step':'report','bidirectional':False,'conditional':False,
     'fields':['event_type','actor','action_id','reasoning','created_at']},

    # ── L1 → agent children ───────────────────────────────────────────────────
    {'id':'L1-CO', 'source':'L1','target':'CO', 'step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L1-CEC','source':'L1','target':'CEC','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L1-OEM','source':'L1','target':'OEM','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L1-RC', 'source':'L1','target':'RC', 'step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L1-BP', 'source':'L1','target':'BP', 'step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L1-CPP','source':'L1','target':'CPP','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},

    # ── L2 → agent children ───────────────────────────────────────────────────
    {'id':'L2-SPK','source':'L2','target':'SPK','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L2-CCC','source':'L2','target':'CCC','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L2-WKC','source':'L2','target':'WKC','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L2-CTP','source':'L2','target':'CTP','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},

    # ── L3 → agent children ───────────────────────────────────────────────────
    {'id':'L3-CRF','source':'L3','target':'CRF','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L3-SAP','source':'L3','target':'SAP','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L3-LES','source':'L3','target':'LES','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L3-MST','source':'L3','target':'MST','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},

    # ── L4 → agent children ───────────────────────────────────────────────────
    {'id':'L4-SEO','source':'L4','target':'SEO','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L4-FDN','source':'L4','target':'FDN','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L4-NMO','source':'L4','target':'NMO','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L4-SPS','source':'L4','target':'SPS','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},

    # ── L5 → agent children ───────────────────────────────────────────────────
    {'id':'L5-PAN','source':'L5','target':'PAN','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L5-CGW','source':'L5','target':'CGW','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},
    {'id':'L5-DCS','source':'L5','target':'DCS','step':'schedule','bidirectional':False,'conditional':False,'fields':['action_type','status']},

    # ── Prerequisite dependency edges (DAG flow across layers) ────────────────
    {'id':'CO-SPK', 'source':'CO', 'target':'SPK','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'CCC-CRF','source':'CCC','target':'CRF','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'CRF-SAP','source':'CRF','target':'SAP','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'SAP-LES','source':'SAP','target':'LES','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'SAP-SEO','source':'SAP','target':'SEO','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'LES-FDN','source':'LES','target':'FDN','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'FDN-NMO','source':'FDN','target':'NMO','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'FDN-PAN','source':'FDN','target':'PAN','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'PAN-CGW','source':'PAN','target':'CGW','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
    {'id':'PAN-DCS','source':'PAN','target':'DCS','step':'schedule','bidirectional':False,'conditional':False,'fields':['unlocks']},
]


def _node_states_for_cycle(cycle, actions, log_entries, momentum, config, fintech_on):
    states = {}
    is_done = cycle.cycle_completed_at is not None
    has_score = cycle.actions_scored > 0

    layer_acts = {i: [] for i in range(1, 6)}
    for a in actions:
        if a.source_layer in layer_acts:
            layer_acts[a.source_layer].append(a)

    for ln in range(1, 6):
        acts = layer_acts[ln]
        if not acts:
            st = 'idle'
        elif any(a.status == 'complete' for a in acts):
            st = 'complete'
        elif any(a.status == 'dispatched' for a in acts):
            st = 'running'
        elif any(a.status == 'escalated' for a in acts):
            st = 'escalated'
        else:
            st = 'active'
        states[f'L{ln}'] = {'status': st, 'badge_count': len([a for a in acts if a.status in ('complete','dispatched')])}

    states['ORC'] = {'status': 'complete' if is_done else ('running' if actions else 'idle'), 'badge_count': 0}

    eng_st = 'complete' if (is_done and has_score) else ('active' if has_score else 'idle')
    for nid in ('DAG', 'BAY', 'EXP'):
        states[nid] = {'status': eng_st, 'badge_count': 0}

    esc_ct = len([a for a in actions if a.status == 'escalated'])
    states['ESC'] = {'status': 'escalated' if esc_ct else ('complete' if is_done else 'idle'), 'badge_count': esc_ct}

    gcc_st = 'complete' if is_done else 'idle'
    for nid in ('GCC', 'NUM', 'ACT', 'MOM'):
        states[nid] = {'status': gcc_st, 'badge_count': 0}

    has_mom = momentum is not None
    con_st = 'complete' if (is_done and has_mom) else 'idle'
    for nid in ('APO', 'LIN', 'CON', 'CAL', 'STR'):
        states[nid] = {'status': con_st, 'badge_count': 0}
    states['FIN'] = {'status': con_st if fintech_on else 'idle', 'badge_count': 0, 'locked': not fintech_on}

    aut_st = 'active' if config else 'idle'
    for nid in ('AUT', 'CHN', 'SPD', 'QHR'):
        states[nid] = {'status': aut_st, 'badge_count': 0}

    states['LOG'] = {'status': 'complete' if log_entries else 'idle', 'badge_count': len(log_entries)}

    # Agent child node states — derived from action queue entries by action_type
    _status_map = {
        'complete': 'complete', 'dispatched': 'running',
        'escalated': 'escalated', 'failed': 'error',
        'queued': 'active',
    }
    for a in actions:
        nid = _AGENT_NODE_MAP.get(a.action_type)
        if nid:
            states[nid] = {
                'status': _status_map.get(a.status, 'active'),
                'badge_count': 1 if a.status == 'complete' else 0,
            }
    # Default idle for any agent node not seen in current cycle's queue
    for nid in _AGENT_NODE_MAP.values():
        if nid not in states:
            states[nid] = {'status': 'idle', 'badge_count': 0}

    return states


def _edge_states_for_cycle(cycle, actions, log_entries, momentum, fintech_on):
    states = {}
    is_done = cycle.cycle_completed_at is not None
    has_score = cycle.actions_scored > 0
    has_mom = momentum is not None

    for eid in ('APO-ORC', 'LIN-ORC', 'CON-ORC', 'CAL-ORC', 'STR-ORC'):
        states[eid] = {'active': is_done and has_mom, 'error': False}
    states['FIN-ORC'] = {'active': is_done and has_mom and fintech_on, 'error': False, 'locked': not fintech_on}

    for eid in ('ORC-BAY', 'BAY-ORC', 'ORC-DAG', 'DAG-ORC', 'ORC-EXP', 'EXP-ORC'):
        states[eid] = {'active': has_score, 'error': False}

    states['AUT-ORC'] = {'active': True, 'error': False}

    dispatched_layers = {a.source_layer for a in actions if a.status in ('dispatched', 'complete', 'queued', 'escalated')}
    for ln in range(1, 6):
        active = ln in dispatched_layers
        states[f'ORC-L{ln}'] = {'active': active, 'error': False}
        states[f'L{ln}-ORC'] = {'active': active and is_done, 'error': False}

    has_esc = any(a.status == 'escalated' for a in actions)
    states['ORC-ESC'] = {'active': has_esc, 'error': False}
    states['ESC-ORC'] = {'active': has_esc, 'error': False}

    for eid in ('ORC-GCC', 'GCC-NUM', 'GCC-ACT', 'GCC-MOM'):
        states[eid] = {'active': is_done, 'error': False}

    states['ORC-LOG'] = {'active': bool(log_entries), 'error': False}

    # Layer → agent child edges and prerequisite cross-edges
    dispatched_types = {a.action_type for a in actions if a.status in ('dispatched', 'complete', 'queued', 'escalated')}
    complete_types   = {a.action_type for a in actions if a.status == 'complete'}

    # Layer-to-agent edges: active when action was dispatched this cycle
    for action_type, nid in _AGENT_NODE_MAP.items():
        parent = _AGENT_PARENT_MAP.get(nid, '')
        edge_id = f'{parent}-{nid}'
        states[edge_id] = {'active': action_type in dispatched_types, 'error': False}

    # Prerequisite edges: active when source is complete and target was dispatched
    _prereq_edges = [
        ('CO-SPK',  'consulting_outreach',     'speaking_proposals'),
        ('CCC-CRF', 'coaching_curriculum',      'course_framework'),
        ('CRF-SAP', 'course_framework',         'sales_page'),
        ('SAP-LES', 'sales_page',               'launch_email_sequence'),
        ('SAP-SEO', 'sales_page',               'seo_content_calendar'),
        ('LES-FDN', 'launch_email_sequence',     'funnel_design'),
        ('FDN-NMO', 'funnel_design',             'newsletter_monetization'),
        ('FDN-PAN', 'funnel_design',             'portfolio_analysis'),
        ('PAN-CGW', 'portfolio_analysis',        'compound_growth'),
        ('PAN-DCS', 'portfolio_analysis',        'dca_schedule'),
    ]
    for eid, src_type, tgt_type in _prereq_edges:
        states[eid] = {
            'active': src_type in complete_types and tgt_type in dispatched_types,
            'error': False,
        }

    return states


def _build_node_payload(node_id, cycle, actions, log_entries, momentum, config):
    """Build inputs/outputs lists for the node inspector."""
    inputs, outputs = [], []
    layer_map = {'L1': 1, 'L2': 2, 'L3': 3, 'L4': 4, 'L5': 5}

    if node_id in layer_map:
        ln = layer_map[node_id]
        my_acts = [a for a in actions if a.source_layer == ln]
        inputs = [{'field': 'actions_dispatched', 'value': [a.action_type for a in my_acts if a.status in ('dispatched','complete')],
                   'type': 'array', 'from_node': 'ORC'}]
        outputs = [{'field': 'action_id', 'value': a.action_type, 'type': 'string', 'to_node': 'ORC',
                    'status': a.status, 'outcome': a.outcome_summary}
                   for a in my_acts]

    elif node_id == 'ORC':
        inputs = [
            {'field': 'actions_scored', 'value': cycle.actions_scored, 'type': 'int', 'from_node': 'BAY'},
            {'field': 'eligible_actions', 'value': cycle.actions_dispatched + cycle.actions_escalated, 'type': 'int', 'from_node': 'DAG'},
            {'field': 'phase', 'value': cycle.phase, 'type': 'string', 'from_node': 'EXP'},
        ]
        outputs = [
            {'field': 'actions_dispatched', 'value': cycle.actions_dispatched, 'type': 'int', 'to_node': 'L1-L5'},
            {'field': 'actions_escalated', 'value': cycle.actions_escalated, 'type': 'int', 'to_node': 'ESC'},
            {'field': 'cycle_reasoning', 'value': (cycle.orchestrator_reasoning or '')[:200], 'type': 'string', 'to_node': 'GCC'},
        ]

    elif node_id == 'BAY':
        outcomes = []
        for ln in range(1, 6):
            ln_acts = [a for a in actions if a.source_layer == ln]
            if ln_acts:
                top = max(ln_acts, key=lambda a: float(a.priority_score or 0))
                outcomes.append({'layer': ln, 'priority_score': float(top.priority_score or 0)})
        inputs = [{'field': 'layer6_momentum_snapshot', 'value': momentum.to_dict() if momentum else None,
                   'type': 'object', 'from_node': 'ORC'}]
        outputs = [{'field': 'ranked_streams', 'value': outcomes, 'type': 'array', 'to_node': 'ORC'}]

    elif node_id == 'DAG':
        eligible = [{'action_type': a.action_type, 'layer': a.source_layer, 'score': float(a.priority_score or 0)}
                    for a in sorted(actions, key=lambda a: float(a.priority_score or 0), reverse=True)[:10]]
        inputs = [{'field': 'action_queue', 'value': len(actions), 'type': 'int', 'from_node': 'ORC'}]
        outputs = [{'field': 'eligible_actions', 'value': eligible, 'type': 'array', 'to_node': 'ORC'}]

    elif node_id == 'EXP':
        inputs = [{'field': 'cycle_number', 'value': cycle.cycle_number, 'type': 'int', 'from_node': 'ORC'},
                  {'field': 'explore_phase_end_month', 'value': config.explore_phase_end_month if config else None, 'type': 'int', 'from_node': 'ORC'}]
        outputs = [{'field': 'phase', 'value': cycle.phase, 'type': 'string', 'to_node': 'ORC'},
                   {'field': 'diversity_required', 'value': cycle.phase == 'explore', 'type': 'bool', 'to_node': 'ORC'}]

    elif node_id == 'ESC':
        esc_acts = [a for a in actions if a.status == 'escalated']
        inputs = [{'field': 'escalated_actions', 'value': [a.action_type for a in esc_acts],
                   'type': 'array', 'from_node': 'ORC'}]
        outputs = [{'field': 'pending_count', 'value': len(esc_acts), 'type': 'int', 'to_node': 'ORC'}]

    elif node_id == 'LOG':
        inputs = [{'field': 'event_type', 'value': e.event_type, 'type': 'string', 'from_node': 'ORC',
                   'actor': e.actor, 'created_at': e.created_at.isoformat()}
                  for e in log_entries]
        outputs = []

    elif node_id in ('AUT', 'CHN', 'SPD', 'QHR') and config:
        inputs = []
        outputs = [
            {'field': 'approved_channels', 'value': config.channel_approvals, 'type': 'object', 'to_node': 'ORC'},
            {'field': 'spend_ceiling', 'value': float(config.spend_ceiling), 'type': 'float', 'to_node': 'ORC'},
            {'field': 'contact_scope', 'value': config.contact_scope, 'type': 'string', 'to_node': 'ORC'},
            {'field': 'quiet_hours', 'value': config.quiet_hours, 'type': 'object', 'to_node': 'ORC'},
        ]

    elif node_id in ('GCC', 'NUM', 'ACT', 'MOM'):
        inputs = [{'field': 'cycle_summary', 'value': {'cycle_number': cycle.cycle_number, 'phase': cycle.phase,
                   'actions_dispatched': cycle.actions_dispatched}, 'type': 'object', 'from_node': 'ORC'}]
        outputs = []

    elif node_id in ('APO', 'LIN', 'CON', 'CAL', 'STR', 'FIN') and momentum:
        field_map = {
            'APO': [('open_rate', 0.0), ('reply_rate', 0.0), ('prospects_contacted', 0)],
            'LIN': [('connection_count', momentum.linkedin_connections), ('post_engagement_rate', 0.0), ('profile_views_7d', 0)],
            'CON': [('subscriber_count', momentum.email_list_size), ('funnel_conversion_rate', float(momentum.funnel_opt_in_rate or 0))],
            'CAL': [('bookings_confirmed_cycle', momentum.consulting_bookings_mo), ('bookings_pending', 0)],
            'STR': [('revenue_confirmed_cycle', 0.0), ('refunds_cycle', 0.0)],
            'FIN': [('investment_balance', float(momentum.investment_balance or 0)), ('portfolio_return_ytd', 0.0)],
        }
        outputs = [{'field': f, 'value': v, 'type': 'float' if isinstance(v, float) else 'int', 'to_node': 'ORC'}
                   for f, v in field_map.get(node_id, [])]
        inputs = []

    return inputs, outputs


@layer6_bp.route('/<sim_id>/layer6/network', methods=['GET'])
@login_required
def get_network(sim_id):
    """Full 26-node network state for the Agent Network Visualization."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    cycle_id = request.args.get('cycle_id')
    if cycle_id:
        cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
        if not cycle:
            return jsonify({'error': 'Cycle not found'}), 404
    else:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()

    config = Layer6Config.query.filter_by(simulation_id=sim_id).first()

    if not cycle:
        nodes = [dict(n, status='idle', badge_count=0, locked=n['conditional']) for n in _NODE_CATALOG]
        edges = [dict(e, active=False, error=False, locked=e['conditional']) for e in _EDGE_CATALOG]
        return jsonify({'nodes': nodes, 'edges': edges, 'cycle': None}), 200

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).all()
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()).first()

    from app.models.platform_settings import PlatformSetting
    fintech_on = PlatformSetting.get('fintech_toggle', 'off') == 'on'

    nstates = _node_states_for_cycle(cycle, actions, log_entries, momentum, config, fintech_on)
    estats = _edge_states_for_cycle(cycle, actions, log_entries, momentum, fintech_on)

    nodes = [dict(n, **nstates.get(n['id'], {'status': 'idle', 'badge_count': 0})) for n in _NODE_CATALOG]
    edges = [dict(e, **estats.get(e['id'], {'active': False, 'error': False})) for e in _EDGE_CATALOG]

    cycle_data = cycle.to_dict()
    if config:
        cycle_data['transition_countdown'] = max(0, config.explore_phase_end_month - cycle.cycle_number)
        cycle_data['explore_phase_end_month'] = config.explore_phase_end_month

    return jsonify({'nodes': nodes, 'edges': edges, 'cycle': cycle_data}), 200


@layer6_bp.route('/<sim_id>/layer6/network/node/<node_id>', methods=['GET'])
@login_required
def get_network_node(sim_id, node_id):
    """Node inspector: overview, inputs, outputs, 10-cycle history."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    node_def = next((n for n in _NODE_CATALOG if n['id'] == node_id), None)
    if not node_def:
        return jsonify({'error': 'Unknown node'}), 404

    cycle_id = request.args.get('cycle_id')
    if cycle_id:
        cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    else:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()

    config = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    from app.models.platform_settings import PlatformSetting
    fintech_on = PlatformSetting.get('fintech_toggle', 'off') == 'on'

    if not cycle:
        return jsonify({'node': node_def, 'cycle': None, 'overview': {},
                        'inputs': [], 'outputs': [], 'history': []}), 200

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).order_by(
        Layer6ExecutionLog.created_at.asc()).all()
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()).first()

    nstates = _node_states_for_cycle(cycle, actions, log_entries, momentum, config, fintech_on)
    cur_state = nstates.get(node_id, {'status': 'idle', 'badge_count': 0})

    overview = {
        'node_id': node_id, 'label': node_def['label'], 'tier': node_def['tier'],
        'status': cur_state['status'],
        'cycle_number': cycle.cycle_number, 'phase': cycle.phase,
        'actions_scored': cycle.actions_scored if node_id == 'ORC' else None,
        'actions_dispatched': cycle.actions_dispatched if node_id == 'ORC' else cur_state.get('badge_count'),
        'actions_escalated': cycle.actions_escalated if node_id == 'ORC' else None,
        'last_computed': cycle.cycle_completed_at.isoformat() if cycle.cycle_completed_at else None,
    }

    inputs, outputs = _build_node_payload(node_id, cycle, actions, log_entries, momentum, config)

    # History: last 10 cycles
    all_cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()).limit(10).all()
    history = []
    for c in all_cycles:
        c_acts = Layer6ActionQueue.query.filter_by(cycle_id=c.id).all()
        c_logs = Layer6ExecutionLog.query.filter_by(cycle_id=c.id).all()
        c_states = _node_states_for_cycle(c, c_acts, c_logs, momentum, config, fintech_on)
        st = c_states.get(node_id, {'status': 'idle', 'badge_count': 0})
        history.append({
            'cycle_id': c.id, 'cycle_number': c.cycle_number,
            'date': c.cycle_started_at.isoformat(),
            'status': st['status'],
            'summary': f"Phase: {c.phase} · {c.actions_dispatched} dispatched · {c.actions_escalated} escalated",
        })

    return jsonify({'node': node_def, 'cycle': cycle.to_dict(), 'overview': overview,
                    'inputs': inputs, 'outputs': outputs, 'history': history}), 200


@layer6_bp.route('/<sim_id>/layer6/network/edge/<edge_id>', methods=['GET'])
@login_required
def get_network_edge(sim_id, edge_id):
    """Edge inspector: payload fields + values for selected cycle, plus 5-cycle history."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    edge_def = next((e for e in _EDGE_CATALOG if e['id'] == edge_id), None)
    if not edge_def:
        return jsonify({'error': 'Unknown edge'}), 404

    cycle_id = request.args.get('cycle_id')
    if cycle_id:
        cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    else:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()

    config = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    from app.models.platform_settings import PlatformSetting
    fintech_on = PlatformSetting.get('fintech_toggle', 'off') == 'on'

    if not cycle:
        return jsonify({'edge': edge_def, 'cycle': None, 'payload': [], 'history': []}), 200

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).all()
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()).first()
    estats = _edge_states_for_cycle(cycle, actions, log_entries, momentum, fintech_on)
    edge_state = estats.get(edge_id, {'active': False, 'error': False})

    # Build payload from real data
    payload = _build_edge_payload(edge_id, edge_def, cycle, actions, log_entries, momentum, config)

    # History: last 5 cycles
    recent_cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()).limit(5).all()
    history = []
    for c in recent_cycles:
        c_acts = Layer6ActionQueue.query.filter_by(cycle_id=c.id).all()
        c_logs = Layer6ExecutionLog.query.filter_by(cycle_id=c.id).all()
        c_estats = _edge_states_for_cycle(c, c_acts, c_logs, momentum, fintech_on)
        c_est = c_estats.get(edge_id, {'active': False})
        history.append({
            'cycle_id': c.id, 'cycle_number': c.cycle_number,
            'date': c.cycle_started_at.isoformat(),
            'active': c_est['active'],
            'summary': f"Cycle {c.cycle_number} · {'active' if c_est['active'] else 'idle'}",
        })

    return jsonify({'edge': edge_def, 'cycle': cycle.to_dict(),
                    'active': edge_state['active'], 'error': edge_state.get('error', False),
                    'payload': payload, 'history': history}), 200


def _build_edge_payload(edge_id, edge_def, cycle, actions, log_entries, momentum, config):
    """Extract real field values for an edge payload."""
    src, tgt = edge_def['source'], edge_def['target']
    fields = edge_def.get('fields', [])
    result = []

    layer_map = {'ORC-L1': 1, 'ORC-L2': 2, 'ORC-L3': 3, 'ORC-L4': 4, 'ORC-L5': 5,
                 'L1-ORC': 1, 'L2-ORC': 2, 'L3-ORC': 3, 'L4-ORC': 4, 'L5-ORC': 5}

    if edge_id in layer_map:
        ln = layer_map[edge_id]
        my_acts = [a for a in actions if a.source_layer == ln]
        if 'actions_dispatched' in fields:
            result.append({'field': 'actions_dispatched', 'value': [a.action_type for a in my_acts if a.status in ('dispatched','complete','queued')], 'type': 'array'})
        if 'action_id' in fields:
            for a in my_acts:
                result.append({'field': 'action_type', 'value': a.action_type, 'type': 'string'})
                result.append({'field': 'status', 'value': a.status, 'type': 'string'})
                result.append({'field': 'outcome_summary', 'value': a.outcome_summary or '—', 'type': 'string'})
        return result

    if src in ('APO', 'LIN', 'CON', 'CAL', 'STR', 'FIN') and tgt == 'ORC':
        mom_vals = {
            'APO': {'open_rate': 0.0, 'reply_rate': 0.0, 'prospects_contacted': 0, 'sequences_active': 0},
            'LIN': {'connection_count': getattr(momentum, 'linkedin_connections', 0) if momentum else 0,
                    'post_engagement_rate': 0.0, 'profile_views_7d': 0},
            'CON': {'subscriber_count': getattr(momentum, 'email_list_size', 0) if momentum else 0,
                    'funnel_conversion_rate': float(getattr(momentum, 'funnel_opt_in_rate', 0) or 0) if momentum else 0.0},
            'CAL': {'bookings_confirmed_cycle': getattr(momentum, 'consulting_bookings_mo', 0) if momentum else 0, 'bookings_pending': 0},
            'STR': {'revenue_confirmed_cycle': 0.0, 'refunds_cycle': 0.0, 'active_subscriptions': 0},
            'FIN': {'investment_balance': float(getattr(momentum, 'investment_balance', 0) or 0) if momentum else 0.0, 'portfolio_return_ytd': 0.0},
        }
        vals = mom_vals.get(src, {})
        for f in fields:
            v = vals.get(f)
            result.append({'field': f, 'value': v, 'type': type(v).__name__ if v is not None else 'null'})
        return result

    if edge_id in ('ORC-BAY', 'BAY-ORC', 'ORC-DAG', 'DAG-ORC', 'ORC-EXP', 'EXP-ORC'):
        cycle_vals = {
            'cycle_number': cycle.cycle_number, 'phase': cycle.phase,
            'explore_phase_end_month': config.explore_phase_end_month if config else None,
            'actions_scored': cycle.actions_scored, 'diversity_required': cycle.phase == 'explore',
            'min_per_layer': 1, 'transition_countdown': max(0, (config.explore_phase_end_month if config else 3) - cycle.cycle_number),
            'action_queue': len(actions), 'eligible_actions': cycle.actions_dispatched,
            'ranked_streams': [{'layer': a.source_layer, 'score': float(a.priority_score or 0)} for a in actions[:5]],
        }
        for f in fields:
            v = cycle_vals.get(f)
            result.append({'field': f, 'value': v, 'type': type(v).__name__ if v is not None else 'null'})
        return result

    if edge_id == 'AUT-ORC' and config:
        vals = {'approved_channels': config.channel_approvals, 'spend_ceiling': float(config.spend_ceiling),
                'contact_scope': config.contact_scope, 'blocked_action_types': config.blocked_actions,
                'quiet_hours': config.quiet_hours}
        for f in fields:
            v = vals.get(f)
            result.append({'field': f, 'value': v, 'type': type(v).__name__ if v is not None else 'null'})
        return result

    if edge_id == 'ORC-LOG':
        for e in log_entries[:10]:
            result.append({'field': 'event', 'value': {'event_type': e.event_type, 'actor': e.actor,
                          'reasoning': e.reasoning, 'created_at': e.created_at.isoformat()}, 'type': 'object'})
        return result

    for f in fields:
        result.append({'field': f, 'value': None, 'type': 'null'})
    return result


@layer6_bp.route('/<sim_id>/layer6/harvest/<connector_id>', methods=['POST'])
@login_required
def retry_connector(sim_id, connector_id):
    """Manually re-harvest a single connector (retry connection for error edges)."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    valid = {'APO', 'LIN', 'CON', 'CAL', 'STR', 'FIN'}
    if connector_id not in valid:
        return jsonify({'error': 'Unknown connector'}), 400

    return jsonify({'ok': True, 'connector_id': connector_id,
                    'message': f'{connector_id} harvest queued. Live integration required for real data.'}), 200


@layer6_bp.route('/<sim_id>/layer6/cycles/compare', methods=['GET'])
@login_required
def compare_cycles(sim_id):
    """Diff two cycles side-by-side: actions, connector values, outcomes, phase."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    a_id = request.args.get('a')
    b_id = request.args.get('b')
    if not a_id or not b_id:
        return jsonify({'error': 'Provide ?a=<cycle_id>&b=<cycle_id>'}), 400

    ca = Layer6Cycle.query.filter_by(id=a_id, simulation_id=sim_id).first()
    cb = Layer6Cycle.query.filter_by(id=b_id, simulation_id=sim_id).first()
    if not ca or not cb:
        return jsonify({'error': 'One or both cycles not found'}), 404

    def cycle_snapshot(c):
        acts = Layer6ActionQueue.query.filter_by(cycle_id=c.id).all()
        by_layer = {}
        for ln in range(1, 6):
            la = [a for a in acts if a.source_layer == ln]
            by_layer[f'L{ln}'] = {'dispatched': len([a for a in la if a.status in ('dispatched','complete')]),
                                   'escalated': len([a for a in la if a.status == 'escalated'])}
        return {'cycle_number': c.cycle_number, 'phase': c.phase,
                'actions_scored': c.actions_scored, 'actions_dispatched': c.actions_dispatched,
                'actions_escalated': c.actions_escalated, 'by_layer': by_layer,
                'started_at': c.cycle_started_at.isoformat()}

    return jsonify({'a': cycle_snapshot(ca), 'b': cycle_snapshot(cb)}), 200


@layer6_bp.route('/<sim_id>/layer6/diff', methods=['GET'])
@login_required
def cycle_diff(sim_id):
    """
    Cycle Diff — compare two cycles across 6 sections.
    ?current=<cycle_id>&prior=<cycle_id>
    Defaults: current=latest, prior=one before latest.
    """
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    all_cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()

    if len(all_cycles) < 1:
        return jsonify({'error': 'No cycles found'}), 404

    current_id = request.args.get('current') or all_cycles[0].id
    prior_id   = request.args.get('prior') or (all_cycles[1].id if len(all_cycles) > 1 else None)

    current_cycle = Layer6Cycle.query.filter_by(id=current_id, simulation_id=sim_id).first()
    prior_cycle   = Layer6Cycle.query.filter_by(id=prior_id, simulation_id=sim_id).first() if prior_id else None

    if not current_cycle:
        return jsonify({'error': 'Current cycle not found'}), 404

    # Helpers
    def _actions_for(cycle):
        if not cycle:
            return []
        return Layer6ActionQueue.query.filter(
            Layer6ActionQueue.cycle_id == cycle.id,
            Layer6ActionQueue.status.in_([
                Layer6ActionQueue.STATUS_DISPATCHED,
                Layer6ActionQueue.STATUS_COMPLETE,
                Layer6ActionQueue.STATUS_ESCALATED,
            ])
        ).all()

    cur_actions  = _actions_for(current_cycle)
    prior_actions = _actions_for(prior_cycle)

    cur_types   = {a.action_type for a in cur_actions}
    prior_types = {a.action_type for a in prior_actions}

    # Summary cards
    from app.models.contact import Contact
    cur_contacts  = Contact.query.filter_by(user_id=sim.user_id, source_cycle_id=current_id).count()
    prior_contacts = Contact.query.filter_by(user_id=sim.user_id, source_cycle_id=prior_id).count() if prior_id else 0

    summary_cards = [
        {
            'label': 'New Contacts',
            'current': cur_contacts,
            'prior': prior_contacts,
            'delta': cur_contacts - prior_contacts,
        },
        {
            'label': 'Emails Sent',
            'current': 0,
            'prior': 0,
            'delta': 0,
        },
        {
            'label': 'Replies',
            'current': 0,
            'prior': 0,
            'delta': 0,
        },
        {
            'label': 'Cycle Cost',
            'current': 0.0,
            'prior': 0.0,
            'delta': 0.0,
            'format': 'dollars',
        },
    ]

    # Section 1 — Agents dispatched
    agents_dispatched = []
    for at in cur_types | prior_types:
        if at in cur_types and at in prior_types:
            badge = 'rerun'
        elif at in cur_types:
            badge = 'new'
        else:
            badge = 'dropped'
        agents_dispatched.append({'action_type': at, 'badge': badge})
    agents_dispatched.sort(key=lambda x: (x['badge'] != 'new', x['badge'] != 'rerun', x['action_type']))

    # Section 2 — CRM contact changes
    new_prospects = []
    for c in Contact.query.filter_by(user_id=sim.user_id, source_cycle_id=current_id,
                                      pipeline_stage='prospect').limit(6).all():
        new_prospects.append({
            'name': c.display_name,
            'company': c.company_name or '',
            'source_agent': c.source or '',
        })

    # Advanced contacts: contacts with stage change activity during current cycle window
    advanced = []
    if current_cycle.cycle_started_at and current_cycle.cycle_completed_at:
        from app.models.contact import ContactActivity
        stage_changes = ContactActivity.query.filter(
            ContactActivity.simulation_id == sim_id,
            ContactActivity.activity_type == 'stage_changed',
            ContactActivity.activity_date >= current_cycle.cycle_started_at,
            ContactActivity.activity_date <= (current_cycle.cycle_completed_at or datetime.utcnow()),
        ).limit(5).all()
        for sc in stage_changes:
            c = Contact.query.get(sc.contact_id)
            if c:
                advanced.append({
                    'name': c.display_name,
                    'company': c.company_name or '',
                    'reason': f'{sc.pipeline_stage_from} → {sc.pipeline_stage_to}',
                })

    crm_contacts = {
        'new_prospects': new_prospects[:5],
        'new_prospects_overflow': max(0, len(new_prospects) - 5),
        'advanced': advanced,
    }

    # Section 3 — Email activity (placeholder until email campaigns are cycle-linked)
    email_activity = {'current': [], 'prior': []}

    # Section 4 — Bayesian score changes
    cur_snaps  = {s.action_type: float(s.posterior_value)
                  for s in CyclePosteriorSnapshot.query.filter_by(cycle_id=current_id).all()}
    prior_snaps = {s.action_type: float(s.posterior_value)
                   for s in CyclePosteriorSnapshot.query.filter_by(cycle_id=prior_id).all()} if prior_id else {}

    bayesian_scores = []
    for at in set(cur_snaps) | set(prior_snaps):
        cur_val   = cur_snaps.get(at, 0.5)
        prior_val = prior_snaps.get(at, 0.5)
        delta     = round(cur_val - prior_val, 4)
        bayesian_scores.append({
            'action_type': at,
            'current': round(cur_val, 4),
            'prior': round(prior_val, 4),
            'delta': delta,
            'direction': 'up' if delta > 0 else ('down' if delta < 0 else 'flat'),
        })
    bayesian_scores.sort(key=lambda x: abs(x['delta']), reverse=True)

    # Section 5 — Signals harvested
    POSITIVE_SIGNALS = {'email_replied', 'booking_created', 'page_view', 'chat_session',
                        'lead_captured', 'payment_received', 'completed'}
    NEGATIVE_SIGNALS = {'email_bounced', 'booking_cancelled', 'no_show', 'failed'}

    cur_logs = Layer6ExecutionLog.query.filter_by(cycle_id=current_id).all()
    signals = []
    for log in cur_logs:
        sentiment = ('positive' if log.event_type in POSITIVE_SIGNALS
                     else 'negative' if log.event_type in NEGATIVE_SIGNALS
                     else None)
        if sentiment:
            signals.append({
                'type': log.event_type,
                'sentiment': sentiment,
                'detail': (log.reasoning or '')[:120],
                'source': log.actor,
            })

    # Section 6 — Orchestrator reasoning
    reasoning = {
        'current': current_cycle.orchestrator_reasoning or '',
        'prior': prior_cycle.orchestrator_reasoning or '' if prior_cycle else '',
    }

    # Cycle selector options
    cycle_options = [
        {'id': c.id, 'label': f'Cycle {c.cycle_number} — {c.phase}', 'number': c.cycle_number}
        for c in all_cycles
    ]

    return jsonify({
        'current_cycle': current_cycle.to_dict(),
        'prior_cycle': prior_cycle.to_dict() if prior_cycle else None,
        'cycle_options': cycle_options,
        'summary_cards': summary_cards,
        'agents_dispatched': agents_dispatched,
        'crm_contacts': crm_contacts,
        'email_activity': email_activity,
        'bayesian_scores': bayesian_scores[:20],
        'signals': signals,
        'reasoning': reasoning,
    }), 200


# ---------------------------------------------------------------------------
# Share token — public read-only endpoints (no login required)
# These mirror the authenticated endpoints above but validate via share token.
# ---------------------------------------------------------------------------

def _validate_share_token(token):
    """Return (share, sim_id, None) or (None, None, error_response)."""
    share = Layer6ShareToken.query.filter_by(token=token).first()
    if not share:
        return None, None, (jsonify({'error': 'Invalid share token'}), 404)
    if share.expires_at < datetime.utcnow():
        return None, None, (jsonify({'error': 'Share link has expired'}), 410)
    return share, share.simulation_id, None


@layer6_bp.route('/share/<token>/network', methods=['GET'])
def share_get_network(token):
    """Public read-only: full 26-node network state for a share token."""
    share, sim_id, err = _validate_share_token(token)
    if err:
        resp, code = err
        return resp, code

    cycle_id = request.args.get('cycle_id') or share.cycle_id
    if cycle_id:
        cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
        if not cycle:
            return jsonify({'error': 'Cycle not found'}), 404
    else:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()

    config = Layer6Config.query.filter_by(simulation_id=sim_id).first()

    if not cycle:
        nodes = [dict(n, status='idle', badge_count=0, locked=n['conditional']) for n in _NODE_CATALOG]
        edges = [dict(e, active=False, error=False, locked=e['conditional']) for e in _EDGE_CATALOG]
        return jsonify({'nodes': nodes, 'edges': edges, 'cycle': None}), 200

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).all()
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()).first()

    from app.models.platform_settings import PlatformSetting
    fintech_on = PlatformSetting.get('fintech_toggle', 'off') == 'on'

    nstates = _node_states_for_cycle(cycle, actions, log_entries, momentum, config, fintech_on)
    estats = _edge_states_for_cycle(cycle, actions, log_entries, momentum, fintech_on)

    nodes = [dict(n, **nstates.get(n['id'], {'status': 'idle', 'badge_count': 0})) for n in _NODE_CATALOG]
    edges = [dict(e, **estats.get(e['id'], {'active': False, 'error': False})) for e in _EDGE_CATALOG]

    cycle_data = cycle.to_dict()
    if config:
        cycle_data['transition_countdown'] = max(0, config.explore_phase_end_month - cycle.cycle_number)
        cycle_data['explore_phase_end_month'] = config.explore_phase_end_month

    return jsonify({'nodes': nodes, 'edges': edges, 'cycle': cycle_data}), 200


@layer6_bp.route('/share/<token>/network/node/<node_id>', methods=['GET'])
def share_get_network_node(token, node_id):
    """Public read-only: node inspector data for a share token."""
    share, sim_id, err = _validate_share_token(token)
    if err:
        resp, code = err
        return resp, code

    node_def = next((n for n in _NODE_CATALOG if n['id'] == node_id), None)
    if not node_def:
        return jsonify({'error': 'Unknown node'}), 404

    cycle_id = request.args.get('cycle_id') or share.cycle_id
    if cycle_id:
        cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    else:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()

    config = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    from app.models.platform_settings import PlatformSetting
    fintech_on = PlatformSetting.get('fintech_toggle', 'off') == 'on'

    if not cycle:
        return jsonify({'node': node_def, 'cycle': None, 'overview': {},
                        'inputs': [], 'outputs': [], 'history': []}), 200

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).order_by(
        Layer6ExecutionLog.created_at.asc()).all()
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()).first()

    nstates = _node_states_for_cycle(cycle, actions, log_entries, momentum, config, fintech_on)
    cur_state = nstates.get(node_id, {'status': 'idle', 'badge_count': 0})

    overview = {
        'node_id': node_id, 'label': node_def['label'], 'tier': node_def['tier'],
        'status': cur_state['status'],
        'cycle_number': cycle.cycle_number, 'phase': cycle.phase,
        'actions_scored': cycle.actions_scored if node_id == 'ORC' else None,
        'actions_dispatched': cycle.actions_dispatched if node_id == 'ORC' else cur_state.get('badge_count'),
        'actions_escalated': cycle.actions_escalated if node_id == 'ORC' else None,
        'last_computed': cycle.cycle_completed_at.isoformat() if cycle.cycle_completed_at else None,
    }

    inputs, outputs = _build_node_payload(node_id, cycle, actions, log_entries, momentum, config)

    all_cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()).limit(10).all()
    history = []
    for c in all_cycles:
        c_acts = Layer6ActionQueue.query.filter_by(cycle_id=c.id).all()
        c_logs = Layer6ExecutionLog.query.filter_by(cycle_id=c.id).all()
        c_states = _node_states_for_cycle(c, c_acts, c_logs, momentum, config, fintech_on)
        st = c_states.get(node_id, {'status': 'idle', 'badge_count': 0})
        history.append({
            'cycle_id': c.id, 'cycle_number': c.cycle_number,
            'date': c.cycle_started_at.isoformat(),
            'status': st['status'],
            'summary': f"Phase: {c.phase} · {c.actions_dispatched} dispatched · {c.actions_escalated} escalated",
        })

    return jsonify({'node': node_def, 'cycle': cycle.to_dict(), 'overview': overview,
                    'inputs': inputs, 'outputs': outputs, 'history': history}), 200


@layer6_bp.route('/share/<token>/network/edge/<edge_id>', methods=['GET'])
def share_get_network_edge(token, edge_id):
    """Public read-only: edge inspector data for a share token."""
    share, sim_id, err = _validate_share_token(token)
    if err:
        resp, code = err
        return resp, code

    edge_def = next((e for e in _EDGE_CATALOG if e['id'] == edge_id), None)
    if not edge_def:
        return jsonify({'error': 'Unknown edge'}), 404

    cycle_id = request.args.get('cycle_id') or share.cycle_id
    if cycle_id:
        cycle = Layer6Cycle.query.filter_by(id=cycle_id, simulation_id=sim_id).first()
    else:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()

    config = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    from app.models.platform_settings import PlatformSetting
    fintech_on = PlatformSetting.get('fintech_toggle', 'off') == 'on'

    if not cycle:
        return jsonify({'edge': edge_def, 'cycle': None, 'payload': [], 'history': []}), 200

    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
    log_entries = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).all()
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()).first()
    estats = _edge_states_for_cycle(cycle, actions, log_entries, momentum, fintech_on)
    edge_state = estats.get(edge_id, {'active': False, 'error': False})

    payload = _build_edge_payload(edge_id, edge_def, cycle, actions, log_entries, momentum, config)

    recent_cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()).limit(5).all()
    history = []
    for c in recent_cycles:
        c_acts = Layer6ActionQueue.query.filter_by(cycle_id=c.id).all()
        c_logs = Layer6ExecutionLog.query.filter_by(cycle_id=c.id).all()
        c_estats = _edge_states_for_cycle(c, c_acts, c_logs, momentum, fintech_on)
        c_est = c_estats.get(edge_id, {'active': False})
        history.append({
            'cycle_id': c.id, 'cycle_number': c.cycle_number,
            'date': c.cycle_started_at.isoformat(),
            'active': c_est['active'],
            'summary': f"Cycle {c.cycle_number} · {'active' if c_est['active'] else 'idle'}",
        })

    return jsonify({'edge': edge_def, 'cycle': cycle.to_dict(),
                    'active': edge_state['active'], 'error': edge_state.get('error', False),
                    'payload': payload, 'history': history}), 200


@layer6_bp.route('/share/<token>/cycles', methods=['GET'])
def share_list_cycles(token):
    """Public read-only: cycle list for a share token."""
    share, sim_id, err = _validate_share_token(token)
    if err:
        resp, code = err
        return resp, code

    cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()
    return jsonify([c.to_dict() for c in cycles]), 200


# ---------------------------------------------------------------------------
# Journey tab — per-layer data (suggested action, steps, artifact, next run)
# ---------------------------------------------------------------------------

_LAYER_STEPS = {
    1: [
        ('cold_email_campaign', 'Cold email'),
        ('consulting_outreach', 'Outreach'),
        ('rate_card', 'Rate card'),
        ('role_search', 'Role search'),
        ('linkedin_optimization', 'LinkedIn opt.'),
        ('booking_page', 'Booking page'),
        ('consulting_proposal', 'Proposal'),
        ('sow', 'SOW'),
        ('agreement', 'Agreement'),
        ('referral_network', 'Referral net.'),
        ('negotiation_script', 'Negotiation'),
    ],
    2: [
        ('speaker_proposals', 'Speaking prop.'),
        ('speaker_fee_rider', 'Speaker fee'),
        ('group_coaching_program', 'Group coaching'),
        ('corporate_training_pitch', 'Corp. training'),
        ('workshop_curriculum', 'Workshop'),
        ('waitlist_landing_page', 'Waitlist page'),
        ('alumni_reactivation', 'Alumni reactiv.'),
        ('roi_calculator', 'ROI calculator'),
    ],
    3: [
        ('course_curriculum', 'Course curric.'),
        ('competitor_research', 'Competitor res.'),
        ('product_sales_page', 'Sales page'),
        ('ebook_gumroad', 'E-book'),
        ('ab_test_plan', 'A/B test plan'),
        ('membership_structure', 'Membership'),
        ('launch_email_sequence', 'Launch sequence'),
        ('affiliate_program', 'Affiliate prog.'),
        ('testimonial_system', 'Testimonials'),
        ('lapsed_buyer_winback', 'Lapsed buyer'),
    ],
    4: [
        ('seo_content_calendar', 'SEO calendar'),
        ('lead_magnet_funnel', 'Lead magnet'),
        ('newsletter_monetization', 'Newsletter'),
        ('saas_product_spec', 'SaaS spec'),
        ('ip_licensing', 'IP licensing'),
        ('affiliate_partnerships', 'Affiliate part.'),
        ('youtube_podcast_strategy', 'YouTube/pod.'),
        ('community_flywheel', 'Community'),
        ('programmatic_ads', 'Prog. ads'),
        ('winback_campaign', 'Win-back'),
    ],
    5: [
        ('income_allocation', 'Income alloc.'),
        ('compound_growth_model', 'Projections'),
        ('fund_recommendations', 'Fund recs.'),
        ('ips', 'IPS'),
        ('real_estate', 'Real estate'),
        ('tax_optimization', 'Tax optim.'),
        ('entity_structure', 'Entity struct.'),
        ('dca_schedule', 'DCA schedule'),
        ('insurance', 'Insurance'),
        ('estate_planning', 'Estate plan.'),
    ],
}

_UNLOCK_NOTES = {
    2: 'Activates once your Layer 1 consulting outreach is complete.',
    3: 'Activates once your Layer 2 group coaching program is complete.',
    4: 'Activates once your Layer 3 course curriculum and sales page are in place.',
    5: 'Activates once your Layer 4 lead magnet funnel is in place.',
}

_LAYER_BLOCKERS = {
    2: 'consulting_outreach',
    3: 'group_coaching_program',
    4: 'product_sales_page',
    5: 'lead_magnet_funnel',
}


@layer6_bp.route('/<sim_id>/layer6/journey', methods=['GET'])
@login_required
def get_journey(sim_id):
    """Per-layer Journey tab data: steps, suggested action, artifact, next run ETA."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.models.agent_action import AgentAction
    from app.models.simulation import SimulationLayer as _SL
    from datetime import timedelta

    # SimulationLayer narratives keyed by layer number
    layer_narratives: dict[int, str | None] = {
        sl.layer_number: sl.ai_narrative
        for sl in _SL.query.filter_by(simulation_id=sim_id).all()
    }

    # Completed actions (latest record per type)
    completed_by_type: dict[str, AgentAction] = {}
    for a in AgentAction.query.filter_by(
        simulation_id=sim_id, status=AgentAction.STATUS_COMPLETE
    ).order_by(AgentAction.completed_at.asc()).all():
        completed_by_type[a.action_type] = a
    completed_types = set(completed_by_type)

    # Queued / dispatched in the action queue (latest per type)
    queued_by_type: dict[str, Layer6ActionQueue] = {}
    for q in Layer6ActionQueue.query.filter(
        Layer6ActionQueue.simulation_id == sim_id,
        Layer6ActionQueue.status.in_([
            Layer6ActionQueue.STATUS_QUEUED,
            Layer6ActionQueue.STATUS_DISPATCHED,
        ])
    ).order_by(Layer6ActionQueue.created_at.asc()).all():
        queued_by_type[q.action_type] = q

    # Escalated per layer
    escalated_by_layer: dict[int, list] = {}
    for e in Layer6ActionQueue.query.filter_by(
        simulation_id=sim_id, status=Layer6ActionQueue.STATUS_ESCALATED
    ).all():
        escalated_by_layer.setdefault(e.source_layer, []).append(e)

    # ETA from last cycle + cadence
    from app.models.layer6 import Layer6Config as _Cfg
    config = _Cfg.query.filter_by(simulation_id=sim_id).first()
    last_cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_started_at.desc()
    ).first()
    cadence_days = {'daily': 1, 'every_3_days': 3, 'weekly': 7}
    eta_text = 'next cycle'
    if config and last_cycle:
        days = cadence_days.get(config.cadence, 1)
        next_run = last_cycle.cycle_started_at + timedelta(days=days)
        from datetime import datetime as _dt
        delta = next_run - _dt.utcnow()
        if delta.total_seconds() > 0:
            hours = delta.total_seconds() / 3600
            eta_text = f'in {int(hours)} hrs' if hours < 48 else f'in {int(hours / 24)} days'

    result = {}
    for layer_num, seq in _LAYER_STEPS.items():
        total = len(seq)
        blocker = _LAYER_BLOCKERS.get(layer_num)
        is_blocked = bool(blocker and blocker not in completed_types)

        # Build steps
        from app.models.artifact import ArtifactVersion as _AV
        steps = []
        for i, (atype, label) in enumerate(seq):
            artifact_fields = []
            artifact_version = None
            artifact_summary = ''
            artifact_version_id = None
            public_url = None
            if atype in completed_types:
                status = 'complete'
                a = completed_by_type[atype]
                action_id = a.id
                raw = a.user_inputs or {}
                artifact_fields = [[k.replace('_', ' ').title(), str(v)[:80]]
                                   for k, v in list(raw.items())[:4] if v]
                artifact_version = 1
                if a.artifact:
                    artifact_summary = a.artifact[:320].strip()
                av = _AV.query.filter_by(action_id=a.id, is_current=True).first()
                if av:
                    artifact_version_id = av.id
                    public_url = av.public_url
            elif atype in queued_by_type:
                q = queued_by_type[atype]
                status = 'running' if q.status == Layer6ActionQueue.STATUS_DISPATCHED else 'queued'
                action_id = q.agent_action_id
            else:
                status = 'pending'
                action_id = None
            steps.append({'seq': i + 1, 'type': atype, 'label': label,
                          'status': status, 'action_id': action_id,
                          'artifact_version_id': artifact_version_id,
                          'artifact_fields': artifact_fields,
                          'artifact_version': artifact_version,
                          'artifact_summary': artifact_summary,
                          'public_url': public_url})

        completed_count = sum(1 for s in steps if s['status'] == 'complete')

        # Suggested action
        layer_esc = escalated_by_layer.get(layer_num, [])
        suggested: dict | None = None
        if is_blocked:
            suggested = None
        elif layer_esc:
            suggested = {'state': 'escalated', 'label': f'{len(layer_esc)} actions need your approval',
                         'type': None, 'action_id': None}
        elif completed_count >= total:
            suggested = {'state': 'all_complete', 'label': 'All actions complete for this layer',
                         'type': None, 'action_id': None}
        else:
            for s in steps:
                if s['status'] in ('queued', 'pending'):
                    prefix = '▶ Suggested first action' if completed_count == 0 else '▶ Suggested action'
                    suggested = {'state': 'queued', 'label': f"{prefix}: {s['label']}",
                                 'type': s['type'], 'action_id': s['action_id']}
                    break

        # Latest artifact — most recently completed step in sequence
        latest_artifact: dict | None = None
        for s in reversed(steps):
            if s['status'] == 'complete':
                a = completed_by_type[s['type']]
                raw_inputs = a.user_inputs or {}
                fields = {
                    k.replace('_', ' ').title(): str(v)[:80]
                    for k, v in list(raw_inputs.items())[:4]
                    if v
                }
                latest_artifact = {
                    'action_type': s['type'],
                    'label': s['label'],
                    'version': 1,
                    'fields': fields,
                    'action_id': a.id,
                }
                break

        # Next pending action
        next_pending: dict | None = None
        if not is_blocked:
            for s in steps:
                if s['status'] in ('queued', 'pending'):
                    next_pending = {'type': s['type'], 'label': s['label'],
                                    'action_id': s['action_id'], 'eta': eta_text}
                    break

        result[str(layer_num)] = {
            'layer_number': layer_num,
            'completed_count': completed_count,
            'total_count': total,
            'is_blocked': is_blocked,
            'unlock_note': _UNLOCK_NOTES.get(layer_num) if is_blocked else None,
            'suggested': suggested,
            'steps': steps,
            'latest_artifact': latest_artifact,
            'next_pending': next_pending,
            'layer_narrative': layer_narratives.get(layer_num),
        }

    return jsonify(result), 200


# ---------------------------------------------------------------------------
# SSE stream (updated with step-level events)
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/layer6/stream')
@login_required
def layer6_stream(sim_id):
    """SSE stream for diagram real-time updates. Polls DB every 5 s."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.blueprints.simulations.sse import sse_event, sse_keepalive
    import time

    def generate():
        last_cycle_id = None
        last_log_count = 0
        last_esc_count = 0
        last_income_count = 0
        start = time.time()
        while time.time() - start < 300:
            cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
                Layer6Cycle.cycle_number.desc()
            ).first()

            if cycle:
                actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
                logs = Layer6ExecutionLog.query.filter_by(cycle_id=cycle.id).all()
                esc_count = len([a for a in actions if a.status == 'escalated'])

                # Cycle just completed
                if cycle.id != last_cycle_id and cycle.cycle_completed_at:
                    last_cycle_id = cycle.id
                    payload = cycle.to_dict()
                    payload['action_queue'] = [a.to_dict() for a in actions]
                    yield sse_event('cycle_complete', {'cycle': payload})
                    last_log_count = len(logs)
                    last_esc_count = esc_count

                # New log entries
                if len(logs) > last_log_count:
                    new_logs = logs[last_log_count:]
                    for lg in new_logs:
                        yield sse_event('log_write', {'event': lg.to_dict()})
                    last_log_count = len(logs)

                # New escalations
                if esc_count > last_esc_count:
                    new_escs = [a for a in actions if a.status == 'escalated'][last_esc_count:]
                    for ea in new_escs:
                        yield sse_event('escalation_added', {'action': ea.to_dict()})
                    last_esc_count = esc_count

                # Step indicator
                if not cycle.cycle_completed_at and actions:
                    dispatched = [a for a in actions if a.status in ('dispatched', 'complete')]
                    if dispatched:
                        yield sse_event('step_running', {'step': 'schedule'})
                    else:
                        yield sse_event('step_running', {'step': 'score'})

            # New income records
            try:
                from app.models.income import LayerIncomeRecord
                income_count = LayerIncomeRecord.query.filter_by(
                    simulation_id=sim_id, is_void=False
                ).count()
                if income_count > last_income_count:
                    last_income_count = income_count
                    yield sse_event('income_recorded', {'count': income_count})
            except Exception:
                pass

            try:
                yield sse_keepalive()
            except GeneratorExit:
                return
            time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


# ---------------------------------------------------------------------------
# Action Items — GCC Action Queue
# ---------------------------------------------------------------------------

@layer6_bp.route('/<sim_id>/action-items', methods=['GET'])
@login_required
def list_action_items(sim_id):
    """Return active action items for the Action Queue, sorted by urgency then recency."""
    sim, err, code = _get_sim_or_404(sim_id)
    if err:
        return err, code

    from app.models.layer6 import ActionItem
    items = ActionItem.query.filter_by(
        simulation_id=sim_id,
        user_id=current_user.id,
        status=ActionItem.STATUS_ACTIVE,
    ).order_by(
        ActionItem.urgency_tier.asc(),
        ActionItem.created_at.desc(),
    ).all()
    return jsonify({'items': [i.to_dict() for i in items], 'count': len(items)})


@layer6_bp.route('/<sim_id>/action-items/<item_id>/dismiss', methods=['POST'])
@login_required
def dismiss_action_item_route(sim_id, item_id):
    """Dismiss a dismissable action item (tier 3 or 4 only)."""
    from app.models.layer6 import ActionItem
    item = ActionItem.query.filter_by(
        id=item_id, simulation_id=sim_id, user_id=current_user.id,
    ).first_or_404()

    if not item.is_dismissable:
        return jsonify({'error': 'This item must be resolved, not dismissed'}), 400
    if item.status != ActionItem.STATUS_ACTIVE:
        return jsonify({'error': 'Item is already resolved or dismissed'}), 409

    from utils.action_items import dismiss_action_item
    dismiss_action_item(item_id)
    return jsonify({'ok': True})


@layer6_bp.route('/<sim_id>/action-items/<item_id>/resolve', methods=['POST'])
@login_required
def resolve_action_item_route(sim_id, item_id):
    """Mark an action item resolved after the user completes the required action."""
    from app.models.layer6 import ActionItem
    item = ActionItem.query.filter_by(
        id=item_id, simulation_id=sim_id, user_id=current_user.id,
    ).first_or_404()

    if item.status != ActionItem.STATUS_ACTIVE:
        return jsonify({'error': 'Item is already resolved or dismissed'}), 409

    from utils.action_items import resolve_action_item
    resolve_action_item(item_id)
    return jsonify({'ok': True})
