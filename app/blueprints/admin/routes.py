from flask import request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.platform_settings import PlatformSetting
from app.models.user import User
from app.models.simulation import Simulation
from app.models.ai_interaction import AIInteraction
from app.models.audit_log import AuditLog


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/settings', methods=['GET'])
@login_required
@admin_required
def list_settings():
    settings = PlatformSetting.query.all()
    return jsonify([{
        'id': s.id,
        'key': s.key,
        'value': s.value,
        'updated_at': s.updated_at.isoformat() if s.updated_at else None,
    } for s in settings]), 200


@admin_bp.route('/settings/<key>', methods=['PUT'])
@login_required
@admin_required
def update_setting(key):
    data = request.get_json()
    if not data or 'value' not in data:
        return jsonify({'error': 'value is required'}), 400
    old_value = PlatformSetting.get(key)
    setting = PlatformSetting.set(key, data['value'], updated_by=current_user.id)
    AuditLog.log('setting_updated', user_id=current_user.id, metadata={
        'key': key,
        'old_value': old_value,
        'new_value': data['value'],
    })
    db.session.commit()
    return jsonify({'key': setting.key, 'value': setting.value}), 200


@admin_bp.route('/settings/simulation_price', methods=['GET'])
@login_required
@admin_required
def get_simulation_price_history():
    """Return current simulation price and full price change history from audit log."""
    from app.models.audit_log import AuditLog as AL

    current_price_cents = int(PlatformSetting.get('simulation_price') or 1000)

    history_rows = AL.query.filter(
        AL.action == 'setting_updated',
    ).order_by(AL.created_at.desc()).all()

    price_history = []
    for row in history_rows:
        meta = row.extra
        if meta.get('key') == 'simulation_price':
            price_history.append({
                'old_value_cents': int(meta.get('old_value') or 0) if meta.get('old_value') else None,
                'new_value_cents': int(meta.get('new_value', 0)),
                'changed_by': row.user_id,
                'timestamp': row.created_at.isoformat(),
            })

    return jsonify({
        'current_price_cents': current_price_cents,
        'current_price_usd': current_price_cents / 100,
        'price_history': price_history,
    }), 200


@admin_bp.route('/revenue', methods=['GET'])
@login_required
@admin_required
def revenue_dashboard():
    from sqlalchemy import func
    from app.models.simulation import Simulation

    total_completed = Simulation.query.filter_by(status=Simulation.STATUS_COMPLETE).count()
    total_refunded = Simulation.query.filter_by(status=Simulation.STATUS_REFUNDED).count()
    revenue_row = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter_by(status=Simulation.STATUS_COMPLETE).first()
    total_revenue_cents = revenue_row[0] if revenue_row else 0

    # Per-user spend top 10
    top_users = db.session.query(
        User.id, User.email, User.full_name, User.total_spend, User.simulation_count
    ).order_by(User.total_spend.desc()).limit(10).all()

    # Token usage
    token_stats = db.session.query(
        func.sum(AIInteraction.prompt_tokens),
        func.sum(AIInteraction.completion_tokens),
    ).first()

    # Price change audit trail
    price_history_rows = AuditLog.query.filter_by(action='setting_updated').order_by(
        AuditLog.created_at.desc()
    ).all()
    price_history = []
    for row in price_history_rows:
        meta = row.extra
        if meta.get('key') == 'simulation_price':
            price_history.append({
                'old_value_cents': int(meta['old_value']) if meta.get('old_value') else None,
                'new_value_cents': int(meta.get('new_value', 0)),
                'changed_by': row.user_id,
                'timestamp': row.created_at.isoformat(),
            })

    return jsonify({
        'total_simulations_completed': total_completed,
        'total_simulations_refunded': total_refunded,
        'refund_rate_pct': round(total_refunded / max(total_completed + total_refunded, 1) * 100, 2),
        'total_revenue_usd': total_revenue_cents / 100,
        'top_users': [{
            'id': u.id, 'email': u.email, 'full_name': u.full_name,
            'total_spend_usd': u.total_spend / 100, 'simulation_count': u.simulation_count,
        } for u in top_users],
        'ai_tokens': {
            'prompt_tokens_total': token_stats[0] or 0,
            'completion_tokens_total': token_stats[1] or 0,
        },
        'price_change_history': price_history,
    }), 200


