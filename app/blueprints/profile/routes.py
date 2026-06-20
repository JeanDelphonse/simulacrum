import os
import re
import secrets
import hashlib
from datetime import datetime, timedelta

from flask import request, jsonify, current_app
from flask_login import login_required, current_user

from app.blueprints.profile import profile_bp
from app.extensions import db, bcrypt
from app.models.profile import UserProfile, SimulationVisibility, UserSession
from app.models.user import User
from app.models.simulation import Simulation, SimulationLayer
from app.models.audit_log import AuditLog
from utils.id_gen import generate_id

_USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,28}[a-z0-9]$')
_RESERVED = {
    'admin', 'support', 'api', 'app', 'www', 'help', 'terms', 'privacy',
    'login', 'signup', 'settings', 'u', 'static', 'dashboard', 'register',
}


def _get_or_create_profile(user):
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        base = re.sub(r'[^a-z0-9]', '-', user.full_name.lower())
        base = re.sub(r'-+', '-', base).strip('-')[:28]
        slug = base or 'user'
        candidate = slug
        n = 1
        while UserProfile.query.filter_by(username=candidate).first():
            candidate = f'{slug}-{n}'
            n += 1
        profile = UserProfile(
            id=generate_id(),
            user_id=user.id,
            username=candidate,
            display_name=user.full_name,
        )
        db.session.add(profile)
        db.session.flush()
    return profile


# ── Username availability ──────────────────────────────────────────────────

@profile_bp.route('/api/profile/username-check')
def username_check():
    username = (request.args.get('username') or '').lower().strip()
    if not username:
        return jsonify({'available': False, 'error': 'username required'}), 400
    if len(username) < 3 or len(username) > 30:
        return jsonify({'available': False, 'error': '3–30 characters required'})
    if not _USERNAME_RE.match(username):
        return jsonify({'available': False, 'error': 'Lowercase letters, numbers, and hyphens only'})
    if username in _RESERVED:
        return jsonify({'available': False, 'error': 'That username is reserved'})
    current_id = None
    if current_user.is_authenticated:
        p = UserProfile.query.filter_by(user_id=current_user.id).first()
        if p:
            current_id = p.id
    existing = UserProfile.query.filter_by(username=username).first()
    if existing and existing.id != current_id:
        return jsonify({'available': False})
    return jsonify({'available': True})


# ── Profile settings ───────────────────────────────────────────────────────

@profile_bp.route('/api/settings/profile', methods=['GET'])
@login_required
def get_profile():
    profile = _get_or_create_profile(current_user)
    db.session.commit()
    return jsonify(_profile_dict(profile))


