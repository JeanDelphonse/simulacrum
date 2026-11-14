"""Founder Track plan and dispatcher (FOUNDER_PLAN.md section 5, PRD "Execution
plan" and "Algorithms to Live By").

A separate founder dispatcher. It reuses the Layer 6 algorithms where they
fit (layer6._ensure_layer_diversity for exploring every layer early) and
never calls run_orchestrator_cycle, never creates Simulation or
Layer6Config rows and never touches config/agents.json.

The plan is a DAG of venture_plan_step rows:
  * one agent step per accepted blueprint, plus the catalog prerequisites of
    those agents (transitively), so every step can actually become ready;
  * the proof-of-concept path from the PRD evidence ladder (agree the plan
    with a named customer, run it and measure, get paid), for the rungs the
    venture has not reached yet;
  * steps other packages add through add_step() (alignment, legal, manual).

A step's key is its agent_key for agent steps and its id otherwise;
prereq_keys lists the keys it waits for. A prerequisite is satisfied when
that step is done or skipped, or isn't in the plan.

States: locked, ready, in_progress, awaiting_approval, done, skipped. Every
transition writes a venture_step_event (through founder_stream.publish, which
the workspace SSE stream reads) and an AuditLog row.

L5 (Capitalize) agent steps stay locked until the venture's evidence rung
reaches the admin gate for the agent's action class (fnd_gate_rung_intros
for intro and follow-up agents, fnd_gate_rung_applications for the rest)
AND investor connection (founder_investor) is switched on for the owner.

Next step: among ready steps, most downstream steps unblocked first (the
layer6._count_unblocked rule, over this plan's DAG), ties to the smaller
effort (shortest job first). While the plan is young (fewer steps finished
than layers in the plan) the pick covers as many layers as it can (explore);
later it leans to the layers that are moving (exploit).
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

STATES = ('locked', 'ready', 'in_progress', 'awaiting_approval', 'done', 'skipped')
OPEN = ('ready', 'in_progress', 'awaiting_approval')
FINISHED = ('done', 'skipped')
TRANSITIONS = {
    'locked': ('ready', 'skipped'),
    'ready': ('locked', 'in_progress', 'awaiting_approval', 'done', 'skipped'),
    'in_progress': ('ready', 'locked', 'awaiting_approval', 'done', 'skipped'),
    'awaiting_approval': ('ready', 'in_progress', 'done', 'skipped'),
    'done': ('ready', 'locked', 'in_progress'),
    'skipped': ('ready', 'locked'),
}
STATE_LABELS = {'locked': 'Locked', 'ready': 'Ready', 'in_progress': 'In progress',
                'awaiting_approval': 'Awaiting approval', 'done': 'Done', 'skipped': 'Skipped'}
SKIP_REASON_MIN = 3

# PRD proof-of-concept path. code -> (layer, rung it proves, title, description, effort,
# agent prerequisites, proof prerequisites).
PROOF_STEPS = (
    ('agree', 2, 3, 'Agree the proof-of-concept plan with a named customer',
     'A named customer accepts a written plan with success criteria set in advance '
     '(evidence rung 3).', 3, ('fnd_poc_plan',), ()),
    ('run', 2, 4, 'Run the proof of concept and measure the results',
     'Measure the results against the agreed criteria, in the customer\'s terms (evidence rung 4).',
     4, (), ('agree',)),
    ('paid', 3, 5, 'Get paid: a pilot or a signed commitment',
     'The customer pays for a pilot or signs a commitment (evidence rung 5).', 3, ('fnd_pilot_offer',), ('run',)),
)
PROOF_TITLES = {code: title for code, _l, _r, title, _d, _e, _a, _p in PROOF_STEPS}


class OrchestratorError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def steps(venture) -> list:
    from app.models.founder import VenturePlanStep
    return (VenturePlanStep.query.filter_by(venture_id=venture.id)
            .order_by(VenturePlanStep.sort_order, VenturePlanStep.created_at, VenturePlanStep.id).all())


def step_key(step) -> str:
    return step.agent_key if step.kind == 'agent' and step.agent_key else step.id


def proof_step(venture, code: str):
    """The plan's proof step for a code (agree / run / paid), or None."""
    from app.models.founder import VenturePlanStep
    title = PROOF_TITLES.get(code)
    if not title:
        return None
    return VenturePlanStep.query.filter_by(venture_id=venture.id, kind='proof', title=title).first()


