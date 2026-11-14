"""Founder Track agent runs (FOUNDER_PLAN.md section 5).

execute(step, user=None, trigger='founder') runs the step's founder agent once:

  1. Refuses when the venture isn't paid, is paused (founder_venture.paused_at
     or status paused), the step is locked or gated, the agent is blocked by
     a guardrail (guardrail_service.check_dispatch) or paused by an admin, or
     a customer/investor-facing agent already reached its daily action cap.
     Admin pauses come from founder_obs_service.dispatch_block_reason (WP7).
  2. Claims the step with a conditional UPDATE (one run per step at a time;
     run_active() reads the agent_started / agent_output / agent_stopped
     markers, claims expire after RUN_TIMEOUT_MINUTES), moves it to
     in_progress and opens an observability run (layer
     'F<layer>', the agent key) when observability_service passes F codes
     through (WP7); founder_run_meta links the run to the venture and step.
  3. Makes ONE Claude call (forced tool deliver_output) with the founder
     system prompt (catalog entry + shared rules) and a briefing: current
     artifacts (truncated), open findings with their citations, upstream step
     outputs and the parameters in force (overrides included). Nothing that
     came from an email import reaches the model.
  4. Checks the output with founder_compliance. On issues it regenerates once
     with stricter rules; if that still fails it stores a safe note instead.
  5. Stores the output as venture_agent_output. Agents with external !=
     'none' turn their drafts into venture_outreach_action rows in
     awaiting_approval and the step waits for approval. Nothing is sent, ever,
     from here. Internal agents complete their step with the output attached.

The route uses execute_async (a background thread, the layer6 pattern); tests
replace _spawn to run inline and AI_CLIENT with a fake.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PROMPT_VERSION = 'fnd-agent-1'
AI_CLIENT = None            # tests: a fake with messages.create(**kw)
MAX_TOKENS = 4000
ARTIFACT_CHARS = 6000
ARTIFACTS_TOTAL_CHARS = 24000
UPSTREAM_CHARS = 3000
MAX_FINDINGS = 20
MAX_DRAFTS = 5
RUN_TIMEOUT_MINUTES = 10    # a run claim older than this is treated as abandoned
EXTERNAL_KINDS = ('customer', 'investor')
DRAFT_TYPES = ('research', 'intro_request', 'followup', 'first_contact', 'financial_message', 'customer_message',
               'application')


class AgentError(ValueError):
    """code: unpaid | paused | locked | state | blocked | cap | unknown | failed."""

    def __init__(self, message, code='failed', status=409):
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Prompt and briefing
# ---------------------------------------------------------------------------

def _tool(agent: dict) -> dict:
    props = {
        'content': {'type': 'string',
                    'description': 'The full output in Markdown, following the output sections in order.'},
        'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
        'assumptions': {'type': 'array', 'items': {'type': 'string'},
                        'description': 'Points that rest on belief rather than evidence, to test next.'},
    }
    required = ['content', 'confidence']
    if agent.get('external', 'none') in EXTERNAL_KINDS:
        props['drafts'] = {
            'type': 'array', 'maxItems': MAX_DRAFTS,
            'description': 'Messages or form answers for the founders to review, approve and send themselves.',
            'items': {'type': 'object', 'properties': {
                'target': {'type': 'string', 'description': 'Who it is for (a role or a name from the briefing).'},
                'subject': {'type': 'string'},
                'body': {'type': 'string'},
            }, 'required': ['body']}}
        required.append('drafts')
    return {'name': 'deliver_output', 'description': 'Return the agent output for the founders.',
            'input_schema': {'type': 'object', 'properties': props, 'required': required}}


def system_prompt(agent: dict, venture, stricter: str = None) -> str:
    from app.services import founder_registry
    layer = int(agent.get('layer') or 1)
    lines = [
        'You are the %s agent on the SimiSI Founder Track, working for the founders of "%s" (stage: %s).'
        % (agent.get('label'), venture.name, venture.stage),
        'Layer L%d %s. %s' % (layer, founder_registry.LAYER_NAMES.get(layer, ''), agent.get('description', '')),
        '',
        'Task: ' + (agent.get('prompt') or ''),
        '',
        'Output sections, in order:',
    ]
    lines += ['- ' + s for s in agent.get('output_spec') or []]
    lines += ['', 'Rules:']
    lines += ['- ' + r for r in founder_registry.rules()]
    if agent.get('external', 'none') in EXTERNAL_KINDS:
        lines += ['- Anything for a %s goes in drafts. The founders review, approve and send each one from '
                  'their own account; you never send.' % agent['external'],
                  '- Drafts say who the founders are and why they are writing, state only facts from the '
                  'briefing and never state financial terms.']
    lines += ['- Respect the parameters in force (autonomy, scope, caps, stopping rule, voice, success metric).',
              '', 'Answer by calling deliver_output.']
    if stricter:
        lines += ['', stricter]
    return '\n'.join(lines)


def _clip(text: str, n: int) -> str:
    text = text or ''
    return text if len(text) <= n else text[:n] + '\n[... truncated]'


def briefing(step, venture, params: dict, inputs: dict = None) -> str:
    """The venture facts the agent works from. Founder uploads and agent
    outputs only: no email-imported contact or mail data."""
    from app.models.founder import VentureAgentOutput, VenturePlanStep
    from app.services import founder_evaluation_service as fes
    from app.services import founder_orchestrator as fo
    parts = ['<step id="%s" layer="L%d">%s\n%s</step>' % (step.id, step.layer, step.title, step.description or '')]
    parts.append('<parameters>%s</parameters>' % json.dumps(params, sort_keys=True, default=str))
    if inputs:
        clean = {str(k)[:40]: str(v)[:1500] for k, v in list(inputs.items())[:10] if str(v or '').strip()}
        if clean:
            parts.append('<founder_inputs>%s</founder_inputs>' % json.dumps(clean, sort_keys=True))
    used = 0
    for a in fes.current_artifacts(venture):
        if used >= ARTIFACTS_TOTAL_CHARS:
            break
        text = _clip(a.text_content, min(ARTIFACT_CHARS, ARTIFACTS_TOTAL_CHARS - used))
        used += len(text)
        parts.append('<artifact kind="%s" version="%d">\n%s\n</artifact>' % (a.kind, a.version, text))
    rows = fes.findings(venture)[:MAX_FINDINGS]
    if rows:
        lines = []
        for f in rows:
            lines.append('- [%s] %s %s (%s, confidence %.2f): %s | cites %s: "%s"' % (
                f.id, f.ftype, f.dimension, f.severity, f.confidence or 0, f.title, f.citation_ref or '',
                (f.citation_text or '')[:240]))
        parts.append('<open_findings>\n%s\n</open_findings>' % '\n'.join(lines))
    keys = list(step.prereq_keys or [])
    if keys:
        ups = [s for s in VenturePlanStep.query.filter_by(venture_id=venture.id).all()
               if fo.step_key(s) in keys and s.state == 'done']
        for s in ups:
            o = (VentureAgentOutput.query.filter_by(plan_step_id=s.id)
                 .order_by(VentureAgentOutput.version.desc()).first())
            body = o.content if o else (s.evidence_note or '')
            if body:
                parts.append('<upstream step="%s" title="%s">\n%s\n</upstream>' % (
                    fo.step_key(s), s.title, _clip(body, UPSTREAM_CHARS)))
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _client():
    if AI_CLIENT is not None:
        return AI_CLIENT
    from app.services import claude
    return claude._client()


def _model(agent: dict) -> str:
    from utils.model_router import MODELS, ModelTier
    try:
        return MODELS[ModelTier(agent.get('model') or 'sonnet')]
    except ValueError:
        return MODELS[ModelTier.SONNET]


def _cost_cents(usage) -> float:
    try:
        from app.services import guide_service
        return float(guide_service._cost_cents(usage))
    except Exception:
        return 0.0


def _call(agent, venture, brief, stricter=None, model=None) -> tuple:
    """(payload dict, usage, model). model: a guardrail model pin, if any."""
    model = model or _model(agent)
    tool = _tool(agent)
    resp = _client().messages.create(
        model=model, max_tokens=MAX_TOKENS, system=system_prompt(agent, venture, stricter),
        tools=[tool], tool_choice={'type': 'tool', 'name': tool['name']},
        messages=[{'role': 'user', 'content': [{'type': 'text', 'text': brief}]}])
    payload = None
    for block in getattr(resp, 'content', None) or []:
        if getattr(block, 'type', None) == 'tool_use':
            payload = getattr(block, 'input', None)
            break
    if not isinstance(payload, dict) or not str(payload.get('content') or '').strip():
        raise AgentError('The agent returned no output.', 'failed', 502)
    return payload, getattr(resp, 'usage', None), model


def _all_text(payload: dict) -> str:
    """Every piece of model text that can be stored or shown: content,
    assumptions, confidence and each draft's target, subject and body."""
    bits = [str(payload.get('content') or ''), str(payload.get('confidence') or '')]
    assumptions = payload.get('assumptions')
    if isinstance(assumptions, (list, tuple)):
        bits += [str(a) for a in assumptions]
    elif assumptions:
        bits.append(str(assumptions))
    for d in payload.get('drafts') or []:
        if isinstance(d, dict):
            bits += [str(d.get('target') or ''), str(d.get('subject') or ''), str(d.get('body') or '')]
    return '\n'.join(bits)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _obs():
    try:
        from app.services import observability_service as obs
        return obs if obs.layer_code('F1') == 'F1' else None
    except Exception:
        return None


