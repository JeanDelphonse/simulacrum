import logging
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


# ---------------------------------------------------------------------------
# Discount management (FR-DISC-01 – FR-DISC-03)
# ---------------------------------------------------------------------------

@admin_bp.route('/discounts', methods=['GET'])
@login_required
@admin_required
def list_discounts():
    from app.models.discount import SimulationDiscount
    rows = SimulationDiscount.query.order_by(SimulationDiscount.created_at.desc()).all()
    return jsonify([r.to_dict() for r in rows]), 200


@admin_bp.route('/discounts', methods=['POST'])
@login_required
@admin_required
def create_discount():
    from datetime import datetime
    from app.models.discount import SimulationDiscount
    data = request.get_json() or {}

    pct = data.get('discount_percentage')
    if pct not in SimulationDiscount.VALID_PERCENTAGES:
        return jsonify({'error': f'discount_percentage must be one of {SimulationDiscount.VALID_PERCENTAGES}'}), 400

    try:
        start_at = datetime.fromisoformat(data['start_at'].replace('Z', ''))
        end_at = datetime.fromisoformat(data['end_at'].replace('Z', ''))
    except (KeyError, ValueError):
        return jsonify({'error': 'start_at and end_at are required ISO datetime strings'}), 400

    if end_at <= start_at:
        return jsonify({'error': 'end_at must be after start_at'}), 400

    label = (data.get('label') or '').strip()[:30] or None

    # Truncate any overlapping active discount (FR-DISC-01: one at a time)
    now = datetime.utcnow()
    overlapping = SimulationDiscount.query.filter(
        SimulationDiscount.start_at < end_at,
        SimulationDiscount.end_at > start_at,
    ).all()
    for existing in overlapping:
        if existing.end_at > start_at:
            existing.end_at = start_at
    db.session.flush()

    discount = SimulationDiscount(
        discount_percentage=pct,
        start_at=start_at,
        end_at=end_at,
        label=label,
        created_by=current_user.id,
    )
    db.session.add(discount)
    AuditLog.log('discount_created', user_id=current_user.id, metadata={
        'discount_percentage': pct,
        'start_at': start_at.isoformat(),
        'end_at': end_at.isoformat(),
        'label': label,
    })
    db.session.commit()
    return jsonify(discount.to_dict()), 201


@admin_bp.route('/discounts/<discount_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_discount(discount_id):
    from app.models.discount import SimulationDiscount
    discount = SimulationDiscount.query.get_or_404(discount_id)
    AuditLog.log('discount_deleted', user_id=current_user.id, metadata={'discount_id': discount_id})
    db.session.delete(discount)
    db.session.commit()
    return jsonify({'deleted': True}), 200


# ---------------------------------------------------------------------------
# Prospect Tier Config (SIM-REQ-PROSPECT-001 — FR-TIER-02)
# ---------------------------------------------------------------------------

@admin_bp.route('/prospect-tier-config', methods=['GET'])
@login_required
@admin_required
def get_prospect_tier_config_route():
    from app.services.pricing_service import get_prospect_tier_config
    cfg = get_prospect_tier_config()
    return jsonify(cfg), 200


@admin_bp.route('/prospect-tier-config', methods=['PUT'])
@login_required
@admin_required
def update_prospect_tier_config():
    data = request.get_json() or {}
    allowed = {
        'prospect_tier1_count', 'prospect_tier2_count', 'prospect_tier2_price_cents',
        'prospect_tier3_count', 'prospect_tier3_price_cents',
    }
    updated = {}
    for key in allowed:
        if key in data:
            try:
                val = int(data[key])
                if val < 0:
                    return jsonify({'error': f'{key} must be non-negative'}), 400
            except (TypeError, ValueError):
                return jsonify({'error': f'{key} must be an integer'}), 400
            PlatformSetting.set(key, str(val), updated_by=current_user.id)
            updated[key] = val

    if not updated:
        return jsonify({'error': 'No valid fields provided'}), 400

    AuditLog.log('prospect_tier_config_updated', user_id=current_user.id, metadata=updated)
    db.session.commit()
    from app.services.pricing_service import get_prospect_tier_config
    return jsonify(get_prospect_tier_config()), 200


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

    # Discount impact: total discount value applied across all simulations (FR-DISC-09)
    discount_impact_row = db.session.query(
        func.coalesce(
            func.sum(Simulation.base_price_at_purchase_cents - Simulation.amount_charged_cents), 0
        )
    ).filter(
        Simulation.status == Simulation.STATUS_COMPLETE,
        Simulation.discount_applied_percentage > 0,
    ).first()
    discount_impact_cents = discount_impact_row[0] if discount_impact_row else 0
    discount_sim_count = Simulation.query.filter(
        Simulation.status == Simulation.STATUS_COMPLETE,
        Simulation.discount_applied_percentage > 0,
    ).count()

    # Per-user spend top 10
    top_users = db.session.query(
        User.id, User.email, User.full_name, User.total_spend, User.simulation_count
    ).order_by(User.total_spend.desc()).limit(10).all()

    # Token usage
    token_stats = db.session.query(
        func.sum(AIInteraction.prompt_tokens),
        func.sum(AIInteraction.completion_tokens),
    ).first()

    # Prospect tier upgrade revenue (FR-TIER-10)
    prospect_upgrade_row = db.session.query(
        func.coalesce(func.sum(Simulation.prospect_tier_paid_cents), 0)
    ).filter(Simulation.prospect_tier_paid_cents > 0).first()
    prospect_upgrade_revenue_cents = prospect_upgrade_row[0] if prospect_upgrade_row else 0
    prospect_upgrade_sim_count = Simulation.query.filter(
        Simulation.prospect_tier_paid_cents > 0
    ).count()

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
        'discount_impact': {
            'total_discount_usd': discount_impact_cents / 100,
            'simulations_count': discount_sim_count,
        },
        'prospect_tier_upgrades': {
            'total_revenue_usd': prospect_upgrade_revenue_cents / 100,
            'simulations_count': prospect_upgrade_sim_count,
        },
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
        'is_deactivated': u.deleted_at is not None,
        'created_at': u.created_at.isoformat(),
    } for u in users]), 200


# ── POST /api/admin/test-outreach-email ─────────────────────────────────────