def _venture(step):
    from app.extensions import db
    from app.models.founder import FounderVenture
    return db.session.get(FounderVenture, step.venture_id)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def _investor_on(venture) -> bool:
    try:
        from app.extensions import db
        from app.models.user import User
        from app.services import founder_feature
        owner = db.session.get(User, venture.owner_user_id)
        return founder_feature.is_enabled(owner, founder_feature.INVESTOR)
    except Exception as exc:
        logger.error('founder investor gate unreadable, keeping L5 locked: %s', exc)
        return False


def gate_rung(agent_key: str) -> int:
    """The minimum evidence rung for an L5 agent's action class."""
    from app.services import founder_config, founder_registry
    a = founder_registry.get(agent_key) or {}
    if a.get('gate') == 'intros' or a.get('action_type_class') in ('intro_request', 'followup', 'first_contact',
                                                                     'financial_message'):
        return int(founder_config.get('fnd_gate_rung_intros'))
    return int(founder_config.get('fnd_gate_rung_applications'))


def gate_reason(venture, step, investor_on=None):
    """Why an L5 agent step can't open yet, or None."""
    if step.kind != 'agent' or int(step.layer or 0) != 5:
        return None
    if investor_on is None:
        investor_on = _investor_on(venture)
    if not investor_on:
        return 'Capitalize agents open when investor connection is switched on for your venture.'
    need = gate_rung(step.agent_key)
    if int(venture.rung or 1) < need:
        return 'Opens at evidence rung %d (you are on rung %d).' % (need, int(venture.rung or 1))
    if not _exemption_recorded(venture):
        return 'Capitalize agents open once the founders record which offering exemption applies (Investors page).'
    return None


def _exemption_recorded(venture) -> bool:
    try:
        from app.services import founder_investors
        return founder_investors.exemption_recorded(venture)
    except Exception as exc:
        logger.error('founder exemption gate unreadable, keeping L5 locked: %s', exc)
        return False


def _missing_prereqs(step, by_key: dict) -> list:
    out = []
    for k in step.prereq_keys or []:
        other = by_key.get(k)
        if other is not None and other.state not in FINISHED:
            out.append(other)
    return out


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def transition(step, to_state: str, user=None, actor_kind: str = 'founder', note: str = None, commit: bool = True):
    """Move a step to a new state. Writes venture_step_event (published to the
    SSE stream) and AuditLog. Raises OrchestratorError for a move the state
    machine doesn't allow."""
    from app.extensions import db
    from app.services import founder_stream, founder_ventures
    if to_state not in STATES:
        raise OrchestratorError('Unknown state.')
    frm = step.state
    if frm == to_state:
        return step
    if to_state not in TRANSITIONS.get(frm, ()):
        raise OrchestratorError('A %s step cannot become %s.' % (STATE_LABELS.get(frm, frm).lower(),
                                                                    STATE_LABELS[to_state].lower()), 409)
    uid = getattr(user, 'id', None)
    step.state = to_state
    step.updated_at = datetime.utcnow()
    if to_state == 'done':
        step.completed_at = step.completed_at or datetime.utcnow()
    elif to_state not in FINISHED:
        step.completed_at = None
    founder_stream.publish(step.venture_id, 'step_' + to_state, {
        'plan_step_id': step.id, 'from_state': frm, 'to_state': to_state, 'actor_user_id': uid,
        'actor_kind': actor_kind, 'note': note, 'title': step.title, 'layer': step.layer}, commit=False)
    founder_ventures.log_event(step.venture_id, 'step_' + to_state, '%s: %s -> %s%s' % (
        step.id, frm, to_state, (' (%s)' % note) if note else ''), user_id=uid)
    if commit:
        db.session.commit()
    return step


