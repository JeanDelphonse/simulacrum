"""Compliance admin and member endpoints (COMPLIANCE_PLAN.md)."""
from __future__ import annotations

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app.blueprints.compliance import compliance_bp
from app.services import legal_text


def _admin():
    if not current_user.is_admin:
        abort(403)


@compliance_bp.route('/admin/legal-text')
@login_required
def admin_legal_text():
    _admin()
    return render_template('admin/legal_text.html', rows=legal_text.all_rows())


@compliance_bp.route('/api/admin/legal-text/<key>', methods=['PUT'])
@login_required
def admin_legal_text_update(key):
    _admin()
    data = request.get_json(silent=True) or {}
    try:
        if 'body' in data:
            legal_text.update(key, data.get('body'), current_user.id)
        if 'approved' in data:
            legal_text.set_status(key, bool(data.get('approved')), current_user.id)
    except KeyError:
        return jsonify({'error': 'Unknown text.'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    row = next(r for r in legal_text.all_rows() if r['key'] == key)
    return jsonify({'ok': True, 'status': row['status'], 'version': row['version']})


# ── Outcome check-ins (A6): the member's own answers; internal only ─────────
@compliance_bp.route('/api/outcome-checkins', methods=['POST'])
@login_required
def outcome_record():
    from app.services import outcome_service
    try:
        row = outcome_service.record(current_user.id, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'id': row.id})


@compliance_bp.route('/admin/compliance')
@login_required
def admin_compliance():
    """Layer 5 switch, compliance events and outcome check-in counts."""
    _admin()
    from app.models.compliance import ComplianceEvent
    from app.services import outcome_service
    events = ComplianceEvent.query.order_by(ComplianceEvent.created_at.desc()).limit(200).all()
    return render_template('admin/compliance.html', events=events, outcomes=outcome_service.admin_summary(),
                           l5=_l5_state())


def _l5_state():
    try:
        from app.services import l5_feature
        return l5_feature.state()
    except ImportError:
        return None


@compliance_bp.route('/api/admin/compliance/l5', methods=['PUT'])
@login_required
def admin_l5_switch():
    _admin()
    from app.services import l5_feature
    data = request.get_json(silent=True) or {}
    try:
        l5_feature.set_mode(data.get('mode'), current_user.id, data.get('reason') or '')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'mode': l5_feature.state()['mode']})
