"""Founder Track readiness report (FOUNDER_PLAN.md WP2).

    POST /api/founder/v/<id>/evaluate         start an evaluation (member only)
    GET  /api/founder/v/<id>/evaluation       status + report_view JSON
    GET  /founder/v/<id>/report               the readiness report page
    POST /api/founder/findings/<id>/rating    rate a finding fair / not fair
"""
from __future__ import annotations

from flask import abort, jsonify, render_template, request
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required


@founder_bp.route('/api/founder/v/<venture_id>/evaluate', methods=['POST'])
@founder_required
def evaluate(venture_id):
    from app.services import founder_evaluation_service as fes
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    running = fes.running_run(v)
    if running is not None:
        return jsonify({'run_id': running.id, 'status': 'running',
                        'message': 'An evaluation is already running. The report updates when it finishes.'}), 202
    if not fes.current_artifacts(v):
        return jsonify({'run_id': None, 'status': 'none',
                        'message': 'Add a document or a spoken pitch first, then run the evaluation.'}), 400
    r = fes.run_async(v, current_user)
    return jsonify({'run_id': r.id, 'status': r.status,
                    'message': 'Evaluation started. It usually takes a few minutes.'}), 202


@founder_bp.route('/api/founder/v/<venture_id>/evaluation')
@founder_required
def evaluation(venture_id):
    from app.services import founder_evaluation_service as fes
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    fes.running_run(v)            # marks a stuck run failed
    return jsonify(fes.report_view(v, current_user))


@founder_bp.route('/founder/v/<venture_id>/report')
@founder_required
def report_page(venture_id):
    from app.services import founder_evaluation_service as fes
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    fes.running_run(v)
    view = fes.report_view(v, current_user)
    return render_template('founder/report.html', venture=v, report=view,
                           has_artifacts=bool(fes.current_artifacts(v)))


@founder_bp.route('/api/founder/findings/<finding_id>/rating', methods=['POST'])
@founder_required
def rate_finding(finding_id):
    from app.extensions import db
    from app.models.founder import EvaluationFinding
    from app.services import founder_evaluation_service as fes
    from app.services import founder_ventures
    f = db.session.get(EvaluationFinding, finding_id)
    if f is None:
        abort(404)
    founder_ventures.require_member(f.venture_id, current_user)
    data = request.get_json(silent=True) or request.form or {}
    if 'fair' not in data:
        return jsonify({'error': 'Say whether the finding is fair.'}), 400
    try:
        out = fes.rate(finding_id, current_user, data.get('fair'), data.get('comment'))
    except fes.EvaluationError as exc:
        return jsonify({'error': str(exc)}), 403
    return jsonify(out)