def refresh(venture, commit: bool = True) -> list:
    """Recompute locked/ready for steps that haven't started. Returns the
    steps that changed."""
    from app.extensions import db
    rows = steps(venture)
    by_key = {step_key(s): s for s in rows}
    inv = _investor_on(venture)
    changed = []
    for s in rows:
        if s.state not in ('locked', 'ready'):
            continue
        blocked = _missing_prereqs(s, by_key) or gate_reason(venture, s, inv)
        want = 'locked' if blocked else 'ready'
        if want != s.state:
            transition(s, want, actor_kind='system', commit=False,
                       note='Prerequisites done' if want == 'ready' else 'Waiting on a prerequisite or gate')
            changed.append(s)
    if commit:
        db.session.commit()
    return changed


def _close_drafts(step, reason: str, user=None) -> int:
    """Block the step's undecided drafts when the step closes, so nothing
    stays waiting for approval on a finished step. Returns how many."""
    from app.models.founder import VentureOutreachAction
    from app.services import founder_autonomy
    n = 0
    for a in VentureOutreachAction.query.filter_by(plan_step_id=step.id).all():
        if a.status in founder_autonomy.PENDING:
            a.status = 'blocked'
            a.block_reason = reason[:300]
            n += 1
    if n:
        from app.services import founder_ventures
        founder_ventures.log_event(step.venture_id, 'drafts_closed', '%s: %d draft%s closed (%s)' % (
            step.id, n, '' if n == 1 else 's', reason), user_id=getattr(user, 'id', None))
    return n


def drop_agent(venture, agent_key: str, user=None) -> list:
    """After the founders decline an agent: skip its plan step if nobody has
    touched it (locked or ready, no output) and no other open step waits on
    it. Returns the skipped steps."""
    from app.extensions import db
    rows = steps(venture)
    out = []
    for s in rows:
        if s.kind != 'agent' or s.agent_key != agent_key or s.state not in ('locked', 'ready') or s.output_id:
            continue
        needed = any(agent_key in (o.prereq_keys or []) and o.state not in FINISHED
                     for o in rows if o.id != s.id)
        if needed:
            continue
        s.skip_reason = 'Agent declined'
        transition(s, 'skipped', user=user, actor_kind='founder', note='Agent declined', commit=False)
        out.append(s)
    if out:
        db.session.flush()
        refresh(venture, commit=False)
    db.session.commit()
    return out


def reopen_agent(venture, agent_key: str, user=None) -> list:
    """Undo drop_agent when the founders accept the agent again: its steps
    skipped with the reason 'Agent declined' go back into the plan."""
    from app.extensions import db
    out = []
    for s in steps(venture):
        if s.kind == 'agent' and s.agent_key == agent_key and s.state == 'skipped' \
                and (s.skip_reason or '') == 'Agent declined':
            s.skip_reason = None
            transition(s, 'locked', user=user, actor_kind='founder', note='Agent accepted again', commit=False)
            out.append(s)
    if out:
        db.session.flush()
        refresh(venture, commit=False)
        db.session.commit()
    return out


def start(step, user):
    if step.state != 'ready':
        raise OrchestratorError('Only a ready step can be started.', 409)
    return transition(step, 'in_progress', user=user, actor_kind='founder', note='Started')


