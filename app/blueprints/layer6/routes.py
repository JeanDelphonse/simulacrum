"""
Layer 6 API — Autonomous Growth Orchestrator endpoints.
All routes are mounted under /api/simulations/<sim_id>/layer6/
"""
from datetime import datetime, timedelta
from flask import request, jsonify, Response, stream_with_context
from flask_login import login_required, current_user
from app.blueprints.layer6 import layer6_bp
from app.extensions import db
from app.models.simulation import Simulation
from app.models.layer6 import (
    Layer6Config, Layer6Cycle, Layer6ActionQueue,
    Layer6Outcome, Layer6Momentum, Layer6ExecutionLog, Layer6ShareToken,
)
from app.models.audit_log import AuditLog


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_sim_or_404(sim_id: str):
    sim = Simulation.query.get(sim_id)
    if not sim:
        return None, jsonify({'error': 'Simulation not found'}), 404
    if sim.user_id != current_user.id:
        return None, jsonify({'error': 'Forbidden'}), 403
    return sim, None, None


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
    try:
        cycle_data = run_orchestrator_cycle(sim_id)
        AuditLog.log('layer6_manual_run', user_id=current_user.id, resource_id=sim_id,
                     metadata={'cycle_number': cycle_data.get('cycle_number')})
        return jsonify(cycle_data), 200
    except Exception as exc:
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

    from app.tasks.layer6 import dispatch_layer6_action
    dispatch_layer6_action.delay(item.id)

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
    """All 49 action types with their statuses for the DAG visualization."""
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
            nodes.append({
                'id': action_type,
                'label': action_def.get('label', action_type),
                'layer': layer_num,
                'status': status,
                'prerequisites': prereqs,
            })

    return jsonify({'nodes': nodes}), 200


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
        start = time.time()
        while time.time() - start < 300:
            cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
                Layer6Cycle.cycle_number.desc()
            ).first()
            if cycle and cycle.id != last_cycle_id and cycle.cycle_completed_at:
                last_cycle_id = cycle.id
                actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).all()
                payload = cycle.to_dict()
                payload['action_queue'] = [a.to_dict() for a in actions]
                yield sse_event('cycle_complete', {'cycle': payload})
            yield sse_keepalive()
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
