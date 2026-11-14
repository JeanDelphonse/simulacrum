"""Founder-side startup page editor (FOUNDER_PLAN.md WP6).

    GET  /founder/v/<id>/page                  the editor (drafts the page on first visit)
    GET  /founder/v/<id>/page/preview          the page as visitors will see it, any status
    POST /api/founder/v/<id>/page              save fields, traction and slug (JSON)
    POST /api/founder/v/<id>/page/logo         upload a logo (multipart, field "logo")
    POST /api/founder/v/<id>/page/redraft      fill empty sections from the latest evaluation
    POST /api/founder/v/<id>/page/publish      {"action": "request" | "publish" | "unpublish"}
    GET  /api/founder/v/<id>/page/analytics    views over time

The startup page is part of the paid Founder Track: for an unpaid venture the
pages redirect to checkout (the owner) or the report (co-founders) and the
APIs answer 402 with the checkout URL, like the workspace (WP4).
"""
from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required, venture_nav


def _checkout_url(v) -> str:
    return url_for('founder.checkout_page', venture_id=v.id)


def _load(venture_id, api: bool = False):
    """(venture, page) for a member of a paid venture. Unpaid: pages redirect
    to checkout (owner) or the report (co-founders); APIs answer 402."""
    from app.services import founder_ventures
    from app.services import startup_page_service as sps
    v = founder_ventures.require_member(venture_id, current_user)
    if not founder_ventures.is_paid(v):
        if api:
            resp = jsonify({'ok': False, 'error': 'The startup page unlocks with the Founder Track.',
                            'checkout_url': _checkout_url(v)})
            resp.status_code = 402
            abort(resp)
        if v.owner_user_id == current_user.id:
            abort(redirect(_checkout_url(v)))
        abort(redirect(url_for('founder.report_page', venture_id=v.id)))
    return v, sps.ensure_page(v, current_user)


def _state(v, page) -> dict:
    from app.extensions import db
    from app.models.user import User
    from app.services import startup_page_service as sps
    holder = sps.publish_holder(v)
    hu = db.session.get(User, holder.user_id) if holder is not None and holder.user_id else None
    req = sps.pending_request(page)
    ru = db.session.get(User, req['user_id']) if req and req.get('user_id') else None
    return {
        'page': page.to_dict(),
        'can_publish': sps.can_publish(page, current_user),
        'holder_name': getattr(hu, 'full_name', None),
        'is_holder': bool(hu is not None and hu.id == current_user.id),
        'missing': sps.missing_for_publish(page),
        'pending_request': ({'by': getattr(ru, 'full_name', 'A co-founder'),
                             'at': req['at'].strftime('%d %b %Y')} if req else None),
        'public': sps.is_public(page),
    }


@founder_bp.route('/founder/v/<venture_id>/page')
@founder_required
def page_editor(venture_id):
    from app.services import startup_page_service as sps
    v, page = _load(venture_id)
    return render_template('founder/page_editor.html', venture=v, page=page, state=_state(v, page),
                           stats=sps.analytics(page), stages=sps.STAGE_LABELS, labels=sps.FIELD_LABELS,
                           supported=sps.supported_fields(v), traction_max=sps.TRACTION_MAX,
                           field_max=sps.FIELD_MAX, nav=venture_nav(v))


@founder_bp.route('/founder/v/<venture_id>/page/preview')
@founder_required
def page_preview(venture_id):
    from app.blueprints.startup.routes import render_page
    from app.services import startup_page_service as sps
    v, page = _load(venture_id)
    ctx = sps.public_context(page)
    ctx['editor_url'] = url_for('founder.page_editor', venture_id=v.id)
    resp, status = render_page(page, ctx, preview=True)
    return resp, status


@founder_bp.route('/api/founder/v/<venture_id>/page', methods=['POST'])
@founder_required
def page_save(venture_id):
    from app.extensions import db
    from app.services import startup_page_service as sps
    v, page = _load(venture_id, api=True)
    data = request.get_json(silent=True) or {}
    try:
        changes = sps.update(page, data, current_user, commit=False)
        if 'slug' in data and (data.get('slug') or '').strip().lower() != page.slug:
            sps.set_slug(page, data.get('slug'), current_user, commit=False)
            changes.append('slug')
        db.session.commit()
    except sps.PageError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    msg = 'Saved.' if changes else 'No changes to save.'
    if page.name_check != 'clear':
        msg += ' ' + sps.name_check_message(page)
    return jsonify(dict(_state(v, page), changes=changes, message=msg))


@founder_bp.route('/api/founder/v/<venture_id>/page/logo', methods=['POST'])
@founder_required
def page_logo_upload(venture_id):
    from app.services import startup_page_service as sps
    v, page = _load(venture_id, api=True)
    try:
        sps.save_logo(page, request.files.get('logo'), current_user)
    except sps.PageError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify(dict(_state(v, page), message='Logo saved.'))


@founder_bp.route('/api/founder/v/<venture_id>/page/redraft', methods=['POST'])
@founder_required
def page_redraft(venture_id):
    from app.services import startup_page_service as sps
    v, page = _load(venture_id, api=True)
    filled = sps.refresh_from_evaluation(page, current_user)
    msg = ('Filled %s from the evaluation.' % ', '.join(sps.FIELD_LABELS[f].lower() for f in filled)) if filled \
        else 'Nothing to fill: the empty sections have no cited passage in the latest evaluation.'
    return jsonify(dict(_state(v, page), filled=filled, message=msg))


@founder_bp.route('/api/founder/v/<venture_id>/page/publish', methods=['POST'])
@founder_required
def page_publish(venture_id):
    from app.services import startup_page_service as sps
    v, page = _load(venture_id, api=True)
    action = (request.get_json(silent=True) or {}).get('action')
    try:
        if action == 'request':
            out = sps.request_publish(page, current_user)
            msg = out['message']
        elif action == 'publish':
            sps.publish(page, current_user)
            msg = 'The page is live.'
        elif action == 'unpublish':
            sps.unpublish(page, current_user)
            msg = 'The page is offline. Visitors now get "not found".'
        else:
            return jsonify({'error': 'Unknown action.'}), 400
    except sps.PageError as exc:
        return jsonify(dict(_state(v, page), error=str(exc))), 403
    return jsonify(dict(_state(v, page), message=msg))


@founder_bp.route('/api/founder/v/<venture_id>/page/analytics')
@founder_required
def page_analytics(venture_id):
    from app.services import startup_page_service as sps
    v, page = _load(venture_id, api=True)
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    return jsonify(sps.analytics(page, days))