def complete(step, user, evidence_note: str = None, output_id: str = None, actor_kind: str = 'founder') -> dict:
    """Mark a step done (the founder with optional evidence, or an agent with
    its output). Re-checks the plan; returns {'step', 'unlocked': [...]}."""
    from app.extensions import db
    if step.state not in OPEN:
        if step.state == 'locked':
            raise OrchestratorError('This step is locked until its prerequisites are done.', 409)
        raise OrchestratorError('This step is already %s.' % STATE_LABELS.get(step.state, step.state).lower(), 409)
    venture = _venture(step)
    if step.kind == 'agent' and gate_reason(venture, step):
        raise OrchestratorError(gate_reason(venture, step), 409)
    note = ' '.join((evidence_note or '').split())[:2000]
    if note:
        step.evidence_note = note
    if output_id:
        step.output_id = output_id
    transition(step, 'done', user=user, actor_kind=actor_kind, commit=False,
               note=('Evidence: ' + note[:200]) if note else ('Agent output attached' if output_id else 'Marked done'))
    _close_drafts(step, 'Step marked done before this draft was decided', user)
    db.session.flush()
    unlocked = [s for s in refresh(venture, commit=False) if s.state == 'ready']
    db.session.commit()
    return {'step': step, 'unlocked': unlocked}


def skip(step, user, reason: str) -> dict:
    """Skip a step. The reason is required and kept."""
    from app.extensions import db
    reason = ' '.join((reason or '').split())
    if len(reason) < SKIP_REASON_MIN:
        raise OrchestratorError('Say why you are skipping this step.')
    if step.state in FINISHED:
        raise OrchestratorError('This step is already %s.' % STATE_LABELS[step.state].lower(), 409)
    step.skip_reason = reason[:2000]
    transition(step, 'skipped', user=user, actor_kind='founder', note='Skipped: ' + reason[:200], commit=False)
    _close_drafts(step, 'Step skipped by the founders', user)
    db.session.flush()
    unlocked = [s for s in refresh(_venture(step), commit=False) if s.state == 'ready']
    db.session.commit()
    return {'step': step, 'unlocked': unlocked}


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------

def _owner_member_id(venture):
    from app.models.founder import VentureMember
    m = VentureMember.query.filter_by(venture_id=venture.id, role='owner', status='active').first()
    return m.id if m else None


def add_step(venture, layer: int, kind: str, title: str, description: str = None, prereq_keys=None,
             effort: int = 3, owner_member_id: str = None, agent_key: str = None, sort_order: int = None,
             user=None, commit: bool = True):
    """Add one step (other packages use this for alignment, legal and manual
    steps). It starts locked; refresh() opens it when its prerequisites are
    done. Returns the step."""
    from app.extensions import db
    from app.models.founder import VenturePlanStep
    from utils.id_gen import generate_pk
    if kind not in VenturePlanStep.KINDS:
        raise OrchestratorError('Unknown step kind.')
    layer = int(layer)
    if layer not in (1, 2, 3, 4, 5):
        raise OrchestratorError('Unknown layer.')
    s = VenturePlanStep(id=generate_pk(), venture_id=venture.id, layer=layer, kind=kind, agent_key=agent_key,
                        title=(title or '').strip()[:300] or 'Step', description=description,
                        state='locked', prereq_keys=list(prereq_keys or []), effort=max(1, min(5, int(effort or 3))),
                        sort_order=sort_order if sort_order is not None else layer * 100 + 90,
                        owner_member_id=owner_member_id or _owner_member_id(venture))
    db.session.add(s)
    db.session.flush()
    from app.services import founder_stream
    founder_stream.publish(venture.id, 'step_added', {'plan_step_id': s.id, 'title': s.title, 'layer': layer,
                                                      'actor_user_id': getattr(user, 'id', None),
                                                      'actor_kind': 'founder' if user else 'system'}, commit=False)
    refresh(venture, commit=False)
    if commit:
        db.session.commit()
    return s


def _with_prereqs(keys) -> list:
    """The keys plus every catalog prerequisite, transitively."""
    from app.services import founder_registry
    out, todo = [], list(keys)
    while todo:
        k = todo.pop(0)
        if k in out:
            continue
        a = founder_registry.get(k)
        if a is None:
            continue
        out.append(k)
        todo.extend(a.get('prerequisites') or [])
    return out


