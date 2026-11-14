"""Founder Track alerts (FOUNDER_PLAN.md WP7, PRD "Agent observability").

Aggregate thresholds over the founder fleet, evaluated by evaluate() and
written to observability_flags so they show up beside guardrail flags in
/admin/observability. Per-run guardrails stay in guardrail_rules; nothing here
changes guardrail_service.

Metrics (founder_alert_rule.metric):
  error_rate_pct          error rate of one founder agent over the last day
  cost_per_venture_cents  model cost of one venture over the last 30 days
  stuck_days              a plan step sitting in one open state this many days
  connector_failures      failed tool/connector calls in founder runs, last day
  above_level_send        any external action sent above its approved autonomy
                          level. Always on; the expected count is zero.

Thresholds: the three metrics with an admin setting (fnd_alert_*) keep their
threshold there only, so /admin/founder and the Observability Alerts tab edit
the same value (set_rule writes it through founder_config.set, which checks the
range). founder_alert_rule holds the on/off switch for every metric and the
threshold for the metrics without a setting (connector_failures).
evaluate() is scheduled from app/scheduler.py and only runs while the
founder_track switch is not off. check_action(action) raises the above-level
flag immediately; the send path calls it right after a send.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

RULE_PREFIX = 'fnd_alert:'

# metric -> (founder_config key or None, fallback threshold, label, unit)
METRICS = {
    'error_rate_pct': ('fnd_alert_error_rate_pct', 20, 'Error rate per agent', '% of finished runs, last 24 hours'),
    'cost_per_venture_cents': ('fnd_alert_cost_per_venture_cents', 2000, 'Cost per venture',
                               'cents, last 30 days'),
    'stuck_days': ('fnd_alert_stuck_days', 14, 'Step stuck in one state', 'days'),
    'connector_failures': (None, 5, 'Connector failures', 'failed calls, last 24 hours'),
    'above_level_send': (None, 0, 'Sent above approved autonomy level', 'any send (always on)'),
}

ERROR_RATE_WINDOW_HOURS = 24
ERROR_RATE_MIN_RUNS = 5          # a rate over fewer finished runs is noise
COST_WINDOW_DAYS = 30
CONNECTOR_WINDOW_HOURS = 24
CONNECTOR_MAX = 100000           # upper bound for a threshold kept in founder_alert_rule
REPEAT_AFTER_HOURS = 24          # don't re-raise the same aggregate alert within a day
OPEN_STATES = ('ready', 'in_progress', 'awaiting_approval')


class AlertError(Exception):
    pass


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _default(metric):
    key, fallback = METRICS[metric][0], METRICS[metric][1]
    if key:
        try:
            from app.services import founder_config
            value = founder_config.get(key)
            if value is not None:
                return float(value)
        except Exception:
            pass
    return float(fallback)


def _row(metric):
    try:
        from app.models.founder import FounderAlertRule
        return FounderAlertRule.query.filter_by(metric=metric).first()
    except Exception:
        return None


def threshold(metric) -> float:
    if METRICS[metric][0]:
        return _default(metric)          # the admin setting is the only source
    row = _row(metric)
    return float(row.threshold) if row is not None else _default(metric)


def enabled(metric) -> bool:
    if metric == 'above_level_send':
        return True
    row = _row(metric)
    return bool(row.enabled) if row is not None else True


def rules() -> list:
    out = []
    for metric, (_key, _fb, label, unit) in METRICS.items():
        row = _row(metric)
        out.append({'metric': metric, 'label': label, 'unit': unit, 'threshold': threshold(metric),
                    'enabled': enabled(metric),
                    'source': 'setting' if _key else ('rule' if row is not None else 'default'),
                    'locked': metric == 'above_level_send'})
    return out


def set_rule(metric, threshold_value=None, enabled_value=None, admin_id=None) -> dict:
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.founder import FounderAlertRule
    if metric not in METRICS:
        raise AlertError('Unknown alert metric.')
    if metric == 'above_level_send':
        raise AlertError('The above-level send alert is always on and has no threshold.')
    setting_key = METRICS[metric][0]
    value = None
    if threshold_value is not None and threshold_value != '':
        try:
            value = float(threshold_value)
        except (TypeError, ValueError):
            raise AlertError('The threshold must be a number.')
        if not math.isfinite(value):
            raise AlertError('The threshold must be a number.')
        if setting_key:
            if value != int(value):
                raise AlertError('The threshold must be a whole number.')
            from app.services import founder_config
            try:
                value = float(founder_config.set(setting_key, int(value), admin_id))   # validates, commits
            except founder_config.FounderConfigError as exc:
                raise AlertError(str(exc).replace(setting_key, METRICS[metric][2]))
        elif value < 0 or value > CONNECTOR_MAX:
            raise AlertError('That threshold is out of range (0 to %d).' % CONNECTOR_MAX)
    row = _row(metric)
    if row is None:
        row = FounderAlertRule(metric=metric, threshold=_default(metric), enabled=True)
        db.session.add(row)
    if value is not None:
        row.threshold = value
    if enabled_value is not None:
        row.enabled = enabled_value not in (False, 'false', '0', 0, 'off')
    AuditLog.log('founder_alert_rule', user_id=admin_id,
                 metadata={'metric': metric, 'threshold': row.threshold, 'enabled': bool(row.enabled)})
    db.session.commit()
    return next(r for r in rules() if r['metric'] == metric)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def _recent_flag(metric, since, target_id=None, agent_key=None) -> bool:
    from app.models.observability import ObservabilityFlag
    q = ObservabilityFlag.query.filter(ObservabilityFlag.rule_name == RULE_PREFIX + metric)
    if since is not None:
        q = q.filter(ObservabilityFlag.created_at >= since)
    if target_id:
        q = q.filter(ObservabilityFlag.target_id == target_id)
    if agent_key:
        q = q.filter(ObservabilityFlag.agent_key == agent_key)
    return q.first() is not None


def _flag(metric, target_type, target_id, reason, severity='high', run_id=None, layer=None, agent_key=None,
          user_id=None):
    from app.extensions import db
    from app.models.observability import ObservabilityFlag
    from utils.id_gen import generate_id
    flag = ObservabilityFlag(id=generate_id(), target_type=target_type, target_id=target_id, run_id=run_id,
                             rule_id=None, rule_name=RULE_PREFIX + metric, layer=layer, agent_key=agent_key,
                             user_id=user_id, severity=severity, action='warn', reason=(reason or '')[:500])
    db.session.add(flag)
    return flag


def above_level(action) -> bool:
    """An external action that went out above the autonomy level approved for it.

    Levels: 1 advisor only, 2 approve each action, 3 approve the campaign,
    4 act within limits; 0 = not permitted. level_required is the level in
    force for the action, level_applied the level it was sent under."""
    if action is None or getattr(action, 'status', None) != 'sent':
        return False
    required, applied = action.level_required, action.level_applied
    if required is None:
        return applied is not None          # sent with no approved level on record
    if required <= 1:
        return True                         # never sent by an agent at level 0 or 1
    if applied is not None and applied > required:
        return True
    if required == 2 and not action.approved_by:
        return True                         # level 2 needs a named approver per action
    return False


def check_action(action, commit: bool = True):
    """Raise the above-level flag for this action at once. Idempotent."""
    from app.extensions import db
    if not above_level(action) or _recent_flag('above_level_send', None, target_id=action.id):
        return None
    owner = _owner(action.venture_id)
    flag = _flag('above_level_send', 'action', action.id,
                 '%s sent at level %s; approved level %s' % (action.action_type, action.level_applied,
                                                             action.level_required),
                 severity='critical', user_id=owner)
    if commit:
        db.session.commit()
    return flag


def _owner(venture_id):
    try:
        from app.extensions import db
        from app.models.founder import FounderVenture
        v = db.session.get(FounderVenture, venture_id)
        return v.owner_user_id if v else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _eval_error_rate(now) -> int:
    from app.models.observability import AgentRun
    from app.services import founder_obs_service as fobs
    limit = threshold('error_rate_pct')
    since = now - timedelta(hours=ERROR_RATE_WINDOW_HOURS)
    stats = {}
    for run in AgentRun.query.filter(AgentRun.layer.in_(fobs.founder_layers()), AgentRun.started_at >= since,
                                     AgentRun.status.in_(('success', 'error', 'blocked'))).all():
        s = stats.setdefault(run.agent_key, {'n': 0, 'err': 0, 'last_err': None, 'layer': run.layer})
        s['n'] += 1
        if run.status == 'error':
            s['err'] += 1
            if s['last_err'] is None or run.started_at > s['last_err'].started_at:
                s['last_err'] = run
    fired = 0
    repeat = now - timedelta(hours=REPEAT_AFTER_HOURS)
    for key, s in stats.items():
        if s['n'] < ERROR_RATE_MIN_RUNS or not s['err']:
            continue
        rate = 100.0 * s['err'] / s['n']
        if rate < limit or _recent_flag('error_rate_pct', repeat, agent_key=key):
            continue
        last = s['last_err']
        _flag('error_rate_pct', 'run', last.id, '%s: %.0f%% of %d runs failed (threshold %.0f%%)'
              % (fobs.agent_label(key), rate, s['n'], limit), run_id=last.id, layer=s['layer'], agent_key=key)
        fired += 1
    return fired


def _eval_cost(now) -> int:
    from app.extensions import db
    from app.models.founder import EvaluationRun, FounderRunMeta, FounderVenture
    from sqlalchemy import func
    limit = threshold('cost_per_venture_cents')
    since = now - timedelta(days=COST_WINDOW_DAYS)
    totals = {}
    for vid, cents in (db.session.query(FounderRunMeta.venture_id, func.sum(FounderRunMeta.cost_cents))
                       .filter(FounderRunMeta.created_at >= since).group_by(FounderRunMeta.venture_id).all()):
        totals[vid] = totals.get(vid, 0) + int(cents or 0)
    # Evaluation passes record their cost on evaluation_run; count those not
    # already counted through founder_run_meta.
    meta_runs = {r for (r,) in db.session.query(FounderRunMeta.obs_run_id)
                 .filter(FounderRunMeta.created_at >= since).all()}
    for r in EvaluationRun.query.filter(EvaluationRun.started_at >= since).all():
        if r.obs_run_id and r.obs_run_id in meta_runs:
            continue
        totals[r.venture_id] = totals.get(r.venture_id, 0) + int(r.cost_cents or 0)
    fired = 0
    repeat = now - timedelta(hours=REPEAT_AFTER_HOURS)
    for vid, cents in totals.items():
        if limit <= 0 or cents < limit or _recent_flag('cost_per_venture_cents', repeat, target_id=vid):
            continue
        v = db.session.get(FounderVenture, vid)
        _flag('cost_per_venture_cents', 'venture', vid, '%s: $%.2f in %d days (threshold $%.2f)'
              % (v.name if v else vid, cents / 100.0, COST_WINDOW_DAYS, limit / 100.0),
              severity='medium', user_id=v.owner_user_id if v else None)
        fired += 1
    return fired


def _eval_stuck(now) -> int:
    from app.models.founder import VenturePlanStep
    days = threshold('stuck_days')
    if days <= 0:
        return 0
    cutoff = now - timedelta(days=days)
    fired = 0
    repeat = now - timedelta(hours=REPEAT_AFTER_HOURS)
    for step in VenturePlanStep.query.filter(VenturePlanStep.state.in_(OPEN_STATES),
                                             VenturePlanStep.updated_at < cutoff).limit(1000).all():
        if _recent_flag('stuck_days', repeat, target_id=step.id):
            continue
        idle = (now - step.updated_at).days
        # Step ids, layer and state only: the title is founder content.
        _flag('stuck_days', 'step', step.id, 'Layer %d step %s has been %s for %d days (threshold %d)'
              % (int(step.layer), step.id, str(step.state).replace('_', ' '), idle, int(days)),
              severity='medium', layer='F%d' % step.layer,
              agent_key=step.agent_key, user_id=_owner(step.venture_id))
        fired += 1
    return fired


def _eval_connectors(now) -> int:
    from app.extensions import db
    from app.models.observability import AgentRun, AgentRunTool
    from app.services import founder_obs_service as fobs
    limit = threshold('connector_failures')
    since = now - timedelta(hours=CONNECTOR_WINDOW_HOURS)
    failed = (db.session.query(AgentRunTool, AgentRun).join(AgentRun, AgentRun.id == AgentRunTool.run_id)
              .filter(AgentRun.layer.in_(fobs.founder_layers()), AgentRunTool.created_at >= since,
                      AgentRunTool.status.in_(('error', 'blocked')))
              .order_by(AgentRunTool.created_at.desc()).all())
    if limit <= 0 or len(failed) < limit:
        return 0
    if _recent_flag('connector_failures', now - timedelta(hours=REPEAT_AFTER_HOURS)):
        return 0
    tool, run = failed[0]
    _flag('connector_failures', 'run', run.id, '%d failed connector calls in %d hours (threshold %d)'
          % (len(failed), CONNECTOR_WINDOW_HOURS, int(limit)), run_id=run.id, layer=run.layer,
          agent_key=run.agent_key)
    return 1


def _eval_above_level(now) -> int:
    from app.models.founder import VentureOutreachAction
    fired = 0
    for action in VentureOutreachAction.query.filter(VentureOutreachAction.status == 'sent').limit(5000).all():
        if check_action(action, commit=False) is not None:
            fired += 1
    return fired


_EVALUATORS = (
    ('above_level_send', _eval_above_level),
    ('error_rate_pct', _eval_error_rate),
    ('cost_per_venture_cents', _eval_cost),
    ('stuck_days', _eval_stuck),
    ('connector_failures', _eval_connectors),
)


def evaluate(now=None) -> dict:
    """Evaluate every enabled rule once. Returns {metric: new flags}. Each
    metric fails on its own, so one broken query can't hide the others."""
    from app.extensions import db
    now = now or datetime.utcnow()
    out = {}
    for metric, fn in _EVALUATORS:
        if not enabled(metric):
            out[metric] = 0
            continue
        try:
            out[metric] = fn(now)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.warning('founder alert %s failed: %s', metric, exc)
            out[metric] = 0
    return out


def recent(hours=24 * 7, limit=200) -> list:
    """Founder alert flags in the window, newest first."""
    from app.models.observability import ObservabilityFlag
    since = datetime.utcnow() - timedelta(hours=int(hours))
    rows = (ObservabilityFlag.query.filter(ObservabilityFlag.rule_name.like(RULE_PREFIX + '%'),
                                           ObservabilityFlag.created_at >= since)
            .order_by(ObservabilityFlag.created_at.desc()).limit(limit).all())
    return [dict(f.to_dict(), metric=(f.rule_name or '')[len(RULE_PREFIX):]) for f in rows]
