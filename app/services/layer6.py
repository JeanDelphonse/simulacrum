"""
Layer 6 Autonomous Growth Orchestrator service.

Implements:
  - Explore/Exploit phase management
  - Dependency graph (DAG) scheduling via Weighted Shortest Job First
  - Bayesian outcome tracking (beta distribution per income stream)
  - Autonomy boundary enforcement
  - Re-calibration event detection
"""
from __future__ import annotations
import logging
import math
from datetime import datetime, date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _months_since(created_at: datetime) -> int:
    now = datetime.utcnow()
    return (now.year - created_at.year) * 12 + (now.month - created_at.month)


def determine_phase(simulation, config) -> str:
    from app.models.layer6 import Layer6Config
    months = _months_since(simulation.created_at)
    if months >= config.explore_phase_end_month:
        return Layer6Config.PHASE_EXPLOIT if hasattr(Layer6Config, 'PHASE_EXPLOIT') else 'exploit'
    return 'explore'


# ---------------------------------------------------------------------------
# Bayesian scoring (Beta distribution on yield probability)
# ---------------------------------------------------------------------------

PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0


def _beta_mean(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta)


def _compute_posterior(outcomes: list[dict]) -> tuple[float, float]:
    """Update Beta prior with observed successes/failures from outcome history."""
    alpha = PRIOR_ALPHA
    beta = PRIOR_BETA
    for o in outcomes:
        actual = float(o.get('actual_income', 0))
        projected = float(o.get('projected_income', 1) or 1)
        ratio = min(actual / projected, 2.0)  # Cap at 2× to avoid outlier domination
        alpha += ratio
        beta += max(0, 1 - ratio)
    return alpha, beta


def score_action(action_type: str, source_layer: int, outcomes_for_layer: list[dict],
                 unblocks_count: int, explore_phase: bool, layer_index: int) -> float:
    """
    Bayesian priority score for an action.

    score = posterior_mean * (1 + unblock_bonus) * phase_diversity_bonus
    """
    alpha, beta = _compute_posterior(outcomes_for_layer)
    posterior_mean = _beta_mean(alpha, beta)

    # Weighted Shortest Job First: reward actions that unblock more downstream work
    unblock_bonus = math.log1p(unblocks_count) * 0.2

    # During exploration, add a diversity bonus to ensure all layers get sampled
    diversity_bonus = (0.15 * (6 - layer_index)) if explore_phase else 0.0

    return round(posterior_mean + unblock_bonus + diversity_bonus, 6)


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

# Static prerequisite map: action_type -> list of action_types that must complete first.
# This is a simplified DAG; real implementation can be made dynamic from agent_actions data.
ACTION_PREREQUISITES: dict[str, list[str]] = {
    # Layer 2 leveraged delivery benefits from L1 income proof
    'speaking_proposals': ['consulting_outreach'],
    'group_coaching_program': ['consulting_rate_card'],
    # Layer 3 products need a validated audience
    'course_curriculum': ['group_coaching_program'],
    'product_sales_page': ['course_curriculum'],
    'launch_email_sequence': ['product_sales_page'],
    # Layer 4 automation needs existing products/list
    'seo_content_calendar': ['product_sales_page'],
    'lead_magnet_funnel': ['launch_email_sequence'],
    'newsletter_monetization': ['lead_magnet_funnel'],
    'saas_product_spec': ['course_curriculum'],
    # Layer 5 wealth deployment needs income from automation
    'income_allocation': ['lead_magnet_funnel'],
    'compound_growth_model': ['income_allocation'],
    'dca_schedule': ['income_allocation'],
}


def _count_unblocked(action_type: str, completed_types: set[str],
                     all_eligible: list[str]) -> int:
    """Count how many other eligible actions this action's completion would unblock."""
    count = 0
    for other in all_eligible:
        prereqs = ACTION_PREREQUISITES.get(other, [])
        if action_type in prereqs:
            remaining = set(prereqs) - completed_types - {action_type}
            if not remaining:
                count += 1
    return count


