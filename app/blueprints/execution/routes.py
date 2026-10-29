"""Plan, approval and autonomy endpoints (EXEC_PLAN P3 — L1, nothing executes)."""
from __future__ import annotations

import logging

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from app.blueprints.execution import execution_bp
from app.models.agent_action import AgentAction
from app.models.simulation import Simulation
from app.services import agent_plans

logger = logging.getLogger(__name__)


def _owned_action(artifact_id):
    action = AgentAction.query.get_or_404(artifact_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return None
    return action


@execution_bp.route('/api/artifacts/<artifact_id>/plan', methods=['GET'])
@login_required
def api_plan(artifact_id):
    """The artifact's current plan, created on first view."""
    action = _owned_action(artifact_id)
    if action is None:
        return jsonify({'error': 'forbidden'}), 403
    plan = agent_plans.get_or_create_plan(action, current_user.id)
    return jsonify(agent_plans.plan_dict(plan))


@execution_bp.route('/api/artifacts/<artifact_id>/plan/regenerate', methods=['POST'])
@login_required
def api_plan_regenerate(artifact_id):
    action = _owned_action(artifact_id)
    if action is None:
        return jsonify({'error': 'forbidden'}), 403
    plan = agent_plans.regenerate_plan(action, current_user.id)
    return jsonify(agent_plans.plan_dict(plan))


@execution_bp.route('/api/plan-steps/<step_id>/decision', methods=['POST'])
@login_required
def api_step_decision(step_id):
    """Approve or reject a step. Records the decision only — nothing runs."""
    body = request.get_json(silent=True) or {}
    try:
        plan = agent_plans.decide(step_id, current_user.id, body.get('decision'))
    except PermissionError:
        return jsonify({'error': 'forbidden'}), 403
    except ValueError as exc:
        return jsonify({'error': 'invalid', 'message': str(exc)}), 400
    return jsonify(agent_plans.plan_dict(plan))


@execution_bp.route('/api/agent-autonomy', methods=['GET'])
@login_required
def api_autonomy():
    return jsonify({'agents': agent_plans.autonomy_overview(current_user.id),
                    'levels': agent_plans.LEVEL_LABELS})


@execution_bp.route('/api/agent-autonomy/<agent_key>', methods=['PUT'])
@login_required
def api_autonomy_set(agent_key):
    body = request.get_json(silent=True) or {}
    try:
        level = agent_plans.set_level(current_user.id, agent_key, body.get('level'))
    except ValueError as exc:
        return jsonify({'error': 'invalid', 'message': str(exc)}), 400
    return jsonify({'agent_key': agent_key, 'level': level})


@execution_bp.route('/settings/agent-autonomy')
@login_required
def autonomy_page():
    return render_template('execution/autonomy.html',
                           agents=agent_plans.autonomy_overview(current_user.id),
                           level_labels=agent_plans.LEVEL_LABELS)
