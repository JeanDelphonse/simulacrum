"""Referral Partner Program API — /api/partners/*"""
from datetime import datetime, timedelta
from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
from app.blueprints.partners import partners_bp
from app.extensions import db
from app.models.partner import (
    ReferralPartner, ReferralSignup, Commission, PartnerPayout,
    AdvisorAccess, AdvisorNote,
)
from app.models.platform_settings import PlatformSetting
from app.models.audit_log import AuditLog
from utils.id_gen import generate_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _program_enabled():
    return PlatformSetting.get('partner_program_enabled', 'true') not in ('false', '0', 'off')


def _get_partner(user_id):
    """Return active ReferralPartner for user, or None."""
    return ReferralPartner.query.filter_by(
        user_id=user_id,
        status=ReferralPartner.STATUS_ACTIVE,
    ).first()


def partner_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        partner = _get_partner(current_user.id)
        if not partner:
            return jsonify({'error': 'Active partner account required'}), 403
        return f(partner, *args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Public — Partner Application
# ---------------------------------------------------------------------------

@partners_bp.route('/apply', methods=['POST'])
def apply():
    if not _program_enabled():
        return jsonify({'error': 'Partner program is not currently accepting applications'}), 403

    data = request.get_json()
    required = ['full_name', 'email', 'partner_type']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400
    if data['partner_type'] not in ReferralPartner.PARTNER_TYPES:
        return jsonify({'error': f'Invalid partner_type. Must be one of: {", ".join(ReferralPartner.PARTNER_TYPES)}'}), 400

    # Prevent duplicate pending/active applications for same email
    existing = ReferralPartner.query.filter_by(email=data['email'].lower()).filter(
        ReferralPartner.status.in_([ReferralPartner.STATUS_PENDING, ReferralPartner.STATUS_ACTIVE])
    ).first()
    if existing:
        return jsonify({'error': 'An application already exists for this email address'}), 409

    # If a logged-in user is applying, link the account
    user_id = current_user.id if current_user.is_authenticated else None

    partner = ReferralPartner(
        id=generate_id(),
        user_id=user_id,
        full_name=data['full_name'],
        business_name=data.get('business_name'),
        email=data['email'].lower(),
        partner_type=data['partner_type'],
        website_url=data.get('website_url'),
        practice_description=data.get('practice_description', '')[:300],
        status=ReferralPartner.STATUS_PENDING,
    )
    db.session.add(partner)
    db.session.commit()

    try:
        from app.services.email_service import send_partner_application_received_email
        send_partner_application_received_email(partner.email, partner.full_name)
    except Exception:
        pass

    return jsonify({
        'message': 'Application submitted. You will receive an email when it is reviewed.',
        'id': partner.id,
    }), 201


# ---------------------------------------------------------------------------
# Partner Dashboard
# ---------------------------------------------------------------------------

@partners_bp.route('/dashboard', methods=['GET'])
@partner_required
def dashboard(partner):
    commission_rate = float(PlatformSetting.get('partner_commission_rate', '0.20'))
    payout_schedule = PlatformSetting.get('payout_schedule', 'monthly')
    min_threshold = float(PlatformSetting.get('min_payout_threshold', '25.00'))
    stripe_complete = bool(partner.stripe_connect_id)
    pending = partner.pending_payout()

    return jsonify({
        'partner': partner.to_dict(),
        'earnings_summary': {
            'total_earned': partner.total_earned(),
            'pending_payout': pending,
            'paid_to_date': partner.paid_to_date(),
            'commission_rate_pct': commission_rate * 100,
        },
        'payout_info': {
            'payout_schedule': payout_schedule,
            'min_payout_threshold': min_threshold,
            'payout_ready': stripe_complete and pending >= min_threshold,
            'stripe_connect_complete': stripe_complete,
        },
        'referral_link': partner.referral_link(),
    }), 200


@partners_bp.route('/clients', methods=['GET'])
@partner_required
def list_clients(partner):
    """Referred clients who have generated at least one Simulation."""
    from app.models.simulation import Simulation
    from app.models.user import User
    from sqlalchemy import func

    signups = ReferralSignup.query.filter_by(partner_id=partner.id).all()
    results = []
    for signup in signups:
        user = User.query.get(signup.referred_user_id)
        if not user:
            continue
        sim_count = Simulation.query.filter_by(
            user_id=signup.referred_user_id,
            status='complete',
        ).count()
        if sim_count == 0:
            continue
        total_commission = db.session.query(
            func.sum(Commission.commission_amount)
        ).filter_by(
            partner_id=partner.id,
            client_user_id=signup.referred_user_id,
        ).filter(Commission.status != 'refunded').scalar() or 0

        last_sim = Simulation.query.filter_by(
            user_id=signup.referred_user_id,
        ).order_by(Simulation.created_at.desc()).first()

        results.append({
            'user_id': user.id,
            'full_name': user.full_name,
            'email': user.email,
            'signup_date': signup.registered_at.isoformat(),
            'simulations_generated': sim_count,
            'total_commission_earned': float(total_commission),
            'last_activity': last_sim.created_at.isoformat() if last_sim else None,
        })

    results.sort(key=lambda r: r['last_activity'] or '', reverse=True)
    return jsonify(results), 200


@partners_bp.route('/commissions', methods=['GET'])
@partner_required
def list_commissions(partner):
    page = int(request.args.get('page', 1))
    per_page = 25
    q = Commission.query.filter_by(partner_id=partner.id).order_by(
        Commission.created_at.desc()
    )
    total = q.count()
    items = q.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({
        'total': total,
        'page': page,
        'commissions': [c.to_dict() for c in items],
    }), 200


@partners_bp.route('/payouts', methods=['GET'])
@partner_required
def list_payouts(partner):
    payouts = PartnerPayout.query.filter_by(partner_id=partner.id).order_by(
        PartnerPayout.initiated_at.desc()
    ).all()
    result = []
    for p in payouts:
        d = p.to_dict()
        # Expand commission line items
        line_items = Commission.query.filter(
            Commission.id.in_(p.commission_ids)
        ).all() if p.commission_ids else []
        from app.models.user import User
        from app.models.simulation import Simulation
        d['line_items'] = []
        for c in line_items:
            client = User.query.get(c.client_user_id)
            d['line_items'].append({
                'commission_id': c.id,
                'simulation_id': c.simulation_id,
                'client_name': client.full_name if client else None,
                'charge': float(c.simulation_charge),
                'rate': float(c.commission_rate),
                'commission': float(c.commission_amount),
            })
        result.append(d)
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Advisor View
# ---------------------------------------------------------------------------

@partners_bp.route('/advisor-clients', methods=['GET'])
@partner_required
def list_advisor_clients(partner):
    accesses = AdvisorAccess.query.filter_by(
        partner_id=partner.id, revoked_at=None,
    ).all()
    from app.models.simulation import Simulation
    from app.models.user import User
    results = []
    for access in accesses:
        sim = Simulation.query.get(access.simulation_id)
        client = User.query.get(access.granted_by) if sim else None
        results.append({
            'access_id': access.id,
            'simulation_id': access.simulation_id,
            'simulation_name': sim.name if sim else None,
            'expertise_zone': sim.expertise_zone if sim else None,
            'client_name': client.full_name if client else None,
            'client_email': client.email if client else None,
            'granted_at': access.granted_at.isoformat(),
            'last_viewed_at': access.last_viewed_at.isoformat() if access.last_viewed_at else None,
        })
    return jsonify(results), 200


@partners_bp.route('/advisor-clients/<sim_id>', methods=['GET'])
@partner_required
def view_advisor_simulation(partner, sim_id):
    """Read-only advisor view of a client Simulation."""
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id,
        simulation_id=sim_id,
        revoked_at=None,
    ).first()
    if not access:
        return jsonify({'error': 'Access not found or revoked'}), 404

    access.last_viewed_at = datetime.utcnow()
    db.session.commit()

    from app.models.simulation import Simulation
    sim = Simulation.query.get(sim_id)
    if not sim:
        return jsonify({'error': 'Simulation not found'}), 404

    # Include coaching notes for this advisor
    notes = AdvisorNote.query.filter_by(advisor_access_id=access.id).all()

    return jsonify({
        'simulation': sim.to_dict(),
        'access': access.to_dict(),
        'coaching_notes': [n.to_dict() for n in notes],
    }), 200