def build_plan(venture, user=None) -> list:
    """Create the plan steps from the accepted blueprints, their catalog
    prerequisites and the proof path. Idempotent: existing steps (and their
    states) are kept; only missing steps are added."""
    from app.extensions import db
    from app.models.founder import AgentBlueprint, VenturePlanStep
    from app.services import founder_registry, founder_stream, founder_ventures
    from utils.id_gen import generate_pk
    accepted = [b for b in AgentBlueprint.query.filter_by(venture_id=venture.id, status='accepted').all()]
    if not accepted:
        raise OrchestratorError('Accept at least one agent on your team first.', 409)
    rung = int(venture.rung or 1)
    proof_codes = [p for p in PROOF_STEPS if rung < p[2]]
    agent_keys = [b.agent_key for b in accepted]
    for p in proof_codes:
        for k in p[6]:
            # The pilot offer joins the plan only when the founders chose it.
            if k == 'fnd_poc_plan' or k in agent_keys:
                agent_keys.append(k)
    keys = _with_prereqs(agent_keys)
    order = {k: i for i, k in enumerate(founder_registry.keys())}
    rows = steps(venture)
    have_agents = {s.agent_key for s in rows if s.kind == 'agent'}
    bp_by_key = {b.agent_key: b for b in AgentBlueprint.query.filter_by(venture_id=venture.id).all()}
    owner_mid = _owner_member_id(venture)
    added = 0
    from app.models.founder import EvaluationFinding
    for k in keys:
        if k in have_agents:
            continue
        a = founder_registry.get(k)
        bp = bp_by_key.get(k)
        desc = a.get('description') or ''
        f = EvaluationFinding.query.filter_by(id=bp.finding_id).first() if bp and bp.finding_id else None
        if f is not None:
            desc += '\nAddresses: %s' % f.title
        elif bp is None:
            desc += '\nNeeded before: %s' % ', '.join(sorted(
                founder_registry.get(o)['label'] for o in keys
                if k in (founder_registry.get(o).get('prerequisites') or [])) or ['the proof path'])
        db.session.add(VenturePlanStep(
            id=generate_pk(), venture_id=venture.id, layer=int(a['layer']), kind='agent', agent_key=k,
            title=a['label'], description=desc.strip(), state='locked',
            prereq_keys=[p for p in (a.get('prerequisites') or []) if p in keys],
            effort=int(a.get('effort') or 3), sort_order=int(a['layer']) * 100 + order.get(k, 50),
            owner_member_id=owner_mid))
        added += 1
    db.session.flush()
    proof_ids = {}
    for code, layer, prove_rung, title, desc, effort, agent_pre, proof_pre in proof_codes:
        existing = proof_step(venture, code)
        if existing is not None:
            proof_ids[code] = existing.id
            continue
        sid = generate_pk()
        proof_ids[code] = sid
        pre = [k for k in agent_pre if k in keys] + [proof_ids[c] for c in proof_pre if c in proof_ids]
        db.session.add(VenturePlanStep(
            id=sid, venture_id=venture.id, layer=layer, kind='proof', title=title, description=desc,
            state='locked', prereq_keys=pre, effort=effort, sort_order=layer * 100 + 60 + prove_rung,
            owner_member_id=owner_mid))
        added += 1
    db.session.flush()
    founder_stream.publish(venture.id, 'plan_built', {
        'actor_user_id': getattr(user, 'id', None), 'actor_kind': 'founder' if user else 'system',
        'note': 'Plan updated: %d new step%s' % (added, '' if added == 1 else 's')}, commit=False)
    founder_ventures.log_event(venture.id, 'plan_built', '%d steps added' % added, user_id=getattr(user, 'id', None))
    refresh(venture, commit=False)
    db.session.commit()
    return steps(venture)


# ---------------------------------------------------------------------------
# Next step
# ---------------------------------------------------------------------------