@profile_bp.route('/api/settings/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.get_json(force=True, silent=True) or {}
    profile = _get_or_create_profile(current_user)

    if 'username' in data:
        username = data['username'].lower().strip()
        if len(username) < 3 or len(username) > 30:
            return jsonify({'error': '3–30 characters required for username'}), 400
        if not _USERNAME_RE.match(username):
            return jsonify({'error': 'Lowercase letters, numbers, and hyphens only'}), 400
        if username in _RESERVED:
            return jsonify({'error': 'That username is reserved'}), 400
        conflict = UserProfile.query.filter(
            UserProfile.username == username, UserProfile.id != profile.id
        ).first()
        if conflict:
            return jsonify({'error': 'Username already taken'}), 409
        if profile.username != username:
            profile.username = username

    str_fields = {
        'display_name': 100, 'tagline': 200, 'location': 100,
        'linkedin_url': 255, 'website_url': 255, 'twitter_url': 255,
        'other_link_url': 255, 'other_link_label': 50,
        'booking_url': 255, 'booking_btn_label': 50,
    }
    for field, max_len in str_fields.items():
        if field in data:
            val = (data[field] or '').strip()[:max_len]
            setattr(profile, field, val or None)

    bool_fields = ('show_contact_form', 'show_booking_btn', 'noindex')
    for field in bool_fields:
        if field in data:
            setattr(profile, field, bool(data[field]))

    if 'bio' in data:
        profile.bio = (data['bio'] or '').strip()[:10000] or None

    # SIM-PRD-BIO-003: JSON section arrays
    json_list_fields = (
        'career_history', 'notable_work', 'ventures', 'education',
        'certifications', 'references_press', 'publications', 'projects',
    )
    for field in json_list_fields:
        if field in data:
            val = data[field]
            if isinstance(val, list):
                setattr(profile, field, val)

    if 'bio_sections_visible' in data and isinstance(data['bio_sections_visible'], dict):
        profile.bio_sections_visible = data['bio_sections_visible']
        profile.bio_edited = True

    profile.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(_profile_dict(profile))


@profile_bp.route('/api/settings/profile/bio/generate', methods=['POST'])
@login_required
def generate_bio():
    profile = _get_or_create_profile(current_user)
    db.session.commit()

    body = request.get_json(silent=True) or {}
    existing_bio = (body.get('existing_bio') or '').strip() or None

    from app.models.resume import Resume
    resume = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).first()

    from app.models.published_page import PublishedPage
    from app.models.simulation import Simulation

    # 1. Prefer published simulations
    published_sim_ids = {
        p.simulation_id for p in
        PublishedPage.query.filter_by(status='live').all()
    }
    published_sims = [
        s for s in Simulation.query.filter_by(user_id=current_user.id).all()
        if s.id in published_sim_ids
    ]

    zones = []
    for sim in published_sims:
        if sim.expertise_zone:
            zones.append({'zone_name': sim.expertise_zone, 'evidence': sim.name or ''})

    # 2. Fall back to resume expertise zones
    if not zones and resume and resume.expertise_zones:
        zones = resume.expertise_zones

    # 3. Fall back to any completed simulation
    if not zones:
        for sim in Simulation.query.filter_by(user_id=current_user.id, status='complete').all():
            if sim.expertise_zone:
                zones.append({'zone_name': sim.expertise_zone})

    # 4. Fall back to ANY simulation (user has one but it may not be complete yet)
    if not zones:
        for sim in Simulation.query.filter_by(user_id=current_user.id).all():
            if sim.expertise_zone:
                zones.append({'zone_name': sim.expertise_zone})

    resume_text = (resume.parsed_text if resume and resume.parsed_text else '') or ''

    # Only block if there is genuinely nothing to work from
    if not resume_text and not zones and not (profile.tagline or profile.bio or current_user.full_name):
        return jsonify({'error': 'Upload a resume or create a simulation first'}), 400

    try:
        from app.services.bio_service import generate_wikipedia_bio
        bio_text = generate_wikipedia_bio(profile, resume_text, zones, existing_bio=existing_bio)
    except Exception as e:
        current_app.logger.error('Bio generation failed: %s', e)
        return jsonify({'error': 'Bio generation failed. Please try again.'}), 500

    return jsonify({'bio': bio_text})


@profile_bp.route('/api/settings/profile/avatar', methods=['POST'])
@login_required
def upload_avatar():
    if 'avatar' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['avatar']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png'):
        return jsonify({'error': 'JPEG or PNG only'}), 400
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 2 * 1024 * 1024:
        return jsonify({'error': 'File must be under 2 MB'}), 400

    upload_base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    avatar_dir = os.path.join(upload_base, 'avatars', current_user.id)
    os.makedirs(avatar_dir, exist_ok=True)
    filename = f'avatar.{ext}'
    path = os.path.join(avatar_dir, filename)
    f.save(path)

    profile = _get_or_create_profile(current_user)
    profile.avatar_path = os.path.join('avatars', current_user.id, filename)
    db.session.commit()
    return jsonify({'avatar_path': profile.avatar_path})


# ── Visibility settings ────────────────────────────────────────────────────

@profile_bp.route('/api/settings/visibility', methods=['GET'])
@login_required
def get_visibility():
    profile = _get_or_create_profile(current_user)
    db.session.commit()

    sims = Simulation.query.filter_by(
        user_id=current_user.id, status='complete',
    ).order_by(Simulation.created_at.asc()).all()

    records = []
    for sim in sims:
        vis = SimulationVisibility.query.filter_by(
            simulation_id=sim.id, user_id=current_user.id,
        ).first()
        if not vis:
            vis = SimulationVisibility(
                id=generate_id(),
                simulation_id=sim.id,
                user_id=current_user.id,
                display_order=len(records),
            )
            db.session.add(vis)
            db.session.flush()
        records.append(_vis_dict(vis, sim))

    db.session.commit()
    return jsonify({
        'is_published': profile.is_published,
        'profile_url': f'/u/{profile.username}',
        'simulations': records,
    })


