import time
import logging
from datetime import datetime
from flask import render_template, redirect, url_for, jsonify, request, send_from_directory, current_app
from flask_login import current_user, login_required
from app.blueprints.pages import pages_bp
from app.extensions import db
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

# ── Landing page cache (avoids DB queries on every unauthenticated hit) ──────
_landing_cache = {'data': None, 'ts': 0.0}
_LANDING_TTL   = 300  # 5 minutes


def _get_landing_data():
    now = time.monotonic()
    if _landing_cache['data'] and now - _landing_cache['ts'] < _LANDING_TTL:
        return _landing_cache['data']

    from app.extensions import db as _db
    from app.models.feedback import UserFeedback
    from app.models.simulation import Simulation as _Sim
    from sqlalchemy import func as _func

    testimonials = UserFeedback.query.filter_by(status='approved').order_by(
        UserFeedback.is_featured.desc(),
        UserFeedback.display_order.asc(),
        UserFeedback.approved_at.desc(),
    ).limit(50).all()
    avg_row = _db.session.query(_func.avg(UserFeedback.star_rating)).filter_by(status='approved').scalar()
    data = {
        'testimonials': [t.to_public_dict() for t in testimonials],
        'trust_stats': {
            'avg_rating':        round(float(avg_row or 0), 1),
            'total_simulations': _Sim.query.count(),
            'approved_count':    len(testimonials),
        },
    }
    _landing_cache['data'] = data
    _landing_cache['ts']   = now
    return data


@pages_bp.route('/sitemap.xml')
def sitemap():
    return send_from_directory(
        current_app.static_folder, 'sitemap.xml',
        mimetype='application/xml'
    )


@pages_bp.route('/ping')
def ping():
    """Lightweight keepalive — no DB queries. Hit by cron to keep Passenger warm."""
    return jsonify({'ok': True}), 200


def _sim_price_usd() -> str:
    """Return formatted simulation price from platform_settings, e.g. '$695'."""
    try:
        from app.models.platform_settings import PlatformSetting
        cents = int(PlatformSetting.get('simulation_price') or current_app.config['SIMULATION_PRICE_CENTS'])
    except Exception:
        cents = int(current_app.config.get('SIMULATION_PRICE_CENTS', 69500))
    return f'${cents // 100:,}' if cents % 100 == 0 else f'${cents / 100:,.2f}'


@pages_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('pages.dashboard'))
    data = _get_landing_data()
    return render_template('landing.html',
                           testimonials=data['testimonials'],
                           trust_stats=data['trust_stats'],
                           sim_price_usd=_sim_price_usd())


@pages_bp.route('/dashboard')
@login_required
def dashboard():
    from app.models.simulation import Simulation
    from app.models.resume import Resume
    from app.models.collaboration import Collaboration
    from app.models.partner import ReferralPartner
    from app.extensions import db as _db
    simulations = Simulation.query.filter_by(user_id=current_user.id).order_by(Simulation.created_at.desc()).all()
    resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    pending_collabs = Collaboration.query.filter_by(
        invitee_email=current_user.email,
        accepted_at=None,
        revoked_at=None,
    ).all()

    # Partner shortcut card (FR-CTP-10)
    partner = None
    if current_user.is_partner:
        partner = ReferralPartner.query.filter_by(
            user_id=current_user.id, status=ReferralPartner.STATUS_ACTIVE,
        ).first()

    # FR-CTP-09: one-time welcome modal for newly approved partners
    show_partner_welcome = current_user.is_partner and not current_user.partner_welcome_shown
    if show_partner_welcome:
        current_user.partner_welcome_shown = True
        _db.session.commit()

    return render_template('dashboard/index.html',
                           simulations=simulations,
                           resumes=resumes,
                           pending_collabs=pending_collabs,
                           partner=partner,
                           show_partner_welcome=show_partner_welcome)


@pages_bp.route('/simulations/<sim_id>/delete', methods=['POST'])
@login_required
def simulation_delete(sim_id):
    from app.models.simulation import Simulation
    from app.extensions import db
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    if sim.status == Simulation.STATUS_COMPLETE:
        from flask import abort
        abort(400)
    db.session.delete(sim)
    db.session.commit()
    return redirect(url_for('pages.dashboard'))


@pages_bp.route('/simulations/<sim_id>/confirmed')
@login_required
def simulation_confirmed(sim_id):
    from app.models.simulation import Simulation
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    cents = sim.amount_charged_cents or int(current_app.config.get('SIMULATION_PRICE_CENTS', 69500))
    paid = f'${cents // 100:,}' if cents % 100 == 0 else f'${cents / 100:,.2f}'
    return render_template('simulations/confirmed.html', simulation=sim, payment_amount=paid)


