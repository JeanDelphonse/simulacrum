"""Founder side of concierge onboarding (FOUNDER_CONCIERGE_PLAN.md 3.10-3.11).

  GET  /founder/concierge/t/<token>    the invitation link: sign in first; only
                                       the verified primary-founder email gets
                                       through, to the preview. Anyone else gets
                                       the M6 page, with no venture data.
  GET  /founder/concierge/<case_id>    the preview (primary founder only; 403
  GET  /api/founder/concierge/<id>     for everyone else, admins included)
  POST /api/founder/concierge/<id>/revision | decline | reopen

These routes are reached from the invitation, so they do not depend on the
founder_track switch: the invitation (and the verified email) is the access.
"""
from __future__ import annotations

import logging

from flask import redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.extensions import db

logger = logging.getLogger(__name__)


def _gate(mode: str, code: int = 200, **ctx):
    return render_template('founder/concierge_gate.html', mode=mode, **ctx), code


@founder_bp.route('/founder/concierge/t/<token>')
def concierge_token(token):
    from app.models.audit_log import AuditLog
    from app.services import concierge_service as cs
    if not getattr(current_user, 'is_authenticated', False):
        # No venture data before sign-in, whatever the token.
        return _gate('signin', next_path=request.path)
    case = cs.case_for_token(token)
    if case is None or not cs.token_usable(case):
        return _gate('invalid', 410)
    if not cs.is_primary(current_user, case):
        reason = 'unverified' if cs.email_matches(current_user, case) else 'other_email'
        AuditLog.log('concierge_invite_refused', user_id=current_user.id, resource_id=case.venture_id,
                     metadata={'case': case.id, 'reason': reason, 'version': case.invite_version})
        db.session.commit()
        return _gate('mismatch', 403, unverified=reason == 'unverified')
    return redirect('/founder/concierge/%s' % case.id)


# ── Preview (step 8) ─────────────────────────────────────────────────────────
# /founder/concierge/<case_id> and its JSON twin: the primary founder only
# (verified matching email). Everyone else, admins included, gets 403.

@founder_bp.record_once
def _register_pending(state):
    def concierge_pending():
        try:
            from app.services import concierge_service as cs
            return cs.pending_for(current_user)
        except Exception:
            return []
    state.app.jinja_env.globals['concierge_pending'] = concierge_pending


def _primary_case(case_id, api: bool = False):
    """(venture, case, None) for the primary founder, else (None, None, response)."""
    from flask import jsonify
    from app.models.founder import ConciergeCase, FounderVenture
    from app.services import concierge_service as cs
    if not getattr(current_user, 'is_authenticated', False):
        if api:
            return None, None, (jsonify({'error': 'Sign in first.'}), 401)
        return None, None, redirect(url_for('pages.login_page') + '?next=' + request.path)
    case = db.session.get(ConciergeCase, case_id) if case_id else None
    venture = db.session.get(FounderVenture, case.venture_id) if case is not None and case.venture_id else None
    if case is None or venture is None:
        return None, None, ((jsonify({'error': 'Not found'}), 404) if api else _gate('invalid', 404))
    if not cs.is_primary(current_user, case):
        return None, None, ((jsonify({'error': 'Only the primary founder can open this evaluation.'}), 403)
                            if api else _gate('mismatch', 403, unverified=cs.email_matches(current_user, case)))
    return venture, case, None


def _preview_kind(case) -> str:
    from datetime import datetime
    from app.services import concierge_service as cs
    now = datetime.utcnow()
    if case.state in ('accepted_paid', 'active'):
        return 'owned'
    in_window = case.expires_at is not None and case.expires_at > now
    if case.state in cs.OPEN_STATES + ('declined',) and in_window:
        return 'preview'
    if case.state in ('evaluating', 'ready_for_review', 'confirmed') and case.shared_at and in_window:
        return 'revising'
    return 'gone'


@founder_bp.route('/founder/concierge/<case_id>')
def concierge_preview(case_id):
    from app.services import concierge_service as cs
    v, c, resp = _primary_case(case_id)
    if resp is not None:
        return resp
    kind = _preview_kind(c)
    if kind == 'owned':
        return redirect('/founder/v/%s' % v.id)
    if kind == 'revising':
        return _gate('revising')
    if kind == 'gone':
        return _gate('invalid', 410)
    c = cs.record_view(v, c, current_user)
    return render_template('founder/concierge_preview.html', p=cs.preview_data(v, c), case_id=c.id,
                           notice=request.args.get('notice'))


@founder_bp.route('/api/founder/concierge/<case_id>')
def api_concierge_preview(case_id):
    from flask import jsonify
    from app.services import concierge_service as cs
    v, c, resp = _primary_case(case_id, api=True)
    if resp is not None:
        return resp
    kind = _preview_kind(c)
    if kind == 'owned':
        return jsonify({'case_id': c.id, 'state': c.state, 'url': '/founder/v/%s' % v.id})
    if kind == 'revising':
        return jsonify({'case_id': c.id, 'state': 'revising'})
    if kind == 'gone':
        return jsonify({'error': 'This evaluation is no longer available.'}), 410
    c = cs.record_view(v, c, current_user)
    return jsonify(cs.preview_data(v, c))


def _founder_action(case_id, fn):
    from flask import jsonify
    from app.blueprints.founder.admin import _same_origin
    from app.services import concierge_service as cs
    if not _same_origin():
        return jsonify({'error': 'Cross-site request refused.'}), 403
    v, c, resp = _primary_case(case_id, api=True)
    if resp is not None:
        return resp
    b = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        fn(v, c, b)
    except cs.ConciergeError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), exc.status
    c = cs.case_for(v.id)
    return jsonify({'ok': True, 'state': c.state})


@founder_bp.route('/api/founder/concierge/<case_id>/revision', methods=['POST'])
def api_concierge_revision(case_id):
    from app.services import concierge_service as cs
    return _founder_action(case_id, lambda v, c, b: cs.request_revision(v, c, b.get('body'), current_user))


@founder_bp.route('/api/founder/concierge/<case_id>/decline', methods=['POST'])
def api_concierge_decline(case_id):
    from app.services import concierge_service as cs

    def go(v, c, b):
        if not b.get('confirm'):
            raise cs.ConciergeError('Confirm that you want to decline.', 400)
        cs.decline(v, c, b.get('reason'), current_user)
    return _founder_action(case_id, go)


@founder_bp.route('/api/founder/concierge/<case_id>/reopen', methods=['POST'])
def api_concierge_undo_decline(case_id):
    from app.services import concierge_service as cs
    return _founder_action(case_id, lambda v, c, b: cs.undo_decline(v, c, current_user))