def build_eligible_actions(simulation_id: str, config, completed_types: set[str],
                            explore_phase: bool) -> list[dict[str, Any]]:
    """
    Return all agent action types eligible for dispatch this cycle, with scores.
    Eligibility: prerequisites complete, not blocked, not already queued/running.
    """
    from app.services.claude import AGENT_ACTION_TYPES
    from app.models.layer6 import Layer6ActionQueue

    blocked = set(config.blocked_actions)
    channel_approvals = config.channel_approvals

    # Gather action types that are already in flight for this simulation
    in_flight = {
        r.action_type for r in Layer6ActionQueue.query.filter(
            Layer6ActionQueue.simulation_id == simulation_id,
            Layer6ActionQueue.status.in_([
                Layer6ActionQueue.STATUS_QUEUED,
                Layer6ActionQueue.STATUS_DISPATCHED,
            ])
        ).all()
    }

    eligible = []
    layer_index = 0
    for layer_num, actions in AGENT_ACTION_TYPES.items():
        layer_index += 1
        for action_type, action_def in actions.items():
            if action_type in blocked:
                continue
            if action_type in in_flight:
                continue
            if action_type in completed_types:
                continue

            # Check prerequisites
            prereqs = ACTION_PREREQUISITES.get(action_type, [])
            if not all(p in completed_types for p in prereqs):
                continue

            # Check channel constraints
            required_channel = action_def.get('channel')
            if required_channel and not channel_approvals.get(required_channel, False):
                continue

            eligible.append({
                'action_type': action_type,
                'source_layer': layer_num,
                'layer_index': layer_index,
                'label': action_def.get('label', action_type),
            })

    return eligible


# ---------------------------------------------------------------------------
# Orchestrator cycle
# ---------------------------------------------------------------------------

