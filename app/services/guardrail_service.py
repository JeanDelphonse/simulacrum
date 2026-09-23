"""PRD-SIM-OBS-001 §9 — from observability to guardrails.

An admin watches what the agents do, spots a pattern, and writes a rule. This
module is what then runs the rule.

Where a rule is evaluated follows the PRD exactly:

  Post-agent (the default).  The completed run is evaluated — its parameters,
      the tools it used, the model it ran on and the artifact it produced. A
      `block` withholds delivery and quarantines the artifact; it does not stop
      the run, because by then the run has happened.

  At dispatch (carve-out one).  Rate limits. They are cheap, and a post-hoc
      check cannot claw back the tokens a runaway agent already spent.

  At the tool-call boundary (carve-out two).  Guardrails on side-effecting
      tools. A real outbound send or an external write is irreversible, so it
      is checked before it commits, not after.

Posture, again from the PRD: capture fails open, enforcement fails closed. If a
`block` rule throws while being evaluated, the output is withheld. If a `log` or
`warn` rule throws, it is skipped and the run proceeds.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import func, insert, select, update

from app.models.observability import (
    ACTION_BLOCK, ACTION_LOG, ACTION_WARN, GuardrailRule, ObservabilityFlag,
    RULE_CONTENT_POLICY, RULE_MODEL_CONSTRAINT, RULE_OUTPUT_FILTER,
    RULE_PARAM_BOUND, RULE_RATE_LIMIT, RULE_TOOL_ALLOWLIST, RULE_TYPES,
    SEVERITIES, AgentRun,
)
from app.services import observability_service as obs
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


# What the user sees instead of a withheld output. Never an error page:
# a blocked result reads as held for review (PRD §9, withheld-result UX).
HELD_NOTICE = (
    '## Held for review\n\n'
    'This result was generated but is being reviewed before it is released. '
    'Nothing is lost — it will appear here once the review clears, usually '
    'within a business day. You do not need to re-run anything.\n'
)

# Tools that reach outside the platform. A guardrail on one of these is checked
# before the call commits, because the call cannot be taken back.
SIDE_EFFECTING_TOOLS = (
    'outbound_email_send',
    'external_publish',
    'external_write',
)


class ToolBlocked(Exception):
    """A side-effecting tool call a guardrail stopped at the boundary."""


class Decision(object):
    """The outcome of one evaluation pass."""

    __slots__ = ('blocked', 'flags', 'model_override')

    def __init__(self, blocked=False, flags=None, model_override=None):
        self.blocked = blocked
        self.flags = flags or []
        self.model_override = model_override

    @property
    def reason(self) -> str:
        return '; '.join(f['reason'] for f in self.flags if f.get('reason'))[:500]

    def __bool__(self):
        return not self.blocked


# ---------------------------------------------------------------------------
# Rule lookup
# ---------------------------------------------------------------------------

def _enabled() -> bool:
    return obs.setting('obs_guardrails_enabled')


def _effective_action(rule) -> str:
    """`block` degrades to `warn` while enforcement is switched off globally.

    This is the staging path: an admin can promote a rule to block, watch what
    it would have withheld, and only then turn enforcement on.
    """
    if rule.action == ACTION_BLOCK and not obs.setting('obs_enforcement_enabled'):
        return ACTION_WARN
    return rule.action


def rules_for(layer, agent_key, rule_types=None) -> list:
    """Enabled rules whose scope covers this agent, narrowest scope last."""
    try:
        query = GuardrailRule.query.filter_by(is_enabled=True)
        if rule_types:
            query = query.filter(GuardrailRule.rule_type.in_(list(rule_types)))
        rules = [r for r in query.all() if r.applies_to(layer, agent_key)]
        order = {'global': 0, 'layer': 1, 'agent': 2}
        rules.sort(key=lambda r: order.get(r.scope, 0))
        return rules
    except Exception as exc:
        logger.warning('guardrail rule lookup failed: %s', exc)
        return []


# ---------------------------------------------------------------------------
# Flag + counter persistence
# ---------------------------------------------------------------------------

def _persist(rule_id, evaluated=1, fired=0, blocked=0):
    """Bump a rule's effect counters, off the caller's transaction."""
    obs.write_out([update(GuardrailRule).where(GuardrailRule.id == rule_id).values(
        eval_count=GuardrailRule.eval_count + evaluated,
        fire_count=GuardrailRule.fire_count + fired,
        block_count=GuardrailRule.block_count + blocked,
    )])


def record_flag(rule, reason, target_type, target_id, run=None, action=None,
                layer=None, agent_key=None, user_id=None) -> dict:
    """Write one flag and return it in the shape the console reads."""
    action = action or _effective_action(rule)
    flag = {
        'id': generate_id(),
        'target_type': target_type,
        'target_id': target_id or (run.id if run else ''),
        'run_id': run.id if run else None,
        'rule_id': rule.id,
        'rule_name': rule.name,
        'layer': layer or (run.layer if run else None),
        'agent_key': agent_key or (run.agent_key if run else None),
        'user_id': user_id or (run.user_id if run else None),
        'severity': rule.severity,
        'action': action,
        'reason': obs.summarize(reason, 480),
        'created_at': datetime.utcnow(),
    }
    obs.write_out([insert(ObservabilityFlag).values(**flag)])
    if run is not None:
        obs.mark_flagged(run.id)
    return flag


# ---------------------------------------------------------------------------
# Rule evaluators. Each returns a reason string when the rule fires, else None.
# ---------------------------------------------------------------------------

_OPS = {
    '<=': lambda a, b: a <= b,
    '>=': lambda a, b: a >= b,
    '<':  lambda a, b: a < b,
    '>':  lambda a, b: a > b,
    '==': lambda a, b: a == b,
    '!=': lambda a, b: a != b,
}


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(',', '').replace('$', '').replace('%', '').strip())
    except (TypeError, ValueError):
        return None


def _eval_param_bound(rule, context):
    """{"field": "invest_pct", "min": 0, "max": 0.55} or {"enum": [...]}"""
    definition = rule.definition
    field = definition.get('field')
    if not field:
        return None
    params = context.get('params') or {}
    if field not in params:
        return None
    raw = params[field]

    if definition.get('enum'):
        allowed = [str(v) for v in definition['enum']]
        if str(raw) not in allowed:
            return '%s is "%s", which is not one of %s' % (field, raw, ', '.join(allowed))
        return None

    number = _as_number(raw)
    if number is None:
        return None
    low, high = definition.get('min'), definition.get('max')
    if low is not None and number < float(low):
        return '%s is %s, below the floor of %s' % (field, number, low)
    if high is not None and number > float(high):
        return '%s is %s, above the cap of %s' % (field, number, high)
    return None


def _eval_tool_allowlist(rule, context):
    """{"mode": "deny"|"allow", "tools": ["outbound_email_send"]}"""
    definition = rule.definition
    listed = {str(t).strip() for t in (definition.get('tools') or []) if str(t).strip()}
    if not listed:
        return None
    used = set(context.get('tools') or [])
    if not used:
        return None
    if definition.get('mode', 'deny') == 'allow':
        stray = sorted(used - listed)
        if stray:
            return 'called %s, which is outside the allow-list' % ', '.join(stray)
        return None
    hit = sorted(used & listed)
    if hit:
        return 'called denied tool%s %s' % ('s' if len(hit) > 1 else '', ', '.join(hit))
    return None


def _eval_content(rule, context):
    """{"forbid": [...], "require": [...], "regex": [...], "max_chars": N}

    Shared by content_policy and output_filter — they differ in intent, not in
    machinery: one is about what an artifact must not say, the other about what
    it must carry (a disclaimer) and how big it may be.
    """
    definition = rule.definition
    text = context.get('artifact') or ''
    if not text:
        return None
    lowered = text.lower()

    for phrase in (definition.get('forbid') or []):
        needle = str(phrase).strip().lower()
        if needle and needle in lowered:
            return 'artifact contains the forbidden phrase "%s"' % phrase

    for pattern in (definition.get('regex') or []):
        try:
            if re.search(str(pattern), text, re.IGNORECASE):
                return 'artifact matches the blocked pattern /%s/' % pattern
        except re.error:
            # A malformed pattern is the admin's typo, not the agent's fault.
            logger.warning('guardrail %s has an invalid regex: %s', rule.name, pattern)

    missing = [str(p) for p in (definition.get('require') or [])
               if str(p).strip().lower() not in lowered]
    if missing:
        return 'artifact is missing required text: %s' % ', '.join(missing)

    cap = definition.get('max_chars')
    if cap and len(text) > int(cap):
        return 'artifact is %d characters, over the %s cap' % (len(text), cap)
    return None


def _eval_model_constraint(rule, context):
    """{"allowed": ["claude-sonnet-4-6"], "pin_to": "claude-sonnet-4-6"}"""
    allowed = [str(m).strip() for m in (rule.definition.get('allowed') or []) if str(m).strip()]
    model = context.get('model')
    if not allowed or not model:
        return None
    if model in allowed:
        return None
    return 'ran on %s, which is not an approved model for this scope' % model


_EVALUATORS = {
    RULE_PARAM_BOUND:      _eval_param_bound,
    RULE_TOOL_ALLOWLIST:   _eval_tool_allowlist,
    RULE_CONTENT_POLICY:   _eval_content,
    RULE_OUTPUT_FILTER:    _eval_content,
    RULE_MODEL_CONSTRAINT: _eval_model_constraint,
}


# ---------------------------------------------------------------------------
# 1. Dispatch gate — rate limits, and model pinning
# ---------------------------------------------------------------------------

def check_dispatch(agent_key, layer, user_id=None, model=None, run=None) -> Decision:
    """Evaluated before the agent runs. Returns a Decision; never raises."""
    decision = Decision()
    if not _enabled():
        return decision
    try:
        layer = obs.layer_code(layer)
        for rule in rules_for(layer, agent_key, (RULE_RATE_LIMIT, RULE_MODEL_CONSTRAINT)):
            try:
                if rule.rule_type == RULE_RATE_LIMIT:
                    reason = _check_rate_limit(rule, agent_key, layer, user_id)
                else:
                    # Pinning is the only part of a model constraint that has to
                    # happen here: substituting an approved model after the call
                    # would mean nothing. The compliance check itself runs
                    # post-agent with everything else.
                    pin = rule.definition.get('pin_to')
                    allowed = rule.definition.get('allowed') or []
                    if pin and model and allowed and model not in allowed:
                        decision.model_override = pin
                    continue
            except Exception as exc:
                logger.warning('dispatch rule %s failed: %s', rule.name, exc)
                # Fail closed only for a rule that would have blocked.
                if _effective_action(rule) == ACTION_BLOCK:
                    decision.blocked = True
                    decision.flags.append(record_flag(
                        rule, 'rule evaluation failed: %s' % exc, 'run', run.id if run else '',
                        run=run, layer=layer, agent_key=agent_key, user_id=user_id))
                continue

            _persist(rule.id)
            if not reason:
                continue
            action = _effective_action(rule)
            _persist(rule.id, evaluated=0, fired=1, blocked=1 if action == ACTION_BLOCK else 0)
            decision.flags.append(record_flag(
                rule, reason, 'run', run.id if run else '', run=run,
                layer=layer, agent_key=agent_key, user_id=user_id))
            if action == ACTION_BLOCK:
                decision.blocked = True
    except Exception as exc:
        # A failure of the gate itself is a capture-side failure: fail open, so
        # a bug here cannot take the product down.
        logger.warning('dispatch guardrail pass failed for %s: %s', agent_key, exc)
    return decision


def _check_rate_limit(rule, agent_key, layer, user_id):
    """{"max_runs": 10, "per_minutes": 60, "per": "user"|"global"}"""
    definition = rule.definition
    try:
        limit = int(definition.get('max_runs', 0))
        minutes = int(definition.get('per_minutes', 60))
    except (TypeError, ValueError):
        return None
    if limit <= 0 or minutes <= 0:
        return None

    since = datetime.utcnow() - timedelta(minutes=minutes)
    stmt = select(func.count()).select_from(AgentRun).where(
        AgentRun.started_at >= since,
        AgentRun.status != 'blocked',
    )
    # Scope the count the same way the rule is scoped, so a per-layer rule
    # throttles the layer and a per-agent rule throttles the agent.
    if rule.scope == 'agent':
        stmt = stmt.where(AgentRun.agent_key == agent_key)
    elif rule.scope == 'layer':
        stmt = stmt.where(AgentRun.layer == layer)
    if definition.get('per', 'user') == 'user' and user_id:
        stmt = stmt.where(AgentRun.user_id == user_id)

    # The run being dispatched already has its header open, so it is in the
    # count — the limit is "this many in the window", inclusive.
    count = obs.read_scalar(stmt, 0)
    if count > limit:
        return '%d runs in the last %d minutes, over the limit of %d' % (count, minutes, limit)
    return None


# ---------------------------------------------------------------------------
# 2. Tool-call boundary — side-effecting tools only
# ---------------------------------------------------------------------------

def check_tool_call(tool_name, input_summary=None, run=None):
    """Raise ToolBlocked if a rule denies this tool. Called before the call."""
    if not _enabled() or run is None:
        return
    try:
        rules = rules_for(run.layer, run.agent_key, (RULE_TOOL_ALLOWLIST,))
    except Exception as exc:
        logger.warning('tool guardrail lookup failed for %s: %s', tool_name, exc)
        return

    for rule in rules:
        try:
            reason = _eval_tool_allowlist(rule, {'tools': [tool_name]})
        except Exception as exc:
            logger.warning('tool rule %s failed: %s', rule.name, exc)
            if _effective_action(rule) == ACTION_BLOCK:
                raise ToolBlocked('%s blocked: rule evaluation failed' % tool_name)
            continue

        _persist(rule.id)
        if not reason:
            continue
        action = _effective_action(rule)
        _persist(rule.id, evaluated=0, fired=1, blocked=1 if action == ACTION_BLOCK else 0)
        record_flag(rule, reason, 'tool', run.id, run=run, action=action)
        if action == ACTION_BLOCK:
            obs.record_tool(tool_name, input_summary, 'blocked by %s' % rule.name,
                            0, 'blocked')
            raise ToolBlocked('%s blocked by guardrail %s' % (tool_name, rule.name))


# ---------------------------------------------------------------------------
# 3. Post-agent enforcement — the default path
# ---------------------------------------------------------------------------

def evaluate_run(run, artifact=None, params=None, model=None, tools=None) -> Decision:
    """Evaluate the completed run. A blocked Decision means withhold the output."""
    decision = Decision()
    if not _enabled() or run is None:
        return decision

    context = {
        'artifact': artifact or '',
        'params': params or {},
        'model': model or run.model,
        'tools': tools if tools is not None else [t['tool_name'] for t in run.tools],
    }

    post_types = (RULE_PARAM_BOUND, RULE_TOOL_ALLOWLIST, RULE_OUTPUT_FILTER,
                  RULE_CONTENT_POLICY, RULE_MODEL_CONSTRAINT)
    try:
        rules = rules_for(run.layer, run.agent_key, post_types)
    except Exception as exc:
        logger.warning('post-run guardrail lookup failed: %s', exc)
        return decision

    for rule in rules:
        evaluator = _EVALUATORS.get(rule.rule_type)
        if evaluator is None:
            continue
        try:
            reason = evaluator(rule, context)
        except Exception as exc:
            logger.warning('guardrail %s failed to evaluate: %s', rule.name, exc)
            # Enforcement fails closed: a broken blocking rule blocks.
            if _effective_action(rule) == ACTION_BLOCK:
                decision.blocked = True
                decision.flags.append(record_flag(
                    rule, 'rule evaluation failed: %s' % exc, 'run', run.id, run=run))
                _persist(rule.id, fired=1, blocked=1)
            continue

        _persist(rule.id)
        if not reason:
            continue

        action = _effective_action(rule)
        target_type = ('artifact' if rule.rule_type in (RULE_OUTPUT_FILTER, RULE_CONTENT_POLICY)
                       else 'run')
        target_id = run.id
        if target_type == 'artifact' and run.artifacts:
            target_id = run.artifacts[0]['id']

        decision.flags.append(record_flag(
            rule, reason, target_type, target_id, run=run, action=action))
        _persist(rule.id, evaluated=0, fired=1, blocked=1 if action == ACTION_BLOCK else 0)
        if action == ACTION_BLOCK:
            decision.blocked = True

    return decision


# ---------------------------------------------------------------------------
# Authoring — validation shared by the API and by "create guardrail from this"
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,79}$')


def validate_rule(payload, existing=None) -> dict:
    """Coerce and check an authored rule. Raises ValueError with a readable message."""
    name = str(payload.get('name') or '').strip().lower().replace(' ', '-')
    if not _NAME_RE.match(name):
        raise ValueError('Name must be a lowercase slug, e.g. invest-cap-55.')

    clash = GuardrailRule.query.filter_by(name=name).first()
    if clash and (existing is None or clash.id != existing.id):
        raise ValueError('A rule named "%s" already exists.' % name)

    rule_type = str(payload.get('rule_type') or '').strip()
    if rule_type not in RULE_TYPES:
        raise ValueError('Unknown rule type: %s' % rule_type)

    scope = str(payload.get('scope') or 'global').strip()
    if scope not in ('global', 'layer', 'agent'):
        raise ValueError('Scope must be global, layer or agent.')
    scope_ref = (str(payload.get('scope_ref') or '').strip() or None) if scope != 'global' else None
    if scope != 'global' and not scope_ref:
        raise ValueError('A %s-scoped rule needs a %s.' % (scope, scope))

    action = str(payload.get('action') or ACTION_LOG).strip()
    if action not in (ACTION_LOG, ACTION_WARN, ACTION_BLOCK):
        raise ValueError('Action must be log, warn or block.')

    severity = str(payload.get('severity') or 'medium').strip()
    if severity not in SEVERITIES:
        raise ValueError('Severity must be one of %s.' % ', '.join(SEVERITIES))

    definition = payload.get('definition')
    if isinstance(definition, str):
        try:
            definition = json.loads(definition or '{}')
        except ValueError:
            raise ValueError('Definition must be valid JSON.')
    if not isinstance(definition, dict):
        raise ValueError('Definition must be a JSON object.')

    return {
        'name': name,
        'rule_type': rule_type,
        'scope': scope,
        'scope_ref': scope_ref,
        'action': action,
        'severity': severity,
        'definition': _validate_definition(rule_type, definition),
        'notes': (str(payload.get('notes') or '').strip() or None),
        'is_enabled': payload.get('is_enabled', True) not in (False, 'false', '0', 0),
    }


def _validate_definition(rule_type, definition) -> dict:
    """Reject a definition that would silently never fire."""
    if rule_type == RULE_PARAM_BOUND:
        if not str(definition.get('field') or '').strip():
            raise ValueError('A parameter bound needs a field name.')
        if definition.get('min') is None and definition.get('max') is None \
                and not definition.get('enum'):
            raise ValueError('Set a minimum, a maximum or an allowed set.')
        for key in ('min', 'max'):
            if definition.get(key) is not None:
                try:
                    definition[key] = float(definition[key])
                except (TypeError, ValueError):
                    raise ValueError('%s must be a number.' % key)
        if definition.get('min') is not None and definition.get('max') is not None \
                and definition['min'] > definition['max']:
            raise ValueError('The minimum cannot be above the maximum.')

    elif rule_type == RULE_TOOL_ALLOWLIST:
        tools = [str(t).strip() for t in (definition.get('tools') or []) if str(t).strip()]
        if not tools:
            raise ValueError('List at least one tool.')
        definition['tools'] = tools
        if definition.get('mode', 'deny') not in ('deny', 'allow'):
            raise ValueError('Mode must be deny or allow.')

    elif rule_type in (RULE_CONTENT_POLICY, RULE_OUTPUT_FILTER):
        if not any(definition.get(k) for k in ('forbid', 'require', 'regex', 'max_chars')):
            raise ValueError('Give at least one forbidden phrase, required phrase, '
                             'pattern or size cap.')
        for pattern in (definition.get('regex') or []):
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise ValueError('Invalid pattern /%s/: %s' % (pattern, exc))

    elif rule_type == RULE_RATE_LIMIT:
        try:
            limit = int(definition.get('max_runs', 0))
            minutes = int(definition.get('per_minutes', 0))
        except (TypeError, ValueError):
            raise ValueError('Max runs and the window must be whole numbers.')
        if limit < 1 or minutes < 1:
            raise ValueError('Set a positive run limit and a positive window.')
        definition['max_runs'], definition['per_minutes'] = limit, minutes
        if definition.get('per', 'user') not in ('user', 'global'):
            raise ValueError('Rate limits count per user or globally.')

    elif rule_type == RULE_MODEL_CONSTRAINT:
        allowed = [str(m).strip() for m in (definition.get('allowed') or []) if str(m).strip()]
        if not allowed:
            raise ValueError('List at least one approved model.')
        definition['allowed'] = allowed
        pin = str(definition.get('pin_to') or '').strip()
        if pin and pin not in allowed:
            raise ValueError('The pinned model has to be one of the approved models.')
        definition['pin_to'] = pin or None

    return definition


def promotion_warning(rule, target_action) -> str:
    """The overfitting guard (PRD §10). Advisory — it never refuses the change.

    A rule hardened around a handful of outlier runs is the failure mode this
    exists to name, so the console shows this before an admin promotes to block.
    """
    if target_action != ACTION_BLOCK:
        return ''
    minimum = obs.setting('obs_min_observations_to_block')
    if (rule.eval_count or 0) >= minimum:
        return ''
    return ('This rule has been evaluated %d times; %d observations are the '
            'configured minimum before blocking. Consider leaving it at warn '
            'until it has seen more runs.' % (rule.eval_count or 0, minimum))
