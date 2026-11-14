"""Founder-granted support access (FOUNDER_PLAN.md WP7).

SimiSI admins see run metadata only. Founder documents, contact names and
message content stay masked in /admin/observability until a founder grants
support access for a limited number of days. Every admin view of unmasked
content is written to the admin access audit.

    GET  /founder/v/<id>/support                 the page with the toggle
    GET  /api/founder/v/<id>/support-access      {active, until, default_days, max_days}
    POST /api/founder/v/<id>/support-access      {grant: true, days} or {grant: false}

The card is templates/founder/_support_access.html; other founder pages can
include it with `venture` and `support` in the context.
"""
from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required, venture_nav


@founder_bp.route('/founder/v/<venture_id>/support')
@founder_required
def support_page(venture_id):
    from app.services import founder_obs_service as fobs
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    return render_template('founder/support.html', venture=v, support=fobs.support_status(v),
                           nav=venture_nav(v))


@founder_bp.route('/api/founder/v/<venture_id>/support-access', methods=['GET'])
@founder_required
def support_access_get(venture_id):
    from app.services import founder_obs_service as fobs
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    return jsonify(fobs.support_status(v))


@founder_bp.route('/api/founder/v/<venture_id>/support-access', methods=['POST'])
@founder_required
def support_access_set(venture_id):
    from app.services import founder_obs_service as fobs
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    as_json = request.is_json
    data = request.get_json(silent=True) or request.form or {}
    grant = data.get('grant') not in (False, 'false', '0', 0, None, '', 'off')
    try:
        status = fobs.set_support_access(v, current_user, grant, data.get('days'))
    except fobs.ControlError as exc:
        if not as_json:
            # The card's plain form post (no JS): back to the page with the reason.
            flash(str(exc), 'error')
            return redirect(url_for('founder.support_page', venture_id=v.id))
        return jsonify({'error': str(exc)}), exc.status
    if not as_json:
        flash('Support access is on.' if status.get('active') else 'Support access is off.', 'success')
        return redirect(url_for('founder.support_page', venture_id=v.id))
    return jsonify(status)
