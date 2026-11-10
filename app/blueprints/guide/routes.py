"""SME Console routes. Every user route checks the beta gate (fails closed) and
touches only the signed-in user's own records."""
from __future__ import annotations

from functools import wraps

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.guide import guide_bp
from app.extensions import db
from app.services import guide_service as gs


def _err(exc, code=400):
    return jsonify({'error': 'refused', 'message': str(exc)}), code


def _status_for(exc) -> int:
    return 429 if 'limit' in str(exc).lower() or "you've used" in str(exc).lower() else 400


def gated(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not gs.is_enabled(current_user):
            return jsonify({'error': 'not_available',
                            'message': 'The SME Console is not available on your account yet.'}), 403
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# ── User: console ─────────────────────────────────────────────────────────────

def _sim():
    """The simulation this console is about (?sim=<id>), if it's the member's own."""
    return gs.simulation_for(current_user.id, request.args.get('sim'))


def _boot(sim=None) -> dict:
    p = gs.prefs(current_user.id)
    g = gs.current_guide(current_user.id)
    sim = sim or _sim()
    return {'guides': [x.to_dict() for x in gs.guides()], 'guide_id': g.id, 'has_chosen': bool(p.guide_id),
            'voice_enabled': p.voice_enabled,
            'speech_in': str(gs.setting('guide_browser_speech_in')).lower() == 'on',
            'first_audio_timeout_s': gs.setting('guide_first_audio_timeout_s'),
            'layers': gs.LAYER_NAMES, 'state': gs.console_state(current_user.id, sim.id if sim else None),
            'simulation': ({'id': sim.id, 'name': sim.name or 'Simulation',
                            'created': sim.created_at.strftime('%b %d, %Y') if sim.created_at else ''} if sim else None),
            'first_name': (current_user.full_name or '').split(' ')[0]}


@guide_bp.route('/guide')
@login_required
def console_page():
    """The SME Console for one simulation: /guide?sim=<id>. Without one, the
    member picks a simulation (or goes straight in when they have only one)."""
    if not gs.is_enabled(current_user):
        return render_template('guide/unavailable.html'), 403
    sim = _sim()
    if sim is None:
        sims = gs.completed_simulations(current_user.id)
        if len(sims) == 1:
            return redirect(url_for('guide.console_page', sim=sims[0].id))
        return render_template('guide/choose_simulation.html', sims=sims)
    return render_template('guide/console.html', boot=_boot(sim))


@guide_bp.route('/api/guide/state')
@gated
def api_state():
    return jsonify(_boot())


@guide_bp.route('/api/guide/prefs', methods=['POST'])
@gated
def api_prefs():
    b = request.get_json(silent=True) or {}
    try:
        if b.get('guide_id'):
            gs.choose_guide(current_user.id, b['guide_id'], b.get('voice_enabled'))
        elif 'voice_enabled' in b:
            p = gs.prefs(current_user.id)
            p.voice_enabled = bool(b['voice_enabled'])
            db.session.commit()
    except gs.GuideError as exc:
        return _err(exc)
    return jsonify(_boot())


@guide_bp.route('/api/guide/sessions', methods=['POST'])
@gated
def api_start():
    b = request.get_json(silent=True) or {}
    try:
        layer = int(b['layer']) if str(b.get('layer') or '') in ('1', '2', '3', '4', '5') else None
        s, facts, turn = gs.start_session(current_user, layer, request.args.get('sim') or b.get('simulation_id'))
    except gs.GuideError as exc:
        return _err(exc, _status_for(exc))
    return jsonify({'session_id': s.id, 'layer': s.layer, 'facts': facts, 'turn': turn,
                    'state': gs.console_state(current_user.id, s.simulation_id)})


@guide_bp.route('/api/guide/sessions/<session_id>/turn', methods=['POST'])
@gated
def api_turn(session_id):
    b = request.get_json(silent=True) or {}
    try:
        turn = gs.turn(current_user, session_id, b.get('text'), b.get('input_mode') or 'typed', b.get('event'))
    except gs.GuideError as exc:
        return _err(exc, _status_for(exc))
    sess = gs.owned_session(current_user.id, session_id)
    return jsonify({'turn': turn, 'state': gs.console_state(current_user.id, getattr(sess, 'simulation_id', None))})


@guide_bp.route('/api/guide/sessions/<session_id>/end', methods=['POST'])
@gated
def api_end(session_id):
    try:
        gs.end_session(current_user, session_id)
    except gs.GuideError as exc:
        return _err(exc)
    return jsonify({'ok': True})


@guide_bp.route('/api/guide/skills/<suggestion_id>', methods=['POST', 'DELETE'])
@gated
def api_skill(suggestion_id):
    try:
        if request.method == 'DELETE':
            gs.remove_skill(current_user.id, suggestion_id)
        else:
            gs.decide_skill(current_user.id, suggestion_id, bool((request.get_json(silent=True) or {}).get('accept')))
    except gs.GuideError as exc:
        return _err(exc)
    sim = _sim()
    return jsonify(gs.console_state(current_user.id, sim.id if sim else None))


@guide_bp.route('/api/guide/goals/<card_id>', methods=['PUT'])
@gated
def api_edit_goal(card_id):
    try:
        card = gs.edit_goal(current_user.id, card_id, request.get_json(silent=True) or {})
    except gs.GuideError as exc:
        return _err(exc)
    return jsonify(gs.console_state(current_user.id, getattr(card, 'simulation_id', None)))


@guide_bp.route('/api/guide/goals/<card_id>/approve', methods=['POST'])
@gated
def api_approve_goal(card_id):
    try:
        card = gs.approve_goal(current_user.id, card_id)
    except gs.GuideError as exc:
        return _err(exc)
    return jsonify({'card': card.to_dict(), 'state': gs.console_state(current_user.id, card.simulation_id)})


# ── User: history (always reachable, even if the beta gate closes) ───────────

@guide_bp.route('/guide/history')
@login_required
def history_page():
    return render_template('guide/history.html', sessions=gs.history(current_user.id),
                           state=gs.console_state(current_user.id), layers=gs.LAYER_NAMES)


@guide_bp.route('/api/guide/history', methods=['GET', 'DELETE'])
@login_required
def api_history():
    if request.method == 'DELETE':
        return jsonify({'deleted': gs.delete_history(current_user.id)})
    return jsonify({'sessions': gs.history(current_user.id)})


@guide_bp.route('/api/guide/history/<session_id>', methods=['GET', 'DELETE'])
@login_required
def api_history_one(session_id):
    try:
        if request.method == 'DELETE':
            gs.owned_session(current_user.id, session_id)
            return jsonify({'deleted': gs.delete_history(current_user.id, session_id)})
        return jsonify({'turns': gs.transcript(current_user.id, session_id)})
    except gs.GuideError as exc:
        return _err(exc, 404)


@guide_bp.route('/api/guide/my-skills/<suggestion_id>', methods=['DELETE'])
@login_required
def api_remove_skill(suggestion_id):
    gs.remove_skill(current_user.id, suggestion_id)
    return jsonify(gs.console_state(current_user.id))


# ── Admin (metadata and settings only; transcripts are never shown) ──────────

@guide_bp.route('/admin/guide')
@login_required
def admin_page():
    if not current_user.is_admin:
        abort(403)
    from app.models.mail_crm import UserFeatureOverride
    from app.models.user import User
    rows = UserFeatureOverride.query.filter(UserFeatureOverride.feature_key == gs.FEATURE_KEY,
                                            UserFeatureOverride.state != 'follow_global').all()
    users = {u.id: u for u in User.query.filter(User.id.in_([r.user_id for r in rows] or ['-'])).all()}
    return render_template('admin/guide.html', mode=gs.mode(), metrics=gs.admin_metrics(),
                           guides=[g.to_dict() for g in gs.guides(enabled_only=False)],
                           overrides=[{'email': getattr(users.get(r.user_id), 'email', r.user_id), 'state': r.state}
                                      for r in rows],
                           settings={k: gs.setting(k) for k in gs.DEFAULTS})


@guide_bp.route('/api/admin/guide/mode', methods=['POST'])
@admin_required
def api_admin_mode():
    try:
        gs.set_mode((request.get_json(silent=True) or {}).get('mode'), current_user.id)
    except gs.GuideError as exc:
        return _err(exc)
    return jsonify({'mode': gs.mode()})


@guide_bp.route('/api/admin/guide/users', methods=['POST'])
@admin_required
def api_admin_users():
    from app.models.user import User
    b = request.get_json(silent=True) or {}
    emails = [e.strip().lower() for e in (b.get('emails') or []) if e and e.strip()]
    found = User.query.filter(db.func.lower(User.email).in_(emails or ['-'])).all()
    missing = sorted(set(emails) - {u.email.lower() for u in found})
    if missing:
        return _err('No user with: ' + ', '.join(missing))
    try:
        n = gs.set_user_access([u.id for u in found], b.get('state') or 'allowed', current_user.id)
    except gs.GuideError as exc:
        return _err(exc)
    return jsonify({'updated': n})


@guide_bp.route('/api/admin/guide/guides/<guide_id>', methods=['POST'])
@admin_required
def api_admin_guide(guide_id):
    from app.models.sme_guide import SmeGuide
    g = SmeGuide.query.get_or_404(guide_id)
    b = request.get_json(silent=True) or {}
    if 'enabled' in b:
        g.enabled = bool(b['enabled'])
    for k, cast in (('pitch', float), ('rate', float), ('sort_order', int), ('voice_index', int)):
        if k in b:
            try:
                setattr(g, k, cast(b[k]))
            except (TypeError, ValueError):
                return _err('Invalid %s.' % k)
    for k in ('voice_ref', 'voice_label', 'style_label'):
        if k in b:
            setattr(g, k, (str(b[k]).strip() or None) if b[k] is not None else None)
    db.session.flush()
    if not SmeGuide.query.filter_by(enabled=True).count():
        db.session.rollback()
        return _err('At least one guide must stay enabled.')
    db.session.commit()
    return jsonify(g.to_dict())