@admin_bp.route('/users', methods=['GET'])
@login_required
@admin_required
def list_users():
    users = User.query.order_by(User.created_at.desc()).limit(100).all()
    return jsonify([{
        'id': u.id,
        'email': u.email,
        'full_name': u.full_name,
        'email_verified': u.email_verified,
        'simulation_count': u.simulation_count,
        'total_spend_usd': u.total_spend / 100,
        'is_admin': u.is_admin,
        'created_at': u.created_at.isoformat(),
    } for u in users]), 200


@admin_bp.route('/user/profile', methods=['GET'])
@login_required
def get_profile():
    return jsonify({
        'id': current_user.id,
        'email': current_user.email,
        'full_name': current_user.full_name,
        'simulation_count': current_user.simulation_count,
        'total_spend_usd': current_user.total_spend / 100,
        'is_admin': current_user.is_admin,
    }), 200


@admin_bp.route('/user/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.get_json()
    if data.get('full_name'):
        current_user.full_name = data['full_name']
    db.session.commit()
    return jsonify({'message': 'Profile updated'}), 200


# ---------------------------------------------------------------------------
# Partner Program Admin
# ---------------------------------------------------------------------------

@admin_bp.route('/partners', methods=['GET'])
@login_required
@admin_required
def list_partners():
    from app.models.partner import ReferralPartner
    status_filter = request.args.get('status')
    q = ReferralPartner.query.order_by(ReferralPartner.applied_at.desc())
    if status_filter:
        q = q.filter_by(status=status_filter)
    partners = q.limit(200).all()
    return jsonify([p.to_dict() for p in partners]), 200


@admin_bp.route('/partners/<partner_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_partner(partner_id):
    from datetime import datetime
    from app.models.partner import ReferralPartner
    from utils.id_gen import generate_id
    partner = ReferralPartner.query.get_or_404(partner_id)
    if partner.status == ReferralPartner.STATUS_ACTIVE:
        return jsonify({'error': 'Partner is already active'}), 409

    partner.status = ReferralPartner.STATUS_ACTIVE
    partner.approved_at = datetime.utcnow()
    partner.approved_by = current_user.id
    if not partner.referral_code:
        partner.referral_code = generate_id()

    # FR-CTP-08: elevate linked user to dual-role partner account
    if partner.user_id:
        linked_user = User.query.get(partner.user_id)
        if linked_user:
            linked_user.is_partner = True

    AuditLog.log('partner_approved', user_id=current_user.id, resource_id=partner_id)
    db.session.commit()

    try:
        from app.services.email_service import send_partner_approved_email
        send_partner_approved_email(partner.email, partner.full_name, partner.referral_code)
    except Exception:
        pass

    return jsonify(partner.to_dict()), 200


@admin_bp.route('/partners/<partner_id>/reject', methods=['POST'])
@login_required
@admin_required
def reject_partner(partner_id):
    from datetime import datetime
    from app.models.partner import ReferralPartner
    partner = ReferralPartner.query.get_or_404(partner_id)
    if partner.status not in (ReferralPartner.STATUS_PENDING, ReferralPartner.STATUS_ACTIVE):
        return jsonify({'error': 'Partner cannot be rejected in current status'}), 409

    data = request.get_json() or {}
    reason = data.get('reason', '').strip()[:500] or None

    partner.status = ReferralPartner.STATUS_INACTIVE
    partner.last_declined_at = datetime.utcnow()
    partner.declined_reason = reason

    # Revoke dual-role if previously active
    if partner.user_id:
        linked_user = User.query.get(partner.user_id)
        if linked_user:
            linked_user.is_partner = False

    AuditLog.log('partner_rejected', user_id=current_user.id, resource_id=partner_id,
                 metadata={'reason': reason})
    db.session.commit()

    try:
        from app.services.email_service import send_partner_rejected_email
        send_partner_rejected_email(partner.email, partner.full_name, reason=reason)
    except Exception:
        pass

    return jsonify({'message': 'Partner rejected', 'id': partner_id}), 200


@admin_bp.route('/partners/<partner_id>/commission-rate', methods=['PUT'])
@login_required
@admin_required
def set_partner_commission_rate(partner_id):
    from app.models.partner import ReferralPartner
    partner = ReferralPartner.query.get_or_404(partner_id)
    data = request.get_json()
    if data is None or 'rate' not in data:
        return jsonify({'error': 'rate is required (decimal, e.g. 0.25 for 25%)'}), 400
    try:
        rate = float(data['rate'])
        if not (0 < rate <= 1):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'error': 'rate must be a decimal between 0 and 1'}), 400
    partner.commission_rate_override = rate
    AuditLog.log('partner_commission_rate_set', user_id=current_user.id, resource_id=partner_id,
                 metadata={'rate': rate})
    db.session.commit()
    return jsonify({'id': partner_id, 'effective_commission_rate': partner.effective_commission_rate()}), 200