@profile_bp.route('/api/settings/visibility/publish', methods=['PUT'])
@login_required
def toggle_publish():
    data = request.get_json(force=True, silent=True) or {}
    profile = _get_or_create_profile(current_user)
    profile.is_published = bool(data.get('is_published', not profile.is_published))
    db.session.commit()
    return jsonify({'is_published': profile.is_published})


@profile_bp.route('/api/settings/visibility/reorder', methods=['PUT'])
@login_required
def reorder_visibility():
    data = request.get_json(force=True, silent=True) or {}
    order = data.get('order', [])
    for i, sim_id in enumerate(order):
        vis = SimulationVisibility.query.filter_by(
            simulation_id=sim_id, user_id=current_user.id,
        ).first()
        if vis:
            vis.display_order = i
    db.session.commit()
    return jsonify({'ok': True})


@profile_bp.route('/api/settings/visibility/<sim_id>', methods=['PUT'])
@login_required
def update_visibility(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    vis = SimulationVisibility.query.filter_by(
        simulation_id=sim_id, user_id=current_user.id,
    ).first()
    if not vis:
        vis = SimulationVisibility(
            id=generate_id(), simulation_id=sim_id, user_id=current_user.id,
        )
        db.session.add(vis)

    data = request.get_json(force=True, silent=True) or {}

    if 'is_public' in data:
        vis.is_public = bool(data['is_public'])
    if 'zone_tagline' in data:
        vis.zone_tagline = (data['zone_tagline'] or '').strip()[:200] or None
    if 'services' in data:
        bullets = [s.strip()[:60] for s in (data['services'] or []) if s.strip()]
        vis.services = bullets[:6]
    if 'availability' in data and data['availability'] in ('available', 'limited', 'unavailable', 'hidden'):
        vis.availability = data['availability']
    if 'display_order' in data:
        vis.display_order = int(data['display_order'])

    vis.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(_vis_dict(vis, sim))


@profile_bp.route('/api/settings/visibility/<sim_id>/ai-tagline', methods=['POST'])
@login_required
def ai_tagline(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    from app.models.resume import Resume
    resume = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).first()
    deliverables = []
    if resume and resume.parsed_text:
        deliverables = [resume.parsed_text[:500]]

    try:
        from app.services.bio_service import suggest_zone_tagline
        tagline = suggest_zone_tagline(sim.expertise_zone or sim.name, deliverables)
    except Exception as e:
        current_app.logger.error(f'AI tagline failed: {e}')
        return jsonify({'error': 'Suggestion failed'}), 500

    return jsonify({'tagline': tagline})


@profile_bp.route('/api/settings/visibility/<sim_id>/ai-services', methods=['POST'])
@login_required
def ai_services(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    l1 = SimulationLayer.query.filter_by(simulation_id=sim_id, layer_number=1).first()
    l2 = SimulationLayer.query.filter_by(simulation_id=sim_id, layer_number=2).first()

    l1_streams = [{'name': s.name} for s in (l1.income_streams if l1 else [])]
    l2_streams = [{'name': s.name} for s in (l2.income_streams if l2 else [])]

    try:
        from app.services.bio_service import suggest_service_bullets
        bullets = suggest_service_bullets(sim.expertise_zone or sim.name, l1_streams, l2_streams)
    except Exception as e:
        current_app.logger.error(f'AI services failed: {e}')
        return jsonify({'error': 'Suggestion failed'}), 500

    return jsonify({'services': bullets})


# ── Partner visibility (advisor access) ───────────────────────────────────

@profile_bp.route('/api/settings/visibility/partners', methods=['GET'])
@login_required
def get_partner_visibility():
    from app.models.partner import ReferralPartner, ReferralSignup, AdvisorAccess
    relationships = db.session.query(ReferralPartner, ReferralSignup).join(
        ReferralSignup, ReferralPartner.id == ReferralSignup.partner_id,
    ).filter(ReferralSignup.referred_user_id == current_user.id).all()

    sims = Simulation.query.filter_by(user_id=current_user.id).order_by(
        Simulation.created_at.desc()
    ).all()

    active_grants = AdvisorAccess.query.filter_by(
        granted_by=current_user.id, revoked_at=None,
    ).all()
    granted_pairs = {(g.partner_id, g.simulation_id) for g in active_grants}
    last_viewed_map = {
        (g.partner_id, g.simulation_id): g.last_viewed_at for g in active_grants
    }

    result = []
    for partner, signup in relationships:
        sims_data = []
        for sim in sims:
            key = (partner.id, sim.id)
            lv = last_viewed_map.get(key)
            sims_data.append({
                'simulation_id': sim.id,
                'simulation_name': sim.name,
                'expertise_zone': sim.expertise_zone,
                'created_at': sim.created_at.isoformat(),
                'shared': key in granted_pairs,
                'last_viewed_at': lv.isoformat() if lv else None,
            })
        result.append({
            'partner_id': partner.id,
            'partner_name': partner.full_name,
            'partner_type': partner.partner_type,
            'business_name': partner.business_name,
            'simulations': sims_data,
        })
    return jsonify(result), 200


@profile_bp.route('/api/settings/visibility/grant', methods=['POST'])
@login_required
def grant_advisor_access():
    from app.models.partner import ReferralSignup, AdvisorAccess
    data = request.get_json(force=True, silent=True) or {}
    partner_id = data.get('partner_id', '').strip()
    sim_id = data.get('simulation_id', '').strip()
    if not partner_id or not sim_id:
        return jsonify({'error': 'partner_id and simulation_id required'}), 400

    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        return jsonify({'error': 'Simulation not found'}), 404

    rel = ReferralSignup.query.filter_by(
        referred_user_id=current_user.id, partner_id=partner_id,
    ).first()
    if not rel:
        return jsonify({'error': 'No referral relationship with this partner'}), 403

    existing = AdvisorAccess.query.filter_by(
        partner_id=partner_id, granted_by=current_user.id, simulation_id=sim_id,
    ).first()
    if existing:
        existing.revoked_at = None
        existing.granted_at = datetime.utcnow()
    else:
        from utils.id_gen import generate_id as _gen
        db.session.add(AdvisorAccess(
            id=_gen(),
            simulation_id=sim_id,
            partner_id=partner_id,
            granted_by=current_user.id,
        ))
    db.session.commit()
    return jsonify({'status': 'granted'}), 200


@profile_bp.route('/api/settings/visibility/revoke', methods=['POST'])
@login_required
def revoke_advisor_access():
    from app.models.partner import ReferralPartner, AdvisorAccess
    data = request.get_json(force=True, silent=True) or {}
    partner_id = data.get('partner_id', '').strip()
    sim_id = data.get('simulation_id', '').strip()

    grant = AdvisorAccess.query.filter_by(
        partner_id=partner_id, granted_by=current_user.id,
        simulation_id=sim_id, revoked_at=None,
    ).first()
    if not grant:
        return jsonify({'error': 'No active grant found'}), 404

    grant.revoked_at = datetime.utcnow()
    db.session.commit()

    partner = ReferralPartner.query.get(partner_id)
    return jsonify({
        'status': 'revoked',
        'partner_name': partner.full_name if partner else 'Partner',
    }), 200


# ── Security settings ──────────────────────────────────────────────────────

@profile_bp.route('/api/settings/security/password', methods=['PUT'])
@login_required
def change_password():
    data = request.get_json(force=True, silent=True) or {}
    current_pw = data.get('current_password', '')
    new_pw = data.get('new_password', '')
    confirm_pw = data.get('confirm_password', '')

    if not current_user.password_hash:
        return jsonify({'error': 'No password set — use Google sign-in'}), 400
    if not bcrypt.check_password_hash(current_user.password_hash, current_pw):
        return jsonify({'error': 'Current password is incorrect'}), 400
    if new_pw != confirm_pw:
        return jsonify({'error': 'Passwords do not match'}), 400
    if len(new_pw) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if not re.search(r'[A-Z]', new_pw):
        return jsonify({'error': 'Password must contain at least one uppercase letter'}), 400
    if not re.search(r'\d', new_pw):
        return jsonify({'error': 'Password must contain at least one number'}), 400
    if not re.search(r'[^A-Za-z0-9]', new_pw):
        return jsonify({'error': 'Password must contain at least one special character'}), 400

    rounds = current_app.config.get('BCRYPT_LOG_ROUNDS', 12)
    current_user.password_hash = bcrypt.generate_password_hash(new_pw, rounds=rounds).decode('utf-8')

    current_jti = getattr(request, '_session_jti', None)
    revoke_q = UserSession.query.filter_by(user_id=current_user.id, revoked_at=None)
    if current_jti:
        revoke_q = revoke_q.filter(UserSession.jti != current_jti)
    revoke_q.update({'revoked_at': datetime.utcnow()}, synchronize_session=False)

    AuditLog.log('password_changed', user_id=current_user.id)
    db.session.commit()

    try:
        from app.services.email_service import send_password_changed_email
        send_password_changed_email(current_user.email, current_user.full_name)
    except Exception:
        pass

    return jsonify({'ok': True})


@profile_bp.route('/api/settings/security/email', methods=['PUT'])
@login_required
def initiate_email_change():
    data = request.get_json(force=True, silent=True) or {}
    new_email = (data.get('new_email') or '').lower().strip()
    current_pw = data.get('current_password', '')

    if not new_email or '@' not in new_email:
        return jsonify({'error': 'Valid email required'}), 400
    if not current_user.password_hash or not bcrypt.check_password_hash(current_user.password_hash, current_pw):
        return jsonify({'error': 'Current password is incorrect'}), 400
    if User.query.filter_by(email=new_email).first():
        return jsonify({'error': 'Email already registered to another account'}), 409

    token = secrets.token_urlsafe(32)
    current_user.pending_email = new_email
    current_user.pending_email_token = token
    current_user.pending_email_token_expires = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()

    try:
        from app.services.email_service import send_email_change_verification
        send_email_change_verification(new_email, current_user.full_name, token)
    except Exception:
        pass

    return jsonify({'message': f'Verification email sent to {new_email}'})


@profile_bp.route('/api/settings/security/email/confirm', methods=['GET', 'POST'])
def confirm_email_change():
    token = request.args.get('token') or (request.get_json(force=True, silent=True) or {}).get('token', '')
    user = User.query.filter_by(pending_email_token=token).first()
    if not user:
        return jsonify({'error': 'Invalid or expired token'}), 400
    if user.pending_email_token_expires and user.pending_email_token_expires < datetime.utcnow():
        return jsonify({'error': 'Token expired — please restart the email change process'}), 400

    old_email = user.email
    user.email = user.pending_email
    user.pending_email = None
    user.pending_email_token = None
    user.pending_email_token_expires = None
    AuditLog.log('email_changed', user_id=user.id)
    db.session.commit()

    try:
        from app.services.email_service import send_email_change_notification
        send_email_change_notification(old_email, user.full_name, user.email)
    except Exception:
        pass

    return jsonify({'ok': True, 'message': 'Email address updated successfully'})


@profile_bp.route('/api/settings/security/sessions', methods=['GET'])
@login_required
def list_sessions():
    sessions = UserSession.query.filter_by(
        user_id=current_user.id, revoked_at=None,
    ).filter(UserSession.expires_at > datetime.utcnow()).order_by(
        UserSession.last_active.desc()
    ).all()

    current_jti = getattr(request, '_session_jti', None)
    return jsonify([_session_dict(s, current_jti) for s in sessions])


@profile_bp.route('/api/settings/security/sessions/<session_id>', methods=['DELETE'])
@login_required
def revoke_session(session_id):
    s = UserSession.query.filter_by(id=session_id, user_id=current_user.id).first()
    if not s:
        return jsonify({'error': 'Not found'}), 404
    s.revoked_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@profile_bp.route('/api/settings/security/sessions', methods=['DELETE'])
@login_required
def revoke_all_sessions():
    current_jti = getattr(request, '_session_jti', None)
    q = UserSession.query.filter_by(user_id=current_user.id, revoked_at=None)
    if current_jti:
        q = q.filter(UserSession.jti != current_jti)
    q.update({'revoked_at': datetime.utcnow()}, synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True})


@profile_bp.route('/api/settings/security/delete-account', methods=['POST'])
@login_required
def delete_account():
    data = request.get_json(force=True, silent=True) or {}
    if data.get('confirm_email', '').lower() != current_user.email:
        return jsonify({'error': 'Email confirmation does not match'}), 400

    current_user.deleted_at = datetime.utcnow()
    recovery_token = secrets.token_urlsafe(32)
    current_user.recovery_token = recovery_token
    current_user.recovery_token_expires = datetime.utcnow() + timedelta(days=30)

    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if profile:
        profile.is_published = False

    AuditLog.log('account_deletion_initiated', user_id=current_user.id)
    db.session.commit()

    try:
        from app.services.email_service import send_account_deletion_email
        send_account_deletion_email(current_user.email, current_user.full_name, recovery_token)
    except Exception:
        pass

    return jsonify({'ok': True, 'message': 'Account scheduled for deletion. You have 30 days to recover it.'})


@profile_bp.route('/api/settings/security/recover-account', methods=['POST'])
def recover_account():
    data = request.get_json(force=True, silent=True) or {}
    token = data.get('token', '')
    user = User.query.filter_by(recovery_token=token).first()
    if not user or not user.deleted_at:
        return jsonify({'error': 'Invalid recovery token'}), 400
    if user.recovery_token_expires and user.recovery_token_expires < datetime.utcnow():
        return jsonify({'error': 'Recovery window has expired'}), 400

    user.deleted_at = None
    user.recovery_token = None
    user.recovery_token_expires = None
    AuditLog.log('account_recovered', user_id=user.id)
    db.session.commit()
    return jsonify({'ok': True, 'message': 'Account restored successfully'})


# ── Helpers ────────────────────────────────────────────────────────────────

def _profile_dict(p):
    return {
        'id': p.id,
        'username': p.username,
        'display_name': p.display_name,
        'tagline': p.tagline,
        'bio': p.bio,
        'bio_generated_at': p.bio_generated_at.isoformat() if p.bio_generated_at else None,
        'bio_edited': p.bio_edited,
        'avatar_path': p.avatar_path,
        'location': p.location,
        'linkedin_url': p.linkedin_url,
        'website_url': p.website_url,
        'twitter_url': p.twitter_url,
        'other_link_url': p.other_link_url,
        'other_link_label': p.other_link_label,
        'booking_url': p.booking_url,
        'booking_btn_label': p.booking_btn_label or 'Book a Call',
        'show_contact_form': p.show_contact_form,
        'show_booking_btn': p.show_booking_btn,
        'is_published': p.is_published,
        'noindex': p.noindex,
        'completeness': p.completeness,
        # SIM-PRD-BIO-003
        'career_history':      p.career_history,
        'notable_work':        p.notable_work,
        'ventures':            p.ventures,
        'education':           p.education,
        'certifications':      p.certifications,
        'references_press':    p.references_press,
        'publications':        p.publications,
        'projects':            p.projects,
        'bio_sections_visible': p.bio_sections_visible,
    }


def _vis_dict(vis, sim):
    return {
        'id': vis.id,
        'simulation_id': sim.id,
        'simulation_name': sim.name,
        'expertise_zone': sim.expertise_zone,
        'created_at': sim.created_at.isoformat(),
        'is_public': vis.is_public,
        'display_order': vis.display_order,
        'zone_tagline': vis.zone_tagline,
        'services': vis.services_list,
        'availability': vis.availability or 'hidden',
    }


def _session_dict(s, current_jti=None):
    return {
        'id': s.id,
        'device': s.device_label,
        'ip': s.ip_truncated,
        'last_active': s.last_active.isoformat() if s.last_active else None,
        'created_at': s.created_at.isoformat(),
        'is_current': s.jti == current_jti if current_jti else False,
    }


# ── My Chats API ───────────────────────────────────────────────────────────

@profile_bp.route('/api/users/me/chats')
@login_required
def get_my_chats():
    """Paginated list of chat sessions grouped by simulation, newest first."""
    from app.models.chat import SimulationChatMessage
    from app.models.simulation import Simulation
    from sqlalchemy import func, case

    page     = request.args.get('page', 1, type=int)
    per_page = 20
    q_str    = (request.args.get('q') or '').strip()

    subq = (
        db.session.query(
            SimulationChatMessage.session_id,
            SimulationChatMessage.simulation_id,
            func.min(SimulationChatMessage.created_at).label('started_at'),
            func.max(SimulationChatMessage.created_at).label('last_message_at'),
            func.count(SimulationChatMessage.id).label('message_count'),
        )
        .filter(
            SimulationChatMessage.user_id == current_user.id,
            SimulationChatMessage.is_archived == False,
            SimulationChatMessage.session_id != None,
        )
    )
    if q_str and len(q_str) >= 3:
        subq = subq.filter(SimulationChatMessage.content.ilike(f'%{q_str}%'))

    rows = (
        subq.group_by(
            SimulationChatMessage.session_id,
            SimulationChatMessage.simulation_id,
        )
        .order_by(db.desc('last_message_at'))
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    sim_ids = list({r.simulation_id for r in rows.items})
    sims = {s.id: s for s in Simulation.query.filter(Simulation.id.in_(sim_ids)).all()}

    # First user message per session as preview
    preview_subq = (
        db.session.query(
            SimulationChatMessage.session_id,
            func.min(SimulationChatMessage.id).label('first_msg_id'),
        )
        .filter(
            SimulationChatMessage.user_id == current_user.id,
            SimulationChatMessage.role == 'user',
            SimulationChatMessage.is_archived == False,
        )
        .group_by(SimulationChatMessage.session_id)
        .subquery()
    )
    first_msgs = {
        m.session_id: m.content
        for m in db.session.query(SimulationChatMessage).join(
            preview_subq, SimulationChatMessage.id == preview_subq.c.first_msg_id
        ).all()
    }

    sessions = []
    for r in rows.items:
        sim = sims.get(r.simulation_id)
        preview = first_msgs.get(r.session_id, '')
        sessions.append({
            'session_id':      r.session_id,
            'simulation_id':   r.simulation_id,
            'simulation_name': sim.name if sim else '',
            'expertise_zone':  sim.expertise_zone if sim else '',
            'started_at':      r.started_at.isoformat(),
            'last_message_at': r.last_message_at.isoformat(),
            'message_count':   r.message_count,
            'preview':         preview[:80] if preview else '',
        })

    return jsonify({
        'sessions': sessions,
        'total':    rows.total,
        'page':     page,
        'pages':    rows.pages,
    })


@profile_bp.route('/api/chats/<session_id>')
@login_required
def get_chat_session(session_id):
    """Full message thread for a session (read-only)."""
    from app.models.chat import SimulationChatMessage
    msgs = (
        SimulationChatMessage.query
        .filter_by(session_id=session_id, user_id=current_user.id, is_archived=False)
        .order_by(SimulationChatMessage.created_at.asc())
        .all()
    )
    if not msgs:
        from flask import abort
        abort(404)
    sim_id = msgs[0].simulation_id
    return jsonify({'session_id': session_id, 'simulation_id': sim_id,
                    'messages': [m.to_dict() for m in msgs]})


@profile_bp.route('/api/chats/<session_id>', methods=['DELETE'])
@login_required
def delete_chat_session(session_id):
    """Soft-delete all messages in a session (is_archived=1)."""
    from app.models.chat import SimulationChatMessage
    updated = (
        SimulationChatMessage.query
        .filter_by(session_id=session_id, user_id=current_user.id)
        .update({'is_archived': True})
    )
    if updated == 0:
        from flask import abort
        abort(404)
    db.session.commit()
    return jsonify({'ok': True, 'session_id': session_id})