def _admin_block(venture, agent_key: str):
    """WP7's pause controls (one agent everywhere, or every agent of a venture),
    through founder_obs_service.dispatch_block_reason when it is installed."""
    try:
        from app.services import founder_obs_service
        fn = founder_obs_service.dispatch_block_reason
    except (ImportError, AttributeError):
        return None
    try:
        return fn(venture, agent_key)
    except Exception as exc:
        logger.error('founder agent pause check failed for %s: %s', agent_key, exc)
        return 'SimiSI could not confirm this agent may run. Try again later.'


def drafts_today(venture) -> int:
    from app.models.founder import VentureOutreachAction
    since = datetime.utcnow() - timedelta(days=1)
    return VentureOutreachAction.query.filter(VentureOutreachAction.venture_id == venture.id,
                                              VentureOutreachAction.created_at >= since).count()


def draft_cap(venture, agent_key: str) -> int:
    """Drafts for customers and investors allowed per rolling day: the admin
    cap, lowered by the agent's own action_cap when it is set. Counted across
    the whole venture (the stricter reading of a per-agent cap)."""
    from app.services import founder_config, founder_team_service
    cap = int(founder_config.get('fnd_action_cap_per_day'))
    own = founder_team_service.effective_param(venture, agent_key, 'action_cap')
    if own is not None:
        try:
            cap = min(cap, int(own))
        except (TypeError, ValueError):
            pass
    return max(0, cap)