@admin_bp.route('/partners/<partner_id>/suspend', methods=['POST'])
@login_required
@admin_required
def suspend_partner(partner_id):
    from app.models.partner import ReferralPartner
    partner = ReferralPartner.query.get_or_404(partner_id)
    partner.status = ReferralPartner.STATUS_SUSPENDED
    AuditLog.log('partner_suspended', user_id=current_user.id, resource_id=partner_id)
    db.session.commit()
    return jsonify({'message': 'Partner suspended', 'id': partner_id}), 200


@admin_bp.route('/partners/<partner_id>/commissions', methods=['GET'])
@login_required
@admin_required
def list_partner_commissions(partner_id):
    from app.models.partner import Commission
    page = int(request.args.get('page', 1))
    per_page = 50
    q = Commission.query.filter_by(partner_id=partner_id).order_by(Commission.created_at.desc())
    total = q.count()
    items = q.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({'total': total, 'page': page, 'commissions': [c.to_dict() for c in items]}), 200


@admin_bp.route('/payouts', methods=['GET'])
@login_required
@admin_required
def list_all_payouts():
    from app.models.partner import PartnerPayout, ReferralPartner
    payouts = PartnerPayout.query.order_by(PartnerPayout.initiated_at.desc()).limit(500).all()
    result = []
    for p in payouts:
        partner = ReferralPartner.query.get(p.partner_id)
        d = p.to_dict()
        d['partner_name'] = partner.full_name if partner else None
        d['partner_email'] = partner.email if partner else None
        result.append(d)
    return jsonify(result), 200


@admin_bp.route('/partners/<partner_id>/payout', methods=['POST'])
@login_required
@admin_required
def trigger_payout(partner_id):
    """Manually trigger a payout for a partner (settles all pending commissions)."""
    from datetime import datetime
    from app.models.partner import ReferralPartner, Commission, PartnerPayout
    from utils.id_gen import generate_id

    partner = ReferralPartner.query.get_or_404(partner_id)
    if not partner.stripe_connect_id:
        return jsonify({'error': 'Partner has no Stripe Connect account'}), 400

    pending_commissions = Commission.query.filter_by(
        partner_id=partner_id,
        status=Commission.STATUS_PENDING,
    ).all()
    if not pending_commissions:
        return jsonify({'error': 'No pending commissions'}), 400

    total = sum(float(c.commission_amount) for c in pending_commissions)
    commission_ids = [c.id for c in pending_commissions]

    payout = PartnerPayout(
        id=generate_id(),
        partner_id=partner_id,
        payout_amount=total,
        status=PartnerPayout.STATUS_PROCESSING,
    )
    payout.commission_ids = commission_ids
    db.session.add(payout)

    for c in pending_commissions:
        c.status = Commission.STATUS_PAID
        c.paid_at = datetime.utcnow()

    AuditLog.log('partner_payout_triggered', user_id=current_user.id, resource_id=partner_id,
                 metadata={'amount': total, 'commission_count': len(commission_ids)})
    db.session.commit()

    return jsonify(payout.to_dict()), 201
