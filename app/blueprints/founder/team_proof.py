"""Founder Track team alignment and proof-of-concept path (FOUNDER_PLAN.md WP5).

Pages:  GET  /founder/v/<id>/alignment                         co-founders and the alignment check
        GET  /founder/v/<id>/roles                             who leads each layer, who decides, step owners, check-ins
        GET  /founder/v/<id>/proof[?plan=<pid>]                proof-of-concept plan, results, benefits case
        GET  /founder/join/<token>                             co-founder invite (public prefix)
        POST /founder/join/<token>                             accept it with the signed-in account
APIs:   GET  /api/founder/v/<id>/alignment                     this member's view
        POST /api/founder/v/<id>/alignment/answers             {answers: {key: {choice, note}}}
        POST /api/founder/v/<id>/alignment/invite              {email, title}            (owner)
        POST /api/founder/v/<id>/alignment/invites/<mid>/revoke                          (owner)
        POST /api/founder/v/<id>/alignment/rights              {decision_type | 'layer_lead', member_id, layer?}
        POST /api/founder/v/<id>/alignment/steps/<sid>/owner   {member_id}
        POST /api/founder/v/<id>/alignment/cadence             {days}
        POST /api/founder/v/<id>/alignment/checkin
        GET  /api/founder/v/<id>/proof                         proof view
        POST /api/founder/v/<id>/proof                         save a draft plan {plan_id?, ...}
        POST /api/founder/v/<id>/proof/<pid>/agree
        POST /api/founder/v/<id>/proof/<pid>/results           {results: [...], quote_text, quote_permission}
        POST /api/founder/v/<id>/proof/<pid>/benefits
        POST /api/founder/v/<id>/proof/<pid>/payment           {kind: paid|commitment, note}
        POST /api/founder/v/<id>/proof/sync                    climb the ladder on evidence already recorded

Venture routes 404 while the track is off for the user, 404 for an unknown
venture and 403 for a non-member. Team, plan and proof are part of the paid
Founder Track (FOUNDER_PLAN section 0): on an unpaid venture the pages redirect
to checkout (the owner) or the report (co-founders) and the APIs answer 402.
The join page 404s while the track is off. Page GETs never write.
"""
from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required


def _venture(venture_id: str):
    from app.services import founder_ventures
    return founder_ventures.require_member(venture_id, current_user)


def _payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    if data is not None:                       # a JSON list, string or number: not a request we accept
        return {}
    return request.form.to_dict() or {}


def _err(message: str, status: int = 400, **extra):
    body = {'ok': False, 'error': message}
    body.update(extra)
    return jsonify(body), status


def _checkout_url(v) -> str:
    return url_for('founder.checkout_page', venture_id=v.id)


def _locked_page(v):
    """None for a paid venture, else the redirect (same rule as WP4's pages)."""
    from app.services import founder_ventures
    if founder_ventures.is_paid(v):
        return None
    if v.owner_user_id == current_user.id:
        return redirect(_checkout_url(v))
    return redirect(url_for('founder.report_page', venture_id=v.id))


def _locked_api(v):
    from app.services import founder_ventures
    if founder_ventures.is_paid(v):
        return None
    return _err('Team alignment and the proof of concept unlock with the Founder Track.', 402,
                checkout_url=_checkout_url(v))


def _api_venture(venture_id: str):
    """(venture, error response or None) for a paid-feature API."""
    v = _venture(venture_id)
    return v, _locked_api(v)


def _nav(v, active: str) -> list:
    """Venture sections (common.venture_nav), so a founder arriving from an
    email can get back to the workspace and plan."""
    from app.blueprints.founder.common import venture_nav
    return venture_nav(v)


def _call(fn, *args, **kwargs):
    """Run a service call, mapping its errors to JSON responses."""
    from app.extensions import db
    from app.services.founder_alignment_service import AlignmentError
    from app.services.founder_proof_service import ProofError
    try:
        return fn(*args, **kwargs), None
    except PermissionError as exc:
        db.session.rollback()
        return None, _err(str(exc), 403)
    except (AlignmentError, ProofError) as exc:
        db.session.rollback()
        return None, _err(str(exc), getattr(exc, 'status', 400))


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def _alignment_context(v):
    from app.services import founder_alignment_service as fas
    return {'view': fas.alignment_view(v, current_user), 'rights': fas.decision_rights(v),
            'leads': fas.layer_leads(v), 'checkin': fas.checkin_summary(v),
            'cadence_choices': fas.CADENCE_CHOICES, 'cadence_max': fas.CADENCE_MAX_DAYS,
            'invite_days': fas.invite_days()}


