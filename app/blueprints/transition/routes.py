"""Transition Package routes (PRD-SIM-TRANS-001, phase 1).

Four audiences, four route groups:
  staff     /admin/transition, /api/admin/transition/*   (site admins)
  employer  /transition/employer/<org_id>                 (org admin or site admin; aggregate only)
  employee  /transition/join/<token>, /transition, /api/transition/*
  coach     /coach, /api/coach/*                          (SMEs marked is_coach)
"""
from __future__ import annotations

from functools import wraps

from flask import Response, abort, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.blueprints.transition import transition_bp
from app.extensions import db
from app.services import transition_service as ts


def _err(exc, code=400):
    return jsonify({'error': 'refused', 'message': str(exc)}), code


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Staff ─────────────────────────────────────────────────────────────────────

def _admin_state() -> dict:
    from app.models.corporate import CorporateAccount
    from app.models.sme import SimiSME
    from app.models.transition import TransitionCohort, TransitionSeat
    from app.models.user import User
    employers = []
    for org in CorporateAccount.query.filter_by(org_type=CorporateAccount.ORG_TRANSITION).order_by(
            CorporateAccount.created_at.desc()).all():
        admin = User.query.get(org.admin_user_id) if org.admin_user_id else None
        cohorts = []
        for c in TransitionCohort.query.filter_by(org_id=org.id).order_by(TransitionCohort.created_at.desc()).all():
            seats = TransitionSeat.query.filter_by(cohort_id=c.id).all()
            cohorts.append(dict(c.to_dict(), seats={st: sum(1 for s in seats if s.status == st)
                                                    for st in TransitionSeat.STATUSES},
                                seat_list=[{'id': s.id, 'name': s.full_name, 'tier': s.tier, 'status': s.status,
                                            'email': _mask(ts._decrypt(s.invite_email_enc)),
                                            'invite_sent': bool(s.invite_sent_at), 'coach_id': s.coach_id}
                                           for s in seats]))
        employers.append({'id': org.id, 'name': org.org_name, 'contact': org.contact_email,
                          'hr_login': admin.email if admin else None, 'cohorts': cohorts})
    return {'employers': employers, 'tiers': ts.tiers(),
            'coaches': [c.to_dict() for c in SimiSME.query.order_by(SimiSME.last_name).all()],
            'settings': {k: ts.setting(k) for k in ts.DEFAULT_SETTINGS}}


def _mask(email):
    if not email or '@' not in email:
        return None
    local, dom = email.split('@', 1)
    return (local[:2] + '•••@' + dom) if len(local) > 2 else ('•••@' + dom)


@transition_bp.route('/admin/transition')
@login_required
def admin_page():
    if not current_user.is_admin:
        abort(403)
    return render_template('transition/admin.html', state=_admin_state())


@transition_bp.route('/api/admin/transition/state')
@admin_required
def api_admin_state():
    return jsonify(_admin_state())


@transition_bp.route('/api/admin/transition/employers', methods=['POST'])
@admin_required
def api_create_employer():
    from app.models.user import User
    b = request.get_json(silent=True) or {}
    try:
        org = ts.create_employer(b.get('name'), b.get('contact_name'), b.get('contact_email'), current_user.id)
    except ts.TransitionError as exc:
        return _err(exc)
    hr = User.query.filter(db.func.lower(User.email) == (b.get('hr_login') or b.get('contact_email') or '').lower()).first()
    if hr is not None:
        org.admin_user_id = hr.id             # this login can open the employer report
        db.session.commit()
    return jsonify(_admin_state())


@transition_bp.route('/api/admin/transition/employers/<org_id>/cohorts', methods=['POST'])
@admin_required
def api_create_cohort(org_id):
    b = request.get_json(silent=True) or {}
    try:
        ts.create_cohort(ts.transition_org(org_id), b.get('name'), b.get('last_working_day'), b.get('invite_date'),
                         b.get('employer_domains'), b.get('min_report_size'), current_user.id)
    except ts.TransitionError as exc:
        return _err(exc)
    return jsonify(_admin_state())


