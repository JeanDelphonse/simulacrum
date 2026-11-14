"""Founder Track observability views and controls (FOUNDER_PLAN.md WP7).

Reads the shared telemetry (agent_runs / agent_run_tools / agent_events /
observability_flags) for runs whose layer is a founder code ('F1'..'F5'), joined
to founder_run_meta for the venture, and the founder tables for everything else.
Nothing here writes to the wealth-layer tables or changes the existing console.

Privacy (PRD "Agent observability"): admins see run metadata by default.
Founder artifact text, contact names and message content are masked unless the
founders granted support access (founder_venture.support_access_until > now),
and every view that shows unmasked content writes an admin_access_audit row.

Controls:
  - pause one founder agent everywhere   -> platform setting (list of agent keys)
  - pause every agent for one venture     -> founder_venture.paused_at
  - re-run a failed run                   -> founder_agent_service.execute(step, trigger='rerun')
The founder dispatcher (WP4) asks dispatch_block_reason(venture, agent_key)
before it runs an agent.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

MASK = '[masked: no support access]'

# admin_access_audit.action values (String(30)); the model's own constants are
# left untouched.
AUDIT_VIEW_TRACE = 'fnd_view_trace'
AUDIT_VIEW_UNMASKED = 'fnd_view_unmasked'

PAUSED_AGENTS_KEY = 'founder_obs_paused_agents'

# Support access the founders can grant. The admin can change both through
# platform_settings; these are the defaults.
SUPPORT_DAYS_KEY = 'founder_support_access_days'
SUPPORT_MAX_DAYS_KEY = 'founder_support_access_max_days'
SUPPORT_DAYS_DEFAULT = 7
SUPPORT_MAX_DAYS_DEFAULT = 30

ROW_LIMIT = 300


class ControlError(Exception):
    """A control the admin asked for that can't be applied (status, message)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def founder_layers() -> tuple:
    from app.services import observability_service as obs
    return tuple(getattr(obs, 'FOUNDER_LAYER_CODES', ('F1', 'F2', 'F3', 'F4', 'F5')))


def layer_names() -> dict:
    try:
        from app.services.founder_registry import LAYER_NAMES
    except Exception:
        LAYER_NAMES = {}
    return {'F%d' % n: LAYER_NAMES.get(n, 'Layer %d' % n) for n in range(1, 6)}


def agent_label(key: str) -> str:
    try:
        from app.services import founder_registry
        meta = founder_registry.get(key)
        if meta:
            return meta.get('label') or key
    except Exception:
        pass
    return key or ''


def _iso(value):
    return value.isoformat() if value else None


def _int_setting(key: str, default: int) -> int:
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get(key, None)
        return int(raw) if raw not in (None, '') else default
    except Exception:
        return default


def _user_labels(user_ids) -> dict:
    from app.models.user import User
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    return {u.id: (u.email or '').split('@')[0] or u.full_name or u.id
            for u in User.query.filter(User.id.in_(ids)).all()}


def _since(hours) -> datetime:
    from app.services import observability_service as obs
    return obs.window_start(hours)


# ---------------------------------------------------------------------------
# Support access and masking
# ---------------------------------------------------------------------------

def support_days() -> tuple:
    """(default days, maximum days) a founder may grant."""
    max_days = max(1, _int_setting(SUPPORT_MAX_DAYS_KEY, SUPPORT_MAX_DAYS_DEFAULT))
    default = min(max(1, _int_setting(SUPPORT_DAYS_KEY, SUPPORT_DAYS_DEFAULT)), max_days)
    return default, max_days


def support_access_active(venture, now=None) -> bool:
    until = getattr(venture, 'support_access_until', None)
    return bool(until and until > (now or datetime.utcnow()))


def set_support_access(venture, user, grant: bool, days=None):
    """Grant (for `days`, bounded) or revoke admin support access. Commits."""
    from app.extensions import db
    from app.services import founder_ventures
    if grant:
        default, max_days = support_days()
        try:
            days = int(days) if days not in (None, '') else default
        except (TypeError, ValueError):
            raise ControlError('Choose a number of days.')
        if days < 1 or days > max_days:
            raise ControlError('Support access can last 1 to %d days.' % max_days)
        venture.support_access_until = datetime.utcnow() + timedelta(days=days)
        founder_ventures.log_event(venture.id, 'support_access_granted', '%d days' % days, user_id=user.id)
    else:
        venture.support_access_until = None
        founder_ventures.log_event(venture.id, 'support_access_revoked', '', user_id=user.id)
    db.session.commit()
    return support_status(venture)


def support_status(venture) -> dict:
    default, max_days = support_days()
    return {'active': support_access_active(venture), 'until': _iso(venture.support_access_until)
            if support_access_active(venture) else None, 'default_days': default, 'max_days': max_days}