# The run marker: a venture_step_event whose note starts with {"event": "agent_.
# 'agent_started' opens a run; 'agent_output' or 'agent_stopped' closes it.
_MARK_PREFIX = '{"event": "agent_'


def run_active(step) -> bool:
    """True while an agent run on this step is claimed and not finished
    (claims older than RUN_TIMEOUT_MINUTES count as abandoned)."""
    from app.models.founder import VentureStepEvent
    row = (VentureStepEvent.query.filter(VentureStepEvent.plan_step_id == step.id,
                                         VentureStepEvent.note.like(_MARK_PREFIX + '%'))
           .order_by(VentureStepEvent.created_at.desc(), VentureStepEvent.id.desc()).first())
    if row is None or not (row.note or '').startswith(_MARK_PREFIX + 'started"'):
        return False
    return bool(row.created_at and row.created_at > datetime.utcnow() - timedelta(minutes=RUN_TIMEOUT_MINUTES))


def _mark(step, event: str, note: str, user=None):
    from app.services import founder_stream
    founder_stream.publish(step.venture_id, event, {
        'plan_step_id': step.id, 'actor_kind': 'agent', 'actor_user_id': getattr(user, 'id', None),
        'note': note}, commit=False)


def preflight(step, venture, trigger='founder', allow_states=('ready', 'in_progress')) -> dict:
    """The catalog entry if the run may go ahead; raises AgentError.
    'in_progress' is accepted only for a step the founders started by hand:
    a step with an agent run under way is refused (code 'running')."""
    from app.services import founder_registry, founder_ventures
    from app.services import founder_orchestrator as fo
    if venture is None:
        raise AgentError('Venture not found.', 'unknown', 404)
    if not founder_ventures.is_paid(venture):
        raise AgentError('Agents unlock with the Founder Track.', 'unpaid', 402)
    if venture.paused_at is not None or venture.status != 'active':
        raise AgentError('This venture is paused, so its agents do not run.', 'paused', 409)
    agent = founder_registry.get(step.agent_key) if step.kind == 'agent' else None
    if agent is None:
        raise AgentError('This step has no agent. Mark it done when you have finished it.', 'unknown', 400)
    if run_active(step):
        raise AgentError('The %s agent is still working on this step. The plan updates when it finishes.'
                         % agent.get('label', 'step'), 'running', 409)
    if trigger == 'rerun':
        allow_states = tuple(allow_states) + ('done', 'awaiting_approval')
    if step.state not in allow_states:
        if step.state == 'locked':
            raise AgentError(fo.gate_reason(venture, step) or 'This step is locked until its prerequisites are done.',
                             'locked', 409)
        raise AgentError('This step is %s.' % fo.STATE_LABELS.get(step.state, step.state).lower(), 'state', 409)
    gate = fo.gate_reason(venture, step)
    if gate:
        raise AgentError(gate, 'locked', 409)
    blocked = _admin_block(venture, step.agent_key)
    if blocked:
        raise AgentError('%s Try again later.' % blocked if not blocked.endswith('later.') else blocked,
                         'paused' if venture.paused_at else 'blocked', 409)
    if agent.get('external', 'none') in EXTERNAL_KINDS:
        cap = draft_cap(venture, step.agent_key)
        if drafts_today(venture) >= cap:
            raise AgentError('Your daily limit of %d drafts for customers and investors is reached. '
                             'It resets within 24 hours.' % cap, 'cap', 429)
    return agent


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _claim(step, agent, user) -> dict:
    """Claim the step for one run. The conditional UPDATE matches only the
    state and updated_at this request read, so of two concurrent claims
    exactly one changes a row; the other gets AgentError('running').
    Returns the prior state, restored if the run fails."""
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    from app.services import founder_orchestrator as fo
    prior = {'state': step.state, 'completed_at': step.completed_at}
    now = datetime.utcnow()
    t = VenturePlanStep.__table__
    res = db.session.execute(t.update().where(t.c.id == step.id).where(t.c.state == step.state)
                             .where(t.c.updated_at == step.updated_at).values(updated_at=now))
    if not res.rowcount:
        db.session.rollback()
        raise AgentError('The %s agent is already working on this step, or the step just changed. '
                         'The plan updates when it finishes.' % agent.get('label', 'step'), 'running', 409)
    step.updated_at = now
    note = '%s agent started' % agent['label']
    if step.state != 'in_progress':
        fo.transition(step, 'in_progress', user=user, actor_kind='founder' if user else 'agent',
                      note=note, commit=False)
    _mark(step, 'agent_started', note, user)
    db.session.commit()
    return prior