def run_orchestrator_cycle(simulation_id: str) -> dict:
    """
    Execute one full orchestrator cycle:
      1. Harvest — refresh outcome data
      2. Score — rank eligible actions
      3. Schedule — dispatch top N within autonomy bounds
      4. Report — write cycle record and execution log

    Returns a summary dict for the API response.
    """
    from app.extensions import db
    from app.models.simulation import Simulation, SimulationLayer, IncomeStream
    from app.models.agent_action import AgentAction
    from app.models.layer6 import (
        Layer6Config, Layer6Cycle, Layer6ActionQueue,
        Layer6Outcome, Layer6ExecutionLog,
    )
    from app.tasks.layer6 import dispatch_layer6_action

    sim = Simulation.query.get(simulation_id)
    if not sim:
        raise ValueError(f'Simulation {simulation_id} not found')

    config = Layer6Config.query.filter_by(simulation_id=simulation_id).first()
    if not config or not config.is_active:
        raise ValueError(f'Layer 6 not configured or inactive for {simulation_id}')

    # Determine phase
    phase = determine_phase(sim, config)

    # Get cycle number
    last_cycle = Layer6Cycle.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).first()
    cycle_number = (last_cycle.cycle_number + 1) if last_cycle else 1

    # Create cycle record
    cycle = Layer6Cycle(
        simulation_id=simulation_id,
        cycle_number=cycle_number,
        phase=phase,
        cycle_started_at=datetime.utcnow(),
    )
    db.session.add(cycle)
    db.session.flush()  # Get cycle.id

    # --- HARVEST: collect completed action types ---
    completed_types: set[str] = {
        a.action_type for a in AgentAction.query.filter_by(
            simulation_id=simulation_id,
            status=AgentAction.STATUS_COMPLETE,
        ).all()
    }

    # --- SCORE: build eligible action list with Bayesian scores ---
    outcomes = Layer6Outcome.query.filter_by(simulation_id=simulation_id).all()
    outcomes_by_layer: dict[int, list[dict]] = {}
    for o in outcomes:
        outcomes_by_layer.setdefault(o.layer_number, []).append(o.to_dict())

    eligible = build_eligible_actions(
        simulation_id, config, completed_types, explore_phase=(phase == 'explore')
    )

    all_eligible_types = [e['action_type'] for e in eligible]
    scored = []
    for e in eligible:
        layer_outcomes = outcomes_by_layer.get(e['source_layer'], [])
        unblocks = _count_unblocked(e['action_type'], completed_types, all_eligible_types)
        priority = score_action(
            action_type=e['action_type'],
            source_layer=e['source_layer'],
            outcomes_for_layer=layer_outcomes,
            unblocks_count=unblocks,
            explore_phase=(phase == 'explore'),
            layer_index=e['layer_index'],
        )
        scored.append({**e, 'priority_score': priority, 'unblocks': unblocks})

    # Exploration: ensure at least one action per layer represented
    if phase == 'explore':
        scored = _ensure_layer_diversity(scored, config.actions_per_cycle)
    else:
        scored.sort(key=lambda x: x['priority_score'], reverse=True)

    dispatched_count = 0
    escalated_count = 0
    queue_entries: list[Layer6ActionQueue] = []

    # --- SCHEDULE: select top N ---
    for action in scored[:config.actions_per_cycle]:
        is_within_bounds, reason = _check_autonomy_bounds(action['action_type'], config)

        entry = Layer6ActionQueue(
            simulation_id=simulation_id,
            cycle_id=cycle.id,
            source_layer=action['source_layer'],
            action_type=action['action_type'],
            priority_score=action['priority_score'],
        )

        if is_within_bounds:
            entry.status = Layer6ActionQueue.STATUS_DISPATCHED
            entry.dispatched_at = datetime.utcnow()
            dispatched_count += 1
        else:
            entry.status = Layer6ActionQueue.STATUS_ESCALATED
            entry.escalation_reason = reason
            escalated_count += 1

        db.session.add(entry)
        queue_entries.append((entry, is_within_bounds))

    db.session.flush()  # Populate entry IDs

    # Write execution log entries
    for entry, within_bounds in queue_entries:
        event_type = (Layer6ExecutionLog.EVENT_DISPATCHED if within_bounds
                      else Layer6ExecutionLog.EVENT_ESCALATED)
        log = Layer6ExecutionLog(
            simulation_id=simulation_id,
            cycle_id=cycle.id,
            action_id=entry.id,
            event_type=event_type,
            actor=Layer6ExecutionLog.ACTOR_ORCHESTRATOR,
            reasoning=f'Score={entry.priority_score:.4f}, layer={entry.source_layer}',
        )
        db.session.add(log)

    # Generate cycle reasoning summary via Claude
    reasoning = _generate_cycle_reasoning(phase, scored[:config.actions_per_cycle],
                                          dispatched_count, escalated_count)

    # Update cycle record
    cycle.actions_scored = len(scored)
    cycle.actions_dispatched = dispatched_count
    cycle.actions_escalated = escalated_count
    cycle.orchestrator_reasoning = reasoning
    cycle.cycle_completed_at = datetime.utcnow()

    db.session.commit()

    # Dispatch actions — skip Celery entirely when no Redis broker is configured
    from flask import current_app as _ca
    _has_redis = bool(_ca.config.get('REDIS_URL'))
    for entry, within_bounds in queue_entries:
        if within_bounds:
            if _has_redis:
                dispatch_layer6_action.delay(entry.id)
            else:
                _execute_action_sync(entry)

    # Check for re-calibration trigger
    _check_recalibration(simulation_id, config)

    return cycle.to_dict()


def _execute_action_sync(entry) -> None:
    """Execute a dispatched layer6 action synchronously — no Celery or Redis required."""
    from datetime import datetime as _dt
    from app.extensions import db
    from app.models.layer6 import Layer6ActionQueue, Layer6ExecutionLog
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation
    from app.models.resume import Resume
    from app.services.claude import execute_agent_action

    sim = Simulation.query.get(entry.simulation_id)
    resume = Resume.query.get(sim.resume_id) if sim else None
    parsed_text = resume.parsed_text if resume else ''

    agent_action = AgentAction(
        simulation_id=entry.simulation_id,
        layer_number=entry.source_layer,
        action_type=entry.action_type,
        status=AgentAction.STATUS_IN_PROGRESS,
    )
    db.session.add(agent_action)
    db.session.flush()
    entry.agent_action_id = agent_action.id
    db.session.commit()

    try:
        result = execute_agent_action(
            action_type=entry.action_type,
            layer_number=entry.source_layer,
            expertise_zone=sim.expertise_zone if sim else '',
            parsed_text=parsed_text,
            user_inputs={},
            user_id=sim.user_id if sim else None,
            simulation_id=entry.simulation_id,
        )
        artifact = result.get('content') or result.get('artifact') or str(result)
        agent_action.artifact = artifact
        agent_action.status = AgentAction.STATUS_COMPLETE
        agent_action.completed_at = _dt.utcnow()
        entry.status = Layer6ActionQueue.STATUS_COMPLETE
        entry.completed_at = _dt.utcnow()
        entry.outcome_summary = artifact[:500] if artifact else ''
        db.session.add(Layer6ExecutionLog(
            simulation_id=entry.simulation_id,
            cycle_id=entry.cycle_id,
            action_id=entry.id,
            event_type=Layer6ExecutionLog.EVENT_COMPLETED,
            actor=Layer6ExecutionLog.ACTOR_ORCHESTRATOR,
            reasoning='Action completed successfully.',
        ))
        db.session.commit()
        logger.info('Layer 6 sync action %s (%s) completed', entry.id, entry.action_type)
    except Exception as exc:
        agent_action.status = AgentAction.STATUS_FAILED
        agent_action.error_message = str(exc)
        db.session.commit()
        logger.exception('Layer 6 sync action %s failed: %s', entry.id, exc)


