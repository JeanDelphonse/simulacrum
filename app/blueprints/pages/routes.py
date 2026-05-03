from flask import render_template, redirect, url_for
from flask_login import current_user, login_required
from app.blueprints.pages import pages_bp


@pages_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('pages.dashboard'))
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
    trust_stats = {
        'avg_rating': round(float(avg_row or 0), 1),
        'total_simulations': _Sim.query.count(),
        'approved_count': len(testimonials),
    }
    return render_template('landing.html',
                           testimonials=[t.to_public_dict() for t in testimonials],
                           trust_stats=trust_stats)


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


@pages_bp.route('/simulations/<sim_id>/confirmed')
@login_required
def simulation_confirmed(sim_id):
    from app.models.simulation import Simulation
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    return render_template('simulations/confirmed.html', simulation=sim)


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


@pages_bp.route('/resumes')
@login_required
def resumes_view():
    from app.models.resume import Resume
    resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    return render_template('resumes/list.html', resumes=resumes)


@pages_bp.route('/resumes/<resume_id>')
@login_required
def resume_detail(resume_id):
    from flask import current_app
    from app.models.resume import Resume
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id).first_or_404()
    stripe_pk = current_app.config.get('STRIPE_PUBLISHABLE_KEY', '')
    return render_template('resumes/detail.html', resume=resume, stripe_pk=stripe_pk)


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
    from app.models.platform_settings import PlatformSetting
    from app.models.user import User
    from app.models.profile import UserProfile
    settings = {s.key: s.value for s in PlatformSetting.query.all()}
    users = User.query.order_by(User.created_at.desc()).limit(50).all()
    user_ids = [u.id for u in users]
    profiles = {p.user_id: p for p in UserProfile.query.filter(UserProfile.user_id.in_(user_ids)).all()}
    return render_template('admin/index.html', settings=settings, users=users, profiles=profiles)


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

    return render_template(
        'simulations/layer6.html',
        sim=sim,
        diagram_cycle=diagram_cycle,
        all_cycles=[c.to_dict_summary() for c in all_cycles],
        momentum=momentum,
        action_pills=[p.to_pill_dict() for p in action_pills],
        escalation_by_layer=escalation_by_layer,
        complete_by_layer=complete_by_layer,
        advisor_mode=True,
        advisor_access=access,
        advisor_notes_by_layer=advisor_notes_by_layer,
        client_profile=client_profile,
        latest_cycle=latest_cycle,
        advisor_shared_sims=advisor_shared_sims,
        advisor_layer6_config=layer6_cfg.to_dict() if layer6_cfg else None,
        advisor_dashboard=advisor_dashboard,
        advisor_journey=advisor_journey,
    )


@pages_bp.route('/simulations/<sim_id>/layer6')
@login_required
def layer6_view(sim_id):
    """Growth Command Center — Layer 6 orchestrator UI."""
    from app.models.simulation import Simulation
    from app.models.layer6 import Layer6Cycle, Layer6ActionQueue, Layer6Momentum
    from app.models.agent_action import AgentAction
    from app.extensions import db as _db
    from sqlalchemy import func as _func
    sim = Simulation.query.get_or_404(sim_id)
    if sim.user_id != current_user.id:
        from flask import abort
        abort(403)

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

    # Active partner flags and suggestions for this client's simulation
    from app.models.partner import AdvisorAccess, AdvisorNote, AdvisorFlag
    active_accesses = AdvisorAccess.query.filter_by(
        simulation_id=sim_id, revoked_at=None,
    ).filter(AdvisorAccess.granted_by == current_user.id).all()
    access_ids = [a.id for a in active_accesses]

    # Flags not dismissed
    active_flags = []
    if access_ids:
        from app.models.partner import AdvisorFlag as _Flag
        flags = _Flag.query.filter(
            _Flag.advisor_access_id.in_(access_ids),
            _Flag.dismissed_at.is_(None),
        ).all()
        for f in flags:
            access = next((a for a in active_accesses if a.id == f.advisor_access_id), None)
            partner_name = None
            if access and access.partner_id:
                from app.models.partner import ReferralPartner as _RP
                p = _RP.query.get(access.partner_id)
                partner_name = p.full_name if p else None
            active_flags.append({**f.to_dict(), 'partner_name': partner_name})

    # Active shared suggestions
    active_suggestions = []
    if access_ids:
        suggestions = AdvisorNote.query.filter(
            AdvisorNote.advisor_access_id.in_(access_ids),
            AdvisorNote.is_shared == True,
            AdvisorNote.suggestion_type == 'next_step',
        ).all()
        for s in suggestions:
            access = next((a for a in active_accesses if a.id == s.advisor_access_id), None)
            partner_name = None
            if access and access.partner_id:
                from app.models.partner import ReferralPartner as _RP
                p = _RP.query.get(access.partner_id)
                partner_name = p.full_name if p else None
            active_suggestions.append({**s.to_dict(), 'partner_name': partner_name})

    return render_template(
        'simulations/layer6.html',
        sim=sim,
        diagram_cycle=diagram_cycle,
        all_cycles=[c.to_dict_summary() for c in all_cycles],
        momentum=momentum,
        action_pills=[p.to_pill_dict() for p in action_pills],
        escalation_by_layer=escalation_by_layer,
        complete_by_layer=complete_by_layer,
        active_flags=active_flags,
        active_suggestions=active_suggestions,
    )


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