@pages_bp.route('/simulations/<sim_id>')
@login_required
def simulation_view(sim_id):
    from app.models.simulation import Simulation
    sim = Simulation.query.get_or_404(sim_id)
    # Allow access to owner or accepted collaborators
    from app.models.collaboration import Collaboration
    is_owner = sim.user_id == current_user.id
    is_collab = Collaboration.query.filter_by(
        simulation_id=sim_id,
        invitee_email=current_user.email,
    ).filter(Collaboration.accepted_at.isnot(None)).first()
    if not is_owner and not is_collab:
        from flask import abort
        abort(403)
    layers = sorted(sim.layers, key=lambda l: l.layer_number)
    return render_template('simulations/detail.html', simulation=sim, layers=layers,
                           is_owner=is_owner)


@pages_bp.route('/simulations/<sim_id>/income')
@login_required
def simulation_income(sim_id):
    from app.models.simulation import Simulation
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    return render_template('simulations/income.html', simulation=sim)


@pages_bp.route('/settings')
@login_required
def settings_redirect():
    return redirect(url_for('pages.settings_profile'))


@pages_bp.route('/settings/profile')
@login_required
def settings_profile():
    from app.models.profile import UserProfile
    from app.models.resume import Resume
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    has_source_data = Resume.query.filter_by(user_id=current_user.id).filter(
        Resume.parsed_text.isnot(None)
    ).first() is not None
    return render_template(
        'settings/index.html',
        active_tab='profile',
        profile=profile,
        has_source_data=has_source_data,
    )


@pages_bp.route('/u/<slug>/edit')
@login_required
def bio_editor(slug: str):
    """Bio page editor — authenticated owner only."""
    from flask import abort
    from app.models.profile import UserProfile
    from app.models.bio_page import BioPage
    profile = UserProfile.query.filter_by(username=slug.lower(), user_id=current_user.id).first()
    if not profile:
        abort(403)
    bio_page = BioPage.query.filter_by(user_id=current_user.id).first()
    return render_template(
        'public/bio_editor.html',
        profile=profile,
        bio_page=bio_page,
        slug=slug,
    )


@pages_bp.route('/settings/visibility')
@login_required
def settings_visibility():
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        from app.models.profile import UserProfile as UP
        import re as _re
        from utils.id_gen import generate_id as _gen
        from app.extensions import db as _db
        base = _re.sub(r'[^a-z0-9]', '-', current_user.full_name.lower())
        base = _re.sub(r'-+', '-', base).strip('-')[:28] or 'user'
        candidate = base
        n = 1
        while UP.query.filter_by(username=candidate).first():
            candidate = f'{base}-{n}'; n += 1
        profile = UP(id=_gen(), user_id=current_user.id, username=candidate, display_name=current_user.full_name)
        _db.session.add(profile); _db.session.commit()
    return render_template('settings/index.html', active_tab='visibility', profile=profile)


@pages_bp.route('/settings/security')
@login_required
def settings_security():
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    return render_template('settings/index.html', active_tab='security', profile=profile)


@pages_bp.route('/settings/testimonials')
@login_required
def settings_testimonials():
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    return render_template('settings/index.html', active_tab='testimonials', profile=profile)


@pages_bp.route('/settings/my-chats')
@login_required
def settings_my_chats():
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    return render_template('settings/index.html', active_tab='my_chats', profile=profile)


@pages_bp.route('/settings/integrations')
@login_required
def settings_integrations():
    from app.models.profile import UserProfile
    from app.models.integration import UserIntegration
    from app.models.platform_settings import PlatformSetting
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    apollo = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='apollo'
    ).first()
    stripe_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='stripe'
    ).first()
    cal_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='cal'
    ).first()
    pandadoc_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='pandadoc'
    ).first()
    ck_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='convertkit'
    ).first()
    kajabi_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='kajabi'
    ).first()
    plaid_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='plaid'
    ).first()
    alpaca_int = UserIntegration.query.filter_by(
        user_id=current_user.id, provider='alpaca'
    ).first()
    sg_connected = bool(PlatformSetting.get('sendgrid_api_key'))
    linkedin_enabled = PlatformSetting.get('linkedin_integration_enabled') == 'on'

    alpaca_meta = alpaca_int.get_meta() if alpaca_int else {}

    # LinkedIn connection state (profile URL stored on user's resume record)
    from app.models.resume import Resume as _Resume
    linkedin_resume = _Resume.query.filter_by(
        user_id=current_user.id, source='linkedin'
    ).order_by(_Resume.created_at.desc()).first()

    return render_template(
        'settings/index.html',
        active_tab='integrations',
        linkedin_enabled=linkedin_enabled,
        linkedin_resume=linkedin_resume,
        profile=profile,
        apollo=apollo,
        stripe_int=stripe_int,
        cal_int=cal_int,
        pandadoc_int=pandadoc_int,
        ck_int=ck_int,
        kajabi_int=kajabi_int,
        plaid_int=plaid_int,
        alpaca_int=alpaca_int,
        alpaca_fintech_toggle=alpaca_meta.get('fintech_toggle', False),
        alpaca_paper=alpaca_meta.get('paper', True),
        sg_connected=sg_connected,
        apollo_connected=request.args.get('apollo_connected') == '1',
        apollo_error=request.args.get('apollo_error'),
        stripe_connected=request.args.get('stripe_connected') == '1',
        stripe_error=request.args.get('stripe_error'),
        cal_connected=request.args.get('cal_connected') == '1',
        cal_error=request.args.get('cal_error'),
        pandadoc_connected=request.args.get('pandadoc_connected') == '1',
        pandadoc_error=request.args.get('pandadoc_error'),
        ck_saved=request.args.get('ck_saved') == '1',
        ck_error=request.args.get('ck_error'),
        kajabi_saved=request.args.get('kajabi_saved') == '1',
        kajabi_error=request.args.get('kajabi_error'),
        sg_saved=request.args.get('sg_saved') == '1',
        sg_error=request.args.get('sg_error'),
    )


