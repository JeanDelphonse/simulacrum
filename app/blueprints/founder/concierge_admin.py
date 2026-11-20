"""/admin/ventures: concierge onboarding (FOUNDER_CONCIERGE_PLAN.md section 3.8).

Admins with the concierge permission (concierge_service.can_concierge: admin
plus an explicit allow on founder_feature 'founder_concierge') onboard a
venture for a founder: queue, create (with recorded consent), encrypted
uploads, per-venture fee override, evaluation with the pre-fill, review edits
and confirm. Other admins get 404 (the area stays hidden), members 403.

Every state change goes through concierge_service.transition (audit-logged).
"""
from __future__ import annotations

import io
from datetime import datetime
from functools import wraps

from flask import abort, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from app.blueprints.founder import founder_bp
from app.extensions import db


def _nav_visible() -> bool:
    try:
        from app.services import concierge_service as cs
        return cs.can_concierge(current_user)
    except Exception:
        return False


@founder_bp.record_once
def _register_nav(state):
    # The admin menu shows the Concierge page only to admins who may open it.
    state.app.jinja_env.globals['concierge_nav'] = _nav_visible


def _err(message, code=400):
    return jsonify({'error': 'refused', 'message': str(message)}), code


def concierge_page(f):
    """Pages: members 403, admins without the concierge permission 404."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        from app.services import concierge_service as cs
        if not current_user.is_admin:
            abort(403)
        if not cs.can_concierge(current_user):
            abort(404)
        return f(*args, **kwargs)
    return decorated


def concierge_api(f):
    """JSON: anonymous and members 403, admins without the permission 404,
    cross-site writes refused."""
    @wraps(f)
    def decorated(*args, **kwargs):
        from app.blueprints.founder.admin import _same_origin
        from app.services import concierge_service as cs
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        if not cs.can_concierge(current_user):
            return jsonify({'error': 'Not found'}), 404
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _same_origin():
            return _err('Cross-site request refused.', 403)
        return f(*args, **kwargs)
    return decorated


def _load(venture_id):
    from app.services import concierge_service as cs
    v, c = cs.venture_and_case(venture_id)
    if v is None:
        abort(404)
    return v, c


def _load_api(venture_id):
    from app.services import concierge_service as cs
    return cs.venture_and_case(venture_id)


def _common():
    from app.services import founder_config as fc
    from app.services import founder_intake_service as fis
    return {'currency': fc.get('fnd_concierge_currency'), 'encryption_ready': fis.encryption_ready()}


# ── Pages ────────────────────────────────────────────────────────────────────

@founder_bp.route('/admin/ventures')
@concierge_page
def admin_ventures():
    from app.services import concierge_service as cs
    state = request.args.get('state') or ''
    if state not in cs.STATES:
        state = ''
    return render_template('admin/concierge_queue.html', rows=cs.queue(state or None), state=state,
                           states=cs.STATES, labels=cs.STATE_LABELS, counts=cs.state_counts(),
                           base_fee=cs.base_fee_cents(), base_fee_label=cs.money(cs.base_fee_cents()), **_common())


@founder_bp.route('/admin/ventures/new')
@concierge_page
def admin_ventures_new():
    return render_template('admin/concierge_new.html', today=datetime.utcnow().strftime('%Y-%m-%d'), **_common())


@founder_bp.route('/admin/ventures/<venture_id>')
@concierge_page
def admin_venture_case(venture_id):
    from app.services import concierge_service as cs
    v, c = _load(venture_id)
    try:
        if cs.recover_stuck(v, c, current_user):
            c = cs.case_for(v.id)
    except cs.ConciergeError:
        db.session.rollback()
    since = cs.running_since(v) if c.state == 'evaluating' else None
    return render_template('admin/concierge_case.html', **cs.case_view(v, c), **_common(),
                           running_since=since, stuck_reset_minutes=cs.STUCK_RESET_MINUTES,
                           progress=_progress(v) if c.state == 'evaluating' else None)


def _progress(v):
    from app.services import founder_evaluation_service as fes
    try:
        return fes.progress(v)
    except Exception:
        return None


# ── API ──────────────────────────────────────────────────────────────────────

@founder_bp.route('/api/admin/ventures', methods=['POST'])
@concierge_api
def api_concierge_create():
    from app.services import concierge_service as cs
    b = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    b = b or {}
    try:
        v, c = cs.create_case(current_user, b.get('primary_name'), b.get('primary_email'),
                              cofounder_emails=b.get('cofounder_emails') or [], source=b.get('source'),
                              referrer=b.get('referrer'), notes=b.get('notes'),
                              consent=str(b.get('consent')).lower() in ('1', 'true', 'on', 'yes'),
                              consent_given_by=b.get('consent_given_by'),
                              nda=str(b.get('nda')).lower() in ('1', 'true', 'on', 'yes'),
                              venture_name=b.get('venture_name'))
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    return jsonify({'venture_id': v.id, 'case_id': c.id, 'state': c.state, 'url': '/admin/ventures/%s' % v.id}), 201


@founder_bp.route('/api/admin/ventures/<venture_id>/upload', methods=['POST'])
@concierge_api
def api_concierge_upload(venture_id):
    from app.services import concierge_service as cs
    from app.services import founder_intake_service as fis
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    try:
        a = cs.upload(v, c, request.files.get('file'), request.form.get('kind') or '', current_user)
    except (cs.ConciergeError, fis.IntakeError) as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    return jsonify({'artifact': a.to_dict(), 'state': cs.case_for(v.id).state})


@founder_bp.route('/admin/ventures/<venture_id>/documents/<artifact_id>')
@concierge_page
def admin_concierge_download(venture_id, artifact_id):
    """The original document, decrypted, always as an attachment. Audited."""
    from app.models.audit_log import AuditLog
    from app.models.founder import VentureArtifact
    from app.services import founder_intake_service as fis
    v, c = _load(venture_id)
    a = db.session.get(VentureArtifact, artifact_id)
    if a is None or a.venture_id != v.id or not a.file_path:
        abort(404)
    try:
        data = fis.artifact_file_bytes(a)
    except fis.IntakeError as exc:
        abort(exc.status if exc.status in (404, 503) else 404)
    AuditLog.log('concierge_document_download', user_id=current_user.id, resource_id=v.id,
                 metadata={'case': c.id, 'artifact': a.id, 'kind': a.kind, 'version': a.version})
    db.session.commit()
    name = a.filename or ('%s_v%d' % (a.kind, a.version))
    resp = send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype='application/octet-stream', max_age=0)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cache-Control'] = 'no-store, private'
    resp.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return resp


@founder_bp.route('/api/admin/ventures/<venture_id>/fee', methods=['POST'])
@concierge_api
def api_concierge_fee(venture_id):
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    b = request.get_json(silent=True) or {}
    cents = b.get('fee_cents')
    if b.get('clear'):
        cents = None
    elif cents is None or cents == '':
        return _err('Enter the fee, or clear the override.')
    try:
        cs.set_fee_override(c, cents, b.get('reason'), current_user)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    return jsonify({'fee_cents': cs.effective_fee_cents(c), 'override': c.fee_override_cents,
                    'fee': cs.money(cs.effective_fee_cents(c))})


# ── Evaluation, review and confirm (step 6) ──────────────────────────────────

@founder_bp.route('/api/admin/ventures/<venture_id>/evaluate', methods=['POST'])
@concierge_api
def api_concierge_evaluate(venture_id):
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    try:
        run = cs.start_evaluation(v, c, current_user)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    # No query after the background run has started (see start_evaluation).
    return jsonify({'evaluation_run_id': run.id, 'state': 'evaluating'}), 202


@founder_bp.route('/api/admin/ventures/<venture_id>/status')
@concierge_api
def api_concierge_status(venture_id):
    from app.services import concierge_service as cs
    from app.services import founder_evaluation_service as fes
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    try:
        if cs.recover_stuck(v, c, current_user):
            c = cs.case_for(v.id)
    except cs.ConciergeError:
        db.session.rollback()
    run = fes.latest_run(v)
    return jsonify({'state': c.state, 'state_label': cs.STATE_LABELS.get(c.state, c.state),
                    'progress': fes.progress(v) if c.state == 'evaluating' else None,
                    'run': ({'id': run.id, 'status': run.status} if run else None)})


@founder_bp.route('/api/admin/ventures/<venture_id>/evaluate/reset', methods=['POST'])
@concierge_api
def api_concierge_eval_reset(venture_id):
    """'Stop and reset' a stuck evaluation (older than STUCK_RESET_MINUTES)."""
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    try:
        done = cs.recover_stuck(v, c, current_user, force=True)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    if not done:
        return _err('No evaluation is running.', 409)
    return jsonify({'ok': True, 'state': cs.case_for(v.id).state,
                    'message': 'Evaluation reset. Run it again when you are ready.'})


@founder_bp.route('/api/admin/ventures/<venture_id>/stream')
@concierge_api
def api_concierge_stream(venture_id):
    """Live progress (SSE) for the admin venture page: the venture's
    founder_stream, admin-only (the founder stream route needs membership)."""
    from app.services import founder_stream
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    return founder_stream.response(v.id)


@founder_bp.route('/api/admin/ventures/<venture_id>/prefill', methods=['POST'])
@concierge_api
def api_concierge_prefill(venture_id):
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    b = request.get_json(silent=True) or {}
    try:
        cs.edit_prefill(c, b.get('field'), b.get('value'), current_user)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    return jsonify({'prefill': c.prefill})


@founder_bp.route('/api/admin/ventures/<venture_id>/findings/<finding_id>', methods=['POST'])
@concierge_api
def api_concierge_finding(venture_id, finding_id):
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    b = request.get_json(silent=True) or {}
    try:
        f = cs.edit_finding(c, v, finding_id, {k: b[k] for k in cs.FINDING_FIELDS if k in b}, current_user)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    return jsonify({'finding': f.to_dict()})


@founder_bp.route('/api/admin/ventures/<venture_id>/confirm', methods=['POST'])
@concierge_api
def api_concierge_confirm(venture_id):
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    try:
        cs.confirm(v, c, current_user)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    return jsonify({'state': c.state})


# ── Share, invite, extend, messages (step 7) ─────────────────────────────────

def _case_action(venture_id, fn):
    from app.services import concierge_service as cs
    v, c = _load_api(venture_id)
    if v is None:
        return _err('Not found.', 404)
    try:
        out = fn(v, c, request.get_json(silent=True) or {})
    except cs.ConciergeError as exc:
        db.session.rollback()
        return _err(exc, exc.status)
    c = cs.case_for(v.id)
    body = {'state': c.state, 'state_label': cs.STATE_LABELS.get(c.state, c.state),
            'expires_at': c.expires_at.isoformat() if c.expires_at else None, 'days_left': cs.days_left(c),
            'invite_version': c.invite_version}
    body.update(out or {})
    return jsonify(body)


@founder_bp.route('/api/admin/ventures/<venture_id>/share', methods=['POST'])
@concierge_api
def api_concierge_share(venture_id):
    from app.services import concierge_service as cs

    def go(v, c, b):
        _c, emailed = cs.share(v, c, current_user)
        return {'emailed': emailed}
    return _case_action(venture_id, go)


@founder_bp.route('/api/admin/ventures/<venture_id>/resend', methods=['POST'])
@concierge_api
def api_concierge_resend(venture_id):
    from app.services import concierge_service as cs

    def go(v, c, b):
        _c, emailed = cs.resend(v, c, current_user, new_email=b.get('email'))
        return {'emailed': emailed, 'primary_email': _c.primary_email}
    return _case_action(venture_id, go)


@founder_bp.route('/api/admin/ventures/<venture_id>/extend', methods=['POST'])
@concierge_api
def api_concierge_extend(venture_id):
    from app.services import concierge_service as cs
    return _case_action(venture_id, lambda v, c, b: cs.extend(c, b.get('reason'), current_user) and None)


@founder_bp.route('/api/admin/ventures/<venture_id>/reopen', methods=['POST'])
@concierge_api
def api_concierge_reopen(venture_id):
    from app.services import concierge_service as cs
    return _case_action(venture_id, lambda v, c, b: cs.reopen_for_review(c, current_user) and None)


@founder_bp.route('/api/admin/ventures/<venture_id>/messages', methods=['POST'])
@concierge_api
def api_concierge_reply(venture_id):
    from app.services import concierge_service as cs

    def go(v, c, b):
        m = cs.admin_reply(v, c, b.get('body'), current_user)
        return {'message_id': m.id}
    return _case_action(venture_id, go)


@founder_bp.route('/api/admin/ventures/<venture_id>/refund', methods=['POST'])
@concierge_api
def api_concierge_refund(venture_id):
    """Full Stripe refund of the consultation fee (D5: the venture stays the
    founder's and locks again)."""
    from app.services import concierge_service as cs

    def go(v, c, b):
        out = cs.refund(c, b.get('reason'), current_user)
        return {'refund': {'id': out.get('refund_id'), 'status': out.get('status')}}
    return _case_action(venture_id, go)