@admin_bp.route('/test-outreach-email', methods=['POST'])
@login_required
@admin_required
def test_outreach_email():
    """Send a real outreach-pipeline test email to the requesting admin."""
    from flask import current_app
    data = request.get_json(silent=True) or {}
    to_addr = (data.get('to') or current_user.email or '').strip()
    if not to_addr:
        return jsonify({'error': 'No recipient address'}), 400

    api_key = current_app.config.get('SENDGRID_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'SENDGRID_API_KEY is not configured on this server'}), 500

    sender_email = current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simulacrumai.io')
    sender_name  = current_app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')

    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import (
            Mail, From, TrackingSettings, OpenTracking, ClickTracking,
        )
        html_body = (
            '<p>This is a <strong>production outreach email test</strong> from SimulacrumAI.io.</p>'
            '<p>If you received this, the outreach email pipeline (SendGrid + open/click tracking) '
            'is working correctly.</p>'
            '<p style="color:#6b7280;font-size:12px;">Sent via Admin → Test Outreach Email</p>'
        )
        message = Mail(
            from_email=From(sender_email, sender_name),
            to_emails=to_addr,
            subject='[Simulacrum Test] Outreach email pipeline check',
            html_content=html_body,
        )
        message.tracking_settings = TrackingSettings(
            open_tracking=OpenTracking(enable=True),
            click_tracking=ClickTracking(enable=True, enable_text=True),
        )
        message.custom_arg = [
            ('simulation_id', 'admin_test'),
            ('contact_id',    'admin_test'),
            ('step_id',       'smoke_test'),
        ]
        client = sg_module.SendGridAPIClient(api_key)
        response = client.send(message)
        msg_id = response.headers.get('X-Message-Id', '')
        AuditLog.log('admin_outreach_email_test', user_id=current_user.id,
                     metadata={'to': to_addr, 'status': response.status_code, 'msg_id': msg_id})
        db.session.commit()
        return jsonify({'ok': True, 'to': to_addr, 'status_code': response.status_code, 'message_id': msg_id}), 200
    except Exception as exc:
        current_app.logger.error('admin test-outreach-email failed: %s', exc, exc_info=True)
        return jsonify({'error': str(exc)}), 500


# ── GET /api/admin/users/<user_id> ──────────────────────────────────────────

@admin_bp.route('/users/<user_id>', methods=['GET'])
@login_required
@admin_required
def get_user(user_id):
    from app.models.profile import UserProfile
    u = User.query.get_or_404(user_id)
    profile = UserProfile.query.filter_by(user_id=u.id).first()
    return jsonify({
        'id':             u.id,
        'email':          u.email,
        'full_name':      u.full_name or '',
        'is_deactivated': u.deleted_at is not None,
        'username':       profile.username if profile else '',
        'display_name':   profile.display_name or '' if profile else '',
        'tagline':        profile.tagline or '' if profile else '',
        'bio':            profile.bio or '' if profile else '',
    }), 200


# ── PUT /api/admin/users/<user_id>/profile ───────────────────────────────────

@admin_bp.route('/users/<user_id>/profile', methods=['PUT'])
@login_required
@admin_required
def admin_update_user_profile(user_id):
    from datetime import datetime
    from app.models.profile import UserProfile
    import re
    u = User.query.get_or_404(user_id)
    data = request.get_json(force=True, silent=True) or {}

    if 'full_name' in data:
        u.full_name = (data['full_name'] or '').strip()[:100] or None

    profile = UserProfile.query.filter_by(user_id=u.id).first()
    if profile:
        if 'username' in data:
            username = (data['username'] or '').lower().strip()
            if username and re.match(r'^[a-z0-9-]{3,30}$', username):
                conflict = UserProfile.query.filter(
                    UserProfile.username == username,
                    UserProfile.id != profile.id,
                ).first()
                if conflict:
                    return jsonify({'error': 'Username already taken'}), 409
                profile.username = username
        if 'display_name' in data:
            profile.display_name = (data['display_name'] or '').strip()[:100] or None
        if 'tagline' in data:
            profile.tagline = (data['tagline'] or '').strip()[:200] or None
        if 'bio' in data:
            profile.bio = (data['bio'] or '').strip()[:10000] or None
        profile.updated_at = datetime.utcnow()

    AuditLog.log('admin_user_profile_updated', user_id=current_user.id,
                 metadata={'target_user_id': user_id, 'fields': list(data.keys())})
    db.session.commit()
    return jsonify({'ok': True}), 200


# ── POST /api/admin/users/<user_id>/deactivate ───────────────────────────────

@admin_bp.route('/users/<user_id>/deactivate', methods=['POST'])
@login_required
@admin_required
def deactivate_user(user_id):
    from datetime import datetime
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        return jsonify({'error': 'Cannot deactivate your own account'}), 400
    u.deleted_at = datetime.utcnow()
    AuditLog.log('admin_user_deactivated', user_id=current_user.id,
                 metadata={'target_user_id': user_id})
    db.session.commit()
    return jsonify({'ok': True}), 200


# ── POST /api/admin/users/<user_id>/reactivate ───────────────────────────────

@admin_bp.route('/users/<user_id>/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_user(user_id):
    u = User.query.get_or_404(user_id)
    u.deleted_at = None
    AuditLog.log('admin_user_reactivated', user_id=current_user.id,
                 metadata={'target_user_id': user_id})
    db.session.commit()
    return jsonify({'ok': True}), 200


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


# ---------------------------------------------------------------------------
# Feedback Moderation (SIM-PRD-FBK-001)
# ---------------------------------------------------------------------------

@admin_bp.route('/feedback', methods=['GET'])
@login_required
@admin_required
def list_feedback():
    from app.models.feedback import UserFeedback
    status_filter = request.args.get('status')
    search = (request.args.get('search') or '').strip().lower()

    q = UserFeedback.query
    if status_filter in ('pending', 'approved', 'rejected'):
        q = q.filter_by(status=status_filter)
    records = q.order_by(UserFeedback.submitted_at.desc()).limit(500).all()

    out = []
    for fb in records:
        row = {
            'id':            fb.id,
            'user_id':       fb.user_id,
            'display_name':  fb.display_name_computed,
            'star_rating':   fb.star_rating,
            'quote_text':    fb.quote_text,
            'outcome_text':  fb.outcome_text,
            'layers':        fb.layer_names_list(),
            'name_display':  fb.name_display,
            'simulation_id': fb.simulation_id,
            'expertise_zone': fb.expertise_zone_snapshot,
            'status':        fb.status,
            'admin_note':    fb.admin_note,
            'is_featured':   fb.is_featured,
            'display_order': fb.display_order,
            'approved_at':   fb.approved_at.isoformat() if fb.approved_at else None,
            'submitted_at':  fb.submitted_at.isoformat(),
            'withdrawn_requested_at': fb.withdrawn_requested_at.isoformat() if fb.withdrawn_requested_at else None,
        }
        if search:
            haystack = (
                (row['display_name'] or '') + ' ' +
                (row['quote_text'] or '') + ' ' +
                ' '.join(l['label'] for l in row['layers'])
            ).lower()
            if search not in haystack:
                continue
        out.append(row)
    return jsonify(out), 200


@admin_bp.route('/feedback/stats', methods=['GET'])
@login_required
@admin_required
def feedback_stats():
    from sqlalchemy import func
    from app.models.feedback import UserFeedback

    total     = UserFeedback.query.count()
    pending   = UserFeedback.query.filter_by(status='pending').count()
    approved  = UserFeedback.query.filter_by(status='approved').count()
    rejected  = UserFeedback.query.filter_by(status='rejected').count()
    avg_row   = db.session.query(func.avg(UserFeedback.star_rating)).filter_by(status='approved').scalar()
    avg_rating = round(float(avg_row or 0), 1)

    from collections import Counter
    layer_counts = Counter()
    for fb in UserFeedback.query.filter_by(status='approved').all():
        for n in (fb.layers_attributed or []):
            layer_counts[n] += 1
    top_layer = layer_counts.most_common(1)[0][0] if layer_counts else None

    return jsonify({
        'total': total, 'pending': pending,
        'approved': approved, 'rejected': rejected,
        'avg_rating': avg_rating, 'top_layer': top_layer,
    }), 200


@admin_bp.route('/feedback/<fb_id>/approve', methods=['PUT'])
@login_required
@admin_required
def approve_feedback(fb_id):
    from datetime import datetime
    from app.models.feedback import UserFeedback
    fb = UserFeedback.query.get_or_404(fb_id)
    fb.status = 'approved'
    fb.is_featured = False
    fb.approved_by = current_user.id
    fb.approved_at = datetime.utcnow()
    db.session.commit()
    _notify_feedback_user(fb, featured=False)
    return jsonify({'ok': True}), 200


@admin_bp.route('/feedback/<fb_id>/feature', methods=['PUT'])
@login_required
@admin_required
def feature_feedback(fb_id):
    from datetime import datetime
    from app.models.feedback import UserFeedback
    fb = UserFeedback.query.get_or_404(fb_id)
    fb.status = 'approved'
    fb.is_featured = True
    fb.approved_by = current_user.id
    fb.approved_at = datetime.utcnow()
    db.session.commit()
    _notify_feedback_user(fb, featured=True)
    return jsonify({'ok': True}), 200


@admin_bp.route('/feedback/<fb_id>/reject', methods=['PUT'])
@login_required
@admin_required
def reject_feedback(fb_id):
    from app.models.feedback import UserFeedback
    fb = UserFeedback.query.get_or_404(fb_id)
    data = request.get_json(force=True, silent=True) or {}
    fb.status = 'rejected'
    fb.admin_note = (data.get('admin_note') or '').strip()[:500] or None
    db.session.commit()
    try:
        from app.models.user import User as _U
        from app.services.email_service import send_feedback_rejected_email
        user = _U.query.get(fb.user_id)
        if user:
            send_feedback_rejected_email(user.email, user.full_name, fb.admin_note)
    except Exception:
        pass
    return jsonify({'ok': True}), 200


@admin_bp.route('/feedback/<fb_id>/unpublish', methods=['PUT'])
@login_required
@admin_required
def unpublish_feedback(fb_id):
    from app.models.feedback import UserFeedback
    fb = UserFeedback.query.get_or_404(fb_id)
    fb.status = 'pending'
    fb.approved_by = None
    fb.approved_at = None
    fb.is_featured = False
    db.session.commit()
    return jsonify({'ok': True}), 200


@admin_bp.route('/feedback/<fb_id>/display-order', methods=['PUT'])
@login_required
@admin_required
def set_feedback_display_order(fb_id):
    from app.models.feedback import UserFeedback
    fb = UserFeedback.query.get_or_404(fb_id)
    data = request.get_json(force=True, silent=True) or {}
    order = data.get('display_order')
    if order is None or not isinstance(order, int):
        return jsonify({'error': 'display_order integer required'}), 400
    fb.display_order = order
    db.session.commit()
    return jsonify({'ok': True}), 200


@admin_bp.route('/feedback/reorder', methods=['PUT'])
@login_required
@admin_required
def reorder_feedback():
    from app.models.feedback import UserFeedback
    data = request.get_json(force=True, silent=True) or {}
    items = data.get('items') or []
    for item in items:
        fb_id = item.get('id')
        order = item.get('display_order')
        if fb_id and isinstance(order, int):
            fb = UserFeedback.query.get(fb_id)
            if fb:
                fb.display_order = order
    db.session.commit()
    return jsonify({'ok': True}), 200


@admin_bp.route('/feedback/bulk-approve', methods=['POST'])
@login_required
@admin_required
def bulk_approve_feedback():
    from datetime import datetime
    from app.models.feedback import UserFeedback
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get('ids') or []
    now = datetime.utcnow()
    for fb_id in ids:
        fb = UserFeedback.query.get(fb_id)
        if fb and fb.status == 'pending':
            fb.status = 'approved'
            fb.approved_by = current_user.id
            fb.approved_at = now
            fb.is_featured = False
    db.session.commit()
    return jsonify({'ok': True, 'count': len(ids)}), 200


@admin_bp.route('/feedback/bulk-reject', methods=['POST'])
@login_required
@admin_required
def bulk_reject_feedback():
    from app.models.feedback import UserFeedback
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get('ids') or []
    admin_note = (data.get('admin_note') or '').strip()[:500] or None
    for fb_id in ids:
        fb = UserFeedback.query.get(fb_id)
        if fb and fb.status == 'pending':
            fb.status = 'rejected'
            fb.admin_note = admin_note
    db.session.commit()
    return jsonify({'ok': True, 'count': len(ids)}), 200


def _notify_feedback_user(fb, featured: bool):
    try:
        from app.models.user import User as _U
        from app.services.email_service import send_feedback_approved_email
        user = _U.query.get(fb.user_id)
        if user:
            send_feedback_approved_email(user.email, user.full_name, featured)
    except Exception:
        pass


# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Analytics API — SIM-PRD-ANALYTICS-001
# ---------------------------------------------------------------------------

@admin_bp.route('/analytics', methods=['GET'])
@login_required
@admin_required
def get_analytics():
    """Return all 7 analytics cards + summary for the given date range."""
    from datetime import datetime as _dt, timedelta as _td
    from flask import request as _req

    end_str   = _req.args.get('end',   _dt.utcnow().strftime('%Y-%m-%d'))
    start_str = _req.args.get('start', (_dt.utcnow() - _td(days=30)).strftime('%Y-%m-%d'))
    try:
        end_dt   = _dt.strptime(end_str,   '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        start_dt = _dt.strptime(start_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format — use YYYY-MM-DD'}), 400

    from app.services.analytics_service import (
        get_summary, get_traffic, get_users, get_simulations,
        get_revenue, get_costs, get_bio, get_email, get_alerts,
    )

    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning('analytics %s failed: %s', fn.__name__, exc)
            return {'error': str(exc)}

    return jsonify({
        'period':      {'start': start_str, 'end': end_str},
        'summary':     _safe(get_summary,     start_dt, end_dt),
        'traffic':     _safe(get_traffic,     start_dt, end_dt),
        'users':       _safe(get_users,       start_dt, end_dt),
        'simulations': _safe(get_simulations, start_dt, end_dt),
        'revenue':     _safe(get_revenue,     start_dt, end_dt),
        'costs':       _safe(get_costs,       start_dt, end_dt),
        'bio':         _safe(get_bio,         start_dt, end_dt),
        'email':       _safe(get_email,       start_dt, end_dt),
        'alerts':      _safe(get_alerts,      start_dt, end_dt),
    })


# ---------------------------------------------------------------------------
# Outreach Email Automation — SIM-PRD-OUTREACH-001
# ---------------------------------------------------------------------------

_OUTREACH_CONFIG_KEYS = {
    'outreach_enabled', 'outreach_initial_delay_hours',
    'outreach_cadence_days', 'outreach_require_approval',
}


@admin_bp.route('/outreach/overview', methods=['GET'])
@login_required
@admin_required
def outreach_overview():
    """Config, live segment counts, and enrollment/queue summary (FR-OUT-01/02)."""
    from app.services import outreach_campaign_service as ocs
    from app.models.outreach_campaign import OutreachEnrollment, OutreachSend

    cfg = ocs.get_config()
    counts = ocs.segment_counts()

    enrollment_stats = {
        'active':    OutreachEnrollment.query.filter_by(status='active').count(),
        'graduated': OutreachEnrollment.query.filter_by(status='graduated').count(),
        'completed': OutreachEnrollment.query.filter_by(status='completed').count(),
        'removed':   OutreachEnrollment.query.filter_by(status='removed').count(),
    }
    queue_pending = OutreachSend.query.filter(
        OutreachSend.status.in_(OutreachSend.PENDING_STATUSES)).count()
    awaiting_approval = OutreachSend.query.filter_by(status='awaiting_approval').count()

    return jsonify({
        'config': cfg,
        'segments': counts,
        'enrollments': enrollment_stats,
        'queue_pending': queue_pending,
        'awaiting_approval': awaiting_approval,
    }), 200


@admin_bp.route('/outreach/config', methods=['PUT'])
@login_required
@admin_required
def outreach_update_config():
    """Update sequence settings: initial delay, cadence, approval mode, master toggle."""
    data = request.get_json(silent=True) or {}
    updated = {}
    for key in _OUTREACH_CONFIG_KEYS:
        if key not in data:
            continue
        val = data[key]
        if key in ('outreach_enabled', 'outreach_require_approval'):
            val = 'true' if str(val).lower() in ('true', '1', 'yes', 'on') else 'false'
        else:
            try:
                iv = int(val)
                if iv < 0:
                    return jsonify({'error': f'{key} must be non-negative'}), 400
                val = str(iv)
            except (TypeError, ValueError):
                return jsonify({'error': f'{key} must be an integer'}), 400
        PlatformSetting.set(key, val, updated_by=current_user.id)
        updated[key] = val
    if not updated:
        return jsonify({'error': 'No valid fields provided'}), 400
    AuditLog.log('outreach_config_updated', user_id=current_user.id, metadata=updated)
    db.session.commit()
    from app.services import outreach_campaign_service as ocs
    return jsonify({'config': ocs.get_config(), 'updated': updated}), 200


@admin_bp.route('/outreach/templates', methods=['GET'])
@login_required
@admin_required
def outreach_list_templates():
    """List all effective templates (drip defaults seeded on demand) (FR-OUT-06)."""
    from app.services import outreach_campaign_service as ocs
    from app.models.outreach_campaign import OutreachTemplate

    ocs.seed_default_templates()
    rows = {t.template_key: t for t in OutreachTemplate.query.all()}
    out = []
    for key in OutreachTemplate.DRIP_KEYS:
        tpl = ocs.get_template(key)
        row = rows.get(key)
        out.append({
            'template_key': key,
            'name': tpl['name'],
            'subject': tpl['subject'],
            'preview_text': tpl['preview_text'],
            'body': tpl['body'],
            'is_drip': True,
            'updated_at': row.updated_at.isoformat() if row and row.updated_at else None,
        })
    # Named (non-drip) broadcast templates
    for t in OutreachTemplate.query.filter_by(is_drip=False).all():
        out.append(t.to_dict())
    return jsonify({
        'templates': out,
        'tokens': ['first_name', 'bio_url', 'dashboard_url', 'slug'],
    }), 200


@admin_bp.route('/outreach/templates/<template_key>', methods=['GET'])
@login_required
@admin_required
def outreach_get_template(template_key):
    from app.services import outreach_campaign_service as ocs
    tpl = ocs.get_template(template_key)
    if not tpl:
        return jsonify({'error': 'Unknown template'}), 404
    return jsonify(tpl), 200


@admin_bp.route('/outreach/templates/<template_key>', methods=['PUT'])
@login_required
@admin_required
def outreach_update_template(template_key):
    """Save template copy — updates the default for all future sends (FR-OUT-06)."""
    from app.models.outreach_campaign import OutreachTemplate
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()
    if not subject or not body:
        return jsonify({'error': 'subject and body are required'}), 400

    row = OutreachTemplate.query.filter_by(template_key=template_key).first()
    is_drip = template_key in OutreachTemplate.DRIP_KEYS
    if not row:
        row = OutreachTemplate(template_key=template_key, is_drip=is_drip, subject=subject, body=body)
        db.session.add(row)
    row.subject = subject[:300]
    row.preview_text = (data.get('preview_text') or '').strip()[:200] or None
    row.body = body
    if 'name' in data:
        row.name = (data.get('name') or '').strip()[:120] or None
    row.updated_by = current_user.id
    AuditLog.log('outreach_template_updated', user_id=current_user.id,
                 metadata={'template_key': template_key})
    db.session.commit()
    return jsonify(row.to_dict()), 200


@admin_bp.route('/outreach/templates/preview', methods=['POST'])
@login_required
@admin_required
def outreach_preview_template():
    """Render subject/preview/body with sample tokens for the live preview panel."""
    from app.services import outreach_campaign_service as ocs
    data = request.get_json(silent=True) or {}
    tokens = ocs.sample_tokens()
    subject = ocs.render_tokens(data.get('subject') or '', tokens)
    preview = ocs.render_tokens(data.get('preview_text') or '', tokens)
    body = ocs.render_tokens(data.get('body') or '', tokens)
    unsub = f'{ocs.BASE_URL}/outreach/unsubscribe/sample'
    html = ocs.render_email_html(body, unsub, preheader=preview)
    return jsonify({'subject': subject, 'preview_text': preview, 'html': html}), 200


@admin_bp.route('/outreach/queue', methods=['GET'])
@login_required
@admin_required
def outreach_queue():
    """Scheduled/queued drip emails waiting to send, grouped by email # (FR-OUT-07)."""
    from app.models.outreach_campaign import OutreachSend
    status = request.args.get('status')
    q = OutreachSend.query.filter(OutreachSend.kind == OutreachSend.KIND_DRIP)
    if status:
        q = q.filter(OutreachSend.status == status)
    else:
        q = q.filter(OutreachSend.status.in_(OutreachSend.PENDING_STATUSES))
    rows = q.order_by(OutreachSend.scheduled_at.asc()).limit(500).all()

    user_ids = list({r.user_id for r in rows})
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    out = []
    for r in rows:
        u = users.get(r.user_id)
        d = r.to_dict()
        d['recipient_name'] = (u.full_name if u else None) or r.to_email
        out.append(d)
    return jsonify({'queue': out, 'count': len(out)}), 200


@admin_bp.route('/outreach/sends/<send_id>', methods=['GET'])
@login_required
@admin_required
def outreach_get_send(send_id):
    """Pre-rendered instance for editing (FR-OUT-07)."""
    from app.models.outreach_campaign import OutreachSend
    from app.services import outreach_campaign_service as ocs
    send = OutreachSend.query.get_or_404(send_id)
    d = send.to_dict()
    unsub = f'{ocs.BASE_URL}/outreach/unsubscribe/preview'
    d['html'] = ocs.render_email_html(send.body_snapshot, unsub,
                                      preheader=send.preview_text or '')
    return jsonify(d), 200


@admin_bp.route('/outreach/sends/<send_id>', methods=['PUT'])
@login_required
@admin_required
def outreach_edit_send(send_id):
    """Instance edit: edit subject/body for this specific send — flags was_edited."""
    from app.models.outreach_campaign import OutreachSend
    send = OutreachSend.query.get_or_404(send_id)
    if send.status not in OutreachSend.PENDING_STATUSES:
        return jsonify({'error': 'Only pending sends can be edited'}), 409
    data = request.get_json(silent=True) or {}
    if 'subject' in data:
        subject = (data['subject'] or '').strip()
        if not subject:
            return jsonify({'error': 'subject cannot be empty'}), 400
        send.subject = subject[:300]
    if 'body' in data:
        body = (data['body'] or '').strip()
        if not body:
            return jsonify({'error': 'body cannot be empty'}), 400
        send.body_snapshot = body
    if 'preview_text' in data:
        send.preview_text = (data['preview_text'] or '').strip()[:200] or None
    send.was_edited = True
    AuditLog.log('outreach_send_edited', user_id=current_user.id, metadata={'send_id': send_id})
    db.session.commit()
    return jsonify(send.to_dict()), 200


@admin_bp.route('/outreach/sends/<send_id>/<action>', methods=['POST'])
@login_required
@admin_required
def outreach_send_action(send_id, action):
    """Per-email controls: pause / resume / skip / send-now / approve (FR-OUT-07/08)."""
    from app.models.outreach_campaign import OutreachSend
    from app.services import outreach_campaign_service as ocs
    send = OutreachSend.query.get_or_404(send_id)

    if action == 'pause':
        if send.status not in OutreachSend.PENDING_STATUSES:
            return jsonify({'error': 'Cannot pause a completed send'}), 409
        send.status = OutreachSend.STATUS_PAUSED
    elif action == 'resume':
        if send.status != OutreachSend.STATUS_PAUSED:
            return jsonify({'error': 'Send is not paused'}), 409
        send.status = OutreachSend.STATUS_QUEUED
    elif action == 'skip':
        if send.status not in OutreachSend.PENDING_STATUSES:
            return jsonify({'error': 'Cannot skip a completed send'}), 409
        send.status = OutreachSend.STATUS_SKIPPED
    elif action == 'approve':
        if send.status != OutreachSend.STATUS_AWAITING_APPROVAL:
            return jsonify({'error': 'Send is not awaiting approval'}), 409
        send.approved_by = current_user.id
        from datetime import datetime as _dt
        send.approved_at = _dt.utcnow()
        send.status = OutreachSend.STATUS_QUEUED
    elif action == 'send-now':
        db.session.commit()
        result = ocs.send_now(send)
        # Advance enrollment if this was a drip step that sent successfully.
        if result.get('status') == 'sent':
            _advance_enrollment_after_send(send)
        AuditLog.log('outreach_send_now', user_id=current_user.id,
                     metadata={'send_id': send_id, 'result': result.get('status')})
        db.session.commit()
        return jsonify({'result': result, 'send': send.to_dict()}), 200
    else:
        return jsonify({'error': 'Unknown action'}), 400

    AuditLog.log(f'outreach_send_{action}', user_id=current_user.id, metadata={'send_id': send_id})
    db.session.commit()
    return jsonify(send.to_dict()), 200


def _advance_enrollment_after_send(send):
    """Advance the drip enrollment after a manual send-now of a drip step."""
    from datetime import timedelta, datetime as _dt
    from app.models.outreach_campaign import OutreachEnrollment
    from app.services import outreach_campaign_service as ocs
    if send.kind != send.KIND_DRIP or not send.enrollment_id:
        return
    enrollment = OutreachEnrollment.query.get(send.enrollment_id)
    if not enrollment or enrollment.status != OutreachEnrollment.STATUS_ACTIVE:
        return
    step = send.step_number or (enrollment.current_step + 1)
    if step <= enrollment.current_step:
        return
    enrollment.current_step = step
    cfg = ocs.get_config()
    if step >= 3:
        enrollment.status = OutreachEnrollment.STATUS_COMPLETED
        enrollment.next_send_at = None
    else:
        enrollment.next_send_at = _dt.utcnow() + timedelta(days=cfg['cadence_days'])
        ocs._materialize_next_send(enrollment, step=step + 1, cfg=cfg)


@admin_bp.route('/outreach/sends/approve-all', methods=['POST'])
@login_required
@admin_required
def outreach_approve_all():
    """Bulk-approve all sends awaiting approval (FR-OUT-08)."""
    from datetime import datetime as _dt
    from app.models.outreach_campaign import OutreachSend
    rows = OutreachSend.query.filter_by(status=OutreachSend.STATUS_AWAITING_APPROVAL).all()
    now = _dt.utcnow()
    for r in rows:
        r.status = OutreachSend.STATUS_QUEUED
        r.approved_by = current_user.id
        r.approved_at = now
    AuditLog.log('outreach_approve_all', user_id=current_user.id, metadata={'count': len(rows)})
    db.session.commit()
    return jsonify({'ok': True, 'approved': len(rows)}), 200


@admin_bp.route('/outreach/enrollments', methods=['GET'])
@login_required
@admin_required
def outreach_list_enrollments():
    from app.models.outreach_campaign import OutreachEnrollment
    status = request.args.get('status', 'active')
    q = OutreachEnrollment.query
    if status != 'all':
        q = q.filter_by(status=status)
    rows = q.order_by(OutreachEnrollment.created_at.desc()).limit(500).all()
    user_ids = list({r.user_id for r in rows})
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    out = []
    for r in rows:
        u = users.get(r.user_id)
        d = r.to_dict()
        d['email'] = u.email if u else None
        d['full_name'] = u.full_name if u else None
        out.append(d)
    return jsonify({'enrollments': out, 'count': len(out)}), 200


@admin_bp.route('/outreach/enrollments/<enrollment_id>/remove', methods=['POST'])
@login_required
@admin_required
def outreach_remove_enrollment(enrollment_id):
    """Remove a user from the sequence — stops all remaining drip emails (FR-OUT-07)."""
    from app.models.outreach_campaign import OutreachEnrollment, OutreachSend
    enrollment = OutreachEnrollment.query.get_or_404(enrollment_id)
    enrollment.status = OutreachEnrollment.STATUS_REMOVED
    OutreachSend.query.filter(
        OutreachSend.enrollment_id == enrollment.id,
        OutreachSend.status.in_(OutreachSend.PENDING_STATUSES),
    ).update({OutreachSend.status: OutreachSend.STATUS_SKIPPED}, synchronize_session=False)
    AuditLog.log('outreach_enrollment_removed', user_id=current_user.id,
                 metadata={'enrollment_id': enrollment_id, 'target_user_id': enrollment.user_id})
    db.session.commit()
    return jsonify({'ok': True}), 200


# ── Broadcast ────────────────────────────────────────────────────────────────

def _broadcast_recipients(data):
    """Resolve the recipient list for a broadcast request payload."""
    from datetime import datetime as _dt
    from app.services import outreach_campaign_service as ocs
    segment = data.get('segment', 'new')
    phase = data.get('phase')
    signup_from = signup_to = None
    if data.get('signup_from'):
        try:
            signup_from = _dt.fromisoformat(data['signup_from'].replace('Z', ''))
        except ValueError:
            pass
    if data.get('signup_to'):
        try:
            signup_to = _dt.fromisoformat(data['signup_to'].replace('Z', ''))
        except ValueError:
            pass
    return ocs.resolve_segment(segment, phase=phase,
                               signup_from=signup_from, signup_to=signup_to)


@admin_bp.route('/outreach/broadcast/count', methods=['POST'])
@login_required
@admin_required
def outreach_broadcast_count():
    """Live recipient count for the selected segment (FR-OUT-09)."""
    recipients = _broadcast_recipients(request.get_json(silent=True) or {})
    return jsonify({'count': len(recipients)}), 200


@admin_bp.route('/outreach/broadcast/test', methods=['POST'])
@login_required
@admin_required
def outreach_broadcast_test():
    """Send a test copy with sample tokens to the admin (FR-OUT-09)."""
    from app.services import outreach_campaign_service as ocs
    data = request.get_json(silent=True) or {}
    to_addr = (data.get('to') or current_user.email or '').strip()
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()
    if not to_addr or not subject or not body:
        return jsonify({'error': 'to, subject, and body are required'}), 400
    result = ocs.send_test_email(to_addr, subject, body, data.get('preview_text') or '')
    code = 200 if result.get('status') == 'sent' else 500
    return jsonify(result), code


@admin_bp.route('/outreach/broadcast/send', methods=['POST'])
@login_required
@admin_required
def outreach_broadcast_send():
    """Send now or schedule a broadcast to a segment (FR-OUT-09).

    Confirmation gate: sends over 50 recipients require confirm=true.
    """
    from datetime import datetime as _dt
    from app.services import outreach_campaign_service as ocs
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()
    if not subject or not body:
        return jsonify({'error': 'subject and body are required'}), 400

    recipients = _broadcast_recipients(data)
    count = len(recipients)
    if count == 0:
        return jsonify({'error': 'No eligible recipients in this segment'}), 400

    if count > 50 and not data.get('confirm'):
        return jsonify({
            'requires_confirmation': True,
            'count': count,
            'message': f'This broadcast will send to {count} recipients. Confirm to proceed.',
        }), 409

    schedule_at = None
    if data.get('schedule_at'):
        try:
            schedule_at = _dt.fromisoformat(data['schedule_at'].replace('Z', ''))
        except ValueError:
            return jsonify({'error': 'schedule_at must be an ISO datetime'}), 400

    result = ocs.create_broadcast(
        recipients=recipients,
        subject=subject,
        body=body,
        preview_text=data.get('preview_text') or '',
        template_key=data.get('template_key') or 'broadcast',
        schedule_at=schedule_at,
        dispatch=(schedule_at is None),
    )
    AuditLog.log('outreach_broadcast_sent', user_id=current_user.id, metadata={
        'segment': data.get('segment'), 'count': count,
        'scheduled': bool(schedule_at), 'subject': subject[:120],
    })
    db.session.commit()
    return jsonify({'ok': True, 'recipient_count': count, **result}), 200


# ═══════════════════════════════════════════════════════════════════════════
# SIM-PRD-SME-001 — Simi SME Assignment
# Subject-matter experts, expertise-zone taxonomy, auto zone assignment,
# user-to-SME matching, coverage map. All endpoints admin-only under /api/admin.
# ═══════════════════════════════════════════════════════════════════════════

_VALID_URL_SCHEMES = ('http://', 'https://')


def _validate_sme_payload(data, categories, existing=None):
    """Return (clean_dict, error). Shared by create + edit."""
    first = (data.get('first_name') or '').strip()
    last = (data.get('last_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    if not first or not last:
        return None, 'First name and last name are required'
    if not email or '@' not in email:
        return None, 'A valid email is required'

    zones = data.get('zones') or []
    if not isinstance(zones, list) or not zones:
        return None, 'At least one expertise zone is required'
    zones = [z.strip().lower() for z in zones if isinstance(z, str) and z.strip()]
    valid = set(categories)
    bad = [z for z in zones if z not in valid]
    if bad:
        return None, f'Unknown expertise zone(s): {", ".join(bad)}'

    bio_url = (data.get('bio_url') or '').strip() or None
    if bio_url and not bio_url.startswith(_VALID_URL_SCHEMES):
        return None, 'Bio URL must start with http:// or https://'

    try:
        capacity = int(data.get('capacity', 50))
    except (TypeError, ValueError):
        return None, 'Capacity must be a number'
    if capacity < 0:
        return None, 'Capacity cannot be negative'

    status = (data.get('status') or 'active').strip().lower()
    if status not in ('active', 'inactive'):
        return None, 'Status must be active or inactive'

    return {
        'first_name': first[:80],
        'last_name': last[:80],
        'email': email[:160],
        'bio_url': bio_url[:500] if bio_url else None,
        'phone': ((data.get('phone') or '').strip() or None),
        'zones': zones,
        'capacity': capacity,
        'status': status,
    }, None


# ── Expertise category taxonomy (FR-SME-03) ───────────────────────────────────

@admin_bp.route('/expertise-categories', methods=['GET'])
@login_required
@admin_required
def list_expertise_categories():
    from app.services import sme_service
    include_inactive = request.args.get('all') == '1'
    from app.models.sme import ExpertiseCategory
    if include_inactive:
        cats = ExpertiseCategory.query.order_by(
            ExpertiseCategory.sort_order, ExpertiseCategory.name,
        ).all()
    else:
        cats = sme_service.active_categories()
    return jsonify([c.to_dict() for c in cats]), 200


@admin_bp.route('/expertise-categories', methods=['POST'])
@login_required
@admin_required
def create_expertise_category():
    import re
    from app.models.sme import ExpertiseCategory
    from utils.id_gen import generate_id
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    slug = (data.get('slug') or name).strip().lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
    if not slug:
        return jsonify({'error': 'Could not derive a slug from the name'}), 400
    if ExpertiseCategory.query.filter_by(slug=slug).first():
        return jsonify({'error': f'Category slug "{slug}" already exists'}), 409

    max_order = db.session.query(db.func.coalesce(db.func.max(ExpertiseCategory.sort_order), 0)).scalar()
    cat = ExpertiseCategory(
        id=generate_id(), name=name[:80], slug=slug[:80],
        is_active=True, sort_order=(max_order or 0) + 10,
    )
    db.session.add(cat)
    AuditLog.log('expertise_category_created', user_id=current_user.id, metadata={'slug': slug})
    db.session.commit()
    return jsonify(cat.to_dict()), 201


@admin_bp.route('/expertise-categories/<cat_id>', methods=['PUT'])
@login_required
@admin_required
def update_expertise_category(cat_id):
    from app.models.sme import ExpertiseCategory
    cat = ExpertiseCategory.query.get_or_404(cat_id)
    data = request.get_json() or {}
    if 'name' in data and data['name'].strip():
        cat.name = data['name'].strip()[:80]
    if 'is_active' in data:
        cat.is_active = bool(data['is_active'])
    if 'sort_order' in data:
        try:
            cat.sort_order = int(data['sort_order'])
        except (TypeError, ValueError):
            pass
    db.session.commit()
    return jsonify(cat.to_dict()), 200


# ── SME CRUD (FR-SME-01/02) ───────────────────────────────────────────────────

@admin_bp.route('/experts', methods=['GET'])
@login_required
@admin_required
def list_experts():
    from app.models.sme import SimiSME
    q = (request.args.get('q') or '').strip().lower()
    status = (request.args.get('status') or '').strip().lower()
    query = SimiSME.query
    if status in ('active', 'inactive'):
        query = query.filter_by(status=status)
    smes = query.order_by(SimiSME.first_name, SimiSME.last_name).all()
    if q:
        smes = [
            s for s in smes
            if q in s.full_name.lower() or q in s.email.lower()
            or any(q in z for z in s.zones)
        ]
    return jsonify([s.to_dict() for s in smes]), 200


@admin_bp.route('/experts', methods=['POST'])
@login_required
@admin_required
def create_expert():
    from app.models.sme import SimiSME
    from app.services import sme_service
    from utils.id_gen import generate_id
    data = request.get_json() or {}
    clean, err = _validate_sme_payload(data, sme_service.active_category_slugs())
    if err:
        return jsonify({'error': err}), 400
    if SimiSME.query.filter_by(email=clean['email']).first():
        return jsonify({'error': 'An SME with this email already exists'}), 409

    sme = SimiSME(id=generate_id())
    sme.first_name = clean['first_name']
    sme.last_name = clean['last_name']
    sme.email = clean['email']
    sme.bio_url = clean['bio_url']
    sme.phone = clean['phone']
    sme.zones = clean['zones']
    sme.capacity = clean['capacity']
    sme.status = clean['status']
    db.session.add(sme)
    AuditLog.log('sme_created', user_id=current_user.id, resource_id=sme.id,
                 metadata={'email': sme.email, 'zones': sme.zones})
    db.session.commit()
    return jsonify(sme.to_dict()), 201


@admin_bp.route('/experts/<sme_id>', methods=['GET'])
@login_required
@admin_required
def get_expert(sme_id):
    from app.models.sme import SimiSME
    sme = SimiSME.query.get_or_404(sme_id)
    return jsonify(sme.to_dict(include_users=True)), 200


@admin_bp.route('/experts/<sme_id>', methods=['PUT'])
@login_required
@admin_required
def update_expert(sme_id):
    from app.models.sme import SimiSME
    from app.services import sme_service
    sme = SimiSME.query.get_or_404(sme_id)
    data = request.get_json() or {}
    clean, err = _validate_sme_payload(data, sme_service.active_category_slugs(), existing=sme)
    if err:
        return jsonify({'error': err}), 400
    dup = SimiSME.query.filter(SimiSME.email == clean['email'], SimiSME.id != sme.id).first()
    if dup:
        return jsonify({'error': 'Another SME already uses this email'}), 409

    zones_changed = set(sme.zones) != set(clean['zones'])
    was_active = sme.is_active

    sme.first_name = clean['first_name']
    sme.last_name = clean['last_name']
    sme.email = clean['email']
    sme.bio_url = clean['bio_url']
    sme.phone = clean['phone']
    sme.zones = clean['zones']
    sme.capacity = clean['capacity']
    sme.status = clean['status']

    flagged = 0
    # Changing zones does not auto-reassign existing users — flag them for review (FR-SME-02).
    if zones_changed and sme.assigned_count > 0:
        sme.needs_review = True
        flagged = sme_service.flag_sme_users_for_reassignment(sme, commit=False)
    # Deactivation stops new assignments and flags existing users for reassignment.
    if was_active and not sme.is_active and sme.assigned_count > 0:
        flagged = max(flagged, sme_service.flag_sme_users_for_reassignment(sme, commit=False))

    AuditLog.log('sme_updated', user_id=current_user.id, resource_id=sme.id,
                 metadata={'zones_changed': zones_changed, 'flagged': flagged})
    db.session.commit()
    return jsonify({**sme.to_dict(), 'flagged_for_review': flagged}), 200


@admin_bp.route('/experts/<sme_id>/deactivate', methods=['POST'])
@login_required
@admin_required
def deactivate_expert(sme_id):
    from app.models.sme import SimiSME
    from app.services import sme_service
    sme = SimiSME.query.get_or_404(sme_id)
    sme.status = SimiSME.STATUS_INACTIVE
    flagged = sme_service.flag_sme_users_for_reassignment(sme, commit=False)
    AuditLog.log('sme_deactivated', user_id=current_user.id, resource_id=sme.id,
                 metadata={'flagged': flagged})
    db.session.commit()
    return jsonify({'ok': True, 'flagged_for_reassignment': flagged}), 200


@admin_bp.route('/experts/<sme_id>/reassign-users', methods=['POST'])
@login_required
@admin_required
def reassign_expert_users(sme_id):
    """Bulk auto-rematch all users currently assigned to this SME (FR-SME-02)."""
    from app.models.sme import SimiSME
    from app.services import sme_service
    sme = SimiSME.query.get_or_404(sme_id)
    rematched, unassigned = sme_service.reassign_sme_users(sme, commit=False)
    sme.needs_review = False
    AuditLog.log('sme_users_reassigned', user_id=current_user.id, resource_id=sme.id,
                 metadata={'rematched': rematched, 'unassigned': unassigned})
    db.session.commit()
    return jsonify({'ok': True, 'rematched': rematched, 'unassigned': unassigned}), 200


# ── Users view + zone/SME assignment (FR-SME-05/06/07) ────────────────────────

@admin_bp.route('/sme-users', methods=['GET'])
@login_required
@admin_required
def list_sme_users():
    """Users with canonical zones + assigned SME. Filterable by zone / sme / unassigned."""
    from app.models.profile import UserProfile
    from app.models.sme import SimiSME
    zone = (request.args.get('zone') or '').strip().lower()
    sme_filter = (request.args.get('sme') or '').strip()
    unassigned_only = request.args.get('unassigned') == '1'

    profiles = UserProfile.query.filter(UserProfile._canonical_zones.isnot(None)).all()
    smes = {s.id: s for s in SimiSME.query.all()}

    rows = []
    for p in profiles:
        pz = p.primary_zone
        if zone and pz != zone and zone not in [z.get('category') for z in p.canonical_zones]:
            continue
        if unassigned_only and p.sme_id:
            continue
        if sme_filter and p.sme_id != sme_filter:
            continue
        sme = smes.get(p.sme_id) if p.sme_id else None
        rows.append({
            'user_id': p.user_id,
            'display_name': p.display_name or p.username,
            'username': p.username,
            'canonical_zones': p.canonical_zones,
            'primary_zone': pz,
            'sme_id': p.sme_id,
            'sme_name': sme.full_name if sme else None,
            'assignment_type': p.sme_assignment_type,
            'needs_reassignment': bool(p.needs_reassignment),
            'zones_computed_at': p.zones_computed_at.isoformat() if p.zones_computed_at else None,
        })
    rows.sort(key=lambda r: (r['sme_id'] is not None, r['display_name'].lower()))
    return jsonify(rows), 200


@admin_bp.route('/sme-users/<user_id>/zones', methods=['PUT'])
@login_required
@admin_required
def update_user_zones(user_id):
    """FR-SME-05 — admin manually edits a user's canonical zones (manual lock)."""
    from app.models.profile import UserProfile
    from app.services import sme_service
    profile = UserProfile.query.filter_by(user_id=user_id).first_or_404()
    data = request.get_json() or {}

    if data.get('reset_to_ai'):
        sme_service.classify_user_zones(profile, force=True, commit=True)
        return jsonify({'ok': True, 'canonical_zones': profile.canonical_zones, 'source': 'ai'}), 200

    zones = data.get('canonical_zones')
    if not isinstance(zones, list):
        return jsonify({'error': 'canonical_zones must be a list'}), 400
    valid = set(sme_service.active_category_slugs())
    clean = []
    for z in zones:
        cat = (z.get('category') or '').strip().lower()
        if cat not in valid:
            return jsonify({'error': f'Unknown category: {cat}'}), 400
        clean.append({
            'category': cat,
            'confidence': float(z.get('confidence', 1.0)),
            'is_primary': bool(z.get('is_primary')),
        })
    # Exactly one primary; default to first if none flagged.
    primaries = [z for z in clean if z['is_primary']]
    if clean and not primaries:
        clean[0]['is_primary'] = True
    elif len(primaries) > 1:
        for z in clean:
            z['is_primary'] = False
        primaries[0]['is_primary'] = True

    profile.canonical_zones = clean
    profile.zones_computed_at = None  # manual lock — not overwritten by re-runs
    AuditLog.log('sme_user_zones_edited', user_id=current_user.id, resource_id=user_id,
                 metadata={'zones': [z['category'] for z in clean]})
    db.session.commit()
    return jsonify({'ok': True, 'canonical_zones': profile.canonical_zones, 'source': 'manual'}), 200


@admin_bp.route('/sme-users/<user_id>/recompute-zones', methods=['POST'])
@login_required
@admin_required
def recompute_user_zones(user_id):
    from app.models.profile import UserProfile
    from app.services import sme_service
    profile = UserProfile.query.filter_by(user_id=user_id).first_or_404()
    force = bool((request.get_json() or {}).get('force'))
    sme_service.run_classification_and_assignment(profile, force=force)
    return jsonify({
        'ok': True,
        'canonical_zones': profile.canonical_zones,
        'sme_id': profile.sme_id,
    }), 200


@admin_bp.route('/sme-users/<user_id>/assign', methods=['POST'])
@login_required
@admin_required
def assign_user_sme(user_id):
    """FR-SME-06/07 — assign a user to an SME (manual) or trigger auto-match."""
    from app.models.profile import UserProfile
    from app.services import sme_service
    profile = UserProfile.query.filter_by(user_id=user_id).first_or_404()
    data = request.get_json() or {}

    if data.get('auto'):
        sme = sme_service.auto_assign_sme(profile, force_over_capacity=bool(data.get('force')))
        return jsonify({'ok': True, 'sme_id': profile.sme_id,
                        'sme_name': sme.full_name if sme else None,
                        'assignment_type': profile.sme_assignment_type}), 200

    sme_id = data.get('sme_id')  # may be None to clear
    try:
        sme_service.manual_assign_sme(profile, sme_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    AuditLog.log('sme_user_assigned', user_id=current_user.id, resource_id=user_id,
                 metadata={'sme_id': sme_id, 'type': 'manual'})
    db.session.commit()
    return jsonify({'ok': True, 'sme_id': profile.sme_id,
                    'assignment_type': profile.sme_assignment_type}), 200


# ── Bulk actions + coverage map (FR-SME-08/09) ────────────────────────────────

@admin_bp.route('/sme/assign-unassigned', methods=['POST'])
@login_required
@admin_required
def sme_assign_unassigned():
    from app.services import sme_service
    count = sme_service.assign_unassigned()
    AuditLog.log('sme_bulk_assign_unassigned', user_id=current_user.id, metadata={'assigned': count})
    db.session.commit()
    return jsonify({'ok': True, 'assigned': count}), 200


@admin_bp.route('/sme/rebalance', methods=['POST'])
@login_required
@admin_required
def sme_rebalance():
    from app.services import sme_service
    slug = ((request.get_json() or {}).get('zone') or '').strip().lower()
    if not slug:
        return jsonify({'error': 'zone slug is required'}), 400
    moved = sme_service.rebalance_zone(slug)
    AuditLog.log('sme_rebalance', user_id=current_user.id, metadata={'zone': slug, 'moved': moved})
    db.session.commit()
    return jsonify({'ok': True, 'moved': moved}), 200


@admin_bp.route('/sme/recompute-all-zones', methods=['POST'])
@login_required
@admin_required
def sme_recompute_all_zones():
    """FR-SME-04 — bulk recompute canonical zones for all users (admin on-demand)."""
    from app.models.profile import UserProfile
    from app.services import sme_service
    force = bool((request.get_json() or {}).get('force'))
    profiles = UserProfile.query.all()
    processed = 0
    for p in profiles:
        try:
            sme_service.classify_user_zones(p, force=force, commit=False)
            sme_service.auto_assign_sme(p, commit=False)
            processed += 1
        except Exception as exc:
            db.session.rollback()
            logging.getLogger(__name__).warning('recompute zones failed for %s: %s', p.user_id, exc)
    db.session.commit()
    AuditLog.log('sme_recompute_all', user_id=current_user.id, metadata={'processed': processed})
    db.session.commit()
    return jsonify({'ok': True, 'processed': processed}), 200


@admin_bp.route('/sme/coverage', methods=['GET'])
@login_required
@admin_required
def sme_coverage():
    from app.services import sme_service
    return jsonify(sme_service.coverage_map()), 200