@pages_bp.route('/settings/notifications')
@login_required
def settings_notifications():
    from app.models.profile import UserProfile
    from app.services.notification_service import get_preferences
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    prefs = get_preferences(current_user.id)
    return render_template(
        'settings/index.html',
        active_tab='notifications',
        profile=profile,
        notif_prefs=prefs,
    )


@pages_bp.route('/resumes')
@login_required
def resumes_view():
    from app.models.resume import Resume
    from app.models.platform_settings import PlatformSetting
    resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    linkedin_enabled = PlatformSetting.get('linkedin_integration_enabled') == 'on'
    return render_template('resumes/list.html', resumes=resumes, linkedin_enabled=linkedin_enabled)


@pages_bp.route('/resumes/<resume_id>')
@login_required
def resume_detail(resume_id):
    from flask import current_app
    from app.models.resume import Resume
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id).first_or_404()
    stripe_pk = current_app.config.get('STRIPE_PUBLISHABLE_KEY', '')
    return render_template('resumes/detail.html', resume=resume, stripe_pk=stripe_pk)


@pages_bp.route('/samples/marcus')
def sample_marcus():
    return render_template('samples/simulacrum_usecase_marcus.html')


@pages_bp.route('/samples/maya')
def sample_maya():
    return render_template('samples/simulacrum_usecase_maya.html')


@pages_bp.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('pages.dashboard'))
    return render_template('auth/login.html')


@pages_bp.route('/register')
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for('pages.dashboard'))
    return render_template('auth/register.html')


@pages_bp.route('/verify-sent')
def verify_sent_page():
    return render_template('auth/verify_sent.html')


@pages_bp.route('/forgot-password')
def forgot_password_page():
    return render_template('auth/reset_password.html')


@pages_bp.route('/admin')
@login_required
def admin_view():
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    from sqlalchemy import func
    from app.models.platform_settings import PlatformSetting
    from app.models.user import User
    from app.models.profile import UserProfile
    from app.models.simulation import Simulation
    from app.models.ai_interaction import AIInteraction
    from app.extensions import db

    settings = {s.key: s.value for s in PlatformSetting.query.all()}
    users = User.query.order_by(User.created_at.desc()).limit(50).all()
    user_ids = [u.id for u in users]
    profiles = {p.user_id: p for p in UserProfile.query.filter(UserProfile.user_id.in_(user_ids)).all()}

    total_completed = Simulation.query.filter_by(status=Simulation.STATUS_COMPLETE).count()
    total_refunded = Simulation.query.filter_by(status=Simulation.STATUS_REFUNDED).count()
    revenue_row = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter_by(status=Simulation.STATUS_COMPLETE).first()
    total_revenue_cents = revenue_row[0] if revenue_row else 0
    refund_rate = round(total_refunded / max(total_completed + total_refunded, 1) * 100, 2)

    token_row = db.session.query(
        func.coalesce(func.sum(AIInteraction.prompt_tokens), 0),
        func.coalesce(func.sum(AIInteraction.completion_tokens), 0),
    ).first()
    total_tokens = (token_row[0] or 0) + (token_row[1] or 0)

    revenue_stats = {
        'total_revenue_usd': total_revenue_cents / 100,
        'total_simulations_completed': total_completed,
        'refund_rate_pct': refund_rate,
        'total_tokens': total_tokens,
    }

    return render_template('admin/index.html', settings=settings, users=users,
                           profiles=profiles, revenue_stats=revenue_stats)


@pages_bp.route('/admin/feedback')
@login_required
def admin_feedback_view():
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    return render_template('admin/feedback.html')


@pages_bp.route('/admin/partners')
@login_required
def admin_partners_view():
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    from app.models.partner import ReferralPartner
    partners = ReferralPartner.query.order_by(ReferralPartner.applied_at.desc()).all()
    return render_template('admin/partners.html', partners=partners)


@pages_bp.route('/admin/corporate')
@login_required
def admin_corporate_view():
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    from app.models.corporate import CorporateAccount
    orgs = CorporateAccount.query.order_by(CorporateAccount.created_at.desc()).all()
    return render_template('admin/corporate.html', orgs=orgs)