def mask(text, unmasked: bool):
    """The text when unmasked; otherwise a placeholder (empty stays empty)."""
    if unmasked or text in (None, ''):
        return text
    return MASK


def _audit(admin_id, action, target, subject_user_id=None):
    from app.services import observability_service as obs
    obs.audit(admin_id, action, target, subject_user_id)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

def paused_agents() -> list:
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get(PAUSED_AGENTS_KEY, '[]') or '[]'
        value = json.loads(raw) if isinstance(raw, str) else raw
        return sorted({str(k) for k in (value or [])})
    except Exception:
        return []


def agent_paused(agent_key: str) -> bool:
    return bool(agent_key) and agent_key in paused_agents()


def set_agent_paused(agent_key: str, paused: bool, admin_id: str) -> list:
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.platform_settings import PlatformSetting
    from app.services import founder_registry
    if not founder_registry.get(agent_key):
        raise ControlError('Unknown founder agent.', 404)
    keys = set(paused_agents())
    (keys.add if paused else keys.discard)(agent_key)
    PlatformSetting.set(PAUSED_AGENTS_KEY, json.dumps(sorted(keys)), updated_by=admin_id)
    AuditLog.log('founder_agent_paused' if paused else 'founder_agent_resumed', user_id=admin_id,
                 metadata={'agent_key': agent_key})
    db.session.commit()
    return sorted(keys)


def set_venture_paused(venture, paused: bool, admin_id: str):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.services import founder_ventures
    venture.paused_at = datetime.utcnow() if paused else None
    founder_ventures.log_event(venture.id, 'admin_paused' if paused else 'admin_resumed',
                               'All agents paused by an admin' if paused else 'Agents resumed by an admin',
                               user_id=admin_id)
    AuditLog.log('founder_venture_paused' if paused else 'founder_venture_resumed', user_id=admin_id,
                 resource_id=venture.id)
    db.session.commit()
    return venture


def dispatch_block_reason(venture, agent_key: str):
    """Why this agent must not run for this venture right now, or None.
    The founder dispatcher calls this before every run."""
    if venture is not None and getattr(venture, 'paused_at', None):
        return 'All agents for this venture are paused.'
    if agent_paused(agent_key):
        return 'This agent is paused for every venture.'
    return None