@transition_bp.route('/api/admin/transition/cohorts/<cohort_id>/seats', methods=['POST'])
@admin_required
def api_add_seats(cohort_id):
    from app.models.transition import TransitionCohort
    c = TransitionCohort.query.get_or_404(cohort_id)
    b = request.get_json(silent=True) or {}
    try:
        res = ts.add_seats(c, ts.parse_roster(b.get('roster_csv') or ''), b.get('unassigned') or {},
                           b.get('default_tier') or 'essentials', current_user.id)
    except ts.TransitionError as exc:
        return _err(exc)
    return jsonify(dict(_admin_state(), result=res))


@transition_bp.route('/api/admin/transition/quote', methods=['POST'])
@admin_required
def api_quote():
    return jsonify(ts.quote((request.get_json(silent=True) or {}).get('counts') or {}))


@transition_bp.route('/api/admin/transition/seats/<seat_id>/<action>', methods=['POST'])
@admin_required
def api_seat_action(seat_id, action):
    b = request.get_json(silent=True) or {}
    try:
        if action == 'assign':
            ts.assign_seat(seat_id, b.get('name'), b.get('email'), current_user.id)
        elif action == 'revoke':
            ts.revoke_seat(seat_id, current_user.id)
        elif action == 'invite':
            s = ts._seat(seat_id)
            if s.status != s.INVITED:
                raise ts.TransitionError('Assign the seat to someone first.')
            ts.send_invite(s)
            db.session.commit()
        elif action == 'coach':
            from app.models.sme import SimiSME
            s = ts._seat(seat_id)
            coach = SimiSME.query.get(b.get('coach_id'))
            if coach is None or not coach.is_coach:
                raise ts.TransitionError('Pick a coach.')
            if s.coach_id != coach.id:
                s.coach_id, s.coach_access = coach.id, False
            db.session.commit()
        else:
            abort(404)
    except ts.TransitionError as exc:
        return _err(exc)
    return jsonify(_admin_state())


@transition_bp.route('/api/admin/transition/coaches/<sme_id>', methods=['POST'])
@admin_required
def api_coach(sme_id):
    from app.models.sme import SimiSME
    c = SimiSME.query.get_or_404(sme_id)
    b = request.get_json(silent=True) or {}
    c.is_coach = bool(b.get('is_coach', c.is_coach))
    for k in ('coach_credentials', 'coach_booking_url'):
        if k in b:
            setattr(c, k, (b.get(k) or '').strip()[:500] or None)
    if 'coach_specialties' in b:
        v = b['coach_specialties']
        c.coach_specialties = v if isinstance(v, list) else [x for x in str(v).split(',')]
    if b.get('coach_seniority') in ('standard', 'senior'):
        c.coach_seniority = b['coach_seniority']
    if b.get('coach_status') in ('active', 'review'):
        c.coach_status = b['coach_status']
    db.session.commit()
    return jsonify(_admin_state())


@transition_bp.route('/api/admin/transition/maintenance', methods=['POST'])
@admin_required
def api_maintenance():
    return jsonify(ts.run_maintenance(force=True))


# ── Employer (aggregate only) ────────────────────────────────────────────────

def _employer_org(org_id):
    try:
        org = ts.transition_org(org_id)
    except ts.TransitionError:
        abort(404)
    if not ts.can_view_employer(current_user, org):
        abort(403)
    return org


@transition_bp.route('/transition/employer/<org_id>')
@login_required
def employer_report(org_id):
    from app.models.transition import TransitionCohort
    org = _employer_org(org_id)
    cohort_id = request.args.get('cohort') or None
    try:
        rep = ts.report(org, cohort_id)
    except ts.TransitionError:
        abort(404)
    return render_template('transition/report.html', org=org, report=rep, cohort_id=cohort_id,
                           cohorts=TransitionCohort.query.filter_by(org_id=org.id).all(),
                           seats=ts.hr_seat_list(org, cohort_id), tiers=ts.tiers())