@pages_bp.route('/admin/users/<target_uid>/integrations')
@login_required
def admin_user_integrations(target_uid):
    """Admin override view for a user's integration settings (FR-SETTINGS-07)."""
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    from app.models.user import User
    from app.models.integration import UserIntegration, IntegrationAuditLog
    target = User.query.get_or_404(target_uid)
    integrations = {
        rec.provider: rec
        for rec in UserIntegration.query.filter_by(user_id=target_uid).all()
    }
    recent_audits = IntegrationAuditLog.query.filter_by(
        target_user_id=target_uid,
    ).order_by(IntegrationAuditLog.created_at.desc()).limit(50).all()
    return render_template(
        'admin/user_integrations.html',
        target_user=target,
        integrations=integrations,
        recent_audits=recent_audits,
    )


@pages_bp.route('/admin/audit')
@login_required
def admin_audit_log():
    """Integration audit log viewer (FR-SETTINGS-09)."""
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    from app.models.integration import IntegrationAuditLog
    page     = request.args.get('page', 1, type=int)
    provider = request.args.get('provider', '')
    q = IntegrationAuditLog.query
    if provider:
        q = q.filter_by(integration_type=provider)
    entries = q.order_by(IntegrationAuditLog.created_at.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    return render_template('admin/audit_log.html', entries=entries, provider_filter=provider)


# Admin API: emergency token revoke
@pages_bp.route('/api/admin/integrations/<target_uid>/<provider>/revoke', methods=['POST'])
@login_required
def admin_revoke_token(target_uid, provider):
    """Emergency token revocation (FR-SETTINGS-10)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    from app.models.integration import UserIntegration, IntegrationAuditLog
    from app.models.user import User

    integration = UserIntegration.query.filter_by(
        user_id=target_uid, provider=provider
    ).first()
    if not integration:
        return jsonify({'error': 'Integration not found'}), 404

    import json as _json
    changes = _json.dumps({
        'access_token_enc': {'from': 'exists', 'to': 'revoked'},
        'health_status': {'from': integration.health_status, 'to': 'revoked'},
    })

    integration.access_token_enc    = None
    integration.refresh_token_enc   = None
    integration.token_expires_at    = None
    integration.health_status       = 'revoked' if hasattr(integration, '_health_revoked') else 'expired'
    integration.disconnected_at     = datetime.utcnow()

    audit = IntegrationAuditLog(
        id=generate_id(),
        admin_user_id=current_user.id,
        target_user_id=target_uid,
        integration_type=provider,
        action='token_revoked',
        changes=changes,
        ip_address=request.remote_addr,
    )
    db.session.add(audit)
    db.session.commit()

    # Notify user
    try:
        from app.services.notification_service import send_notification
        target = User.query.get(target_uid)
        if target:
            send_notification(
                user_id=target_uid,
                notification_type='escalation',
                title=f'Your {provider.title()} connection was revoked',
                body=(
                    f'Your {provider.title()} connection was revoked by a Simulacrum '
                    f'administrator. Please reconnect from Settings → Integrations.'
                ),
                cta_url=f'/settings/integrations?reauth={provider}',
                cta_label='Reconnect →',
                priority='high',
            )
    except Exception as exc:
        logger.warning('Revoke notification failed: %s', exc)

    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Partner Program public pages
# ---------------------------------------------------------------------------

@pages_bp.route('/partners/apply')
def partner_apply_page():
    from app.models.platform_settings import PlatformSetting
    from app.models.partner import ReferralPartner
    commission_rate = int(float(PlatformSetting.get('partner_commission_rate', '0.20')) * 100)
    return render_template('partners/apply.html',
                           commission_rate=commission_rate,
                           partner_types=ReferralPartner.PARTNER_TYPES)


@pages_bp.route('/partners/dashboard')
@login_required
def partner_dashboard():
    from app.models.partner import ReferralPartner
    partner = ReferralPartner.query.filter_by(
        user_id=current_user.id,
        status=ReferralPartner.STATUS_ACTIVE,
    ).first()
    if not partner:
        return redirect(url_for('pages.partner_apply_page'))
    return render_template('partners/dashboard.html', partner=partner)


@pages_bp.route('/partners/advisor-view/<sim_id>')
@login_required
def partner_advisor_view(sim_id):
    from app.models.partner import ReferralPartner, AdvisorAccess, AdvisorNote
    from app.models.simulation import Simulation
    from app.models.user import User
    partner = ReferralPartner.query.filter_by(
        user_id=current_user.id,
        status=ReferralPartner.STATUS_ACTIVE,
    ).first()
    if not partner:
        from flask import abort
        abort(403)
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id, simulation_id=sim_id, revoked_at=None,
    ).first()
    if not access:
        from flask import abort
        abort(403)
    sim = Simulation.query.get_or_404(sim_id)
    client = User.query.get(access.granted_by)
    layers = sorted(sim.layers, key=lambda l: l.layer_number)
    notes = AdvisorNote.query.filter_by(advisor_access_id=access.id).all()
    return render_template('partners/advisor_view.html',
                           simulation=sim,
                           layers=layers,
                           notes=notes,
                           client_name=client.full_name if client else 'Unknown')


@pages_bp.route('/partners/clients/<client_uid>/integrations')
@login_required
def advisor_client_integrations(client_uid):
    """Read-only integration status view for partner advisors (FR-SETTINGS-07)."""
    from flask import abort
    from app.models.partner import ReferralPartner, AdvisorAccess
    from app.models.integration import UserIntegration
    from app.models.user import User

    partner = ReferralPartner.query.filter_by(
        user_id=current_user.id, status=ReferralPartner.STATUS_ACTIVE,
    ).first()
    if not partner:
        abort(403)

    # Any shared simulation grants read access to client integrations
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id, granted_by=client_uid, revoked_at=None,
    ).first()
    if not access:
        abort(404)

    client = User.query.get_or_404(client_uid)
    integrations = {
        rec.provider: rec
        for rec in UserIntegration.query.filter_by(user_id=client_uid).all()
    }
    total_possible = 9  # Apollo, Stripe, Cal, PandaDoc, ConvertKit, Kajabi, LinkedIn, Plaid, Alpaca
    connected_count = sum(
        1 for rec in integrations.values()
        if rec.is_connected and not rec.is_expired
    )
    return render_template(
        'partners/client_integrations.html',
        client=client,
        integrations=integrations,
        connected_count=connected_count,
        total_possible=total_possible,
        partner=partner,
        access=access,
    )


@pages_bp.route('/partners/clients/<client_uid>/simulations/<sim_id>/gcc')
@login_required
def advisor_gcc_view(client_uid, sim_id):
    """Advisor mode GCC view — partner sees client's GCC with coaching overlay."""
    from flask import abort
    from datetime import datetime as _dt
    from app.models.partner import ReferralPartner, AdvisorAccess, AdvisorNote
    from app.models.simulation import Simulation
    from app.models.profile import UserProfile
    from app.models.layer6 import Layer6Config, Layer6Cycle, Layer6ActionQueue, Layer6Momentum
    from app.models.agent_action import AgentAction
    from app.extensions import db as _db
    from sqlalchemy import func as _func

    partner = ReferralPartner.query.filter_by(
        user_id=current_user.id, status=ReferralPartner.STATUS_ACTIVE,
    ).first()
    if not partner:
        abort(403)

    # Validate access — 404 to avoid enumeration
    access = AdvisorAccess.query.filter_by(
        partner_id=partner.id,
        simulation_id=sim_id,
        revoked_at=None,
    ).first()
    if not access or access.granted_by != client_uid:
        abort(404)

    access.last_viewed_at = _dt.utcnow()
    _db.session.commit()

    sim = Simulation.query.get_or_404(sim_id)
    client_profile = UserProfile.query.filter_by(user_id=client_uid).first()

    # All shared sims for this partner/client pair (for banner selector)
    all_shared_accesses = AdvisorAccess.query.filter_by(
        partner_id=partner.id, granted_by=client_uid, revoked_at=None,
    ).all()
    advisor_shared_sims = []
    for _sa in all_shared_accesses:
        _sa_sim = Simulation.query.get(_sa.simulation_id)
        if _sa_sim:
            advisor_shared_sims.append({
                'sim_id': _sa_sim.id,
                'name': _sa_sim.name or _sa_sim.expertise_zone or 'Simulation',
                'expertise_zone': _sa_sim.expertise_zone,
                'is_current': _sa_sim.id == sim_id,
            })

    all_cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()
    latest_cycle = all_cycles[0] if all_cycles else None

    diagram_cycle = None
    if latest_cycle:
        diagram_cycle = latest_cycle.to_dict()
        actions = Layer6ActionQueue.query.filter_by(cycle_id=latest_cycle.id).order_by(
            Layer6ActionQueue.priority_score.desc()
        ).all()
        diagram_cycle['action_queue'] = [a.to_dict() for a in actions]

    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()
    ).first()

    action_pills = Layer6ActionQueue.query.filter(
        Layer6ActionQueue.simulation_id == sim_id,
        Layer6ActionQueue.status.in_(['dispatched', 'complete', 'escalated']),
    ).order_by(Layer6ActionQueue.dispatched_at.desc()).limit(20).all()

    esc_rows = _db.session.query(
        Layer6ActionQueue.source_layer,
        _func.count(Layer6ActionQueue.id).label('cnt'),
    ).filter_by(simulation_id=sim_id, status='escalated').group_by(
        Layer6ActionQueue.source_layer
    ).all()
    escalation_by_layer = {r.source_layer: r.cnt for r in esc_rows}

    done_rows = _db.session.query(
        AgentAction.layer_number,
        _func.count(AgentAction.id).label('cnt'),
    ).filter_by(simulation_id=sim_id, status=AgentAction.STATUS_COMPLETE).group_by(
        AgentAction.layer_number
    ).all()
    complete_by_layer = {r.layer_number: r.cnt for r in done_rows}

    # Advisor notes for this access record
    advisor_notes = AdvisorNote.query.filter_by(
        advisor_access_id=access.id, simulation_id=sim_id,
    ).order_by(AdvisorNote.created_at.desc()).all()
    advisor_notes_by_layer = {}
    for note in advisor_notes:
        key = note.layer_number or 0
        advisor_notes_by_layer.setdefault(key, []).append(note.to_dict())

    layer6_cfg = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    advisor_dashboard = None
    advisor_journey = None
    try:
        from app.services.layer6 import build_dashboard, build_journey_data
        advisor_dashboard = build_dashboard(sim_id)
        advisor_journey = build_journey_data(sim_id)
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).error('advisor GCC data build failed: %s', _e)

    # Variables shared with gcc_view template
    from app.models.simulation import Simulation as _Sim, SimulationLayer as _SL, IncomeStream as _IS
    from app.models.layer6 import Layer6Outcome as _Out
    from app.models.agent_action import AgentAction as _AA
    from datetime import date as _date

    sim_layers = _SL.query.filter_by(simulation_id=sim_id).order_by(_SL.layer_number).all()
    layer_ids = [l.id for l in sim_layers]

    outcome_rows = _db.session.query(
        _Out.layer_number, _func.sum(_Out.actual_income).label('total'),
    ).filter_by(simulation_id=sim_id).group_by(_Out.layer_number).all()
    income_by_layer = {r.layer_number: float(r.total or 0) for r in outcome_rows}
    total_income = sum(income_by_layer.values())

    this_month = _date.today().strftime('%Y-%m')
    income_this_month = float(
        _db.session.query(_func.sum(_Out.actual_income))
        .filter_by(simulation_id=sim_id, reporting_month=this_month).scalar() or 0
    )

    recent_income = _Out.query.filter_by(simulation_id=sim_id).order_by(
        _Out.created_at.desc()
    ).limit(20).all()

    layer_actions = _AA.query.filter_by(simulation_id=sim_id).order_by(
        _AA.created_at.desc()
    ).all()

    client_zone_count = _Sim.query.filter_by(user_id=client_uid).count()
    client_projected_annual = 0
    if layer_ids:
        client_projected_annual = sum(
            (s.est_monthly_high or 0) * 12
            for s in _IS.query.filter(_IS.layer_id.in_(layer_ids)).all()
        )

    from flask import request as _req
    active_tab = _req.args.get('tab', 'queue')

    return render_template(
        'simulations/layer6.html',
        sim=sim,
        diagram_cycle=diagram_cycle,
        all_cycles=[c.to_dict_summary() for c in all_cycles],
        momentum=momentum,
        action_pills=[p.to_pill_dict() for p in action_pills],
        escalation_by_layer=escalation_by_layer,
        complete_by_layer=complete_by_layer,
        active_items=[],
        active_item_count=0,
        income_by_layer=income_by_layer,
        total_income=total_income,
        income_this_month=income_this_month,
        urgent_layers=[],
        recent_income=[r.to_dict() for r in recent_income],
        sim_layers=sim_layers,
        layer6_config=layer6_cfg.to_dict() if layer6_cfg else None,
        active_tab=active_tab,
        layer_actions=[a.to_dict() for a in layer_actions],
        active_flags=[],
        active_suggestions=[],
        advisor_mode=True,
        advisor_access=access,
        advisor_notes_by_layer=advisor_notes_by_layer,
        client_profile=client_profile,
        latest_cycle=latest_cycle,
        advisor_shared_sims=advisor_shared_sims,
        advisor_layer6_config=layer6_cfg.to_dict() if layer6_cfg else None,
        advisor_dashboard=advisor_dashboard,
        advisor_journey=advisor_journey,
        profile=client_profile,
        zone_count=client_zone_count,
        projected_annual=client_projected_annual,
        actual_income=total_income,
    )