def _count_unblocked(key: str, done_keys: set, candidates: list) -> int:
    """layer6._count_unblocked over this plan: how many locked steps would
    have every prerequisite met once this one is done. (layer6's version
    reads the global wealth ACTION_PREREQUISITES map, so it can't take a
    venture's DAG; the rule is the same.)"""
    n = 0
    for other in candidates:
        pre = set(other.prereq_keys or [])
        if key in pre and not (pre - done_keys - {key}):
            n += 1
    return n


def _diversity():
    try:
        from app.services.layer6 import _ensure_layer_diversity
        return _ensure_layer_diversity
    except Exception:          # pragma: no cover - layer6 import failure
        return None


def is_exploring(rows) -> bool:
    layers = {s.layer for s in rows}
    finished = sum(1 for s in rows if s.state in FINISHED)
    return finished < len(layers)


def ranked(venture) -> dict:
    """Ready steps ranked, with how many steps each would unblock."""
    rows = steps(venture)
    by_key = {step_key(s): s for s in rows}
    done_keys = {k for k, s in by_key.items() if s.state in FINISHED}
    inv = _investor_on(venture)
    # Steps that can open once their prerequisites are met (gate-locked L5 steps can't).
    waiting = [s for s in rows if s.state == 'locked' and not gate_reason(venture, s, inv)]
    progress = {}
    for s in rows:
        if s.state == 'done':
            progress[s.layer] = progress.get(s.layer, 0) + 1
    explore = is_exploring(rows)
    items = []
    for s in rows:
        if s.state != 'ready':
            continue
        unb = _count_unblocked(step_key(s), done_keys, waiting)
        # Higher is better: unblocked first, then shorter effort; later in the
        # plan (exploit) the layer that is moving breaks the remaining ties.
        score = unb * 100.0 + (6 - int(s.effort or 3)) * 10.0
        if not explore:
            score += min(progress.get(s.layer, 0), 9)
        score -= s.sort_order / 100000.0
        items.append({'step': s, 'unblocks': unb, 'priority_score': score, 'source_layer': s.layer,
                      'action_type': s.id})
    items.sort(key=lambda d: d['priority_score'], reverse=True)
    return {'items': items, 'explore': explore}


def next_steps(venture) -> dict:
    """{'primary': dict|None, 'alternates': [dict], 'explore': bool}. The
    number of alternates is the admin setting fnd_next_alternates."""
    from app.services import founder_config
    n_alt = int(founder_config.get('fnd_next_alternates'))
    r = ranked(venture)
    items, chosen = r['items'], []
    if items:
        div = _diversity() if r['explore'] else None
        if div is not None:
            chosen = div([dict(i) for i in items], 1 + n_alt)
        else:
            chosen = items[:1 + n_alt]
    out = [_card(i['step'], i['unblocks']) for i in chosen]
    return {'primary': out[0] if out else None, 'alternates': out[1:], 'explore': r['explore']}


def _card(step, unblocks: int) -> dict:
    from app.services import founder_registry
    d = step.to_dict()
    d['unblocks'] = unblocks
    d['layer_name'] = founder_registry.LAYER_NAMES.get(step.layer, '')
    d['state_label'] = STATE_LABELS.get(step.state, step.state)
    bits = []
    if unblocks:
        bits.append('Unblocks %d step%s' % (unblocks, '' if unblocks == 1 else 's'))
    bits.append('Effort %d of 5' % int(step.effort or 3))
    d['why'] = ' · '.join(bits)
    d['running'] = _running(step)
    return d