def execute(step, user=None, trigger: str = 'founder', inputs: dict = None):
    """Run the step's agent synchronously. Returns the VentureAgentOutput.
    Raises AgentError."""
    from app.extensions import db
    from app.models.founder import FounderVenture
    venture = db.session.get(FounderVenture, step.venture_id)
    agent = preflight(step, venture, trigger)
    prior = _claim(step, agent, user)
    return _run(step, venture, agent, user, trigger, inputs, prior)


def execute_async(step, user=None, trigger: str = 'founder', inputs: dict = None):
    """Check, claim the step (in_progress) and run the agent in a thread."""
    from app.extensions import db
    from app.models.founder import FounderVenture
    venture = db.session.get(FounderVenture, step.venture_id)
    agent = preflight(step, venture, trigger)
    prior = _claim(step, agent, user)
    _spawn(step.id, getattr(user, 'id', None), trigger, inputs, prior)
    return step


def _spawn(step_id: str, user_id, trigger, inputs, prior=None) -> None:
    from flask import current_app
    app = current_app._get_current_object()

    def _work():
        with app.app_context():
            _run_by_id(step_id, user_id, trigger, inputs, prior)

    threading.Thread(target=_work, daemon=True).start()


def _run_by_id(step_id, user_id, trigger, inputs, prior=None):
    from app.extensions import db
    from app.models.founder import FounderVenture, VenturePlanStep
    from app.models.user import User
    from app.services import founder_registry
    try:
        step = db.session.get(VenturePlanStep, step_id)
        venture = db.session.get(FounderVenture, step.venture_id) if step else None
        user = db.session.get(User, user_id) if user_id else None
        if step is None or venture is None:
            return None
        return _run(step, venture, founder_registry.get(step.agent_key), user, trigger, inputs, prior)
    except AgentError as exc:
        logger.warning('founder agent run %s stopped: %s', step_id, exc)
    except Exception as exc:
        logger.exception('founder agent run %s crashed: %s', step_id, exc)
    return None