@pages_bp.route('/simulations/<sim_id>/gcc')
@login_required
def gcc_view(sim_id):
    """Growth Command Center v2.0 — 4-tab action-first layout."""
    from flask import request as _req, redirect, url_for
    from app.models.simulation import Simulation, SimulationLayer
    from app.models.layer6 import (
        ActionItem, Layer6Cycle, Layer6ActionQueue, Layer6Config,
        Layer6Momentum, Layer6Outcome,
    )
    from app.models.agent_action import AgentAction
    from app.extensions import db as _db
    from sqlalchemy import func as _func

    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        from flask import abort
        abort(403)

    # Active action items for Action Queue
    active_items = ActionItem.query.filter_by(
        simulation_id=sim_id,
        user_id=current_user.id,
        status=ActionItem.STATUS_ACTIVE,
    ).order_by(ActionItem.urgency_tier.asc(), ActionItem.created_at.desc()).all()

    # All orchestrator cycles (Cycle + Visuals tabs) — latest first
    all_cycle_objs = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()
    latest_cycle = all_cycle_objs[0] if all_cycle_objs else None

    # Build diagram_cycle payload (latest cycle + its action queue)
    diagram_cycle = None
    if latest_cycle:
        diagram_cycle = latest_cycle.to_dict()
        dq_actions = Layer6ActionQueue.query.filter_by(cycle_id=latest_cycle.id).order_by(
            Layer6ActionQueue.priority_score.desc()
        ).all()
        diagram_cycle['action_queue'] = [a.to_dict() for a in dq_actions]

    # Latest momentum snapshot
    momentum = Layer6Momentum.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Momentum.snapshot_date.desc()
    ).first()

    # Per-layer income totals (My Layers + Income tab)
    outcome_rows = _db.session.query(
        Layer6Outcome.layer_number,
        _func.sum(Layer6Outcome.actual_income).label('total'),
    ).filter_by(simulation_id=sim_id).group_by(Layer6Outcome.layer_number).all()
    income_by_layer = {r.layer_number: float(r.total or 0) for r in outcome_rows}
    total_income = sum(income_by_layer.values())

    # This month's income
    from datetime import date
    this_month = date.today().strftime('%Y-%m')
    month_rows = _db.session.query(
        _func.sum(Layer6Outcome.actual_income).label('total'),
    ).filter_by(simulation_id=sim_id, reporting_month=this_month).scalar()
    income_this_month = float(month_rows or 0)

    # Completed action count per layer (My Layers status dot)
    done_rows = _db.session.query(
        AgentAction.layer_number,
        _func.count(AgentAction.id).label('cnt'),
    ).filter_by(simulation_id=sim_id, status=AgentAction.STATUS_COMPLETE).group_by(
        AgentAction.layer_number
    ).all()
    complete_by_layer = {r.layer_number: r.cnt for r in done_rows}

    # Layers with active tier-1 or tier-2 items (amber dot in My Layers)
    urgent_layers = set(
        i.layer_number for i in active_items
        if i.urgency_tier in (1, 2) and i.layer_number
    )

    # Recent income events (Income tab — last 20)
    recent_income = Layer6Outcome.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Outcome.created_at.desc()
    ).limit(20).all()

    # All agent actions across layers (Layer Actions tab)
    layer_actions = AgentAction.query.filter_by(
        simulation_id=sim_id,
    ).order_by(AgentAction.created_at.desc()).all()

    # SimulationLayer names for My Layers tab
    sim_layers = SimulationLayer.query.filter_by(simulation_id=sim_id).order_by(
        SimulationLayer.layer_number
    ).all()

    # Layer 6 config (for settings modal)
    layer6_config = Layer6Config.query.filter_by(simulation_id=sim_id).first()

    # Profile for header
    from app.models.profile import UserProfile as _UP
    profile = _UP.query.filter_by(user_id=current_user.id).first()

    tab = _req.args.get('tab', 'queue')

    return render_template(
        'simulations/layer6.html',
        sim=sim,
        active_items=[i.to_dict() for i in active_items],
        active_item_count=len(active_items),
        latest_cycle=latest_cycle,
        momentum=momentum,
        income_by_layer=income_by_layer,
        total_income=total_income,
        income_this_month=income_this_month,
        complete_by_layer=complete_by_layer,
        urgent_layers=list(urgent_layers),
        recent_income=[r.to_dict() for r in recent_income],
        sim_layers=sim_layers,
        profile=profile,
        active_tab=tab,
        diagram_cycle=diagram_cycle,
        all_cycles=[c.to_dict_summary() for c in all_cycle_objs],
        layer_actions=[a.to_dict() for a in layer_actions],
        layer6_config=layer6_config.to_dict() if layer6_config else None,
        action_pills=[],
        escalation_by_layer={},
        active_flags=[],
        active_suggestions=[],
        zone_count=0,
        projected_annual=0,
        actual_income=total_income,
    )


