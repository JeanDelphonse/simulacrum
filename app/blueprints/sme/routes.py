"""SIM-PRD-SME-002 — SME console + recommendation API.

Two surfaces, both JSON:
  * /api/sme/*          — the SME console (sme_required). Read-only over assigned users,
                          write only to the SME's own recommendations / profile.
  * /api/sme-advisor/*  — the USER side of the loop (login_required). Apply / dismiss a
                          recommendation (user is the actor), opt out, request-different,
                          re-opt-in.

RBAC (FR-SMV-01/02): sme_required resolves the SME from the login session; every
per-user endpoint re-checks assigned-only scope and logs denials as security events.
"""
from functools import wraps
from flask import request, jsonify, g
from flask_login import login_required, current_user

from app.blueprints.sme import sme_bp, sme_user_bp
from app.extensions import db
from app.models.sme import SimiSME, SmeRecommendation
from app.models.profile import UserProfile
from app.services import sme_console_service as svc


# ── RBAC ──────────────────────────────────────────────────────────────────────

def sme_required(f):
    """Resolve the active SME behind this login session or 403. Stashes it on g.sme."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        sme = svc.get_sme_for_login(current_user)
        if not sme:
            return jsonify({'error': 'SME console access required'}), 403
        g.sme = sme
        return f(*args, **kwargs)
    return decorated


def _require_assigned(sme, user_id):
    """Return the assigned profile or (None, error_response). Logs denials (FR-SMV-02)."""
    profile = svc.assigned_profile(sme, user_id)
    if not profile:
        svc.log_denial(sme, user_id)
        return None, (jsonify({'error': 'Not found'}), 404)
    return profile, None


# ── SME console: identity + caseload ──────────────────────────────────────────

@sme_bp.route('/me', methods=['GET'])
@sme_required
def me():
    return jsonify(g.sme.to_dict()), 200


@sme_bp.route('/caseload', methods=['GET'])
@sme_required
def caseload():
    svc.log_access(g.sme.id, 'view_caseload')
    return jsonify({
        'sme': {'id': g.sme.id, 'name': g.sme.full_name, 'can_view_l5': g.sme.can_view_l5,
                'capacity': g.sme.capacity, 'assigned_count': g.sme.assigned_count},
        'users': svc.caseload(g.sme),
    }), 200


# ── SME console: user drill-down (read-only) ──────────────────────────────────

@sme_bp.route('/users/<user_id>', methods=['GET'])
@sme_required
def user_overview(user_id):
    profile, err = _require_assigned(g.sme, user_id)
    if err:
        return err
    svc.log_access(g.sme.id, 'view_user', user_id=user_id)
    return jsonify(svc.user_overview(g.sme, profile)), 200


@sme_bp.route('/users/<user_id>/simulations/<sim_id>', methods=['GET'])
@sme_required
def user_simulation(user_id, sim_id):
    """Agents-by-layer, signal pipeline, and artifacts for one simulation (L5-gated)."""
    from app.models.simulation import Simulation
    profile, err = _require_assigned(g.sme, user_id)
    if err:
        return err
    sim = Simulation.query.filter_by(id=sim_id, user_id=user_id).first()
    if not sim:
        return jsonify({'error': 'Simulation not found'}), 404
    svc.log_access(g.sme.id, 'view_agents', user_id=user_id, detail={'sim_id': sim_id})
    return jsonify({
        'simulation': {
            'id': sim.id, 'name': sim.name, 'expertise_zone': sim.expertise_zone,
            'lifecycle_phase': sim.lifecycle_phase, 'status': sim.status,
        },
        'agents_by_layer': svc.agents_by_layer(sim),
        'signals': svc.signal_pipeline(user_id),
        'artifacts': svc.artifacts_for_sim(sim, g.sme),
        'can_view_l5': g.sme.can_view_l5,
    }), 200


# ── SME console: recommendations ──────────────────────────────────────────────

@sme_bp.route('/recommendations', methods=['GET'])
@sme_required
def my_recommendations():
    """Log across all the SME's users: statuses + apply rate (advice-quality feedback only)."""
    recs = (
        SmeRecommendation.query.filter_by(sme_id=g.sme.id)
        .order_by(SmeRecommendation.created_at.desc()).all()
    )
    # Apply rate is framed as learning feedback, never a volume/leaderboard metric (FR-SMV-10).
    resolved = [r for r in recs if r.status in (
        SmeRecommendation.STATUS_APPLIED, SmeRecommendation.STATUS_DISMISSED)]
    applied = [r for r in recs if r.status == SmeRecommendation.STATUS_APPLIED]
    apply_rate = round(len(applied) / len(resolved), 2) if resolved else None

    # Resolve display names for the SME's users in one pass.
    from app.models.user import User
    uids = list({r.user_id for r in recs})
    profiles = {p.user_id: p for p in UserProfile.query.filter(UserProfile.user_id.in_(uids)).all()} if uids else {}
    users = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}

    def _name(uid):
        p = profiles.get(uid)
        if p and p.display_name:
            return p.display_name
        u = users.get(uid)
        return (u.full_name or u.email) if u else uid

    out = []
    for r in recs:
        d = r.to_dict()
        d['user_name'] = _name(r.user_id)
        out.append(d)
    return jsonify({
        'recommendations': out,
        'apply_rate': apply_rate,   # advice-quality feedback only
        'total': len(recs),
    }), 200