def _run(step, venture, agent, user, trigger, inputs, prior=None):
    from app.services import guardrail_service
    from app.services import founder_team_service
    prior = prior or {'state': 'ready', 'completed_at': None}
    uid = getattr(user, 'id', None)
    layer_code = 'F%d' % int(step.layer)
    params = founder_team_service.effective_params(venture, step.agent_key)
    decision = guardrail_service.check_dispatch(step.agent_key, layer_code, user_id=uid, model=_model(agent))
    if not decision:
        _fail(step, user, 'Blocked by a SimiSI guardrail: %s' % (decision.reason or 'agent paused'), prior)
        raise AgentError('This agent is paused by a SimiSI guardrail. Try again later.', 'blocked', 409)
    # An admin model-constraint rule can pin the agent to an approved model.
    model = getattr(decision, 'model_override', None) or _model(agent)
    obs = _obs()
    if obs is None:
        return _do_run(step, venture, agent, user, trigger, inputs, params, None, None, prior, model)
    with obs.run_context(step.agent_key, layer_code, user_id=uid, dispatch_source='founder_' + trigger,
                         params={'venture_id': venture.id, 'plan_step_id': step.id,
                                 'autonomy_level': params.get('autonomy_level')}) as run:
        return _do_run(step, venture, agent, user, trigger, inputs, params, obs, run, prior, model)


def _restore(step, user, note, prior=None, commit=True):
    """End a run without a new result: the step goes back to where it was
    before the run (a re-run of a finished step stays finished, with its
    original completion time) and the run marker closes."""
    from app.extensions import db
    from app.services import founder_orchestrator as fo
    prior = prior or {}
    back = prior.get('state')
    if back not in ('done', 'awaiting_approval', 'in_progress'):
        back = 'ready'
    if step.state == 'in_progress' and back != 'in_progress':
        fo.transition(step, back, user=user, actor_kind='agent', note=note[:200], commit=False)
        if back == 'done' and prior.get('completed_at'):
            step.completed_at = prior['completed_at']
    _mark(step, 'agent_stopped', note[:200], user)
    if commit:
        db.session.commit()


def _fail(step, user, note, prior=None):
    from app.extensions import db
    try:
        db.session.rollback()
        db.session.refresh(step)
        _restore(step, user, note, prior)
    except Exception as exc:
        logger.error('founder step %s could not be reset: %s', step.id, exc)
        db.session.rollback()