@pages_bp.route('/simulations/<sim_id>/layers/<int:layer_number>')
@login_required
def layer_detail_view(sim_id, layer_number):
    """Layer detail page — all Glance cards for one income layer."""
    from app.models.simulation import Simulation, SimulationLayer
    from app.models.agent_action import AgentAction

    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        from flask import abort
        abort(403)

    if layer_number not in range(1, 6):
        from flask import abort
        abort(404)

    sim_layer = SimulationLayer.query.filter_by(
        simulation_id=sim_id, layer_number=layer_number
    ).first()

    artifacts = AgentAction.query.filter_by(
        simulation_id=sim_id,
        layer_number=layer_number,
        status=AgentAction.STATUS_COMPLETE,
    ).order_by(AgentAction.completed_at.desc()).all()

    layer_names = {
        1: 'Active income', 2: 'Leveraged income', 3: 'Productized income',
        4: 'Automated income', 5: 'Wealth deployment',
    }

    return render_template(
        'simulations/layer_detail.html',
        sim=sim,
        sim_layer=sim_layer,
        layer_number=layer_number,
        layer_name=layer_names.get(layer_number, f'Layer {layer_number}'),
        artifacts=artifacts,
    )


@pages_bp.route('/simulations/<sim_id>/layer6')
@login_required
def layer6_view(sim_id):
    """Redirect legacy /layer6 URL to the new GCC v2.0 route."""
    from flask import redirect, url_for
    return redirect(url_for('pages.gcc_view', sim_id=sim_id), 301)