@sme_bp.route('/users/<user_id>/recommendations', methods=['POST'])
@sme_required
def issue_recommendation(user_id):
    profile, err = _require_assigned(g.sme, user_id)
    if err:
        return err
    data = request.get_json() or {}
    rec_type = (data.get('type') or '').strip()
    rationale = (data.get('rationale') or '').strip()
    payload = data.get('payload') or {}
    sim_id = data.get('simulation_id')

    if rec_type not in SmeRecommendation.ALL_TYPES:
        return jsonify({'error': 'Unknown recommendation type'}), 400
    if not rationale:
        return jsonify({'error': 'A rationale is required'}), 400

    # Sim-scoped recommendations must reference a simulation the user owns.
    if sim_id:
        from app.models.simulation import Simulation
        if not Simulation.query.filter_by(id=sim_id, user_id=user_id).first():
            return jsonify({'error': 'Simulation not found for this user'}), 400

    try:
        rec = svc.create_recommendation(
            g.sme, profile, rec_type, rationale,
            payload=payload, simulation_id=sim_id,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(rec.to_dict()), 201


# ── SME console: own profile ──────────────────────────────────────────────────

@sme_bp.route('/profile', methods=['GET', 'PUT'])
@sme_required
def profile():
    sme = g.sme
    if request.method == 'GET':
        return jsonify(sme.to_dict()), 200
    # Editable fields limited; zone changes go through the admin (§7).
    data = request.get_json() or {}
    if 'phone' in data:
        sme.phone = (data.get('phone') or '').strip() or None
    if 'bio_url' in data:
        sme.bio_url = (data.get('bio_url') or '').strip() or None
    db.session.commit()
    return jsonify(sme.to_dict()), 200


# ══════════════════════════════════════════════════════════════════════════════
# USER side of the loop — /api/sme-advisor (login_required, the user is the actor)
# ══════════════════════════════════════════════════════════════════════════════

def _my_profile():
    return UserProfile.query.filter_by(user_id=current_user.id).first()


@sme_user_bp.route('/card', methods=['GET'])
@login_required
def expert_card():
    """The 'Your Simi Expert' card + pending recommendations for the current user."""
    profile = _my_profile()
    card = svc.expert_card(profile)
    sim_id = request.args.get('simulation_id')
    pending = svc.pending_recommendations(current_user.id, simulation_id=sim_id) if card else []
    return jsonify({
        'expert': card,
        'pending_recommendations': [r.to_dict(include_sme=True) for r in pending],
        'opted_out': bool(profile and profile.sme_opted_out),
    }), 200


@sme_user_bp.route('/recommendations/<rec_id>/seen', methods=['POST'])
@login_required
def mark_recommendation_seen(rec_id):
    rec = SmeRecommendation.query.filter_by(id=rec_id, user_id=current_user.id).first()
    if not rec:
        return jsonify({'error': 'Not found'}), 404
    svc.mark_seen(current_user.id, rec_ids=[rec_id])
    return jsonify({'ok': True}), 200


@sme_user_bp.route('/recommendations/<rec_id>/apply', methods=['POST'])
@login_required
def apply_recommendation(rec_id):
    rec = SmeRecommendation.query.filter_by(id=rec_id, user_id=current_user.id).first()
    if not rec:
        return jsonify({'error': 'Not found'}), 404
    ok, msg = svc.apply_recommendation(rec, current_user)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg, 'recommendation': rec.to_dict()}), 200


@sme_user_bp.route('/recommendations/<rec_id>/dismiss', methods=['POST'])
@login_required
def dismiss_recommendation(rec_id):
    rec = SmeRecommendation.query.filter_by(id=rec_id, user_id=current_user.id).first()
    if not rec:
        return jsonify({'error': 'Not found'}), 404
    reason = (request.get_json() or {}).get('reason')
    ok, msg = svc.dismiss_recommendation(rec, current_user, reason=reason)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg}), 200


@sme_user_bp.route('/opt-out', methods=['POST'])
@login_required
def opt_out():
    profile = _my_profile()
    if not profile:
        return jsonify({'error': 'No profile'}), 400
    svc.opt_out(profile)
    return jsonify({'ok': True, 'opted_out': True}), 200


@sme_user_bp.route('/request-different', methods=['POST'])
@login_required
def request_different():
    profile = _my_profile()
    if not profile or not profile.sme_id:
        return jsonify({'error': 'You have no assigned expert.'}), 400
    svc.request_different(profile)
    return jsonify({'ok': True, 'flagged': True}), 200


@sme_user_bp.route('/opt-in', methods=['POST'])
@login_required
def opt_in():
    profile = _my_profile()
    if not profile:
        return jsonify({'error': 'No profile'}), 400
    sme = svc.opt_in(profile)
    return jsonify({
        'ok': True,
        'assigned': bool(sme),
        'expert': svc.expert_card(profile),
    }), 200

