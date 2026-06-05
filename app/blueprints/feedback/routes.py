from datetime import datetime

from flask import request, jsonify
from flask_login import login_required, current_user

from app.blueprints.feedback import feedback_bp
from app.extensions import db
from app.models.feedback import UserFeedback
from app.models.simulation import Simulation
from app.models.platform_settings import PlatformSetting

_VALID_LAYERS = {0, 1, 2, 3, 4, 5, 6}
_SEGMENT_DEFAULT_THRESHOLD = 20


# ── Submit new feedback ─────────────────────────────────────────────────────

@feedback_bp.route('/api/feedback', methods=['POST'])
@login_required
def submit_feedback():
    data = request.get_json(force=True, silent=True) or {}

    star_rating = data.get('star_rating')
    outcome_text = (data.get('outcome_text') or '').strip()[:300]
    quote_text = (data.get('quote_text') or '').strip()[:200]
    name_display = data.get('name_display', 'first_last_initial')
    layers_attributed = data.get('layers_attributed') or []
    simulation_id = data.get('simulation_id') or None

    if not star_rating or not isinstance(star_rating, int) or not (1 <= star_rating <= 5):
        return jsonify({'error': 'Star rating (1–5) is required'}), 400
    if not outcome_text:
        return jsonify({'error': 'Outcome text is required'}), 400
    if not quote_text:
        return jsonify({'error': 'Testimonial quote is required'}), 400
    if name_display not in ('full', 'first_last_initial', 'first_only', 'anonymous'):
        return jsonify({'error': 'Invalid name display preference'}), 400

    layers_attributed = [n for n in layers_attributed if isinstance(n, int) and n in _VALID_LAYERS]

    expertise_zone_snapshot = None
    if simulation_id:
        sim = Simulation.query.filter_by(id=simulation_id, user_id=current_user.id).first()
        if sim:
            expertise_zone_snapshot = sim.expertise_zone
        else:
            simulation_id = None

    existing = UserFeedback.query.filter_by(
        user_id=current_user.id, simulation_id=simulation_id
    ).first()
    if existing:
        return jsonify({'error': 'You have already submitted feedback for this simulation.'}), 409

    fb = UserFeedback(
        user_id=current_user.id,
        simulation_id=simulation_id,
        star_rating=star_rating,
        layers_attributed=layers_attributed,
        outcome_text=outcome_text,
        quote_text=quote_text,
        name_display=name_display,
        expertise_zone_snapshot=expertise_zone_snapshot,
    )
    db.session.add(fb)
    db.session.commit()

    _dispatch_submission_emails(fb)

    return jsonify({'ok': True, 'id': fb.id}), 201


def _dispatch_submission_emails(fb: UserFeedback):
    try:
        from app.models.user import User
        from app.models.platform_settings import PlatformSetting
        from app.services.email_service import (
            send_feedback_received_email,
            send_admin_new_feedback_email,
        )
        user = User.query.get(fb.user_id)
        if user:
            send_feedback_received_email(user.email, user.full_name)
        admin_email = PlatformSetting.get('admin_email', None)
        if admin_email:
            send_admin_new_feedback_email(
                admin_email,
                fb.display_name_computed,
                fb.star_rating,
                fb.quote_text,
                fb.outcome_text,
                fb.layer_names_list(),
            )
    except Exception:
        pass


# ── Get own submissions ─────────────────────────────────────────────────────

@feedback_bp.route('/api/feedback/mine', methods=['GET'])
@login_required
def get_my_feedback():
    records = UserFeedback.query.filter_by(
        user_id=current_user.id,
    ).order_by(UserFeedback.submitted_at.desc()).all()

    out = []
    for fb in records:
        out.append({
            'id':           fb.id,
            'star_rating':  fb.star_rating,
            'quote_text':   fb.quote_text,
            'outcome_text': fb.outcome_text,
            'layers_attributed': fb.layers_attributed,
            'name_display': fb.name_display,
            'simulation_id': fb.simulation_id,
            'status':       fb.status,
            'admin_note':   fb.admin_note,
            'is_featured':  fb.is_featured,
            'approved_at':  fb.approved_at.isoformat() if fb.approved_at else None,
            'submitted_at': fb.submitted_at.isoformat(),
            'updated_at':   fb.updated_at.isoformat() if fb.updated_at else None,
            'withdrawn_requested_at': fb.withdrawn_requested_at.isoformat() if fb.withdrawn_requested_at else None,
        })
    return jsonify(out)