def _compose(payload: dict, agent: dict, outcome: str, layer: int) -> tuple:
    """(content, drafts) exactly as they are stored."""
    from app.services import founder_compliance
    if outcome == 'fallback':
        content, drafts = founder_compliance.safe_note(agent.get('label')), []
    else:
        content = payload.get('content') or ''
        assumptions = payload.get('assumptions')
        if assumptions:
            items = assumptions if isinstance(assumptions, (list, tuple)) else [assumptions]
            content = content.rstrip() + '\n\n**Assumptions to test**\n' + '\n'.join(
                '- %s' % str(a)[:300] for a in items[:10])
        if payload.get('confidence'):
            content = content.rstrip() + '\n\nConfidence: %s' % payload['confidence']
        drafts = [d for d in (payload.get('drafts') or []) if isinstance(d, dict) and str(d.get('body') or '').strip()]
    return founder_compliance.ensure_disclaimer(content, layer), drafts


def _do_run(step, venture, agent, user, trigger, inputs, params, obs, run, prior=None, model=None):
    from app.extensions import db
    from app.models.founder import FounderRunMeta, VentureAgentOutput, VentureOutreachAction
    from app.services import founder_autonomy, founder_compliance, founder_stream, founder_ventures
    from app.services import founder_orchestrator as fo
    uid = getattr(user, 'id', None)
    exemption = founder_compliance.exemption_for(venture)
    brief = briefing(step, venture, params, inputs)
    cost, outcome, used_model = 0.0, 'clean', None
    try:
        payload, usage, used_model = _call(agent, venture, brief, model=model)
        cost += _cost_cents(usage)
        _usage(obs, used_model, usage)
        # The check covers every field that is stored or shown (_all_text).
        issues = founder_compliance.check(_all_text(payload), step.layer, exemption)
        if issues:
            founder_compliance.log(uid, step.agent_key, issues, 'regenerated')
            payload, usage, used_model = _call(agent, venture, brief, founder_compliance.stricter_rules(issues),
                                               model=model)
            cost += _cost_cents(usage)
            _usage(obs, used_model, usage)
            issues = founder_compliance.check(_all_text(payload), step.layer, exemption)
            outcome = 'regenerated'
            if issues:
                founder_compliance.log(uid, step.agent_key, issues, 'fallback')
                outcome = 'fallback'
                if obs is not None:
                    obs.record_warning('founder compliance fallback', rules=sorted({i['rule'] for i in issues}))
    except AgentError as exc:
        _fail(step, user, str(exc), prior)
        if obs is not None:
            obs.finish_run(status=obs.STATUS_ERROR, error_code='AgentError')
        raise
    except Exception as exc:
        logger.exception('founder agent %s failed: %s', step.agent_key, exc)
        _fail(step, user, 'The agent could not finish. Try again.', prior)
        if obs is not None:
            obs.finish_run(status=obs.STATUS_ERROR, error_code=exc.__class__.__name__)
        raise AgentError('The agent could not finish. Nothing was saved; try again.', 'failed', 502)

    content, drafts = _compose(payload, agent, outcome, step.layer)

    # The founders may have marked the step done or skipped it while the agent
    # worked. Read the state again: the output is kept, but no drafts are made
    # and the step's state is left alone.
    db.session.refresh(step)
    still_ours = step.state == 'in_progress'

    prev = (VentureAgentOutput.query.filter_by(plan_step_id=step.id)
            .order_by(VentureAgentOutput.version.desc()).first())
    out = VentureAgentOutput(venture_id=venture.id, plan_step_id=step.id, agent_key=step.agent_key,
                             content=content, version=(prev.version + 1) if prev else 1,
                             obs_run_id=getattr(run, 'id', None))
    db.session.add(out)
    db.session.flush()
    level = founder_autonomy.level_for(venture, step.agent_key)
    if run is not None and getattr(run, 'id', None):
        db.session.add(FounderRunMeta(obs_run_id=run.id, venture_id=venture.id, plan_step_id=step.id,
                                      agent_key=step.agent_key, trigger=trigger if trigger in FounderRunMeta.TRIGGERS
                                      else 'founder', autonomy_level=level, prompt_version=PROMPT_VERSION,
                                      cost_cents=int(round(cost))))
        try:
            obs.record_artifact('founder_output', content=content, content_ref='venture_agent_output:%s' % out.id)
        except Exception as exc:
            logger.debug('obs artifact skipped: %s', exc)

    external = agent.get('external', 'none') in EXTERNAL_KINDS
    cls = agent.get('action_type_class') or 'research'
    action_type = cls if cls in DRAFT_TYPES else ('customer_message' if agent.get('external') == 'customer'
                                                  else 'first_contact')
    made, notes = [], []
    if external and outcome != 'fallback' and still_ours:
        if not drafts:
            drafts = [{'target': None, 'subject': agent.get('label'), 'body': payload.get('content') or ''}]
        drafts = drafts[:MAX_DRAFTS]
        # The daily cap counts what this run adds, not only what came before.
        room = max(0, draft_cap(venture, step.agent_key) - drafts_today(venture))
        dropped = max(0, len(drafts) - room)
        for d in drafts[:room]:
            a = VentureOutreachAction(venture_id=venture.id, plan_step_id=step.id, action_type=action_type,
                                      level_required=level, level_applied=None, status='awaiting_approval',
                                      target_ref=str(d.get('target') or '')[:200] or None,
                                      subject=(str(d.get('subject') or '')[:300] or None),
                                      body=str(d.get('body') or ''))
            db.session.add(a)
            made.append(a)
        db.session.flush()
        if dropped:
            notes.append('%d draft%s not kept because the daily limit is reached'
                         % (dropped, '' if dropped == 1 else 's'))
    if not still_ours:
        notes.append('the step was closed while the agent worked, so no drafts were made')
    founder_ventures.log_event(venture.id, 'agent_output', '%s v%d (%s)%s%s' % (
        step.agent_key, out.version, outcome, ', %d drafts' % len(made) if made else '',
        ''.join('; ' + n for n in notes)), user_id=uid)
    finished = '%s finished%s' % (agent['label'], ' with %d draft%s for your approval' % (
        len(made), '' if len(made) == 1 else 's') if made else '')
    if notes:
        finished += ' (%s)' % '; '.join(notes)
    # 'agent_output' also closes the run marker (see run_active).
    founder_stream.publish(venture.id, 'agent_output', {
        'plan_step_id': step.id, 'output_id': out.id, 'actor_kind': 'agent', 'note': finished[:300]}, commit=False)
    db.session.commit()

    if not still_ours:
        return out
    try:
        if outcome == 'fallback':
            _restore(step, None, 'Output withheld by the founder rules', prior)
        elif made:
            step.output_id = out.id
            fo.transition(step, 'awaiting_approval', user=None, actor_kind='agent',
                          note='%d draft%s awaiting approval' % (len(made), '' if len(made) == 1 else 's'))
        elif external:
            # Every draft fell to the daily cap: nothing to approve yet.
            _restore(step, None, 'Daily draft limit reached; run it again tomorrow', prior)
        else:
            fo.complete(step, None, output_id=out.id, actor_kind='agent')
    except fo.OrchestratorError as exc:
        # The step moved between the re-read and now; the output is stored.
        logger.warning('founder step %s left as %s after its run: %s', step.id, step.state, exc)
        db.session.rollback()
    return out


def _usage(obs, model, usage):
    if obs is None or usage is None:
        return
    try:
        obs.record_usage(model=model, prompt_tokens=getattr(usage, 'input_tokens', None),
                         completion_tokens=getattr(usage, 'output_tokens', None))
    except Exception as exc:
        logger.debug('obs usage skipped: %s', exc)


def latest_output(step):
    from app.models.founder import VentureAgentOutput
    return (VentureAgentOutput.query.filter_by(plan_step_id=step.id)
            .order_by(VentureAgentOutput.version.desc()).first())