def _ensure_layer_diversity(scored: list[dict], n: int) -> list[dict]:
    """During exploration: pick at least one action per layer, fill remainder by score."""
    by_layer: dict[int, list[dict]] = {}
    for s in scored:
        by_layer.setdefault(s['source_layer'], []).append(s)

    selected: list[dict] = []
    # Pick best-scored action from each layer
    for layer_actions in by_layer.values():
        layer_actions.sort(key=lambda x: x['priority_score'], reverse=True)
        selected.append(layer_actions[0])

    selected.sort(key=lambda x: x['priority_score'], reverse=True)
    if len(selected) >= n:
        return selected[:n]

    # Fill remaining slots from the full pool by score
    used = {s['action_type'] for s in selected}
    remainder = [s for s in sorted(scored, key=lambda x: x['priority_score'], reverse=True)
                 if s['action_type'] not in used]
    selected.extend(remainder[:n - len(selected)])
    return selected


def _check_autonomy_bounds(action_type: str, config) -> tuple[bool, str]:
    """
    Return (is_within_bounds, escalation_reason).
    Actions that touch external channels or spending beyond approved limits are escalated.
    """
    channel_map = {
        'cold_email_campaign': 'email',
        'consulting_outreach': 'email',
        'referral_network_activation': 'email',
        'launch_email_sequence': 'email_funnels',
        'lead_magnet_funnel': 'email_funnels',
        'waitlist_landing_page': 'email_funnels',
        'linkedin_optimization': 'linkedin',
        'corporate_training_outreach': 'linkedin',
        'booking_page': 'calendar',
        'seo_content_calendar': 'content_publishing',
        'youtube_podcast_strategy': 'content_publishing',
    }
    required_channel = channel_map.get(action_type)
    if required_channel:
        approvals = config.channel_approvals
        if not approvals.get(required_channel, False):
            return False, f'Channel "{required_channel}" not approved in autonomy boundary settings'

    if action_type in config.blocked_actions:
        return False, 'Action type is in the blocked list'

    return True, ''


def _generate_cycle_reasoning(phase: str, top_actions: list[dict],
                               dispatched: int, escalated: int) -> str:
    """Generate a plain-language summary of the cycle's decision logic."""
    if not top_actions:
        return (f'Phase: {phase}. No eligible actions found this cycle — '
                'all prerequisites are pending or actions are blocked.')

    action_names = ', '.join(a['action_type'].replace('_', ' ') for a in top_actions[:3])
    return (
        f'Phase: {phase.upper()}. '
        f'Top actions selected: {action_names}. '
        f'Dispatched {dispatched} action(s), escalated {escalated} for approval. '
        f'Scoring weighted by Bayesian yield posterior and dependency unblocking value.'
    )