@partners_bp.route('/advisor-clients/<sim_id>/notes', methods=['POST'])
@partner_required
def add_advisor_note(partner, sim_id):
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id, simulation_id=sim_id, revoked_at=None,
    ).first()
    if not access:
        return jsonify({'error': 'Access not found or revoked'}), 404

    data = request.get_json()
    if not data or not data.get('note_text', '').strip():
        return jsonify({'error': 'note_text is required'}), 400

    note = AdvisorNote(
        id=generate_id(),
        advisor_access_id=access.id,
        simulation_id=sim_id,
        layer_number=data.get('layer_number'),
        note_text=data['note_text'].strip(),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify(note.to_dict()), 201


@partners_bp.route('/advisor-clients/<sim_id>/notes/<note_id>', methods=['PUT'])
@partner_required
def update_advisor_note(partner, sim_id, note_id):
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id, simulation_id=sim_id, revoked_at=None,
    ).first()
    if not access:
        return jsonify({'error': 'Access not found'}), 404

    note = AdvisorNote.query.filter_by(
        id=note_id, advisor_access_id=access.id,
    ).first_or_404()

    data = request.get_json()
    if data.get('note_text', '').strip():
        note.note_text = data['note_text'].strip()
        note.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify(note.to_dict()), 200


@partners_bp.route('/advisor-clients/<sim_id>/notes/<note_id>', methods=['DELETE'])
@partner_required
def delete_advisor_note(partner, sim_id, note_id):
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id, simulation_id=sim_id, revoked_at=None,
    ).first()
    if not access:
        return jsonify({'error': 'Access not found'}), 404

    note = AdvisorNote.query.filter_by(
        id=note_id, advisor_access_id=access.id,
    ).first_or_404()
    db.session.delete(note)
    db.session.commit()
    return jsonify({'message': 'Note deleted'}), 200


