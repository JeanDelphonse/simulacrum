"""Founder Track tab of /admin/observability (FOUNDER_PLAN.md WP7).

Observe (all GET, ?hours= window like the rest of the console):
  /api/admin/observability/founder/meta
  /api/admin/observability/founder/fleet              runs, success rate, latency, cost by layer and agent
  /api/admin/observability/founder/runs               recent founder runs (?venture= &status=)
  /api/admin/observability/founder/ventures           venture picker for the trace
  /api/admin/observability/founder/ventures/<id>/trace   timeline (audited; masked unless support access)
  /api/admin/observability/founder/autonomy           external actions, levels, approvers, blocked attempts
  /api/admin/observability/founder/eval-quality       finding accuracy ratings, parameter override rates
  /api/admin/observability/founder/compliance         blocks and the exemption each venture recorded
  /api/admin/observability/founder/connectors         connector calls and failures (until WP8: tool spans)
  /api/admin/observability/founder/startup-pages      views, unlocks, payment problems, updates sent
  /api/admin/observability/founder/alerts             alert rules and recent alert flags

Control (POST/PUT):
  /api/admin/observability/founder/alerts             PUT  {metric, threshold?, enabled?}
  /api/admin/observability/founder/alerts/evaluate    POST run the evaluator now
  /api/admin/observability/founder/agents/<key>/pause POST {paused: bool}  one agent, every venture
  /api/admin/observability/founder/ventures/<id>/pause POST {paused: bool} every agent, one venture
  /api/admin/observability/founder/runs/<run_id>/rerun POST re-run a failed run

Platform Admin only, like every other route in this console.
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from app.blueprints.observability import observability_bp
from app.blueprints.observability.routes import _hours, admin_required
from app.extensions import db

# The founder-side support-access toggle lives on the founder blueprint but is
# part of this package. founder/__init__.py loads area modules from a fixed
# list that doesn't name it, so it's imported here, before either blueprint is
# registered (create_app imports observability_bp first).
from app.blueprints.founder import support as _founder_support  # noqa: E402,F401

_BASE = '/api/admin/observability/founder'


def _svc():
    from app.services import founder_obs_service
    return founder_obs_service


def _venture_or_404(venture_id):
    from app.models.founder import FounderVenture
    v = db.session.get(FounderVenture, venture_id)
    if v is None:
        return None, (jsonify({'error': 'Venture not found'}), 404)
    return v, None


def _flag_value(data, key='paused'):
    return data.get(key) not in (False, 'false', '0', 0, None, 'off')


@observability_bp.route(_BASE + '/meta', methods=['GET'])
@login_required
@admin_required
def founder_meta():
    from app.services import founder_feature, founder_registry
    svc = _svc()
    paused = set(svc.paused_agents())
    names = svc.layer_names()
    return jsonify({
        'enabled': founder_feature.mode() != 'off',
        'mode': founder_feature.mode(),
        'layers': [{'code': c, 'name': names[c]} for c in svc.founder_layers()],
        'agents': [{'key': a['action_type'], 'label': a.get('label') or a['action_type'],
                    'layer': 'F%s' % a.get('layer'), 'paused': a['action_type'] in paused}
                   for a in founder_registry.all_agents()],
        'mask_note': 'Content is masked unless the founders granted support access.',
    }), 200


@observability_bp.route(_BASE + '/fleet', methods=['GET'])
@login_required
@admin_required
def founder_fleet():
    return jsonify(_svc().fleet(_hours())), 200


@observability_bp.route(_BASE + '/runs', methods=['GET'])
@login_required
@admin_required
def founder_runs():
    status = (request.args.get('status') or '').strip() or None
    venture = (request.args.get('venture') or '').strip() or None
    return jsonify({'runs': _svc().recent_runs(_hours(), venture, status)}), 200


@observability_bp.route(_BASE + '/ventures', methods=['GET'])
@login_required
@admin_required
def founder_ventures_list():
    return jsonify({'ventures': _svc().ventures(_hours())}), 200


@observability_bp.route(_BASE + '/ventures/<venture_id>/trace', methods=['GET'])
@login_required
@admin_required
def founder_venture_trace(venture_id):
    v, err = _venture_or_404(venture_id)
    if err:
        return err
    return jsonify(_svc().venture_trace(v, current_user.id, max(_hours(), 24 * 90))), 200


@observability_bp.route(_BASE + '/ventures/<venture_id>/pause', methods=['POST'])
@login_required
@admin_required
def founder_venture_pause(venture_id):
    v, err = _venture_or_404(venture_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _svc().set_venture_paused(v, _flag_value(data), current_user.id)
    return jsonify({'venture_id': v.id, 'paused': v.paused_at is not None,
                    'paused_at': v.paused_at.isoformat() if v.paused_at else None}), 200


@observability_bp.route(_BASE + '/agents/<agent_key>/pause', methods=['POST'])
@login_required
@admin_required
def founder_agent_pause(agent_key):
    svc = _svc()
    data = request.get_json(silent=True) or {}
    try:
        paused = svc.set_agent_paused(agent_key, _flag_value(data), current_user.id)
    except svc.ControlError as exc:
        return jsonify({'error': str(exc)}), exc.status
    return jsonify({'agent_key': agent_key, 'paused': agent_key in paused, 'paused_agents': paused}), 200


@observability_bp.route(_BASE + '/runs/<run_id>/rerun', methods=['POST'])
@login_required
@admin_required
def founder_rerun(run_id):
    svc = _svc()
    try:
        out = svc.rerun(run_id, current_user.id)
    except svc.ControlError as exc:
        return jsonify({'error': str(exc)}), exc.status
    return jsonify(out), 200


@observability_bp.route(_BASE + '/autonomy', methods=['GET'])
@login_required
@admin_required
def founder_autonomy_audit():
    return jsonify(_svc().autonomy_audit(_hours(), current_user.id)), 200


@observability_bp.route(_BASE + '/eval-quality', methods=['GET'])
@login_required
@admin_required
def founder_eval_quality():
    return jsonify(_svc().eval_quality()), 200


@observability_bp.route(_BASE + '/compliance', methods=['GET'])
@login_required
@admin_required
def founder_compliance_view():
    return jsonify(_svc().compliance(_hours())), 200


@observability_bp.route(_BASE + '/connectors', methods=['GET'])
@login_required
@admin_required
def founder_connectors():
    return jsonify(_svc().connectors(_hours())), 200


@observability_bp.route(_BASE + '/startup-pages', methods=['GET'])
@login_required
@admin_required
def founder_startup_pages():
    return jsonify(_svc().startup_pages(_hours())), 200


@observability_bp.route(_BASE + '/alerts', methods=['GET'])
@login_required
@admin_required
def founder_alerts_get():
    from app.services import founder_alerts
    return jsonify({'rules': founder_alerts.rules(), 'flags': founder_alerts.recent(max(_hours(), 24 * 7))}), 200


@observability_bp.route(_BASE + '/alerts', methods=['PUT'])
@login_required
@admin_required
def founder_alerts_put():
    from app.services import founder_alerts
    data = request.get_json(silent=True) or {}
    try:
        rule = founder_alerts.set_rule(data.get('metric'), data.get('threshold'), data.get('enabled'),
                                       current_user.id)
    except founder_alerts.AlertError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'rule': rule}), 200


@observability_bp.route(_BASE + '/alerts/evaluate', methods=['POST'])
@login_required
@admin_required
def founder_alerts_evaluate():
    from app.services import founder_alerts
    return jsonify({'fired': founder_alerts.evaluate()}), 200