def _run_params(run) -> dict:
    """AgentRun.params_json as a dict. founder_agent_service.run_context stores
    {'venture_id', 'plan_step_id', 'autonomy_level'} there for every founder
    run, including the failed ones that never get a founder_run_meta row."""
    raw = getattr(run, 'params_json', None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return raw if isinstance(raw, dict) else {}


def run_ctx(run, meta) -> dict:
    """Venture, step, agent and autonomy level of a founder run: from its
    founder_run_meta row when there is one, otherwise from the run header."""
    p = _run_params(run)
    return {'venture_id': (meta.venture_id if meta is not None else None) or p.get('venture_id'),
            'plan_step_id': (meta.plan_step_id if meta is not None else None) or p.get('plan_step_id'),
            'agent_key': (meta.agent_key if meta is not None else None) or run.agent_key,
            'autonomy_level': meta.autonomy_level if meta is not None else p.get('autonomy_level')}


def _params_mention(value):
    """SQL: the run header's params hold this id as a JSON string value. Ids
    are 9-character keys, so the quoted match is specific; callers that need
    the exact key re-read the params in Python."""
    from sqlalchemy import Text, cast
    from app.models.observability import AgentRun
    safe = str(value).replace('!', '!!').replace('%', '!%').replace('_', '!_')
    return cast(AgentRun.params_json, Text).like('%"' + safe + '"%', escape='!')


def venture_run_clause(venture_id):
    """SQL: an AgentRun belongs to this venture (founder_run_meta, or the run
    header for runs that failed before a meta row was written)."""
    from sqlalchemy import or_
    from app.extensions import db
    from app.models.founder import FounderRunMeta
    from app.models.observability import AgentRun
    return or_(AgentRun.id.in_(db.session.query(FounderRunMeta.obs_run_id)
                               .filter(FounderRunMeta.venture_id == venture_id)),
               _params_mention(venture_id))


def _step_run_clause(plan_step_id):
    from sqlalchemy import or_
    from app.extensions import db
    from app.models.founder import FounderRunMeta
    from app.models.observability import AgentRun
    return or_(AgentRun.id.in_(db.session.query(FounderRunMeta.obs_run_id)
                               .filter(FounderRunMeta.plan_step_id == plan_step_id)),
               _params_mention(plan_step_id))


RERUN_AUDIT = 'founder_obs_rerun'


def _already_rerun(run_id: str) -> bool:
    from app.models.audit_log import AuditLog
    q = AuditLog.query.filter(AuditLog.action == RERUN_AUDIT, AuditLog._metadata.like('%' + run_id + '%'))
    for row in q.limit(20).all():
        try:
            if (json.loads(row._metadata or '{}') or {}).get('obs_run_id') == run_id:
                return True
        except ValueError:
            continue
    return False


def _newer_step_run(run, plan_step_id) -> bool:
    from app.models.observability import AgentRun
    if run.started_at is None:
        return False
    return (AgentRun.query.filter(AgentRun.layer.in_(founder_layers()), AgentRun.id != run.id,
                                  AgentRun.started_at > run.started_at, _step_run_clause(plan_step_id))
            .first() is not None)


def rerun_block(run, step, venture=None, agent_key=None, check_pause=True):
    """(message, status) when this run must not be re-run, or None.

    Only the latest run of a step can be re-run, only once, only while it
    failed and only while its step is back at 'ready' (a failed run puts it
    there). That keeps a stale failure from re-opening a step that has since
    finished, and two admins from paying for the same re-run twice."""
    from app.models.observability import STATUS_ERROR
    if run.status != STATUS_ERROR:
        return 'Only a failed run can be re-run.', 409
    if step is None:
        return 'This run has no plan step to re-run.', 409
    if _already_rerun(run.id):
        return 'This run was already re-run.', 409
    if _newer_step_run(run, step.id):
        return 'This step has run again since. Re-run the latest run if it failed.', 409
    if check_pause:
        reason = dispatch_block_reason(venture, agent_key)
        if reason:
            return reason, 409
    if step.state != 'ready':
        return 'This step is %s, so there is nothing to re-run.' % str(step.state).replace('_', ' '), 409
    return None


def rerun(run_id: str, admin_id: str) -> dict:
    """Re-run the plan step behind a failed founder run."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import FounderRunMeta, FounderVenture, VenturePlanStep
    from app.models.observability import AgentRun
    run = db.session.get(AgentRun, run_id)
    if run is None or run.layer not in founder_layers():
        raise ControlError('Founder run not found.', 404)
    ctx = run_ctx(run, FounderRunMeta.query.filter_by(obs_run_id=run_id).first())
    if not ctx.get('venture_id'):
        raise ControlError('Founder run not found.', 404)
    step = db.session.get(VenturePlanStep, ctx['plan_step_id']) if ctx.get('plan_step_id') else None
    if step is not None and step.venture_id != ctx['venture_id']:
        step = None
    venture = db.session.get(FounderVenture, ctx['venture_id'])
    blocked = rerun_block(run, step, venture, ctx.get('agent_key'))
    if blocked:
        raise ControlError(*blocked)
    try:
        from app.services import founder_agent_service
        execute = founder_agent_service.execute
    except (ImportError, AttributeError):
        raise ControlError('Founder agents are not installed on this server yet.', 503)
    agent_error = getattr(founder_agent_service, 'AgentError', None)
    preflight = getattr(founder_agent_service, 'preflight', None)
    if preflight is not None:
        # Refusals (unpaid, locked, cap...) come before the audit row, so a
        # refused attempt does not use up the one re-run this run gets.
        try:
            preflight(step, venture, 'rerun', allow_states=('ready',))
        except Exception as exc:
            if agent_error is not None and isinstance(exc, agent_error):
                raise ControlError(str(exc), int(getattr(exc, 'status', 409) or 409))
            raise
    AuditLog.log(RERUN_AUDIT, user_id=admin_id, resource_id=ctx['venture_id'],
                 metadata={'obs_run_id': run_id, 'plan_step_id': step.id, 'agent_key': ctx.get('agent_key')})
    db.session.commit()
    try:
        output = execute(step, user=None, trigger='rerun')
    except Exception as exc:
        if agent_error is not None and isinstance(exc, agent_error):
            # The founder dispatcher refused after all: say why.
            raise ControlError(str(exc), int(getattr(exc, 'status', 409) or 409))
        logger.exception('founder re-run of %s failed: %s', run_id, exc)
        raise ControlError('The re-run failed. The new run is in the list with its error.', 502)
    return {'rerun_of': run_id, 'plan_step_id': step.id, 'output_id': getattr(output, 'id', None)}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _run_rows(since, venture_id=None):
    """(AgentRun, FounderRunMeta|None) for founder-layer runs in the window."""
    from app.extensions import db
    from app.models.founder import FounderRunMeta
    from app.models.observability import AgentRun
    q = (db.session.query(AgentRun, FounderRunMeta)
         .outerjoin(FounderRunMeta, FounderRunMeta.obs_run_id == AgentRun.id)
         .filter(AgentRun.layer.in_(founder_layers()), AgentRun.started_at >= since))
    if venture_id:
        q = q.filter(venture_run_clause(venture_id))
    return q.order_by(AgentRun.started_at.desc())


def _bucket():
    return {'runs': 0, 'success': 0, 'errors': 0, 'blocked': 0, 'running': 0, 'latency_total': 0,
            'latency_n': 0, 'cost_cents': 0, 'tokens': [], 'flagged': 0}


def _add(b, run, meta):
    b['runs'] += 1
    status = run.status or ''
    if status == 'success':
        b['success'] += 1
    elif status == 'error':
        b['errors'] += 1
    elif status == 'blocked':
        b['blocked'] += 1
    else:
        b['running'] += 1
    if run.flag_count:
        b['flagged'] += 1
    if run.latency_ms is not None:
        b['latency_total'] += int(run.latency_ms)
        b['latency_n'] += 1
    if meta is not None and meta.cost_cents:
        b['cost_cents'] += int(meta.cost_cents)
    b['tokens'].append((run.model, run.prompt_tokens, run.completion_tokens))


def _finish(b) -> dict:
    from app.services import observability_service as obs
    finished = b['success'] + b['errors'] + b['blocked']
    estimated = obs.estimate_spend(b['tokens'])
    cost = round(b['cost_cents'] / 100.0, 2) if b['cost_cents'] else estimated
    return {'runs': b['runs'], 'success': b['success'], 'errors': b['errors'], 'blocked': b['blocked'],
            'running': b['running'], 'flagged': b['flagged'],
            'success_rate': round(100.0 * b['success'] / finished, 1) if finished else None,
            'error_rate': round(100.0 * b['errors'] / finished, 1) if finished else None,
            'avg_latency_ms': int(b['latency_total'] / b['latency_n']) if b['latency_n'] else None,
            'cost': cost, 'cost_estimated': not b['cost_cents'] and bool(b['runs'])}


def fleet(hours) -> dict:
    since = _since(hours)
    names = layer_names()
    by_layer = {code: _bucket() for code in founder_layers()}
    by_agent, total = {}, _bucket()
    for run, meta in _run_rows(since).limit(20000).all():
        for b in (by_layer.setdefault(run.layer, _bucket()), by_agent.setdefault(run.agent_key, _bucket()), total):
            _add(b, run, meta)
    paused = set(paused_agents())
    layers = [dict(_finish(by_layer[c]), layer=c, layer_name=names.get(c, c)) for c in founder_layers()]
    agents = []
    try:
        from app.services import founder_registry
        catalog = {a['action_type']: a for a in founder_registry.all_agents()}
    except Exception:
        catalog = {}
    # Every catalog agent is listed, run or not, so an admin can pause one
    # before it runs and resume one that a pause has kept out of the window.
    order = {k: n for n, k in enumerate(catalog)}
    for key in list(catalog) + sorted(paused):
        by_agent.setdefault(key, _bucket())
    for key, b in by_agent.items():
        meta = catalog.get(key) or {}
        agents.append(dict(_finish(b), agent_key=key, agent_label=meta.get('label') or key,
                           layer='F%s' % meta['layer'] if meta.get('layer') else None, paused=key in paused))
    agents.sort(key=lambda a: (not a['paused'], -a['runs'], order.get(a['agent_key'], len(order)), a['agent_key']))
    return {'hours': int(hours), 'totals': _finish(total), 'layers': layers, 'agents': agents,
            'paused_agents': sorted(paused)}


def _source_trigger(source):
    """founder_agent_service tags runs 'founder_<trigger>'; show the trigger."""
    source = source or ''
    return source[len('founder_'):] if source.startswith('founder_') else (source or None)


def recent_runs(hours, venture_id=None, status=None, limit=50) -> list:
    from app.extensions import db
    from app.models.founder import FounderVenture, VenturePlanStep
    rows = _run_rows(_since(hours), venture_id)
    if status:
        from app.models.observability import AgentRun
        rows = rows.filter(AgentRun.status == status)
    rows = [(run, meta, run_ctx(run, meta)) for run, meta in rows.limit(limit).all()]
    if venture_id:
        # The params match in SQL is textual; keep only exact venture matches.
        rows = [r for r in rows if r[2]['venture_id'] == venture_id]
    vids = {c['venture_id'] for _, _, c in rows if c['venture_id']}
    vnames = {v.id: v.name for v in FounderVenture.query.filter(FounderVenture.id.in_(vids or ['-'])).all()}
    labels = _user_labels([m.approved_by for _, m, _ in rows if m is not None])
    step_ids = {c['plan_step_id'] for r, _, c in rows if r.status == 'error' and c['plan_step_id']}
    steps = {st.id: st for st in VenturePlanStep.query.filter(VenturePlanStep.id.in_(step_ids or ['-'])).all()}
    out = []
    for run, meta, ctx in rows:
        can_rerun = False
        if run.status == 'error' and ctx['plan_step_id'] and ctx['venture_id']:
            step = steps.get(ctx['plan_step_id'])
            if step is not None and step.venture_id == ctx['venture_id']:
                # Pauses don't hide the button; the re-run says why it is refused.
                can_rerun = rerun_block(run, step, check_pause=False) is None
        out.append({'run_id': run.id, 'agent_key': run.agent_key, 'agent_label': agent_label(run.agent_key),
                    'layer': run.layer, 'status': run.status, 'latency_ms': run.latency_ms,
                    'error_code': run.error_code, 'started_at': _iso(run.started_at),
                    'venture_id': ctx['venture_id'], 'venture_name': vnames.get(ctx['venture_id']),
                    'trigger': meta.trigger if meta else _source_trigger(run.dispatch_source),
                    'autonomy_level': ctx['autonomy_level'],
                    'approved_by': labels.get(meta.approved_by) if meta and meta.approved_by else None,
                    'cost_cents': meta.cost_cents if meta else None,
                    'can_rerun': can_rerun})
    return out


def ventures(hours) -> list:
    """Every venture, with run counts in the window, for the trace picker."""
    from app.extensions import db
    from app.models.founder import FounderRunMeta, FounderVenture
    from sqlalchemy import func
    since = _since(hours)
    counts = dict(db.session.query(FounderRunMeta.venture_id, func.count(FounderRunMeta.id))
                  .filter(FounderRunMeta.created_at >= since).group_by(FounderRunMeta.venture_id).all())
    cost = dict(db.session.query(FounderRunMeta.venture_id, func.coalesce(func.sum(FounderRunMeta.cost_cents), 0))
                .group_by(FounderRunMeta.venture_id).all())
    rows = FounderVenture.query.order_by(FounderVenture.updated_at.desc()).limit(500).all()
    return [{'id': v.id, 'name': v.name, 'stage': v.stage, 'status': v.status, 'rung': v.rung,
             'paid': v.paid_at is not None, 'paused': v.paused_at is not None, 'paused_at': _iso(v.paused_at),
             'support_access': support_access_active(v), 'runs': int(counts.get(v.id, 0)),
             'cost_cents': int(cost.get(v.id, 0) or 0), 'updated_at': _iso(v.updated_at)} for v in rows]


def venture_trace(venture, admin_id, hours=24 * 90) -> dict:
    """Runs, step changes, approvals and evaluations for one venture, newest
    first. Content is masked unless support access is active. Audited."""
    from app.models.founder import (EvaluationRun, VenturePlanStep, VentureOutreachAction, VentureStepEvent)
    unmasked = support_access_active(venture)
    _audit(admin_id, AUDIT_VIEW_UNMASKED if unmasked else AUDIT_VIEW_TRACE, venture.id, venture.owner_user_id)
    since = _since(hours)
    items = []
    for r in recent_runs(hours, venture.id, limit=ROW_LIMIT):
        items.append(dict(r, kind='run', at=r['started_at']))
    steps = {s.id: s for s in VenturePlanStep.query.filter_by(venture_id=venture.id).all()}
    events = (VentureStepEvent.query.filter(VentureStepEvent.venture_id == venture.id,
                                            VentureStepEvent.created_at >= since)
              .order_by(VentureStepEvent.created_at.desc()).limit(ROW_LIMIT).all())
    actions = (VentureOutreachAction.query.filter(VentureOutreachAction.venture_id == venture.id,
                                                  VentureOutreachAction.created_at >= since)
               .order_by(VentureOutreachAction.created_at.desc()).limit(ROW_LIMIT).all())
    labels = _user_labels([e.actor_user_id for e in events] + [a.approved_by for a in actions])
    declined = _founder_declined(actions)
    for e in events:
        step = steps.get(e.plan_step_id)
        items.append({'kind': 'step', 'at': _iso(e.created_at), 'plan_step_id': e.plan_step_id,
                      'step_title': step.title if step else None, 'layer': 'F%d' % step.layer if step else None,
                      'from_state': e.from_state, 'to_state': e.to_state, 'actor_kind': e.actor_kind,
                      'actor': labels.get(e.actor_user_id), 'note': mask(e.note, unmasked)})
    for a in actions:
        items.append(dict(_action_row(a, unmasked, labels, declined), kind='approval', at=_iso(a.sent_at or a.created_at)))
    for r in (EvaluationRun.query.filter(EvaluationRun.venture_id == venture.id, EvaluationRun.started_at >= since)
              .order_by(EvaluationRun.started_at.desc()).limit(50).all()):
        items.append({'kind': 'evaluation', 'at': _iso(r.started_at), 'status': r.status,
                      'overall_score': r.overall_score, 'cost_cents': r.cost_cents, 'run_id': r.obs_run_id})
    items.sort(key=lambda i: i.get('at') or '', reverse=True)
    return {'venture': {'id': venture.id, 'name': venture.name, 'stage': venture.stage, 'status': venture.status,
                        'rung': venture.rung, 'one_liner': mask(venture.one_liner, unmasked),
                        'paused': venture.paused_at is not None, 'paused_at': _iso(venture.paused_at)},
            'unmasked': unmasked,
            'support_access_until': _iso(venture.support_access_until) if unmasked else None,
            'items': items[:ROW_LIMIT * 2]}


# The one block reason the platform writes for a founder decline; anything
# else on a declined draft is the founder's own words.
DEFAULT_DECLINE_REASON = 'Declined by the founder'


def _founder_declined(actions) -> set:
    """Ids of these actions a founder declined (their block_reason is the
    founder's free text). Other blocks come from compliance or guardrails."""
    ids = [a.id for a in actions if a.block_reason]
    if not ids:
        return set()
    try:
        from app.models.agent_plan import ActionApproval
        return {r.target_id for r in ActionApproval.query.filter(
            ActionApproval.target_type == 'fnd_outreach', ActionApproval.target_id.in_(ids),
            ActionApproval.decision != 'approve').all()}
    except Exception as exc:
        logger.debug('action approvals not readable: %s', exc)
        return set(ids)       # can't tell: treat every reason as founder text


def block_reason(a, unmasked, declined) -> str:
    reason = a.block_reason
    if not reason or a.id not in declined or reason == DEFAULT_DECLINE_REASON:
        return reason
    return mask(reason, unmasked)


def _action_row(a, unmasked, labels, declined=frozenset()) -> dict:
    try:
        from app.services import founder_alerts
        above = founder_alerts.above_level(a)
    except Exception:
        above = False
    return {'action_id': a.id, 'venture_id': a.venture_id, 'action_type': a.action_type,
            'level_required': a.level_required, 'level_applied': a.level_applied, 'status': a.status,
            'approved_by': labels.get(a.approved_by) if a.approved_by else None,
            'block_reason': block_reason(a, unmasked, declined), 'sent_at': _iso(a.sent_at), 'created_at': _iso(a.created_at),
            'above_level': above, 'target': mask(a.target_ref, unmasked), 'subject': mask(a.subject, unmasked),
            'body': mask(a.body, unmasked)}


def autonomy_audit(hours, admin_id) -> dict:
    from app.models.founder import FounderVenture, VentureOutreachAction
    since = _since(hours)
    rows = (VentureOutreachAction.query.filter(VentureOutreachAction.created_at >= since)
            .order_by(VentureOutreachAction.created_at.desc()).limit(ROW_LIMIT).all())
    vmap = {v.id: v for v in FounderVenture.query.filter(
        FounderVenture.id.in_({r.venture_id for r in rows} or ['-'])).all()}
    labels = _user_labels([r.approved_by for r in rows])
    declined = _founder_declined(rows)
    out, unmasked_ventures, counts = [], set(), {}
    for r in rows:
        v = vmap.get(r.venture_id)
        unmasked = support_access_active(v) if v else False
        if unmasked and (r.body or r.subject or r.target_ref
                         or (r.block_reason and r.id in declined and r.block_reason != DEFAULT_DECLINE_REASON)):
            unmasked_ventures.add(r.venture_id)
        row = _action_row(r, unmasked, labels, declined)
        row['venture_name'] = v.name if v else None
        out.append(row)
        counts[r.status] = counts.get(r.status, 0) + 1
    for vid in sorted(unmasked_ventures):
        _audit(admin_id, AUDIT_VIEW_UNMASKED, 'autonomy:%s' % vid, vmap[vid].owner_user_id)
    return {'hours': int(hours), 'actions': out, 'by_status': counts,
            'above_level': sum(1 for r in out if r['above_level']),
            'blocked': [r for r in out if r['status'] == 'blocked']}


def eval_quality() -> dict:
    from app.extensions import db
    from app.models.founder import AgentBlueprint, AgentParamOverride, EvaluationFinding, FindingRating
    rated = (db.session.query(FindingRating, EvaluationFinding)
             .join(EvaluationFinding, EvaluationFinding.id == FindingRating.finding_id).all())
    by_dim, by_agent = {}, {}
    for rating, finding in rated:
        for bucket, key in ((by_dim, finding.dimension), (by_agent, finding.recommended_agent or '(none)')):
            b = bucket.setdefault(key, {'ratings': 0, 'fair': 0})
            b['ratings'] += 1
            b['fair'] += 1 if rating.fair else 0

    def rows(bucket, name):
        return sorted([{name: k, 'ratings': b['ratings'], 'fair': b['fair'],
                        'accuracy': round(100.0 * b['fair'] / b['ratings'], 1) if b['ratings'] else None}
                       for k, b in bucket.items()], key=lambda r: -r['ratings'])

    total = sum(b['ratings'] for b in by_dim.values())
    fair = sum(b['fair'] for b in by_dim.values())
    blueprints = AgentBlueprint.query.all()
    bp_by_id = {b.id: b for b in blueprints}
    per_agent = {}
    for b in blueprints:
        per_agent.setdefault(b.agent_key, {'blueprints': 0, 'overridden': set(), 'overrides': 0})
        per_agent[b.agent_key]['blueprints'] += 1
    by_param = {}
    for o in AgentParamOverride.query.all():
        by_param[o.param_key] = by_param.get(o.param_key, 0) + 1
        bp = bp_by_id.get(o.blueprint_id)
        if bp is not None:
            per_agent[bp.agent_key]['overrides'] += 1
            per_agent[bp.agent_key]['overridden'].add(bp.id)
    override_rows = sorted([{'agent_key': k, 'agent_label': agent_label(k), 'blueprints': v['blueprints'],
                             'overrides': v['overrides'],
                             'override_rate': round(100.0 * len(v['overridden']) / v['blueprints'], 1)
                             if v['blueprints'] else None} for k, v in per_agent.items()],
                           key=lambda r: -(r['override_rate'] or 0))
    target = None
    try:
        from app.services import founder_config
        target = (founder_config.get('fnd_targets') or {}).get('finding_accuracy')
    except Exception:
        pass
    agent_rows = rows(by_agent, 'agent_key')
    for r in agent_rows:
        r['agent_label'] = agent_label(r['agent_key'])
    return {'ratings': total, 'fair': fair, 'accuracy': round(100.0 * fair / total, 1) if total else None,
            'accuracy_target': target, 'by_dimension': rows(by_dim, 'dimension'), 'by_agent': agent_rows,
            'overrides_by_agent': override_rows,
            'overrides_by_param': sorted([{'param_key': k, 'overrides': n} for k, n in by_param.items()],
                                         key=lambda r: -r['overrides'])}


def compliance(hours) -> dict:
    from app.extensions import db
    from app.models.founder import ComplianceProfile, FounderVenture, VentureOutreachAction
    from app.models.observability import AgentEvent, AgentRun, ObservabilityFlag
    from sqlalchemy import func
    since = _since(hours)
    profiles = {p.venture_id: p for p in ComplianceProfile.query.all()}
    ventures_out, by_exemption = [], {}
    for v in FounderVenture.query.order_by(FounderVenture.name.asc()).limit(500).all():
        p = profiles.get(v.id)
        ex = p.exemption if p and p.recorded_at else 'not_recorded'
        by_exemption[ex] = by_exemption.get(ex, 0) + 1
        ventures_out.append({'venture_id': v.id, 'venture_name': v.name, 'exemption': ex,
                             'recorded_at': _iso(p.recorded_at) if p else None,
                             'mailbox_consent': bool(p and p.mailbox_consent_at),
                             'contacts_consent': bool(p and p.contacts_consent_at)})
    blocked = (VentureOutreachAction.query.filter(VentureOutreachAction.status == 'blocked',
                                                  VentureOutreachAction.created_at >= since)
               .order_by(VentureOutreachAction.created_at.desc()).limit(ROW_LIMIT).all())
    vnames = {v['venture_id']: v['venture_name'] for v in ventures_out}
    # The compliance view never unmasks: a founder's own decline note stays hidden.
    declined = _founder_declined(blocked)
    warnings = (db.session.query(func.count(AgentEvent.id)).join(AgentRun, AgentRun.id == AgentEvent.run_id)
                .filter(AgentRun.layer.in_(founder_layers()), AgentEvent.type == 'warning',
                        AgentEvent.ts >= since).scalar() or 0)
    flags = (db.session.query(ObservabilityFlag.rule_name, func.count(ObservabilityFlag.id))
             .filter(ObservabilityFlag.layer.in_(founder_layers()), ObservabilityFlag.created_at >= since)
             .group_by(ObservabilityFlag.rule_name).all())
    return {'hours': int(hours), 'ventures': ventures_out, 'by_exemption': by_exemption,
            'blocked_actions': [{'action_id': a.id, 'venture_id': a.venture_id,
                                 'venture_name': vnames.get(a.venture_id), 'action_type': a.action_type,
                                 'block_reason': block_reason(a, False, declined), 'created_at': _iso(a.created_at)} for a in blocked],
            'warning_events': int(warnings),
            'flags_by_rule': sorted([{'rule_name': n or '(none)', 'count': int(c)} for n, c in flags],
                                    key=lambda r: -r['count'])}


def connectors(hours) -> dict:
    """Tool and connector calls made inside founder runs. Mailbox and form
    connectors arrive with investor connection (WP8); until then this shows
    whatever tool spans founder runs recorded."""
    from app.extensions import db
    from app.models.founder import ComplianceProfile
    from app.models.observability import AgentRun, AgentRunTool
    from sqlalchemy import func
    since = _since(hours)
    rows = (db.session.query(AgentRunTool.tool_name, func.count(AgentRunTool.id),
                             func.sum(db.case((AgentRunTool.status.in_(('error', 'blocked')), 1), else_=0)))
            .join(AgentRun, AgentRun.id == AgentRunTool.run_id)
            .filter(AgentRun.layer.in_(founder_layers()), AgentRunTool.created_at >= since)
            .group_by(AgentRunTool.tool_name).all())
    tools = sorted([{'tool': t, 'calls': int(n), 'failures': int(f or 0)} for t, n, f in rows],
                   key=lambda r: -r['failures'])
    consents = {'mailbox': ComplianceProfile.query.filter(ComplianceProfile.mailbox_consent_at.isnot(None)).count(),
                'contacts': ComplianceProfile.query.filter(
                    ComplianceProfile.contacts_consent_at.isnot(None)).count()}
    return {'hours': int(hours), 'tools': tools, 'failures': sum(t['failures'] for t in tools),
            'calls': sum(t['calls'] for t in tools), 'consents': consents,
            'placeholder': True}


def startup_pages(hours) -> dict:
    from app.extensions import db
    from app.models.founder import StartupPage, StartupPageVisit
    from sqlalchemy import func
    since = _since(hours)
    pages = StartupPage.query.order_by(StartupPage.updated_at.desc()).limit(500).all()
    views = {}
    for pid, view, n in (db.session.query(StartupPageVisit.startup_page_id, StartupPageVisit.view,
                                          func.count(StartupPageVisit.id))
                         .filter(StartupPageVisit.created_at >= since)
                         .group_by(StartupPageVisit.startup_page_id, StartupPageVisit.view).all()):
        views.setdefault(pid, {})[view] = int(n)
    access = _optional_access_counts()
    updates = _optional_update_counts()
    out = []
    for p in pages:
        empty = {'unlocks': 0, 'incomplete': 0, 'refunds': 0, 'requests': 0}
        a = (access.get(p.id) or empty) if access is not None else {}
        out.append({'id': p.id, 'name': p.name, 'slug': p.slug, 'status': p.status, 'name_check': p.name_check,
                    'public_views': views.get(p.id, {}).get('public', 0),
                    'investor_views': views.get(p.id, {}).get('investor', 0),
                    'unlocks': a.get('unlocks') if access is not None else None,
                    'payments_incomplete': a.get('incomplete') if access is not None else None,
                    'refunds': a.get('refunds') if access is not None else None,
                    'requests': a.get('requests') if access is not None else None,
                    'updates_sent': (updates or {}).get(p.venture_id, 0) if updates is not None else None})
    return {'hours': int(hours), 'pages': out,
            'totals': {'pages': len(out), 'published': sum(1 for p in out if p['status'] == 'published'),
                       'views': sum(p['public_views'] + p['investor_views'] for p in out),
                       'unlocks': sum(p['unlocks'] or 0 for p in out)},
            'investor_tables': access is not None}


def _optional_access_counts():
    """{page_id: {...}} from investor_access, or None when the table is absent."""
    from app.extensions import db
    try:
        from app.models.founder import InvestorAccess
        out = {}
        for a in InvestorAccess.query.all():
            b = out.setdefault(a.startup_page_id, {'unlocks': 0, 'incomplete': 0, 'refunds': 0, 'requests': 0})
            if a.status == 'active':
                b['unlocks'] += 1
            elif a.status == 'refunded':
                b['refunds'] += 1
            elif a.status == 'requested':
                b['requests'] += 1
            elif a.kind == 'paid' and a.status == 'pending':
                b['incomplete'] += 1
        return out
    except Exception as exc:
        db.session.rollback()
        logger.debug('investor_access not readable: %s', exc)
        return None


def _optional_update_counts():
    from app.extensions import db
    try:
        from app.models.founder import InvestorUpdate
        out = {}
        for u in InvestorUpdate.query.filter_by(status='sent').all():
            out[u.venture_id] = out.get(u.venture_id, 0) + 1
        return out
    except Exception as exc:
        db.session.rollback()
        logger.debug('investor_update not readable: %s', exc)
        return None