@transition_bp.route('/transition/employer/<org_id>/seats.csv')
@login_required
def employer_seats_csv(org_id):
    org = _employer_org(org_id)
    return Response(ts.seats_csv(org, request.args.get('cohort') or None), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="seats-%s.csv"' % org.id})


# ── Employee ──────────────────────────────────────────────────────────────────

@transition_bp.route('/transition/join/<token>')
def join_page(token):
    from app.models.corporate import CorporateAccount
    try:
        seat = ts.seat_for_token(token)
    except ts.TransitionError as exc:
        return render_template('transition/join.html', error=str(exc)), 410
    session['transition_invite'] = token          # survives sign-up and onboarding
    org = CorporateAccount.query.get(seat.org_id)
    return render_template('transition/join.html', error=None, token=token, seat=seat,
                           employer=org.org_name if org else 'Your employer', tier=ts.tiers()[seat.tier])


@transition_bp.route('/api/transition/join/<token>', methods=['POST'])
@login_required
def api_join(token):
    b = request.get_json(silent=True) or {}
    try:
        ts.activate(token, current_user, bool(b.get('share_outcomes')), b.get('consent') is True)
    except ts.TransitionError as exc:
        return _err(exc)
    session.pop('transition_invite', None)
    return jsonify({'ok': True, 'next': url_for('transition.employee_page')})


@transition_bp.route('/transition')
@login_required
def employee_page():
    view = ts.employee_view(current_user.id)
    if view is None:
        return redirect(url_for('pages.dashboard'))
    from app.models.profile import UserProfile
    from app.models.transition import TransitionOutcome
    prof = UserProfile.query.filter_by(user_id=current_user.id).first()
    return render_template('transition/employee.html', v=view, outcomes=ts.OUTCOME_LABELS,
                           outcome_keys=TransitionOutcome.OUTCOMES,
                           bio_edit_url=url_for('pages.bio_editor', slug=prof.username) if prof else None)


@transition_bp.route('/api/transition/<action>', methods=['POST'])
@login_required
def api_employee_action(action):
    b = request.get_json(silent=True) or {}
    try:
        if action == 'outcome':
            ts.report_outcome(current_user.id, b.get('outcome'), bool(b.get('share')))
        elif action == 'sharing':
            ts.set_sharing(current_user.id, bool(b.get('share')))
        elif action == 'coach-access':
            ts.set_coach_access(current_user.id, bool(b.get('granted')))
        elif action == 'change-coach':
            ts.change_coach(current_user.id)
        else:
            abort(404)
    except ts.TransitionError as exc:
        return _err(exc)
    return jsonify(ts.employee_view(current_user.id))


@transition_bp.route('/api/transition/sessions/<session_id>/rate', methods=['POST'])
@login_required
def api_rate(session_id):
    try:
        ts.rate_session(current_user.id, session_id, (request.get_json(silent=True) or {}).get('rating'))
    except (ts.TransitionError, TypeError, ValueError) as exc:
        return _err(exc)
    return jsonify(ts.employee_view(current_user.id))


@transition_bp.route('/api/transition/sessions/<session_id>/notes', methods=['GET', 'POST'])
@login_required
def api_employee_notes(session_id):
    try:
        if request.method == 'POST':
            ts.add_note('employee', session_id, (request.get_json(silent=True) or {}).get('body'), user=current_user)
        return jsonify({'notes': ts.notes_for(session_id, user=current_user)})
    except ts.TransitionError as exc:
        return _err(exc, 404)


# ── Coach ─────────────────────────────────────────────────────────────────────

def _coach():
    c = ts.coach_for_login(current_user)
    if c is None:
        abort(403)
    return c


@transition_bp.route('/coach')
@login_required
def coach_page():
    c = _coach()
    return render_template('transition/coach.html', coach=c, caseload=ts.coach_caseload(c))


@transition_bp.route('/api/coach/caseload')
@login_required
def api_coach_caseload():
    return jsonify({'caseload': ts.coach_caseload(_coach())})


@transition_bp.route('/api/coach/seats/<seat_id>/sessions', methods=['POST'])
@login_required
def api_coach_session(seat_id):
    c = _coach()
    b = request.get_json(silent=True) or {}
    try:
        sess = ts.log_session(c, seat_id, b.get('status'), b.get('scheduled_at'))
    except (ts.TransitionError, ValueError) as exc:
        return _err(exc)
    return jsonify({'session': sess.to_dict(), 'caseload': ts.coach_caseload(c)})


@transition_bp.route('/api/coach/sessions/<session_id>/notes', methods=['GET', 'POST'])
@login_required
def api_coach_notes(session_id):
    c = _coach()
    try:
        if request.method == 'POST':
            ts.add_note('coach', session_id, (request.get_json(silent=True) or {}).get('body'), coach=c)
        return jsonify({'notes': ts.notes_for(session_id, coach=c)})
    except ts.TransitionError as exc:
        return _err(exc, 404)