# ── Edit pending feedback ───────────────────────────────────────────────────

@feedback_bp.route('/api/feedback/<fb_id>', methods=['PUT'])
@login_required
def edit_feedback(fb_id):
    fb = UserFeedback.query.filter_by(id=fb_id, user_id=current_user.id).first_or_404()
    if fb.status != 'pending':
        return jsonify({'error': 'Only pending submissions can be edited'}), 400

    data = request.get_json(force=True, silent=True) or {}

    if 'outcome_text' in data:
        fb.outcome_text = (data['outcome_text'] or '').strip()[:300]
        if not fb.outcome_text:
            return jsonify({'error': 'Outcome text cannot be blank'}), 400

    if 'quote_text' in data:
        fb.quote_text = (data['quote_text'] or '').strip()[:200]
        if not fb.quote_text:
            return jsonify({'error': 'Quote cannot be blank'}), 400

    if 'name_display' in data:
        if data['name_display'] not in ('full', 'first_last_initial', 'first_only', 'anonymous'):
            return jsonify({'error': 'Invalid name display preference'}), 400
        fb.name_display = data['name_display']

    if 'layers_attributed' in data:
        layers = data['layers_attributed'] or []
        fb.layers_attributed = [n for n in layers if isinstance(n, int) and n in _VALID_LAYERS]

    db.session.commit()
    return jsonify({'ok': True})


# ── Request withdrawal ──────────────────────────────────────────────────────

@feedback_bp.route('/api/feedback/<fb_id>/withdraw', methods=['POST'])
@login_required
def request_withdrawal(fb_id):
    fb = UserFeedback.query.filter_by(id=fb_id, user_id=current_user.id).first_or_404()
    if fb.status not in ('approved',):
        return jsonify({'error': 'Only approved testimonials can be withdrawn'}), 400
    if fb.withdrawn_requested_at:
        return jsonify({'error': 'Withdrawal already requested'}), 400

    fb.withdrawn_requested_at = datetime.utcnow()
    db.session.commit()

    try:
        from app.services.email_service import send_feedback_withdrawal_request_email
        admin_email = PlatformSetting.get('admin_email', None)
        if admin_email:
            send_feedback_withdrawal_request_email(admin_email, fb.display_name_computed, fb.id)
    except Exception:
        pass

    return jsonify({'ok': True})


# ── Public endpoints ────────────────────────────────────────────────────────

@feedback_bp.route('/api/feedback/public', methods=['GET'])
def get_public_feedback():
    utm_source = (request.args.get('utm_source') or '').lower()

    threshold = int(PlatformSetting.get('feedback_segment_threshold', str(_SEGMENT_DEFAULT_THRESHOLD)))

    approved = UserFeedback.query.filter_by(status='approved').all()
    total_approved = len(approved)

    featured = [f for f in approved if f.is_featured]
    non_featured = [f for f in approved if not f.is_featured]

    featured.sort(key=lambda f: (f.display_order or 9999, f.approved_at or datetime.min))
    non_featured.sort(key=lambda f: (f.display_order or 9999, f.approved_at or datetime.min))

    if total_approved >= threshold and utm_source:
        if utm_source in ('twitter', 'x'):
            non_featured.sort(key=lambda f: -f.star_rating)
        elif utm_source == 'linkedin':
            def _linkedin_rank(f):
                layers = f.layers_attributed or []
                return (0 if (1 in layers or 2 in layers) else 1, f.display_order or 9999)
            non_featured.sort(key=_linkedin_rank)

    ordered = featured + non_featured
    return jsonify([f.to_public_dict() for f in ordered[:50]])


@feedback_bp.route('/api/feedback/stats/public', methods=['GET'])
def get_public_stats():
    from sqlalchemy import func
    from app.models.simulation import Simulation as Sim

    avg_rating = db.session.query(
        func.avg(UserFeedback.star_rating)
    ).filter_by(status='approved').scalar() or 0.0

    approved_count = UserFeedback.query.filter_by(status='approved').count()
    total_sims = Sim.query.count()

    return jsonify({
        'avg_rating':     round(float(avg_rating), 1),
        'approved_count': approved_count,
        'total_simulations': total_sims,
    })