# ---------------------------------------------------------------------------
# In-app partner application (FR-CTP-02 through FR-CTP-04)
# ---------------------------------------------------------------------------

@partners_bp.route('/apply-in-app', methods=['POST'])
@login_required
def apply_in_app():
    """Authenticated client applies to become a partner from within the app."""
    if not _program_enabled():
        return jsonify({'error': 'Partner program is not currently accepting applications'}), 403

    # Check for existing application
    existing = ReferralPartner.query.filter_by(email=current_user.email).filter(
        ReferralPartner.status.in_([ReferralPartner.STATUS_PENDING, ReferralPartner.STATUS_ACTIVE])
    ).first()
    if existing:
        return jsonify({'error': 'An application already exists for your account'}), 409

    # Enforce 30-day reapplication cooldown (FR-CTP-07)
    declined = ReferralPartner.query.filter_by(
        email=current_user.email,
        status=ReferralPartner.STATUS_INACTIVE,
    ).order_by(ReferralPartner.applied_at.desc()).first()
    if declined and declined.last_declined_at:
        days_since = (datetime.utcnow() - declined.last_declined_at).days
        if days_since < 30:
            eligible_date = (declined.last_declined_at + __import__('datetime').timedelta(days=30)).strftime('%B %d, %Y')
            return jsonify({'error': f'You may reapply after {eligible_date}'}), 429

    data = request.get_json() or {}
    if not data.get('partner_type'):
        return jsonify({'error': 'partner_type is required'}), 400
    if data['partner_type'] not in ReferralPartner.PARTNER_TYPES:
        return jsonify({'error': f'Invalid partner_type'}), 400

    from app.models.simulation import Simulation
    sim_count = Simulation.query.filter_by(user_id=current_user.id, status='complete').count()

    partner = ReferralPartner(
        id=generate_id(),
        user_id=current_user.id,
        full_name=current_user.full_name,
        email=current_user.email,
        partner_type=data['partner_type'],
        business_name=data.get('business_name'),
        website_url=data.get('website_url'),
        practice_description=data.get('practice_description', '')[:300],
        application_source='in_app',
        simulations_at_apply=sim_count,
        status=ReferralPartner.STATUS_PENDING,
    )
    db.session.add(partner)
    db.session.commit()

    try:
        from app.services.email_service import send_partner_application_received_email
        send_partner_application_received_email(partner.email, partner.full_name)
    except Exception:
        pass

    return jsonify({
        'message': 'Application submitted. You will receive an email when it is reviewed.',
        'id': partner.id,
    }), 201