@pages_bp.route('/share/layer6/<token>')
def layer6_share_view(token):
    """Read-only orchestrator diagram share view."""
    from datetime import datetime as _dt
    from flask import abort
    from app.models.layer6 import Layer6ShareToken, Layer6Cycle, Layer6ActionQueue
    from app.models.simulation import Simulation

    share = Layer6ShareToken.query.filter_by(token=token).first_or_404()
    if share.expires_at < _dt.utcnow():
        abort(410)

    sim = Simulation.query.get_or_404(share.simulation_id)

    all_cycles = Layer6Cycle.query.filter_by(simulation_id=sim.id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()

    pinned = None
    if share.cycle_id:
        pinned = Layer6Cycle.query.get(share.cycle_id)
    display_cycle = pinned or (all_cycles[0] if all_cycles else None)

    diagram_cycle = None
    if display_cycle:
        diagram_cycle = display_cycle.to_dict()
        actions = Layer6ActionQueue.query.filter_by(cycle_id=display_cycle.id).order_by(
            Layer6ActionQueue.priority_score.desc()
        ).all()
        diagram_cycle['action_queue'] = [a.to_dict() for a in actions]

    return render_template(
        'simulations/layer6_share.html',
        sim=sim,
        diagram_cycle=diagram_cycle,
        all_cycles=[c.to_dict_summary() for c in all_cycles],
        share=share,
        read_only=True,
    )


@pages_bp.route('/legal/terms')
def legal_terms():
    """Terms of Service — stable URL (FR-TOS-10)."""
    from app.models.platform_settings import PlatformSetting
    tos_version = PlatformSetting.get('tos_version', '1.0')
    return render_template('legal/terms.html', tos_version=tos_version)


@pages_bp.route('/legal/privacy')
def legal_privacy():
    """Privacy Policy — stable URL (FR-TOS-10)."""
    from app.models.platform_settings import PlatformSetting
    pp_version = PlatformSetting.get('privacy_policy_version', '1.0')
    return render_template('legal/privacy.html', pp_version=pp_version)


@pages_bp.route('/contacts')
@login_required
def contacts_list():
    from app.models.contact import Contact
    from app.extensions import db as _db
    from sqlalchemy import func as _func
    stage_counts = dict(
        _db.session.query(Contact.pipeline_stage, _func.count(Contact.id))
        .filter_by(user_id=current_user.id, is_archived=False)
        .group_by(Contact.pipeline_stage).all()
    )
    total = sum(stage_counts.values())
    return render_template('contacts/list.html', stage_counts=stage_counts, total=total)


@pages_bp.route('/contacts/<contact_id>')
@login_required
def contact_detail(contact_id):
    from app.models.contact import Contact, ContactActivity
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    activities = ContactActivity.query.filter_by(contact_id=contact_id).order_by(
        ContactActivity.activity_date.desc()
    ).limit(100).all()
    return render_template('contacts/detail.html', contact=contact, activities=activities)


@pages_bp.route('/simulations/<sim_id>/contacts')
@login_required
def sim_contacts_view(sim_id):
    """Contact list with simulation context — shows contacts associated via agent actions."""
    from app.models.simulation import Simulation
    from app.models.contact import Contact, ContactActivity
    from app.extensions import db as _db
    from sqlalchemy import func as _func

    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()

    stage_counts = dict(
        _db.session.query(Contact.pipeline_stage, _func.count(Contact.id))
        .filter_by(user_id=current_user.id, is_archived=False)
        .group_by(Contact.pipeline_stage).all()
    )
    total = sum(stage_counts.values())
    return render_template('contacts/list.html',
                           stage_counts=stage_counts,
                           total=total,
                           sim=sim)


@pages_bp.route('/ref/<code>')
def referral_redirect(code):
    """Capture referral click, store in session, redirect to register."""
    from datetime import datetime
    from flask import session
    from app.models.partner import ReferralPartner, ReferralSignup
    from app.extensions import db

    partner = ReferralPartner.query.filter_by(
        referral_code=code, status=ReferralPartner.STATUS_ACTIVE,
    ).first()
    if partner:
        session['referral_code'] = code
        session['referral_clicked_at'] = datetime.utcnow().isoformat()
        # Log the click immediately (referred_user_id unknown until registration)
        # We store only the click time; ReferralSignup row created on register
    return redirect(url_for('pages.register_page') + f'?ref={code}')
