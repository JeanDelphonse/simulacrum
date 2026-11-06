"""Compliance admin and member endpoints (COMPLIANCE_PLAN.md)."""
from __future__ import annotations

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app.blueprints.compliance import compliance_bp
from app.services import legal_text


def _admin():
    if not current_user.is_admin:
        abort(403)


@compliance_bp.route('/admin/legal-text')
@login_required
def admin_legal_text():
    _admin()
    return render_template('admin/legal_text.html', rows=legal_text.all_rows())


@compliance_bp.route('/api/admin/legal-text/<key>', methods=['PUT'])
@login_required
def admin_legal_text_update(key):
    _admin()
    data = request.get_json(silent=True) or {}
    try:
        if 'body' in data:
            legal_text.update(key, data.get('body'), current_user.id)
        if 'approved' in data:
            legal_text.set_status(key, bool(data.get('approved')), current_user.id)
    except KeyError:
        return jsonify({'error': 'Unknown text.'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    row = next(r for r in legal_text.all_rows() if r['key'] == key)
    return jsonify({'ok': True, 'status': row['status'], 'version': row['version']})


# ── Outcome check-ins (A6): the member's own answers; internal only ─────────
@compliance_bp.route('/api/outcome-checkins', methods=['POST'])
@login_required
def outcome_record():
    from app.services import outcome_service
    try:
        row = outcome_service.record(current_user.id, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'id': row.id})


@compliance_bp.route('/admin/compliance')
@login_required
def admin_compliance():
    """Layer 5 switch, compliance events and outcome check-in counts."""
    _admin()
    from app.models.compliance import ComplianceEvent
    from app.services import outcome_service
    events = ComplianceEvent.query.order_by(ComplianceEvent.created_at.desc()).limit(200).all()
    from app.models.platform_settings import PlatformSetting
    from app.services import send_limits
    limits = [(k, PlatformSetting.get(k, d) or d) for k, d in send_limits.DEFAULTS.items()]
    return render_template('admin/compliance.html', events=events, outcomes=outcome_service.admin_summary(),
                           l5=_l5_state(), limits=limits,
                           business_address=PlatformSetting.get('outreach_business_address', '') or '')


def _l5_state():
    try:
        from app.services import l5_feature
        return l5_feature.state()
    except ImportError:
        return None


@compliance_bp.route('/api/admin/compliance/l5', methods=['PUT'])
@login_required
def admin_l5_switch():
    _admin()
    from app.services import l5_feature
    data = request.get_json(silent=True) or {}
    try:
        l5_feature.set_mode(data.get('mode'), current_user.id, data.get('reason') or '')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'mode': l5_feature.state()['mode']})


# ── The member's mailing address for commercial email (C1, D7) ──────────────
@compliance_bp.route('/api/settings/postal-address', methods=['PUT'])
@login_required
def postal_address_update():
    from app.models.audit_log import AuditLog
    from app.extensions import db
    from app.services import sender_identity
    try:
        value = sender_identity.set_postal_address(current_user, (request.get_json(silent=True) or {}).get('address'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    AuditLog.log('postal_address_set', user_id=current_user.id)
    db.session.commit()
    # Clear the GCC reminder now that sends can go out.
    try:
        from app.models.layer6 import ActionItem
        from utils.action_items import resolve_action_item
        for item in ActionItem.query.filter_by(user_id=current_user.id, item_type='postal_address_required',
                                               status=ActionItem.STATUS_ACTIVE).all():
            resolve_action_item(item.id, emit_sse=False)
    except Exception:
        pass
    return jsonify({'ok': True, 'address': value})


@compliance_bp.route('/api/admin/compliance/settings', methods=['PUT'])
@login_required
def admin_compliance_settings():
    """SimiSI's postal address and the send limits (Admin settings, safe defaults)."""
    _admin()
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.platform_settings import PlatformSetting
    from app.services import send_limits
    data = request.get_json(silent=True) or {}
    allowed = {'outreach_business_address'} | set(send_limits.DEFAULTS)
    changed = {}
    for key, value in data.items():
        if key not in allowed:
            return jsonify({'error': 'Unknown setting %s.' % key}), 400
        value = str(value).strip()
        if key in send_limits.DEFAULTS:
            try:
                send_limits.parse(key, value)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        PlatformSetting.set(key, value)
        changed[key] = value
    AuditLog.log('compliance_settings_updated', user_id=current_user.id, metadata=changed)
    db.session.commit()
    return jsonify({'ok': True})



@compliance_bp.route('/api/campaigns/<action_id>/resume', methods=['POST'])
@login_required
def campaign_resume(action_id):
    """The owner resumes a campaign the bounce/complaint guard paused."""
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation
    from app.services import send_limits
    action = AgentAction.query.get_or_404(action_id)
    sim = Simulation.query.get(action.simulation_id)
    if not sim or sim.user_id != current_user.id:
        return jsonify({'error': 'Not your campaign.'}), 403
    return jsonify({'ok': True, 'resumed_steps': send_limits.resume_campaign(action_id, current_user.id)})