def _running(step) -> bool:
    """True while an agent run on the step is under way."""
    if step.kind != 'agent' or step.state != 'in_progress':
        return False
    from app.services import founder_agent_service
    return founder_agent_service.run_active(step)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def plan_view(venture) -> dict:
    """Lanes per layer with step cards, for the workspace and plan pages."""
    from app.models.founder import VentureAgentOutput, VentureOutreachAction
    from app.services import founder_autonomy, founder_registry
    rows = steps(venture)
    by_key = {step_key(s): s for s in rows}
    inv = _investor_on(venture)
    outputs = {}
    for o in VentureAgentOutput.query.filter_by(venture_id=venture.id).order_by(VentureAgentOutput.version).all():
        outputs[o.plan_step_id] = o
    drafts = {}
    for a in VentureOutreachAction.query.filter_by(venture_id=venture.id).order_by(
            VentureOutreachAction.created_at).all():
        drafts.setdefault(a.plan_step_id, []).append(a)
    lanes = []
    for n, name in founder_registry.LAYER_NAMES.items():
        cards = []
        for s in [x for x in rows if x.layer == n]:
            d = s.to_dict()
            d['state_label'] = STATE_LABELS.get(s.state, s.state)
            d['gate'] = gate_reason(venture, s, inv)
            d['waiting_on'] = [p.title for p in _missing_prereqs(s, by_key)]
            agent = founder_registry.get(s.agent_key) if s.kind == 'agent' else None
            d['agent_label'] = agent['label'] if agent else None
            d['external'] = (agent or {}).get('external', 'none')
            d['running'] = _running(s)
            o = outputs.get(s.id)
            d['output'] = {'id': o.id, 'version': o.version} if o else None
            d['drafts'] = [dict(a.to_dict(), approver=founder_autonomy.approver_name(a),
                                approver_user_id=getattr(founder_autonomy.approver_for(a), 'user_id', None))
                           for a in drafts.get(s.id, []) if a.status in founder_autonomy.PENDING]
            cards.append(d)
        counts = {st: sum(1 for c in cards if c['state'] == st) for st in STATES}
        lanes.append({'layer': n, 'name': name, 'steps': cards, 'counts': counts,
                      'done': counts['done'] + counts['skipped'], 'total': len(cards)})
    total = len(rows)
    finished = sum(1 for s in rows if s.state in FINISHED)
    return {'lanes': lanes, 'total': total, 'finished': finished, 'has_plan': bool(rows)}


def activity(venture, limit: int = 30) -> list:
    """Recent plan events, newest first, as readable lines."""
    import json
    from app.models.founder import VentureStepEvent
    rows = (VentureStepEvent.query.filter_by(venture_id=venture.id)
            .order_by(VentureStepEvent.created_at.desc(), VentureStepEvent.id.desc()).limit(limit).all())
    out = []
    for r in rows:
        try:
            extra = json.loads(r.note) if r.note else {}
        except ValueError:
            extra = {'note': r.note}
        if not isinstance(extra, dict):
            extra = {'note': str(extra)}
        # The run marker duplicates the 'In progress' line written by the same claim.
        if extra.get('event') == 'agent_started' and any(
                o.plan_step_id == r.plan_step_id and o.to_state == 'in_progress' and o.created_at and r.created_at
                and abs((o.created_at - r.created_at).total_seconds()) < 5 for o in rows):
            continue
        title = extra.get('title')
        if extra.get('event') == 'rung_changed' and extra.get('to_rung'):
            # Evaluation (WP2) and the proof path (WP5) move the evidence rung.
            text = 'Evidence rung %s of 5' % extra['to_rung']
            if extra.get('note'):
                text += ': %s' % extra['note']
        elif r.to_state and title:
            text = '%s: %s' % (title, STATE_LABELS.get(r.to_state, r.to_state))
            if extra.get('note') and r.actor_kind != 'system':
                text += ' (%s)' % extra['note']
        else:
            text = extra.get('note') or (extra.get('event') or 'update').replace('_', ' ').capitalize()
        out.append({'id': r.id, 'when': r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '',
                    'iso': r.created_at.isoformat() if r.created_at else '', 'actor': r.actor_kind,
                    'event': extra.get('event'), 'text': text[:300], 'to_state': r.to_state})
    return out
