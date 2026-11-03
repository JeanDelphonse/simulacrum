"""Transition Package page and enterprise inquiries."""
from __future__ import annotations

from functools import wraps

from flask import abort, jsonify, render_template, request, session
from flask_login import current_user, login_required

from app.blueprints.marketing import marketing_bp
from app.services import enterprise_inquiry_service as eis


def _client_ip() -> str:
    fwd = request.headers.get('X-Forwarded-For', '')
    return (fwd.split(',')[0].strip() if fwd else '') or (request.remote_addr or '')


def _wants_json() -> bool:
    return request.is_json or 'application/json' in (request.headers.get('Accept') or '')


def _page(status=200, **state):
    return render_template('marketing/transition_package.html', mode=eis.page_mode(),
                           csrf_token=eis.csrf_token(session), headcount_bands=eis.HEADCOUNT_BANDS,
                           timings=eis.TIMINGS, honeypot=eis.HONEYPOT_FIELD, **state), status


@marketing_bp.route('/transition-package')
def transition_package():
    return _page()


@marketing_bp.route('/enterprise/inquiry', methods=['POST'])
def enterprise_inquiry():
    form = (request.get_json(silent=True) or {}) if request.is_json else request.form.to_dict()
    try:
        eis.submit(form, session, _client_ip())
    except eis.InquiryRejected as exc:
        if _wants_json():
            return jsonify({'error': 'rejected', 'message': str(exc)}), 400
        return _page(400, form_error=str(exc), form_values=form)
    if _wants_json():
        return jsonify({'ok': True})
    return _page(thanks=True)


# ── Admin ─────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


@marketing_bp.route('/admin/inquiries')
@login_required
def admin_inquiries():
    if not current_user.is_admin:
        abort(403)
    from app.models.enterprise_inquiry import EnterpriseInquiry
    status = request.args.get('status') or ''
    q = EnterpriseInquiry.query
    if status in EnterpriseInquiry.STATUSES:
        q = q.filter_by(status=status)
    rows = q.order_by(EnterpriseInquiry.created_at.desc()).limit(500).all()
    counts = {s: EnterpriseInquiry.query.filter_by(status=s).count() for s in EnterpriseInquiry.STATUSES}
    return render_template('admin/inquiries.html', inquiries=[r.to_dict() for r in rows], counts=counts,
                           status=status, settings={k: eis.setting(k) for k in eis.DEFAULTS})


@marketing_bp.route('/api/admin/inquiries/<inquiry_id>/<action>', methods=['POST'])
@admin_required
def api_admin_inquiry(inquiry_id, action):
    try:
        if action == 'status':
            inq = eis.set_status(inquiry_id, (request.get_json(silent=True) or {}).get('status'), current_user.id)
        elif action == 'promote':
            inq = eis.promote_to_prospect(inquiry_id, current_user.id)
        else:
            abort(404)
    except eis.InquiryRejected as exc:
        return jsonify({'error': 'refused', 'message': str(exc)}), 400
    return jsonify(inq.to_dict())