def _check_recalibration(simulation_id: str, config) -> None:
    """
    Trigger a Re-Calibration Event if actual income < 60% of projected
    for two consecutive months.
    """
    from app.models.layer6 import Layer6Outcome, Layer6ExecutionLog
    from app.extensions import db

    now = datetime.utcnow()
    months_to_check = [
        (now - timedelta(days=30)).strftime('%Y-%m'),
        (now - timedelta(days=60)).strftime('%Y-%m'),
    ]

    underperforming = 0
    for month in months_to_check:
        outcomes = Layer6Outcome.query.filter_by(
            simulation_id=simulation_id, reporting_month=month
        ).all()
        if not outcomes:
            continue
        total_actual = sum(float(o.actual_income) for o in outcomes)
        total_projected = sum(float(o.projected_income) for o in outcomes) or 1
        if total_actual < 0.6 * total_projected:
            underperforming += 1

    if underperforming >= 2:
        log = Layer6ExecutionLog(
            simulation_id=simulation_id,
            event_type=Layer6ExecutionLog.EVENT_RECALIBRATED,
            actor=Layer6ExecutionLog.ACTOR_ORCHESTRATOR,
            reasoning=(
                'Actual income below 60% of projected for two consecutive months. '
                'Re-calibration triggered: priority scores reset from updated posteriors.'
            ),
        )
        db.session.add(log)
        db.session.commit()
        logger.info('Layer 6 re-calibration triggered for simulation %s', simulation_id)


# ---------------------------------------------------------------------------
# Dashboard assembly
# ---------------------------------------------------------------------------

def build_dashboard(simulation_id: str) -> dict:
    """
    Assemble all three Growth Command Center zones in one call.
    Returns: { numbers, actions, momentum }
    """
    from app.models.simulation import Simulation, SimulationLayer, IncomeStream
    from app.models.layer6 import (
        Layer6Config, Layer6Cycle, Layer6ActionQueue,
        Layer6Outcome, Layer6Momentum, Layer6ExecutionLog,
    )

    sim = Simulation.query.get(simulation_id)
    if not sim:
        return {}

    config = Layer6Config.query.filter_by(simulation_id=simulation_id).first()

    # --- NUMBERS zone ---
    layers = SimulationLayer.query.filter_by(simulation_id=simulation_id).order_by(
        SimulationLayer.layer_number
    ).all()

    outcomes = Layer6Outcome.query.filter_by(simulation_id=simulation_id).all()
    actual_by_layer: dict[int, float] = {}
    projected_by_layer: dict[int, float] = {}
    for o in outcomes:
        actual_by_layer[o.layer_number] = actual_by_layer.get(o.layer_number, 0) + float(o.actual_income)
        projected_by_layer[o.layer_number] = projected_by_layer.get(o.layer_number, 0) + float(o.projected_income)

    layer_metrics = []
    for layer in layers:
        actual = actual_by_layer.get(layer.layer_number, 0)
        projected = projected_by_layer.get(layer.layer_number, 0)
        layer_metrics.append({
            'layer_number': layer.layer_number,
            'layer_name': layer.layer_name,
            'actual_income': actual,
            'projected_income': projected,
            'variance': actual - projected,
            'variance_pct': round((actual - projected) / projected * 100, 1) if projected else 0,
        })

    total_actual = sum(m['actual_income'] for m in layer_metrics)
    total_projected = sum(m['projected_income'] for m in layer_metrics)

    numbers = {
        'layers': layer_metrics,
        'total_actual': total_actual,
        'total_projected': total_projected,
        'total_variance': total_actual - total_projected,
    }

    # --- ACTIONS zone ---
    recent_queue = Layer6ActionQueue.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6ActionQueue.created_at.desc()
    ).limit(50).all()

    escalations = Layer6ActionQueue.query.filter_by(
        simulation_id=simulation_id,
        status=Layer6ActionQueue.STATUS_ESCALATED,
    ).all()

    last_cycle = Layer6Cycle.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).first()

    actions = {
        'recent_log': [q.to_dict() for q in recent_queue],
        'escalations': [q.to_dict() for q in escalations],
        'last_cycle': last_cycle.to_dict() if last_cycle else None,
        'config': config.to_dict() if config else None,
    }

    # --- MOMENTUM zone ---
    latest_snapshot = Layer6Momentum.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Momentum.snapshot_date.desc()
    ).first()

    momentum = latest_snapshot.to_dict() if latest_snapshot else {}

    return {
        'numbers': numbers,
        'actions': actions,
        'momentum': momentum,
    }
