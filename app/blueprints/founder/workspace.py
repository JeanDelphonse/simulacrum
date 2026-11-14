"""Founder workspace, team and plan (FOUNDER_PLAN.md WP4).

Pages:  GET  /founder/v/<id>                         workspace: one next step, layer lanes, activity
        GET  /founder/v/<id>/team                    agent team, recommended vs your settings
        GET  /founder/v/<id>/plan                    the full plan by layer
APIs:   POST /api/founder/v/<id>/team/build          recommend agents from the open findings
        POST /api/founder/v/<id>/team/override       {param_key, value, blueprint_id | layer}
        POST /api/founder/v/<id>/team/accept         {layer}
        POST /api/founder/v/<id>/team/<bp>/status    {status: accepted | declined}
        POST /api/founder/v/<id>/plan/build          create / extend the plan
        GET  /api/founder/v/<id>/next                {primary, alternates}
        GET  /api/founder/v/<id>/live                the workspace's live area as HTML (re-render)
        POST /api/founder/steps/<id>/start | complete | skip | run
        GET  /api/founder/outputs/<id>               an agent output
        POST /api/founder/actions/<id>/approve | reject
        GET  /api/founder/v/<id>/stream              SSE (founder_stream)

Every route 404s while the track is off for the user, 404s for an unknown
venture and 403s for a non-member. The team, plan and agents need a paid
venture: pages redirect to checkout (the owner) or the report (co-founders),
APIs answer 402 with the checkout URL.
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
    return request.get_json(silent=True) or request.form.to_dict() or {}


def _err(message: str, status: int = 400, **extra):
    body = {'ok': False, 'error': message}
    body.update(extra)
    return jsonify(body), status


def _checkout_url(v) -> str:
    return url_for('founder.checkout_page', venture_id=v.id)


def _locked_redirect(v):
    if v.owner_user_id == current_user.id:
        return redirect(_checkout_url(v))
    return redirect(url_for('founder.report_page', venture_id=v.id))


def _paid_api(v):
    from app.services import founder_ventures
    if founder_ventures.is_paid(v):
        return None
    return _err('The team, plan and agents unlock with the Founder Track.', 402, checkout_url=_checkout_url(v))


def _step(step_id: str):
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    s = db.session.get(VenturePlanStep, step_id)
    if s is None:
        abort(404)
    v = _venture(s.venture_id)
    return s, v


def _nav(v, active: str) -> list:
    from app.blueprints.founder.common import venture_nav
    return venture_nav(v), active


def _live_context(v) -> dict:
    from app.services import founder_orchestrator as fo
    from app.services import founder_team_service as fts
    plan = fo.plan_view(v)
    return {'venture': v, 'plan': plan, 'next': fo.next_steps(v) if plan['has_plan'] else
            {'primary': None, 'alternates': [], 'explore': False},
            'activity': fo.activity(v), 'team_count': len(fts.blueprints(v)),
            'accepted_count': sum(1 for b in fts.blueprints(v) if b.status == 'accepted'),
            'me': current_user.id}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@founder_bp.route('/founder/v/<venture_id>')
@founder_required
def workspace_page(venture_id):
    from app.services import founder_evaluation_service as fes
    from app.services import founder_ventures
    v = _venture(venture_id)
    paid = founder_ventures.is_paid(v)
    ctx = _live_context(v) if paid else {'venture': v}
    nav, active = _nav(v, 'workspace')
    return render_template('founder/workspace.html', paid=paid, nav=nav, active=active,
                           has_report=fes.latest_run(v, done_only=True) is not None,
                           is_owner=v.owner_user_id == current_user.id,
                           checkout_url=_checkout_url(v), **ctx)


@founder_bp.route('/founder/v/<venture_id>/team')
@founder_required
def team_page(venture_id):
    from app.services import founder_ventures
    from app.services import founder_team_service as fts
    v = _venture(venture_id)
    if not founder_ventures.is_paid(v):
        return _locked_redirect(v)
    if not fts.blueprints(v) and fts.view(v)['has_findings']:
        fts.build(v, current_user)
    nav, active = _nav(v, 'team')
    from app.services import founder_autonomy
    return render_template('founder/team.html', venture=v, team=fts.view(v), nav=nav, active=active,
                           level_labels=founder_autonomy.LEVEL_LABELS)


@founder_bp.route('/founder/v/<venture_id>/plan')
@founder_required
def plan_page(venture_id):
    from app.services import founder_ventures
    v = _venture(venture_id)
    if not founder_ventures.is_paid(v):
        return _locked_redirect(v)
    nav, active = _nav(v, 'plan')
    return render_template('founder/plan.html', nav=nav, active=active, **_live_context(v))


# ---------------------------------------------------------------------------
# Team APIs
# ---------------------------------------------------------------------------

@founder_bp.route('/api/founder/v/<venture_id>/team/build', methods=['POST'])
@founder_required
def team_build(venture_id):
    from app.services import founder_team_service as fts
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    rows = fts.build(v, current_user)
    return jsonify({'ok': True, 'blueprints': [b.to_dict() for b in rows]})


@founder_bp.route('/api/founder/v/<venture_id>/team/override', methods=['POST'])
@founder_required
def team_override(venture_id):
    from app.services import founder_team_service as fts
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    d = _payload()
    try:
        out = fts.override(v, current_user, d.get('param_key', ''), d.get('value'),
                           blueprint_id=d.get('blueprint_id') or None,
                           layer=d.get('layer') if d.get('layer') not in (None, '') else None)
    except fts.TeamError as exc:
        return _err(str(exc), exc.status)
    return jsonify(dict(out, ok=True))


@founder_bp.route('/api/founder/v/<venture_id>/team/accept', methods=['POST'])
@founder_required
def team_accept(venture_id):
    from app.services import founder_orchestrator as fo
    from app.services import founder_team_service as fts
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        n = fts.accept(v, current_user, _payload().get('layer'))
    except fts.TeamError as exc:
        return _err(str(exc), exc.status)
    # Keep an existing plan in step with the team.
    if fo.steps(v):
        try:
            fo.build_plan(v, current_user)
        except fo.OrchestratorError:
            pass
    return jsonify({'ok': True, 'accepted': n})


@founder_bp.route('/api/founder/v/<venture_id>/team/<blueprint_id>/status', methods=['POST'])
@founder_required
def team_status(venture_id, blueprint_id):
    from app.services import founder_orchestrator as fo
    from app.services import founder_team_service as fts
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        bp = fts.set_status(v, current_user, blueprint_id, _payload().get('status', ''))
    except fts.TeamError as exc:
        return _err(str(exc), exc.status)
    # Keep an existing plan in step with the team, as team_accept does.
    note = None
    if fo.steps(v):
        try:
            if bp.status == 'accepted':
                fo.reopen_agent(v, bp.agent_key, current_user)
                fo.build_plan(v, current_user)
            elif bp.status == 'declined':
                dropped = fo.drop_agent(v, bp.agent_key, current_user)
                if not dropped and any(s.kind == 'agent' and s.agent_key == bp.agent_key and s.state not in fo.FINISHED
                                       for s in fo.steps(v)):
                    note = ('Its plan step stays: it has started, has output or another step needs it. '
                            'Skip it on the plan if you no longer want it.')
        except fo.OrchestratorError:
            pass
    return jsonify({'ok': True, 'blueprint': bp.to_dict(), 'message': note})


# ---------------------------------------------------------------------------
# Plan APIs
# ---------------------------------------------------------------------------

@founder_bp.route('/api/founder/v/<venture_id>/plan/build', methods=['POST'])
@founder_required
def plan_build(venture_id):
    from app.services import founder_orchestrator as fo
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        rows = fo.build_plan(v, current_user)
    except fo.OrchestratorError as exc:
        return _err(str(exc), exc.status)
    return jsonify({'ok': True, 'steps': [s.to_dict() for s in rows], 'next': fo.next_steps(v)})


@founder_bp.route('/api/founder/v/<venture_id>/next')
@founder_required
def plan_next(venture_id):
    from app.services import founder_orchestrator as fo
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    return jsonify(dict(fo.next_steps(v), ok=True))


@founder_bp.route('/api/founder/v/<venture_id>/live')
@founder_required
def workspace_live(venture_id):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    view = request.args.get('view', 'workspace')
    tpl = 'founder/_plan_live.html' if view == 'plan' else 'founder/_workspace_live.html'
    return render_template(tpl, **_live_context(v))


@founder_bp.route('/api/founder/v/<venture_id>/stream')
@founder_required
def workspace_stream(venture_id):
    from app.services import founder_stream
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    return founder_stream.response(v.id)


def _step_result(s, extra=None):
    from app.services import founder_orchestrator as fo
    v = fo._venture(s)
    body = {'ok': True, 'step': s.to_dict(), 'next': fo.next_steps(v)}
    if extra:
        body.update(extra)
    return jsonify(body)


@founder_bp.route('/api/founder/steps/<step_id>/start', methods=['POST'])
@founder_required
def step_start(step_id):
    from app.services import founder_orchestrator as fo
    s, v = _step(step_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        fo.start(s, current_user)
    except fo.OrchestratorError as exc:
        return _err(str(exc), exc.status)
    return _step_result(s)


@founder_bp.route('/api/founder/steps/<step_id>/complete', methods=['POST'])
@founder_required
def step_complete(step_id):
    from app.services import founder_orchestrator as fo
    s, v = _step(step_id)
    locked = _paid_api(v)
    if locked:
        return locked
    d = _payload()
    try:
        res = fo.complete(s, current_user, evidence_note=d.get('evidence_note'))
    except fo.OrchestratorError as exc:
        return _err(str(exc), exc.status)
    return _step_result(s, {'unlocked': [x.to_dict() for x in res['unlocked']]})


@founder_bp.route('/api/founder/steps/<step_id>/skip', methods=['POST'])
@founder_required
def step_skip(step_id):
    from app.services import founder_orchestrator as fo
    s, v = _step(step_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        res = fo.skip(s, current_user, _payload().get('reason', ''))
    except fo.OrchestratorError as exc:
        return _err(str(exc), exc.status)
    return _step_result(s, {'unlocked': [x.to_dict() for x in res['unlocked']]})


@founder_bp.route('/api/founder/steps/<step_id>/run', methods=['POST'])
@founder_required
def step_run(step_id):
    from app.services import founder_agent_service as fas
    s, v = _step(step_id)
    locked = _paid_api(v)
    if locked:
        return locked
    d = _payload()
    inputs = d.get('inputs') if isinstance(d.get('inputs'), dict) else None
    trigger = 'rerun' if d.get('rerun') else 'founder'
    try:
        fas.execute_async(s, current_user, trigger=trigger, inputs=inputs)
    except fas.AgentError as exc:
        return _err(str(exc), exc.status, code=exc.code)
    return jsonify({'ok': True, 'step': s.to_dict(),
                    'message': 'The agent is working. The plan updates when it finishes.'}), 202


@founder_bp.route('/api/founder/outputs/<output_id>')
@founder_required
def output_view(output_id):
    from app.extensions import db
    from app.models.founder import VentureAgentOutput, VenturePlanStep
    o = db.session.get(VentureAgentOutput, output_id)
    if o is None:
        abort(404)
    v = _venture(o.venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    s = db.session.get(VenturePlanStep, o.plan_step_id)
    return jsonify({'ok': True, 'output': o.to_dict(), 'step_title': s.title if s else ''})


def _decide(action_id: str, approve: bool):
    from app.extensions import db
    from app.models.founder import VentureOutreachAction
    from app.services import founder_autonomy
    a = db.session.get(VentureOutreachAction, action_id)
    if a is None:
        abort(404)
    v = _venture(a.venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        if approve:
            founder_autonomy.approve(a, current_user)
        else:
            founder_autonomy.reject(a, current_user, _payload().get('reason'))
    except founder_autonomy.AutonomyError as exc:
        return _err(str(exc), exc.status)
    msg = ('Approved. SimiSI does not send it: copy it and send it from your own account.' if approve
           else 'Draft declined.')
    return jsonify({'ok': True, 'action': a.to_dict(), 'message': msg})


@founder_bp.route('/api/founder/actions/<action_id>/approve', methods=['POST'])
@founder_required
def action_approve(action_id):
    return _decide(action_id, True)


@founder_bp.route('/api/founder/actions/<action_id>/reject', methods=['POST'])
@founder_required
def action_reject(action_id):
    return _decide(action_id, False)
