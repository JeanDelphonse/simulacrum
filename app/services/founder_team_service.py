"""Founder Track team builder (FOUNDER_PLAN.md section 5, PRD "Startup layers,
agent catalog and parameters").

Each open finding (not a strength) points at one founder agent: the finding's
recommended_agent, or the first catalog agent for its rubric dimension
(founder_registry.DIMENSION_AGENTS). build() keeps one agent_blueprint per
such agent, so only agents tied to a finding are recommended.

Recommended parameters come from the catalog default_params, the evaluation
(scope), the admin caps (budget, actions per day) and the autonomy matrix row
for the agent's action_type_class. The blueprint keeps the recommended values;
a founder's change is an agent_param_override row (per agent, or for a whole
layer) holding the previous and the new value. The recommended value always
stays visible next to the one in force.

Override rules: autonomy can always be lowered (to 1, advisor only); raising
is limited by the matrix maximum and the first-contact cap. The value in
force is re-clamped to today's limits whenever it is read, so an admin who
lowers the matrix lowers every venture with it.

    build(venture) -> [AgentBlueprint]
    override(venture, user, param_key, new_value, blueprint_id=None, layer=None)
    accept(venture, user, layer)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TEXT_MAX = 500
SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2}


class TeamError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Recommended parameters
# ---------------------------------------------------------------------------

def agent_for_finding(finding):
    """The catalog agent a finding points at, or None (strengths, unknown)."""
    from app.services import founder_registry
    if finding is None or finding.ftype == 'strength':
        return None
    key = finding.recommended_agent
    if key and founder_registry.get(key):
        return key
    cands = founder_registry.DIMENSION_AGENTS.get(finding.dimension) or []
    return cands[0] if cands else None


def recommended_params(agent_key: str, finding=None, venture=None) -> dict:
    from app.services import founder_autonomy, founder_config, founder_registry
    agent = founder_registry.get(agent_key) or {}
    p = founder_registry.default_params(agent_key)
    for k in founder_registry.PARAM_KEYS:
        p.setdefault(k, None)
    p['autonomy_level'] = founder_autonomy.default_level(agent_key)
    scope = dict(p.get('scope') or {})
    if venture is not None:
        scope.setdefault('stage', venture.stage)
    if finding is not None:
        scope['focus'] = finding.title
        scope['dimension'] = finding.dimension
        if finding.citation_ref:
            scope['source'] = finding.citation_ref
    p['scope'] = scope
    if p.get('budget_cap') is None:
        p['budget_cap'] = founder_config.get('fnd_budget_cap_cents')
    if agent.get('external', 'none') != 'none':
        cap = founder_config.get('fnd_action_cap_per_day')
        p['action_cap'] = cap if p.get('action_cap') is None else min(int(p['action_cap']), cap)
    return p


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _open_findings(venture) -> list:
    from app.models.founder import EvaluationFinding
    rows = EvaluationFinding.query.filter(EvaluationFinding.venture_id == venture.id,
                                          EvaluationFinding.superseded_at.is_(None),
                                          EvaluationFinding.ftype != 'strength').all()
    return sorted(rows, key=lambda f: (SEVERITY_RANK.get(f.severity, 3), -(f.confidence or 0), f.created_at or 0,
                                       f.id))


def blueprints(venture, layer=None) -> list:
    from app.models.founder import AgentBlueprint
    from app.services import founder_registry
    order = {k: i for i, k in enumerate(founder_registry.keys())}
    q = AgentBlueprint.query.filter_by(venture_id=venture.id)
    if layer is not None:
        q = q.filter_by(layer=int(layer))
    return sorted(q.all(), key=lambda b: (b.layer, order.get(b.agent_key, 999)))


def build(venture, user=None) -> list:
    """Recommend one blueprint per agent tied to an open finding. Idempotent:
    existing blueprints keep their status and overrides; a recommended
    blueprint whose findings are all resolved is removed; an accepted one
    stays (the plan may depend on it) with its finding link cleared."""
    from app.extensions import db
    from app.models.founder import AgentBlueprint
    from app.services import founder_registry, founder_ventures
    tied = {}
    for f in _open_findings(venture):
        key = agent_for_finding(f)
        if key and key not in tied:
            tied[key] = f
    existing = {b.agent_key: b for b in AgentBlueprint.query.filter_by(venture_id=venture.id).all()}
    added = removed = 0
    for key, f in tied.items():
        agent = founder_registry.get(key)
        bp = existing.get(key)
        if bp is None:
            db.session.add(AgentBlueprint(venture_id=venture.id, agent_key=key, layer=int(agent['layer']),
                                          status='recommended', finding_id=f.id,
                                          params=recommended_params(key, f, venture)))
            added += 1
        else:
            bp.finding_id = f.id
            bp.params = recommended_params(key, f, venture)
    for key, bp in existing.items():
        if key in tied:
            continue
        if bp.status == 'recommended':
            db.session.delete(bp)
            removed += 1
        else:
            bp.finding_id = None
    founder_ventures.log_event(venture.id, 'team_built', '%d agents recommended, %d added, %d removed'
                               % (len(tied), added, removed), user_id=getattr(user, 'id', None))
    _publish(venture.id, 'team_built', {'note': 'Team updated: %d agents tied to your findings' % len(tied)},
             user)
    db.session.commit()
    return blueprints(venture)


# ---------------------------------------------------------------------------
# Effective parameters and overrides
# ---------------------------------------------------------------------------

def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _loads(text):
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _overrides_for(venture_id: str, bp) -> list:
    """Overrides that apply to a blueprint, oldest first."""
    from app.models.founder import AgentParamOverride
    rows = AgentParamOverride.query.filter(
        AgentParamOverride.venture_id == venture_id,
        ((AgentParamOverride.blueprint_id == bp.id)
         | ((AgentParamOverride.blueprint_id.is_(None)) & (AgentParamOverride.layer == bp.layer)))).all()
    return sorted(rows, key=lambda r: (r.created_at, r.id))


def _clamp_autonomy(agent_key: str, level) -> int:
    from app.services import founder_autonomy
    lim = founder_autonomy.limits(agent_key)
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = lim['default']
    return max(founder_autonomy.MIN_LEVEL if lim['max'] else 0, min(level, lim['max']))


def _clamp(agent_key: str, key: str, value):
    from app.services import founder_config
    if key == 'autonomy_level':
        return _clamp_autonomy(agent_key, value)
    if key == 'budget_cap' and value is not None:
        return min(int(value), founder_config.get('fnd_budget_cap_cents'))
    if key == 'action_cap' and value is not None:
        return min(int(value), founder_config.get('fnd_action_cap_per_day'))
    return value


def effective(venture, bp) -> dict:
    """{'recommended', 'current', 'overridden': {key: source}} for a blueprint.
    The latest override wins (per agent or layer-wide)."""
    rec = dict(bp.params or recommended_params(bp.agent_key, None, venture))
    cur, src = dict(rec), {}
    for r in _overrides_for(venture.id, bp):
        cur[r.param_key] = _loads(r.new_value)
        src[r.param_key] = 'agent' if r.blueprint_id else 'layer'
    for k in list(cur):
        cur[k] = _clamp(bp.agent_key, k, cur[k])
    rec['autonomy_level'] = _clamp_autonomy(bp.agent_key, rec.get('autonomy_level'))
    return {'recommended': rec, 'current': cur, 'overridden': src}


def effective_params(venture, agent_key: str) -> dict:
    """Parameters in force for an agent: its blueprint's, or the recommended
    defaults (layer-wide overrides applied) when it has none."""
    from app.models.founder import AgentBlueprint
    from app.services import founder_registry
    bp = AgentBlueprint.query.filter_by(venture_id=venture.id, agent_key=agent_key).first()
    if bp is None:
        agent = founder_registry.get(agent_key)
        if agent is None:
            raise TeamError('Unknown agent.', 404)
        bp = AgentBlueprint(venture_id=venture.id, agent_key=agent_key, layer=int(agent['layer']),
                            params=recommended_params(agent_key, None, venture))
        bp.id = '__none__'
    return effective(venture, bp)['current']


def effective_param(venture, agent_key: str, key: str):
    return effective_params(venture, agent_key).get(key)


def _validate(key: str, value, agent_keys: list, current: dict) -> tuple:
    """(clean value, note). Raises TeamError."""
    from app.services import founder_autonomy, founder_config, founder_registry
    if key not in founder_registry.PARAM_KEYS:
        raise TeamError('Unknown parameter.')
    if key == 'autonomy_level':
        try:
            level = int(value)
        except (TypeError, ValueError):
            raise TeamError('Autonomy is a level from 1 to 4.')
        if level < founder_autonomy.MIN_LEVEL or level > 4:
            raise TeamError('Autonomy is a level from 1 to 4.')
        allowed = [k for k in agent_keys
                   if level <= int(current.get(k, {}).get('autonomy_level') or 0) or founder_autonomy.can_raise(k, level)]
        if not allowed:
            top = max(founder_autonomy.limits(k)['max'] for k in agent_keys) if agent_keys else 0
            raise TeamError('Autonomy can be raised to level %d at most here (%s). You can always lower it.'
                            % (top, founder_autonomy.LEVEL_LABELS.get(top, '')), 403)
        clamped = [k for k in agent_keys if k not in allowed or level > founder_autonomy.limits(k)['max']]
        note = ''
        if clamped:
            note = '%d agent%s stay%s at a lower level because of the autonomy limits.' % (
                len(clamped), '' if len(clamped) == 1 else 's', 's' if len(clamped) == 1 else '')
        return level, note
    if key in ('budget_cap', 'action_cap'):
        if value in (None, ''):
            raise TeamError('Enter a whole number.')
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise TeamError('Enter a whole number.')
        cap = founder_config.get('fnd_budget_cap_cents' if key == 'budget_cap' else 'fnd_action_cap_per_day')
        if n < 0:
            raise TeamError('The cap cannot be negative.')
        if n > cap:
            raise TeamError('The %s cannot go above %d (the platform limit).'
                            % ('budget cap (cents)' if key == 'budget_cap' else 'daily action cap', cap), 403)
        return n, ''
    if key == 'scope':
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip().startswith('{') else _parse_scope(value)
            except ValueError:
                value = {'criteria': value.strip()}
        if not isinstance(value, dict):
            raise TeamError('Scope must be a set of criteria.')
        clean = {}
        for k, v in list(value.items())[:12]:
            k = str(k).strip()[:40]
            if k:
                clean[k] = v if isinstance(v, (int, float, bool)) or v is None else str(v)[:TEXT_MAX]
        return clean, ''
    text = ' '.join(str(value or '').split())
    if not text:
        raise TeamError('Enter a value.')
    if len(text) > TEXT_MAX:
        raise TeamError('Keep it under %d characters.' % TEXT_MAX)
    return text, ''


def _scope_value(v: str):
    v = v.strip()
    if v.lstrip('-').isdigit() and len(v) < 16:
        return int(v)
    if v.lower() in ('true', 'false'):
        return v.lower() == 'true'
    return v


def _parse_scope(text: str) -> dict:
    """Read the scope as it is shown ('key: value; key: value', keys with
    spaces for underscores) back into separate criteria. Text that doesn't
    follow that shape is kept whole as {'criteria': text}."""
    text = (text or '').strip()
    out = {}
    for part in [p for p in text.split(';') if p.strip()]:
        if ':' not in part:
            return {'criteria': text}
        k, v = part.split(':', 1)
        k = '_'.join(k.strip().lower().split())
        if not k or len(k) > 40 or not k.replace('_', '').isalnum() or not v.strip():
            return {'criteria': text}
        out[k] = _scope_value(v)
    return out or {'criteria': text}


def override(venture, user, param_key: str, new_value, blueprint_id: str = None, layer=None) -> dict:
    """Change one parameter for an agent (blueprint_id) or a whole layer.
    Logged with the previous value. Returns {'override_id', 'previous',
    'value', 'note'}. Raises TeamError."""
    from app.extensions import db
    from app.models.founder import AgentBlueprint, AgentParamOverride
    from app.services import founder_ventures
    if bool(blueprint_id) == (layer is not None and layer != ''):
        raise TeamError('Choose one agent or one layer.')
    uid = getattr(user, 'id', None)
    if founder_ventures.get_member(venture, uid) is None:
        raise TeamError('Only the founders can change agent settings.', 403)
    if blueprint_id:
        bp = AgentBlueprint.query.filter_by(id=blueprint_id, venture_id=venture.id).first()
        if bp is None:
            raise TeamError('Agent not found.', 404)
        targets = [bp]
        layer_n = None
    else:
        try:
            layer_n = int(layer)
        except (TypeError, ValueError):
            raise TeamError('Unknown layer.')
        if layer_n not in (1, 2, 3, 4, 5):
            raise TeamError('Unknown layer.')
        targets = blueprints(venture, layer_n)
        if not targets:
            raise TeamError('No agents in this layer yet.', 404)
    current = {b.agent_key: effective(venture, b)['current'] for b in targets}
    clean, note = _validate(param_key, new_value, [b.agent_key for b in targets], current)
    if blueprint_id:
        previous = current[targets[0].agent_key].get(param_key)
    else:
        last = (AgentParamOverride.query.filter_by(venture_id=venture.id, blueprint_id=None, layer=layer_n,
                                                   param_key=param_key)
                .order_by(AgentParamOverride.created_at.desc()).first())
        previous = _loads(last.new_value) if last else {b.agent_key: current[b.agent_key].get(param_key)
                                                        for b in targets}
    row = AgentParamOverride(venture_id=venture.id, blueprint_id=blueprint_id or None, layer=layer_n,
                             param_key=param_key, previous_value=_dumps(previous), new_value=_dumps(clean),
                             user_id=uid)
    db.session.add(row)
    db.session.flush()
    founder_ventures.log_event(venture.id, 'param_override', '%s %s: %s -> %s' % (
        targets[0].agent_key if blueprint_id else 'L%d' % layer_n, param_key, _dumps(previous)[:150],
        _dumps(clean)[:150]), user_id=uid)
    _publish(venture.id, 'param_override', {'note': 'Changed %s for %s' % (
        param_key.replace('_', ' '), _label(targets[0].agent_key) if blueprint_id else 'layer %d' % layer_n)}, user)
    db.session.commit()
    return {'override_id': row.id, 'previous': previous, 'value': clean, 'note': note}


def accept(venture, user, layer) -> int:
    """Accept every recommended agent in a layer. Returns how many."""
    from app.extensions import db
    from app.services import founder_ventures
    uid = getattr(user, 'id', None)
    if founder_ventures.get_member(venture, uid) is None:
        raise TeamError('Only the founders can accept agents.', 403)
    try:
        layer = int(layer)
    except (TypeError, ValueError):
        raise TeamError('Unknown layer.')
    n = 0
    for bp in blueprints(venture, layer):
        if bp.status == 'recommended':       # declined agents stay declined
            bp.status = 'accepted'
            n += 1
    founder_ventures.log_event(venture.id, 'team_accepted', 'L%d: %d agents accepted' % (layer, n), user_id=uid)
    _publish(venture.id, 'team_accepted', {'note': 'Layer %d team accepted' % layer}, user)
    db.session.commit()
    return n


def set_status(venture, user, blueprint_id: str, status: str):
    """Accept or decline one agent."""
    from app.extensions import db
    from app.models.founder import AgentBlueprint
    from app.services import founder_ventures
    if status not in ('accepted', 'declined', 'recommended'):
        raise TeamError('Unknown status.')
    bp = AgentBlueprint.query.filter_by(id=blueprint_id, venture_id=venture.id).first()
    if bp is None:
        raise TeamError('Agent not found.', 404)
    bp.status = status
    founder_ventures.log_event(venture.id, 'team_' + status, bp.agent_key, user_id=getattr(user, 'id', None))
    db.session.commit()
    return bp


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

def _label(key: str) -> str:
    from app.services import founder_registry
    a = founder_registry.get(key)
    return a['label'] if a else key


def _fmt(key: str, value) -> str:
    from app.services import founder_autonomy
    if value is None or value == '' or value == {}:
        return 'Not set'
    if key == 'autonomy_level':
        return '%s · %s' % (value, founder_autonomy.LEVEL_LABELS.get(int(value), ''))
    if key == 'budget_cap':
        return '$%s' % format(int(value) / 100.0, ',.2f') if int(value) else '$0 (no spend)'
    if key == 'action_cap':
        return '%s a day' % value
    if isinstance(value, dict):
        shown = ['%s: %s' % (k.replace('_', ' '), v) for k, v in value.items() if v not in (None, '', [], {})]
        return '; '.join(shown) or 'Not set'
    return str(value)


PARAM_LABELS = {
    'autonomy_level': 'Autonomy',
    'scope': 'Scope',
    'budget_cap': 'Budget cap',
    'action_cap': 'Action cap',
    'stopping_rule': 'Stopping rule',
    'voice': 'Voice',
    'success_metric': 'Success metric',
}


def view(venture) -> dict:
    """Everything team.html shows, by layer."""
    from app.models.founder import AgentParamOverride, EvaluationFinding
    from app.services import founder_autonomy, founder_registry
    bps = blueprints(venture)
    finding_ids = [b.finding_id for b in bps if b.finding_id]
    findings = {f.id: f for f in EvaluationFinding.query.filter(EvaluationFinding.id.in_(finding_ids or ['-'])).all()}
    history = AgentParamOverride.query.filter_by(venture_id=venture.id).order_by(
        AgentParamOverride.created_at.desc()).limit(50).all()
    layers = []
    for n, name in founder_registry.LAYER_NAMES.items():
        items = []
        for b in [x for x in bps if x.layer == n]:
            agent = founder_registry.get(b.agent_key) or {}
            eff = effective(venture, b)
            lim = founder_autonomy.limits(b.agent_key)
            f = findings.get(b.finding_id)
            params = []
            for k in founder_registry.PARAM_KEYS:
                rec, cur = eff['recommended'].get(k), eff['current'].get(k)
                params.append({'key': k, 'label': PARAM_LABELS[k], 'recommended': _fmt(k, rec),
                               'current': _fmt(k, cur), 'raw': cur, 'changed': _dumps(rec) != _dumps(cur)})
            items.append({
                'id': b.id, 'agent_key': b.agent_key, 'label': agent.get('label', b.agent_key),
                'description': agent.get('description', ''), 'status': b.status,
                'external': agent.get('external', 'none'), 'effort': agent.get('effort'),
                'finding': ({'title': f.title, 'severity': f.severity, 'ftype': f.ftype,
                             'citation_text': f.citation_text, 'citation_ref': f.citation_ref} if f else None),
                'params': params, 'autonomy': int(eff['current'].get('autonomy_level') or 0),
                'autonomy_recommended': int(eff['recommended'].get('autonomy_level') or 0),
                'autonomy_max': lim['max'],
                'levels': [(lv, founder_autonomy.LEVEL_LABELS[lv]) for lv in range(1, 5)],
            })
        layers.append({'layer': n, 'name': name, 'agents': items,
                       'pending': sum(1 for i in items if i['status'] == 'recommended')})
    hist = []
    for r in history:
        who = r.blueprint_id and next((b.agent_key for b in bps if b.id == r.blueprint_id), None)
        hist.append({'when': r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '',
                     'target': _label(who) if who else ('Layer %s' % r.layer if r.layer else 'Agent'),
                     'param': PARAM_LABELS.get(r.param_key, r.param_key),
                     'previous': _short(_loads(r.previous_value)), 'value': _short(_loads(r.new_value))})
    return {'layers': layers, 'total': len(bps), 'history': hist,
            'has_findings': bool(_open_findings(venture))}


def _short(v) -> str:
    if isinstance(v, dict):
        return '; '.join('%s: %s' % (k, val) for k, val in list(v.items())[:4])[:120]
    return ('' if v is None else str(v))[:120]


def _publish(venture_id, event, data, user=None):
    try:
        from app.services import founder_stream
        d = dict(data, actor_kind='founder' if user is not None else 'system')
        if user is not None:
            d['actor_user_id'] = getattr(user, 'id', None)
        founder_stream.publish(venture_id, event, d, commit=False)
    except Exception as exc:
        logger.debug('founder stream publish skipped: %s', exc)