@partners_bp.route('/application-status', methods=['GET'])
@login_required
def application_status():
    """Return current user's partner application status and any cooldown info."""
    partner = ReferralPartner.query.filter_by(user_id=current_user.id).order_by(
        ReferralPartner.applied_at.desc()
    ).first()
    if not partner:
        return jsonify({'status': 'none'}), 200

    result = {'status': partner.status, 'partner_id': partner.id}
    if partner.status == ReferralPartner.STATUS_INACTIVE and partner.last_declined_at:
        import datetime as _dt
        eligible = partner.last_declined_at + _dt.timedelta(days=30)
        result['can_reapply'] = datetime.utcnow() >= eligible
        result['reapply_eligible_date'] = eligible.isoformat()
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Referral invitations (FR-PAR-15)
# ---------------------------------------------------------------------------

@partners_bp.route('/send-referral', methods=['POST'])
@partner_required
def send_referral(partner):
    """Send a branded referral invitation email to a prospective client."""
    from app.models.partner import ReferralInvitation

    data = request.get_json()
    recipient_email = (data.get('recipient_email') or '').strip().lower()
    recipient_first_name = (data.get('recipient_first_name') or '').strip()
    personal_message = (data.get('personal_message') or '').strip()[:500]

    if not recipient_email:
        return jsonify({'error': 'recipient_email is required'}), 400

    invitation = ReferralInvitation(
        id=generate_id(),
        partner_id=partner.id,
        recipient_email=recipient_email,
        recipient_first_name=recipient_first_name or None,
        personal_message=personal_message or None,
        status=ReferralInvitation.STATUS_SENT,
    )
    db.session.add(invitation)
    db.session.commit()

    try:
        from app.services.email_service import send_referral_invitation_email
        send_referral_invitation_email(
            partner=partner,
            recipient_email=recipient_email,
            recipient_first_name=recipient_first_name,
            personal_message=personal_message,
            invitation_id=invitation.id,
        )
    except Exception as e:
        pass  # invitation row already logged; email failure is non-fatal

    return jsonify(invitation.to_dict()), 201


@partners_bp.route('/referral-invitations', methods=['GET'])
@partner_required
def list_referral_invitations(partner):
    from app.models.partner import ReferralInvitation
    items = ReferralInvitation.query.filter_by(partner_id=partner.id).order_by(
        ReferralInvitation.sent_at.desc()
    ).all()
    return jsonify([i.to_dict() for i in items]), 200


@partners_bp.route('/referral-open/<invitation_id>', methods=['GET'])
def referral_open_pixel(invitation_id):
    """1×1 tracking pixel — marks invitation as opened."""
    from app.models.partner import ReferralInvitation
    from flask import send_file
    import io
    inv = ReferralInvitation.query.get(invitation_id)
    if inv and not inv.opened_at:
        inv.opened_at = datetime.utcnow()
        db.session.commit()
    # Return a 1×1 transparent GIF
    pixel = b'GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;'
    return send_file(io.BytesIO(pixel), mimetype='image/gif')


# ---------------------------------------------------------------------------
# Commission helper (called from webhook)
# ---------------------------------------------------------------------------

def maybe_log_commission(simulation_id: str, user_id: str, charge_cents: int):
    """Called on payment_intent.succeeded. Creates Commission if user is referred."""
    if not _program_enabled():
        return

    signup = ReferralSignup.query.filter_by(referred_user_id=user_id).first()
    if not signup:
        return

    partner = ReferralPartner.query.filter_by(
        id=signup.partner_id,
        status=ReferralPartner.STATUS_ACTIVE,
    ).first()
    if not partner:
        return

    # Attribution window check — only applies before first attribution
    if not signup.attributed_at:
        days_since_registration = (datetime.utcnow() - signup.registered_at).days
        if days_since_registration > 30:
            return  # Outside attribution window
        signup.attributed_at = datetime.utcnow()

    # FR-CTP-12: commission eligibility begins at approval date
    if partner.approved_at and partner.user_id == user_id:
        return  # partner cannot earn commission on their own simulations

    # Use partner's individual rate override if set, else platform default
    rate = partner.effective_commission_rate()
    charge_dollars = charge_cents / 100
    commission_amount = round(charge_dollars * rate, 2)

    commission = Commission(
        id=generate_id(),
        partner_id=partner.id,
        simulation_id=simulation_id,
        client_user_id=user_id,
        simulation_charge=charge_dollars,
        commission_rate=rate,
        commission_amount=commission_amount,
        status=Commission.STATUS_PENDING,
    )
    db.session.add(commission)
    db.session.commit()