@founder_bp.route('/founder/v/<venture_id>/alignment')
@founder_required
def tp_alignment_page(venture_id):
    v = _venture(venture_id)
    locked = _locked_page(v)
    if locked:
        return locked
    return render_template('founder/alignment.html', venture=v, section='alignment', nav=_nav(v, 'alignment'),
                           active='alignment', **_alignment_context(v))


@founder_bp.route('/founder/v/<venture_id>/roles')
@founder_required
def tp_roles_page(venture_id):
    v = _venture(venture_id)
    locked = _locked_page(v)
    if locked:
        return locked
    return render_template('founder/alignment.html', venture=v, section='roles', nav=_nav(v, 'roles'),
                           active='roles', **_alignment_context(v))


@founder_bp.route('/api/founder/v/<venture_id>/alignment')
@founder_required
def tp_alignment_api(venture_id):
    v, err = _api_venture(venture_id)
    if err:
        return err
    ctx = _alignment_context(v)
    ctx['cadence_choices'] = list(ctx['cadence_choices'])
    return jsonify(dict(ctx, ok=True))


@founder_bp.route('/api/founder/v/<venture_id>/alignment/answers', methods=['POST'])
@founder_required
def tp_alignment_answers(venture_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    out, err = _call(fas.submit_answers, v, current_user, _payload().get('answers'))
    if err:
        return err
    return jsonify(dict(out, ok=True, view=fas.alignment_view(v, current_user)))


@founder_bp.route('/api/founder/v/<venture_id>/alignment/invite', methods=['POST'])
@founder_required
def tp_invite(venture_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    d = _payload()
    out, err = _call(fas.invite, v, current_user, d.get('email', ''), d.get('title', ''))
    if err:
        return err
    member, sent = out
    msg = ('Invite sent to %s.' % member.invite_email) if sent else \
        ("We couldn't send the email. Share the invite link with %s yourself." % member.invite_email)
    return jsonify({'ok': True, 'member_id': member.id, 'email_sent': sent, 'message': msg,
                    'join_url': fas.join_url(member.invite_token)}), 201


@founder_bp.route('/api/founder/v/<venture_id>/alignment/invites/<member_id>/revoke', methods=['POST'])
@founder_required
def tp_revoke_invite(venture_id, member_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    _out, err = _call(fas.revoke_invite, v, current_user, member_id)
    return err or jsonify({'ok': True})


@founder_bp.route('/api/founder/v/<venture_id>/alignment/rights', methods=['POST'])
@founder_required
def tp_rights(venture_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    d = _payload()
    _out, err = _call(fas.reassign_right, v, current_user, d.get('decision_type'), d.get('member_id'),
                      d.get('layer'))
    return err or jsonify({'ok': True, 'rights': fas.decision_rights(v), 'leads': fas.layer_leads(v)})


@founder_bp.route('/api/founder/v/<venture_id>/alignment/steps/<step_id>/owner', methods=['POST'])
@founder_required
def tp_step_owner(venture_id, step_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    out, err = _call(fas.assign_step, v, current_user, step_id, _payload().get('member_id') or None)
    return err or jsonify({'ok': True, 'owner_member_id': out.owner_member_id})


@founder_bp.route('/api/founder/v/<venture_id>/alignment/cadence', methods=['POST'])
@founder_required
def tp_cadence(venture_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    out, err = _call(fas.set_cadence, v, current_user, _payload().get('days'))
    return err or jsonify({'ok': True, 'cadence_days': out, 'checkin': fas.checkin_summary(v)})


@founder_bp.route('/api/founder/v/<venture_id>/alignment/checkin', methods=['POST'])
@founder_required
def tp_checkin(venture_id):
    from app.services import founder_alignment_service as fas
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    _out, err = _call(fas.record_checkin, v, current_user)
    return err or jsonify({'ok': True, 'checkin': fas.checkin_summary(v)})


# ---------------------------------------------------------------------------
# Co-founder invite (public prefix /founder/join/)
# ---------------------------------------------------------------------------

@founder_bp.route('/founder/join/<token>', methods=['GET', 'POST'])
def tp_join_page(token):
    from app.services import founder_alignment_service as fas
    from app.services import founder_feature
    if not founder_feature.public_pages_enabled():
        abort(404)
    member, venture, problem = fas.resolve_invite(token)
    inviter = None
    if venture is not None:
        from app.extensions import db
        from app.models.user import User
        owner = db.session.get(User, venture.owner_user_id)
        inviter = (owner.full_name or 'A founder') if owner else 'A founder'
    error = fas.INVITE_PROBLEMS.get(problem) if problem else None
    already = False
    mismatch = None
    if not problem and current_user.is_authenticated:
        from app.services import founder_ventures
        already = founder_ventures.get_member(venture, current_user.id) is not None
        if not already and not fas.email_matches(member, current_user):
            mismatch = fas.mismatch_message(member)
    if request.method == 'POST' and not problem:
        if not current_user.is_authenticated:
            return redirect('/login?next=' + url_for('founder.tp_join_page', token=token))
        try:
            v = fas.accept_invite(token, current_user)
        except fas.AlignmentError as exc:
            error = str(exc)
        else:
            return redirect(url_for('founder.tp_alignment_page', venture_id=v.id))
    status = 404 if problem == 'invalid' else (410 if problem in ('used', 'expired') else 200)
    return render_template('founder/join.html', token=token, venture=venture if not problem else None,
                           member=member if not problem else None, inviter=inviter if not problem else None,
                           error=error, already=already, mismatch=mismatch,
                           next_url=url_for('founder.tp_join_page', token=token)), status


# ---------------------------------------------------------------------------
# Proof of concept
# ---------------------------------------------------------------------------

@founder_bp.route('/founder/v/<venture_id>/proof')
@founder_required
def tp_proof_page(venture_id):
    from app.services import founder_proof_service as fps
    v = _venture(venture_id)
    locked = _locked_page(v)
    if locked:
        return locked
    plan_id = request.args.get('plan') or None
    return render_template('founder/proof.html', venture=v, proof=fps.view(v, plan_id), nav=_nav(v, 'proof'),
                           active='proof')


@founder_bp.route('/api/founder/v/<venture_id>/proof', methods=['GET', 'POST'])
@founder_required
def tp_proof_api(venture_id):
    from app.services import founder_proof_service as fps
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    if request.method == 'POST':
        d = _payload()
        out, err = _call(fps.save_plan, v, current_user, d, d.get('plan_id') or None)
        if err:
            return err
        return jsonify({'ok': True, 'plan_id': out.id, 'proof': fps.view(v, out.id)})
    return jsonify({'ok': True, 'proof': fps.view(v, request.args.get('plan') or None)})


@founder_bp.route('/api/founder/v/<venture_id>/proof/sync', methods=['POST'])
@founder_required
def tp_proof_sync(venture_id):
    from app.services import founder_proof_service as fps
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    reached = fps.sync_rung(v, user_id=current_user.id)
    return jsonify({'ok': True, 'reached': reached, 'proof': fps.view(v)})


@founder_bp.route('/api/founder/v/<venture_id>/proof/<plan_id>/agree', methods=['POST'])
@founder_required
def tp_proof_agree(venture_id, plan_id):
    from app.services import founder_proof_service as fps
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    _out, err = _call(fps.agree, v, current_user, plan_id)
    return err or jsonify({'ok': True, 'proof': fps.view(v, plan_id)})


@founder_bp.route('/api/founder/v/<venture_id>/proof/<plan_id>/results', methods=['POST'])
@founder_required
def tp_proof_results(venture_id, plan_id):
    from app.services import founder_proof_service as fps
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    d = _payload()
    _out, err = _call(fps.record_result, v, current_user, plan_id, d.get('results'), d.get('quote_text', ''),
                      d.get('quote_permission', False))
    return err or jsonify({'ok': True, 'proof': fps.view(v, plan_id)})


@founder_bp.route('/api/founder/v/<venture_id>/proof/<plan_id>/benefits', methods=['POST'])
@founder_required
def tp_proof_benefits(venture_id, plan_id):
    from app.services import founder_proof_service as fps
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    out, err = _call(fps.benefits_case, v, current_user, plan_id)
    return err or jsonify({'ok': True, 'benefits_text': out, 'proof': fps.view(v, plan_id)})


@founder_bp.route('/api/founder/v/<venture_id>/proof/<plan_id>/payment', methods=['POST'])
@founder_required
def tp_proof_payment(venture_id, plan_id):
    from app.services import founder_proof_service as fps
    v, locked = _api_venture(venture_id)
    if locked:
        return locked
    d = _payload()
    _out, err = _call(fps.record_payment, v, current_user, plan_id, d.get('kind', 'paid'), d.get('note', ''))
    return err or jsonify({'ok': True, 'proof': fps.view(v, plan_id)})
