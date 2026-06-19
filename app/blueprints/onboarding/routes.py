"""
Onboarding wizard routes (SIM-PRD-ONBOARD-001 v1.1).

GET  /onboarding              — full-screen 7-step wizard page
POST /api/onboarding/step     — advance onboarding_step; complete when step==7
"""
from datetime import datetime

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.onboarding import onboarding_bp
from app.extensions import db


@onboarding_bp.route('/onboarding')
@login_required
def wizard():
    if current_user.onboarding_completed_at is not None:
        return redirect(url_for('pages.dashboard'))
    step = current_user.onboarding_step or 1
    return render_template('onboarding/wizard.html', initial_step=step)


@onboarding_bp.route('/api/onboarding/step', methods=['POST'])
@login_required
def advance_step():
    data = request.get_json(force=True, silent=True) or {}
    try:
        step = int(data.get('step', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid step'}), 400

    if step < 1 or step > 7:
        return jsonify({'error': 'Step must be 1–7'}), 400

    # Only advance; never allow regressing the persisted step
    if step >= (current_user.onboarding_step or 1):
        if step == 7:
            current_user.onboarding_step = 7
            current_user.onboarding_completed_at = datetime.utcnow()
        else:
            current_user.onboarding_step = step + 1
        db.session.commit()

    return jsonify({
        'onboarding_step': current_user.onboarding_step,
        'completed': current_user.onboarding_completed_at is not None,
    })
