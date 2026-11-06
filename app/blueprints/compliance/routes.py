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
