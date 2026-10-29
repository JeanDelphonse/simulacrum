"""Execute approved, REVERSIBLE plan steps (EXEC_PLAN P4 — autonomy L2).

The only executable tools in P4 are the two reversible ones:

  stripe_payment_link   a product + price + payment link on the user's own
                        connected Stripe account (no money moves until a
                        customer pays). Undo: deactivate the link.
  calcom_booking_link   an event type on the user's own Cal.com account.
                        Undo: delete the event type.

Everything that sends, signs, posts or trades is refused here no matter how it
is approved — those are hard-gate classes, handled (sends) in P5.

Every execution:
  * requires an approved step, a current plan, and the user's autonomy for the
    agent at L2 or above (checked now, not when the plan was made)
  * is bounded by admin-configurable limits (platform settings):
      exec_max_runs_per_day            (default 20, per user)
      exec_max_runs_per_agent_per_day  (default 5, per user and agent)
      exec_budget_cents_per_month      (default 1000, per user)
  * runs inside an observability run, through tool_span(side_effecting=True),
    so guardrail rules are enforced at the tool-call boundary. Capture fails
    open; a blocking rule — or a broken one — blocks (fails closed)
  * is written to execution_runs, the artifact action log and the audit log
  * is idempotent: a succeeded step returns its existing run
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

EXECUTABLE_TOOLS = frozenset({'stripe_payment_link', 'calcom_booking_link'})
_EXECUTABLE_CLASSES = frozenset({'money_link', 'reversible'})

DEFAULT_LIMITS = {
    'exec_max_runs_per_day': 20,
    'exec_max_runs_per_agent_per_day': 5,
    'exec_budget_cents_per_month': 1000,
}


class ExecutionRefused(Exception):
    """A user-facing refusal; nothing was executed."""


def limit(key: str) -> int:
    try:
        from app.models.platform_settings import PlatformSetting
        return max(0, int(PlatformSetting.get(key, DEFAULT_LIMITS[key])))
    except Exception:
        return DEFAULT_LIMITS[key]


# ---------------------------------------------------------------------------
# Inputs: defaults from the artifact, validated before anything runs
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(r'\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)')


def default_inputs(tool_key: str, action) -> dict:
    """Prefill for the confirmation form, taken from the artifact."""
    from app.models.artifact import ArtifactVersion
    from app.services.agent_registry import get_agent_meta
    current = ArtifactVersion.current_for(action.id) if action else None
    text = (current.content if current else getattr(action, 'artifact', '')) or ''
    label = (get_agent_meta(action.action_type) or {}).get('label', 'Offer') if action else 'Offer'
    m = _MONEY_RE.search(text)
    amount = float(m.group(1).replace(',', '')) if m else 0.0
    if tool_key == 'stripe_payment_link':
        return {'product_name': label[:120], 'amount_usd': amount or 100.0, 'description': ''}
    if tool_key == 'calcom_booking_link':
        return {'title': 'Discovery call', 'length_minutes': 30, 'price_usd': 0.0, 'description': ''}
    return {}


def _validate(tool_key: str, raw: dict) -> dict:
    raw = raw or {}
    if tool_key == 'stripe_payment_link':
        name = str(raw.get('product_name') or '').strip()
        try:
            cents = int(round(float(raw.get('amount_usd')) * 100))
        except (TypeError, ValueError):
            raise ExecutionRefused('Enter the price in USD.')
        if not name or len(name) > 120:
            raise ExecutionRefused('Give the product a name (up to 120 characters).')
        if not 100 <= cents <= 10_000_000:
            raise ExecutionRefused('The price must be between $1 and $100,000.')
        return {'product_name': name, 'amount_cents': cents,
                'description': str(raw.get('description') or '')[:500]}
    if tool_key == 'calcom_booking_link':
        title = str(raw.get('title') or '').strip()
        try:
            length = int(raw.get('length_minutes'))
            price_cents = int(round(float(raw.get('price_usd') or 0) * 100))
        except (TypeError, ValueError):
            raise ExecutionRefused('Enter the length in minutes and the price in USD.')
        if not title or len(title) > 120:
            raise ExecutionRefused('Give the session a title (up to 120 characters).')
        if not 10 <= length <= 480:
            raise ExecutionRefused('The length must be between 10 and 480 minutes.')
        if not 0 <= price_cents <= 10_000_000:
            raise ExecutionRefused('The price must be between $0 and $100,000.')
        return {'title': title, 'length': length, 'price_cents': price_cents,
                'description': str(raw.get('description') or '')[:500]}
    raise ExecutionRefused('This step cannot be executed.')


# ---------------------------------------------------------------------------
# Tool calls (the only code that reaches an integration)
# ---------------------------------------------------------------------------

def _connected(user_id: str, provider: str):
    from app.models.integration import UserIntegration
    rec = UserIntegration.query.filter_by(user_id=user_id, provider=provider).first()
    if not rec or not rec.is_connected:
        name = {'stripe': 'Stripe', 'cal': 'Cal.com'}.get(provider, provider)
        raise ExecutionRefused('Connect %s first (Settings → Integrations).' % name)
    return rec


def _run_stripe(user_id, action, inputs):
    from app.services import stripe_connect_service as sc
    _connected(user_id, 'stripe')
    res = sc.create_stripe_payment_link(
        user_id, inputs['product_name'], inputs['amount_cents'], description=inputs['description'],
        simulation_id=action.simulation_id, layer_number=action.layer_number,
        action_type=action.action_type, action_id=action.id, artifact_id=action.id)
    return {'url': res.get('payment_link_url'), 'payment_link_id': res.get('payment_link_id'),
            'product_id': res.get('product_id'), 'price_id': res.get('price_id')}


def _undo_stripe(user_id, response):
    from app.services import stripe_connect_service as sc
    _, s = sc._get_integration(user_id)
    s.PaymentLink.modify(response['payment_link_id'], active=False)


def _run_calcom(user_id, action, inputs):
    from app.services.cal_service import CalClient
    rec = _connected(user_id, 'cal')
    client = CalClient(rec.decrypt_access_token())
    ev = client.create_event_type(inputs['title'], inputs['length'], price=inputs['price_cents'],
                                  description=inputs['description'])
    me = {}
    try:
        me = client.get_me() or {}
    except Exception:
        pass
    username = (me.get('user') or me).get('username') if isinstance(me, dict) else None
    url = client.get_booking_page_url(username, ev.get('slug')) if username else None
    return {'event_type_id': ev.get('id'), 'slug': ev.get('slug'), 'url': url}


def _undo_calcom(user_id, response):
    from app.services.cal_service import CalClient
    rec = _connected(user_id, 'cal')
    CalClient(rec.decrypt_access_token()).delete_event_type(response['event_type_id'])


RUNNERS = {'stripe_payment_link': _run_stripe, 'calcom_booking_link': _run_calcom}
UNDOERS = {'stripe_payment_link': _undo_stripe, 'calcom_booking_link': _undo_calcom}


# ---------------------------------------------------------------------------
# Execute / undo
# ---------------------------------------------------------------------------

def executable(step, tool) -> bool:
    return bool(step and tool and step.tool_key in EXECUTABLE_TOOLS and tool.enabled
                and tool.reversible and tool.side_effect_class in _EXECUTABLE_CLASSES)


def _check_limits(user_id: str, agent_key: str, cost_cents: int) -> None:
    from app.models.agent_plan import ExecutionRun
    now = datetime.utcnow()
    counted = (ExecutionRun.STATUS_SUCCEEDED, ExecutionRun.STATUS_RUNNING, ExecutionRun.STATUS_UNDONE)
    day = ExecutionRun.query.filter(ExecutionRun.user_id == user_id,
                                    ExecutionRun.created_at >= now - timedelta(days=1),
                                    ExecutionRun.status.in_(counted))
    if day.count() >= limit('exec_max_runs_per_day'):
        raise ExecutionRefused('Daily execution limit reached (%d). Try again tomorrow.'
                               % limit('exec_max_runs_per_day'))
    if day.filter(ExecutionRun.agent_key == agent_key).count() >= limit('exec_max_runs_per_agent_per_day'):
        raise ExecutionRefused('Daily limit for this agent reached (%d).'
                               % limit('exec_max_runs_per_agent_per_day'))
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = sum(r.cost_cents or 0 for r in ExecutionRun.query.filter(
        ExecutionRun.user_id == user_id, ExecutionRun.created_at >= month_start,
        ExecutionRun.status.in_(counted)).all())
    if spent + (cost_cents or 0) > limit('exec_budget_cents_per_month'):
        raise ExecutionRefused('Monthly execution budget reached.')


def execute_step(step_id: str, user_id: str, raw_inputs: dict) -> dict:
    """Run one approved reversible step. Returns the execution run as a dict."""
    from app.extensions import db
    from app.models.agent_action import AgentAction
    from app.models.agent_plan import AgentPlan, ExecutionRun, PlanStep
    from app.models.audit_log import AuditLog
    from app.services import agent_plans, artifact_actions
    from app.services import observability_service as obs
    from app.services.guardrail_service import ToolBlocked

    step = PlanStep.query.get(step_id)
    plan = AgentPlan.query.get(step.plan_id) if step else None
    if step is None or plan is None or plan.user_id != user_id:
        raise PermissionError('not your plan')
    if plan.status in (AgentPlan.STATUS_SUPERSEDED, AgentPlan.STATUS_REJECTED):
        raise ExecutionRefused('This plan is no longer active — rebuild it first.')
    if step.approval_status != PlanStep.APPROVAL_APPROVED:
        raise ExecutionRefused('Approve this step before running it.')
    tool = agent_plans.ensure_tools().get(step.tool_key)
    if not executable(step, tool):
        raise ExecutionRefused('This step cannot be executed in this release.')
    level = agent_plans.effective_level(user_id, plan.agent_key)
    if level not in ('L2', 'L3'):
        raise ExecutionRefused('Your autonomy for this agent is %s; execution needs L2 or higher '
                               '(Settings → Agent Autonomy).' % level)
    done = ExecutionRun.query.filter_by(step_id=step.id, status=ExecutionRun.STATUS_SUCCEEDED).first()
    if done is not None:
        return done.to_dict()                       # idempotent
    action = AgentAction.query.get(plan.action_id)
    if action is None:
        raise ExecutionRefused('The artifact for this plan no longer exists.')
    inputs = _validate(step.tool_key, raw_inputs)
    _check_limits(user_id, plan.agent_key, tool.cost_cents)

    run = ExecutionRun(plan_id=plan.id, step_id=step.id, user_id=user_id, agent_key=plan.agent_key,
                       tool_key=step.tool_key, status=ExecutionRun.STATUS_RUNNING,
                       cost_cents=tool.cost_cents)
    run.request = inputs
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    obs_run = obs.start_run(agent_key=plan.agent_key, layer=action.layer_number, user_id=user_id,
                            simulation_id=plan.simulation_id, action_id=action.id,
                            params=inputs, dispatch_source='execution')
    status, response, error, guard = ExecutionRun.STATUS_FAILED, None, None, None
    try:
        with obs.tool_span(step.tool_key, {k: v for k, v in inputs.items() if k != 'description'},
                           side_effecting=True) as span:
            response = RUNNERS[step.tool_key](user_id, action, inputs)
            span['output'] = 'created %s' % (response.get('url') or response)
        status = ExecutionRun.STATUS_SUCCEEDED
    except ToolBlocked as exc:
        status, guard = ExecutionRun.STATUS_BLOCKED, str(exc)[:300]
    except ExecutionRefused as exc:
        error = str(exc)[:500]
    except Exception as exc:
        logger.error('plan step %s (%s) failed: %s', step.id, step.tool_key, exc, exc_info=True)
        error = ('%s: %s' % (exc.__class__.__name__, exc))[:500]
    finally:
        if obs_run is not None:
            obs.finish_run(status='success' if status == ExecutionRun.STATUS_SUCCEEDED else
                           ('blocked' if status == ExecutionRun.STATUS_BLOCKED else 'error'),
                           error_code=None if status == ExecutionRun.STATUS_SUCCEEDED else status)

    db.session.remove()   # the tool call may have run long; start from a fresh session
    run = ExecutionRun.query.get(run_id)
    run.status, run.response, run.error, run.guardrail_result = status, response, error, guard
    run.finished_at = datetime.utcnow()
    run.obs_run_id = getattr(obs_run, 'id', None)
    AuditLog.log('plan_step_executed', user_id=user_id, resource_id=step_id,
                 metadata={'tool': run.tool_key, 'status': status, 'run_id': run.id})
    db.session.commit()
    artifact_actions.log_action(plan.action_id, 'execute', actor_id=user_id, status=status,
                                result={'tool': run.tool_key, 'run_id': run.id,
                                        'url': (response or {}).get('url')})
    if status == ExecutionRun.STATUS_SUCCEEDED:
        agent_plans.resolve_escalation(step_id)
    return run.to_dict()


def undo_run(run_id: str, user_id: str) -> dict:
    from app.extensions import db
    from app.models.agent_plan import AgentPlan, ExecutionRun
    from app.models.audit_log import AuditLog
    from app.services import artifact_actions

    run = ExecutionRun.query.get(run_id)
    if run is None or run.user_id != user_id:
        raise PermissionError('not your run')
    if run.status != ExecutionRun.STATUS_SUCCEEDED:
        raise ExecutionRefused('Only a completed execution can be undone.')
    try:
        UNDOERS[run.tool_key](user_id, run.response)
    except ExecutionRefused:
        raise
    except Exception as exc:
        logger.error('undo of run %s failed: %s', run_id, exc, exc_info=True)
        raise ExecutionRefused('Undo failed: %s' % exc)
    run.status = ExecutionRun.STATUS_UNDONE
    AuditLog.log('plan_step_undone', user_id=user_id, resource_id=run.step_id,
                 metadata={'tool': run.tool_key, 'run_id': run.id})
    db.session.commit()
    plan = AgentPlan.query.get(run.plan_id)
    if plan:
        artifact_actions.log_action(plan.action_id, 'undo', actor_id=user_id,
                                    result={'tool': run.tool_key, 'run_id': run.id})
    return run.to_dict()
