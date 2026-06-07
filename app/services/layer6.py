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
# Redis lock helpers (FR-ORCH-04)
# ---------------------------------------------------------------------------

def _acquire_cycle_lock(simulation_id: str) -> bool:
    try:
        from flask import current_app
        redis_url = current_app.config.get('REDIS_URL')
        if not redis_url:
            return True  # No Redis — allow cycle, no concurrent protection
        import redis as _redis
        r = _redis.from_url(redis_url)
        result = r.set(f'l6_cycle_lock:{simulation_id}', '1', nx=True, ex=600)
        return bool(result)
    except Exception as exc:
        logger.warning('Redis cycle lock acquire failed: %s', exc)
        return True  # Fail open — don't block cycle on Redis error


def _release_cycle_lock(simulation_id: str) -> None:
    try:
        from flask import current_app
        redis_url = current_app.config.get('REDIS_URL')
        if not redis_url:
            return
        import redis as _redis
        r = _redis.from_url(redis_url)
        r.delete(f'l6_cycle_lock:{simulation_id}')
    except Exception as exc:
        logger.warning('Redis cycle lock release failed: %s', exc)


# ---------------------------------------------------------------------------
# Adaptive dispatch window (FR-ORCH-07)
# ---------------------------------------------------------------------------

CADENCE_MINUTES = {
    'every_12h': 720, 'daily': 1440, 'every_3_days': 4320,
    'every_48h': 2880, 'every_72h': 4320, 'every_168h': 10080, 'weekly': 10080,
}


def _dispatch_window_minutes(cadence: str) -> float:
    interval = CADENCE_MINUTES.get(cadence, 1440)
    return max(interval * 0.8, 360)  # max(80%, 6h)


# ---------------------------------------------------------------------------
# Cold start (FR-ORCH-08, FR-ORCH-09)
# ---------------------------------------------------------------------------

COLD_START_PRIORS = {
    'rate_card': 0.80, 'linkedin_optimize': 0.75, 'booking_page': 0.70,
    'cold_email_campaign': 0.65, 'consulting_outreach': 0.60, 'outreach_email': 0.55,
    'role_search': 0.50, 'speaking_proposals': 0.48, 'coaching_curriculum': 0.45,
    'workshop_content': 0.42, 'course_framework': 0.40, 'sales_page': 0.38,
    'ebook_guide': 0.35, 'seo_content_calendar': 0.33, 'launch_email_sequence': 0.30,
    'referral_network': 0.28, 'social_proof': 0.25, 'membership_structure': 0.23,
    'affiliate_program': 0.20, 'newsletter_monetization': 0.20, 'youtube_podcast': 0.18,
    'saas_product_spec': 0.18, 'portfolio_analysis': 0.15, 'compound_growth': 0.15,
    'real_estate_strategy': 0.15, 'tax_optimization': 0.15, 'entity_structure': 0.13,
    'investment_policy_statement': 0.13, 'fund_recommendations': 0.10, 'dca_schedule': 0.05,
}

COLD_START_SEQUENCE = [
    'rate_card', 'linkedin_optimize', 'booking_page',
    'cold_email_campaign', 'consulting_outreach',
]

# Trust level presets (ENH-09)
TRUST_PRESETS = {
    'full_auto': {
        'email': True, 'email_funnels': True, 'linkedin': True,
        'calendar': True, 'content_publishing': True,
    },
    'balanced': {
        'email': True, 'email_funnels': True, 'linkedin': False,
        'calendar': False, 'content_publishing': False,
    },
    'review_all': {
        'email': False, 'email_funnels': False, 'linkedin': False,
        'calendar': False, 'content_publishing': False,
    },
}


def seed_cold_start_priors(simulation_id: str) -> None:
    from app.extensions import db
    from app.models.bayesian import BayesianPosterior
    for action_type, prior_value in COLD_START_PRIORS.items():
        key = f'yield:{action_type}'
        existing = BayesianPosterior.query.filter_by(
            simulation_id=simulation_id, posterior_key=key
        ).first()
        if not existing:
            db.session.add(BayesianPosterior(
                simulation_id=simulation_id,
                posterior_key=key,
                value=prior_value,
            ))
    try:
        db.session.commit()
    except Exception as exc:
        logger.warning('Cold start prior seeding failed: %s', exc)
        db.session.rollback()


def _is_cold_start(simulation_id: str) -> bool:
    from app.models.layer6 import Layer6Cycle
    return not Layer6Cycle.query.filter_by(simulation_id=simulation_id).first()


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _months_since(created_at: datetime) -> int:
    now = datetime.utcnow()
    return (now.year - created_at.year) * 12 + (now.month - created_at.month)


EXPLORE_CYCLES = 19
TRANSITION_CYCLES = 3


def determine_phase(simulation_id: str, cycle_number: int) -> str:
    if cycle_number <= EXPLORE_CYCLES:
        return 'explore'
    if cycle_number <= EXPLORE_CYCLES + TRANSITION_CYCLES:
        return 'transition'
    # Re-explore trigger: trailing-3-cycle yield avg < 50% of peak
    if _should_re_explore(simulation_id):
        return 'explore'
    return 'exploit'


def _should_re_explore(simulation_id: str) -> bool:
    from app.models.layer6 import Layer6Cycle, Layer6Outcome
    cycles = Layer6Cycle.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).all()
    if len(cycles) < 4:
        return False

    def _cycle_yield(cyc):
        outs = Layer6Outcome.query.filter_by(simulation_id=simulation_id).filter(
            Layer6Outcome.created_at >= cyc.cycle_started_at,
            Layer6Outcome.created_at <= (cyc.cycle_completed_at or cyc.cycle_started_at),
        ).all()
        proj = sum(float(o.projected_income) for o in outs) or 1
        actual = sum(float(o.actual_income) for o in outs)
        return actual / proj

    trailing = [_cycle_yield(c) for c in cycles[:3]]
    peak = max(_cycle_yield(c) for c in cycles)
    return (sum(trailing) / 3) < (peak * 0.50)


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


PHASE_WEIGHTS = {
    'explore':    {'yield': 0.20, 'noise': 0.40, 'dependency': 0.30, 'layer': 0.10, 'cost': 0.00},
    'transition': {'yield': 0.40, 'noise': 0.15, 'dependency': 0.25, 'layer': 0.10, 'cost': 0.10},
    'exploit':    {'yield': 0.50, 'noise': 0.00, 'dependency': 0.25, 'layer': 0.10, 'cost': 0.15},
}


def score_action(action_type: str, source_layer: int, outcomes_for_layer: list[dict],
                 unblocks_count: int, phase: str, layer_index: int,
                 _noise_seed: float = None) -> float:
    """
    Phase-dependent Bayesian priority score for an action.
    """
    import random as _rnd
    w = PHASE_WEIGHTS.get(phase, PHASE_WEIGHTS['exploit'])
    alpha, beta = _compute_posterior(outcomes_for_layer)
    posterior_mean = _beta_mean(alpha, beta)
    dependency_value = min(1.0, math.log1p(unblocks_count) * 0.5)
    layer_weight = 1.0 - (layer_index - 1) / 5.0
    noise = (_noise_seed if _noise_seed is not None else _rnd.random())
    cost_efficiency = 0.5  # neutral until FR-ORCH-20 cost data available
    score = (
        w['yield'] * posterior_mean +
        w['noise'] * noise +
        w['dependency'] * dependency_value +
        w['layer'] * layer_weight +
        w['cost'] * cost_efficiency
    )
    return round(score, 6)


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


# ---------------------------------------------------------------------------
# Integration awareness
# ---------------------------------------------------------------------------

# Which integrations meaningfully boost which action types.
# Apollo already helps automatically via ProspectResearchEngine._has_integration();
# these weights are applied on top of the Bayesian score so the orchestrator
# prefers integration-enabled actions when relevant providers are connected.
INTEGRATION_BOOST: dict[str, dict[str, float]] = {
    'apollo':     {
        'cold_email_campaign': 0.15, 'consulting_outreach': 0.15,
        'outreach_email': 0.12, 'referral_network': 0.08,
    },
    'cal.com':    {
        'booking_page': 0.20, 'consulting_proposal': 0.08,
    },
    'pandadoc':   {
        'consulting_proposal': 0.18, 'consulting_agreement': 0.18,
    },
    'convertkit': {
        'launch_email_sequence': 0.18, 'newsletter_monetization': 0.15,
        'funnel_design': 0.12, 'waitlist_landing_page': 0.10,
    },
}


def _get_active_integrations(user_id: str) -> dict:
    """Return {provider: UserIntegration} for all connected (non-expired) integrations."""
    try:
        from app.models.integration import UserIntegration
        rows = UserIntegration.query.filter_by(user_id=user_id).all()
        return {
            r.provider: r for r in rows
            if r.is_connected and not r.is_expired and r.health_status != 'disabled'
        }
    except Exception as exc:
        logger.warning('Could not load integrations for user %s: %s', user_id, exc)
        return {}


def _integration_boost_for_action(action_type: str, active_integrations: dict) -> float:
    """Total score boost from all connected integrations for this action."""
    total = 0.0
    for provider, boosts in INTEGRATION_BOOST.items():
        if provider in active_integrations and action_type in boosts:
            total += boosts[action_type]
    return min(total, 0.30)  # cap so integration alone can't dominate


def _build_integration_user_inputs(action_type: str, active_integrations: dict,
                                   sim) -> dict:
    """
    Inject integration-specific context into user_inputs for orchestrator-dispatched
    actions. Agents inspect these keys to tailor their output or trigger API calls.
    """
    import json as _json
    extras: dict = {}

    # Cal.com — inject booking URL for booking_page and proposal agents
    if 'cal.com' in active_integrations:
        cal = active_integrations['cal.com']
        booking_url = ''
        if cal.meta_json:
            try:
                meta = _json.loads(cal.meta_json)
                booking_url = meta.get('booking_url') or meta.get('username', '')
                if booking_url and not booking_url.startswith('http'):
                    booking_url = f'https://cal.com/{booking_url}'
            except Exception:
                pass
        if booking_url:
            extras['booking_url'] = booking_url
            extras['calendar_platform'] = 'cal.com'

    # PandaDoc — signal that document sending is available
    if 'pandadoc' in active_integrations:
        extras['send_via_pandadoc'] = True

    # ConvertKit — signal that list/sequence push is available
    if 'convertkit' in active_integrations:
        extras['push_to_convertkit'] = True

    # Apollo — signal availability (ProspectResearchEngine already checks natively,
    # but agents can use this flag to tailor copy e.g. "will be sent via Apollo")
    if 'apollo' in active_integrations:
        extras['apollo_send_enabled'] = True

    return extras


def build_eligible_actions(simulation_id: str, config, completed_types: set[str],
                            phase: str) -> list[dict[str, Any]]:
    """
    Return all agent action types eligible for dispatch this cycle, with scores.
    Eligibility: prerequisites complete, not blocked, not already queued/running.
    """
    from app.services.claude import AGENT_ACTION_TYPES
    from app.models.layer6 import Layer6ActionQueue

    blocked = set(config.blocked_actions)
    channel_approvals = config.channel_approvals

    # Gather action types actively in flight (dispatched only — queued rows from prior
    # cycles that were never selected should remain eligible for re-scoring)
    in_flight = {
        r.action_type for r in Layer6ActionQueue.query.filter(
            Layer6ActionQueue.simulation_id == simulation_id,
            Layer6ActionQueue.status == Layer6ActionQueue.STATUS_DISPATCHED,
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

def _dispatch_with_celery(dispatch_entries, simulation_id):
    from celery import chain as celery_chain, group as celery_group
    from app.tasks.layer6 import dispatch_layer6_action

    within_bounds = [e for e, ok in dispatch_entries if ok]
    if not within_bounds:
        return

    action_types_in_cycle = {e.action_type for e in within_bounds}

    independent, dependent = [], []
    for entry in within_bounds:
        prereqs = ACTION_PREREQUISITES.get(entry.action_type, [])
        same_cycle_prereqs = [p for p in prereqs if p in action_types_in_cycle]
        if same_cycle_prereqs:
            dependent.append(entry)
        else:
            independent.append(entry)

    ind_sigs = [dispatch_layer6_action.si(e.id) for e in independent]
    dep_sigs = [dispatch_layer6_action.si(e.id) for e in dependent]

    if ind_sigs and dep_sigs:
        celery_chain(celery_group(*ind_sigs), *dep_sigs).delay()
    elif ind_sigs:
        celery_group(*ind_sigs).delay()
    elif dep_sigs:
        celery_chain(*dep_sigs).delay()


def _snapshot_posteriors(simulation_id: str, cycle_id: str) -> None:
    """Snapshot all Bayesian posteriors at this cycle's Score step (FR-DIFF-10)."""
    from app.extensions import db
    from app.models.bayesian import BayesianPosterior
    from app.models.layer6 import CyclePosteriorSnapshot

    posteriors = BayesianPosterior.query.filter_by(simulation_id=simulation_id).all()
    for bp in posteriors:
        # posterior_key is 'yield:action_type' — extract action_type
        parts = bp.posterior_key.split(':', 1)
        action_type = parts[1] if len(parts) == 2 else bp.posterior_key
        snap = CyclePosteriorSnapshot(
            simulation_id=simulation_id,
            cycle_id=cycle_id,
            action_type=action_type,
            posterior_value=bp.value,
        )
        db.session.add(snap)
    if posteriors:
        db.session.commit()


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

    sim = Simulation.query.get(simulation_id)
    if not sim:
        raise ValueError(f'Simulation {simulation_id} not found')

    config = Layer6Config.query.filter_by(simulation_id=simulation_id).first()
    if not config or not config.is_active:
        raise ValueError(f'Layer 6 not configured or inactive for {simulation_id}')

    # Acquire Redis cycle lock — skip if another worker is running this simulation
    if not _acquire_cycle_lock(simulation_id):
        logger.info('Cycle lock held for %s — skipping', simulation_id)
        return {'skipped': True, 'reason': 'cycle_locked'}

    # Recover stale dispatched entries — anything still "dispatched" after 30 min
    # means the sync execution thread died or the Celery worker never ran.
    # Mark them failed so they become eligible for re-dispatch this cycle.
    stale_cutoff = datetime.utcnow() - timedelta(minutes=30)
    stale_entries = Layer6ActionQueue.query.filter(
        Layer6ActionQueue.simulation_id == simulation_id,
        Layer6ActionQueue.status == Layer6ActionQueue.STATUS_DISPATCHED,
        Layer6ActionQueue.dispatched_at < stale_cutoff,
    ).all()
    for s in stale_entries:
        s.status = Layer6ActionQueue.STATUS_FAILED
        logger.warning('Recovered stale dispatched entry %s (%s)', s.id, s.action_type)
    if stale_entries:
        db.session.flush()

    # Get cycle number first (needed for determine_phase)
    last_cycle = Layer6Cycle.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).first()
    cycle_number = (last_cycle.cycle_number + 1) if last_cycle else 1

    # Determine phase using cycle-number-based 37% rule
    phase = determine_phase(simulation_id, cycle_number)

    # Enforce 5-8 agents per cycle (FR-ORCH-07)
    n_to_dispatch = max(5, min(8, config.actions_per_cycle))

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

    # Load active integrations once — used for scoring boost and user_input injection
    active_integrations = _get_active_integrations(sim.user_id)
    logger.info('Active integrations for %s: %s', simulation_id, list(active_integrations.keys()))

    # --- SCORE: build eligible action list with Bayesian scores ---
    outcomes = Layer6Outcome.query.filter_by(simulation_id=simulation_id).all()
    outcomes_by_layer: dict[int, list[dict]] = {}
    for o in outcomes:
        outcomes_by_layer.setdefault(o.layer_number, []).append(o.to_dict())

    eligible = build_eligible_actions(
        simulation_id, config, completed_types, phase=phase
    )

    all_eligible_types = [e['action_type'] for e in eligible]
    scored = []
    import hashlib as _hl
    for e in eligible:
        layer_outcomes = outcomes_by_layer.get(e['source_layer'], [])
        unblocks = _count_unblocked(e['action_type'], completed_types, all_eligible_types)
        _seed = int(_hl.md5(f'{e["action_type"]}{cycle_number}'.encode()).hexdigest(), 16) % 10000 / 10000
        priority = score_action(
            action_type=e['action_type'],
            source_layer=e['source_layer'],
            outcomes_for_layer=layer_outcomes,
            unblocks_count=unblocks,
            phase=phase,
            layer_index=e['layer_index'],
            _noise_seed=_seed,
        )
        # Apply integration boost on top of Bayesian score
        priority = round(priority + _integration_boost_for_action(
            e['action_type'], active_integrations), 6)
        scored.append({**e, 'priority_score': priority, 'unblocks': unblocks})

    # Exploration: ensure at least one action per layer represented
    if phase == 'explore':
        scored = _ensure_layer_diversity(scored, n_to_dispatch)
    else:
        scored.sort(key=lambda x: x['priority_score'], reverse=True)
        scored = scored[:n_to_dispatch]

    dispatched_count = 0
    escalated_count = 0
    dispatch_entries: list[tuple[Layer6ActionQueue, bool]] = []

    # --- SCHEDULE: persist ALL scored actions; dispatch only top N ---
    to_dispatch = {a['action_type'] for a in scored[:n_to_dispatch]}

    for action in scored:
        is_top_n = action['action_type'] in to_dispatch
        is_within_bounds, reason = (
            _check_autonomy_bounds(action['action_type'], config) if is_top_n
            else (False, '')
        )

        entry = Layer6ActionQueue(
            simulation_id=simulation_id,
            cycle_id=cycle.id,
            source_layer=action['source_layer'],
            action_type=action['action_type'],
            priority_score=action['priority_score'],
        )

        if not is_top_n:
            entry.status = Layer6ActionQueue.STATUS_QUEUED  # scored but not selected
        elif is_within_bounds:
            entry.status = Layer6ActionQueue.STATUS_DISPATCHED
            entry.dispatched_at = datetime.utcnow()
            dispatched_count += 1
        else:
            entry.status = Layer6ActionQueue.STATUS_ESCALATED
            entry.escalation_reason = reason
            escalated_count += 1

        db.session.add(entry)
        if is_top_n:
            dispatch_entries.append((entry, is_within_bounds))

    db.session.flush()  # Populate entry IDs

    # Write execution log entries for dispatched / escalated only
    for entry, within_bounds in dispatch_entries:
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

    # Generate cycle reasoning summary
    reasoning = _generate_cycle_reasoning(phase, scored[:n_to_dispatch],
                                          dispatched_count, escalated_count)

    # Update cycle record
    cycle.actions_scored = len(scored)
    cycle.actions_dispatched = dispatched_count
    cycle.actions_escalated = escalated_count
    cycle.orchestrator_reasoning = reasoning
    cycle.cycle_completed_at = datetime.utcnow()

    # Generate plain-language user insight (ENH-08)
    try:
        cycle.user_insight = _generate_user_insight(phase, scored[:n_to_dispatch], cycle_number)
    except Exception as _ue:
        logger.warning('User insight generation failed: %s', _ue)

    db.session.commit()

    # Snapshot Bayesian posteriors for Cycle Diff (FR-DIFF-10)
    try:
        _snapshot_posteriors(simulation_id, cycle.id)
    except Exception as _spe:
        logger.warning('Posterior snapshot failed for cycle %s: %s', cycle.id, _spe)

    # Fire escalation notifications (best-effort, must not raise)
    try:
        from app.services.notification_service import send_notification as _send_notif
        from flask import request as _req, has_request_context as _hrc
        _base = ''
        if _hrc():
            _base = _req.host_url.rstrip('/')
        for _entry, _within in dispatch_entries:
            if not _within:
                _action_label = _entry.action_type.replace('_', ' ').title()
                _send_notif(
                    user_id=sim.user_id,
                    notification_type='escalation',
                    title=f'{_action_label} needs your approval',
                    body=(
                        f'The orchestrator cannot continue until you approve or reject '
                        f'this action: {_action_label}. '
                        f'Reason: {_entry.escalation_reason or "autonomy boundary exceeded"}.'
                    ),
                    cta_url=f'{_base}/simulations/{simulation_id}/layer6',
                    cta_label='Review in GCC →',
                    simulation_id=simulation_id,
                    priority='high',
                )
    except Exception as _ne:
        logger.warning('Escalation notification failed: %s', _ne)

    # Dispatch actions — skip Celery entirely when no Redis broker is configured
    from flask import current_app as _ca
    _has_redis = bool(_ca.config.get('REDIS_URL'))
    _app_obj = _ca._get_current_object()

    if _has_redis:
        _dispatch_with_celery(dispatch_entries, simulation_id)
    else:
        # Run in a background thread so the web response returns immediately.
        # GoDaddy shared hosting kills long-running requests before Claude responds.
        import threading as _threading
        for entry, within_bounds in dispatch_entries:
            if within_bounds:
                _eid = entry.id

                def _bg(eid=_eid, app=_app_obj):
                    with app.app_context():
                        from app.extensions import db as _db
                        from app.models.layer6 import Layer6ActionQueue as _Q
                        fresh = _db.session.get(_Q, eid)
                        if fresh and fresh.status == _Q.STATUS_DISPATCHED:
                            _execute_action_sync(fresh)

                _threading.Thread(target=_bg, daemon=True).start()

    # Check for re-calibration trigger
    _check_recalibration(simulation_id, config)

    # Release Redis cycle lock and seed cold start priors on first cycle
    _release_cycle_lock(simulation_id)
    if cycle_number == 1:
        try:
            seed_cold_start_priors(simulation_id)
        except Exception as exc:
            logger.warning('Cold start prior seeding failed: %s', exc)

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

    from utils.model_router import get_tier
    _active_integrations = _get_active_integrations(sim.user_id) if sim else {}
    _injected_inputs = _build_integration_user_inputs(
        entry.action_type, _active_integrations, sim
    )
    try:
        result = execute_agent_action(
            action_type=entry.action_type,
            layer_number=entry.source_layer,
            expertise_zone=sim.expertise_zone if sim else '',
            parsed_text=parsed_text,
            user_inputs=_injected_inputs,
            user_id=sim.user_id if sim else None,
            simulation_id=entry.simulation_id,
            dispatch_source='orchestrator',
            action_id=agent_action.id,
        )
        artifact = result if isinstance(result, str) else str(result)
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
            model_tier=get_tier(entry.action_type).value,
        ))
        db.session.commit()
        logger.info('Layer 6 sync action %s (%s) completed', entry.id, entry.action_type)

        # Post-completion: dispatch outreach emails based on trust level
        if entry.action_type == 'outreach_email':
            try:
                from app.tasks.agent import _dispatch_outreach_emails
                _dispatch_outreach_emails(agent_action.id, entry.simulation_id, sim.user_id if sim else None)
            except Exception as _de:
                logger.warning('outreach_email post-send dispatch failed: %s', _de)

        if entry.action_type == 'cold_email_campaign':
            try:
                from app.tasks.agent import _dispatch_cold_email_campaign
                _dispatch_cold_email_campaign(agent_action.id, entry.simulation_id, sim.user_id if sim else None)
            except Exception as _de:
                logger.warning('cold_email_campaign post-send dispatch failed: %s', _de)

        # Deploy artifact to integration chain (FR-WIRE-01)
        try:
            from app.services.wire_service import deploy_to_integration
            deploy_to_integration(
                user_id=sim.user_id if sim else '',
                simulation_id=entry.simulation_id,
                action_id=agent_action.id,
                action_type=entry.action_type,
                artifact=artifact,
                layer_number=entry.source_layer,
            )
        except Exception as _we:
            logger.warning('wire deploy failed for %s: %s', entry.action_type, _we)

        # Notify user of agent completion (best-effort)
        try:
            from app.services.notification_service import send_notification as _sn
            from flask import request as _req2, has_request_context as _hrc2
            _base2 = _req2.host_url.rstrip('/') if _hrc2() else ''
            _label2 = entry.action_type.replace('_', ' ').title()
            _sn(
                user_id=sim.user_id,
                notification_type='agent_complete',
                title=f'Your {_label2} agent completed',
                body=f'{_label2} is ready. {(artifact or "")[:120]}',
                cta_url=f'{_base2}/simulations/{entry.simulation_id}/layer6',
                cta_label='View in GCC →',
                simulation_id=entry.simulation_id,
            )
        except Exception as _ne2:
            logger.warning('Agent complete notification failed: %s', _ne2)

    except Exception as exc:
        agent_action.status = AgentAction.STATUS_FAILED
        agent_action.error_message = str(exc)
        entry.status = Layer6ActionQueue.STATUS_FAILED
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
        # outreach_email and cold_email_campaign are NOT here — they generate draft
        # artifacts only; actual email sending is gated by the post-completion dispatch
        # hooks (_dispatch_outreach_emails / _dispatch_cold_email_campaign).
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


def _generate_user_insight(phase: str, top_actions: list[dict], cycle_number: int) -> str:
    """Plain-language narration of what the AI prioritised this cycle (ENH-08)."""
    if not top_actions:
        return ''
    try:
        import anthropic
        from flask import current_app
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
        action_names = ', '.join(a['action_type'].replace('_', ' ') for a in top_actions[:3])
        prompt = (
            f"Cycle {cycle_number}, Phase: {phase.upper()}. "
            f"Top actions I selected: {action_names}.\n\n"
            "In 1-2 sentences of warm, plain English, tell the user what you prioritised "
            "and why it matters for their wealth-building journey. Speak as 'I' (the AI). "
            "Start with the action. Do not use words like Bayesian, posterior, or technical terms."
        )
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=120,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        logger.warning('_generate_user_insight failed: %s', exc)
        return ''


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

    from app.models.agent_action import AgentAction
    completed_by_layer: dict[int, int] = {}
    for a in AgentAction.query.filter_by(
        simulation_id=simulation_id, status=AgentAction.STATUS_COMPLETE
    ).all():
        completed_by_layer[a.layer_number] = completed_by_layer.get(a.layer_number, 0) + 1

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
            'completed_count': completed_by_layer.get(layer.layer_number, 0),
        })

    total_actual = sum(m['actual_income'] for m in layer_metrics)
    total_projected = sum(m['projected_income'] for m in layer_metrics)

    # ROI ratio (ENH-06): revenue captured ÷ simulation cost
    roi_ratio = 0.0
    if sim.amount_charged_cents and sim.amount_charged_cents > 0:
        roi_ratio = round(total_actual / (sim.amount_charged_cents / 100), 1)

    numbers = {
        'layers': layer_metrics,
        'total_actual': total_actual,
        'total_projected': total_projected,
        'total_variance': total_actual - total_projected,
        'roi_ratio': roi_ratio,
        'simulation_cost_usd': (sim.amount_charged_cents or 0) / 100,
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


# ---------------------------------------------------------------------------
# Journey data — mirrors get_journey() API logic, called server-side for
# the advisor GCC view to avoid the _get_sim_or_404 advisor check round-trip.
# ---------------------------------------------------------------------------

_LAYER_STEPS = {
    1: [('cold_email_campaign','Cold email'),('consulting_outreach','Outreach'),
        ('rate_card','Rate card'),('role_search','Role search'),
        ('linkedin_optimization','LinkedIn opt.'),('booking_page','Booking page'),
        ('consulting_proposal','Proposal'),('sow','SOW'),('agreement','Agreement'),
        ('referral_network','Referral net.'),('negotiation_script','Negotiation')],
    2: [('speaker_proposals','Speaking prop.'),('speaker_fee_rider','Speaker fee'),
        ('group_coaching_program','Group coaching'),('corporate_training_pitch','Corp. training'),
        ('workshop_curriculum','Workshop'),('waitlist_landing_page','Waitlist page'),
        ('alumni_reactivation','Alumni reactiv.'),('roi_calculator','ROI calculator')],
    3: [('course_curriculum','Course curric.'),('competitor_research','Competitor res.'),
        ('product_sales_page','Sales page'),('ebook_gumroad','E-book'),
        ('ab_test_plan','A/B test plan'),('membership_structure','Membership'),
        ('launch_email_sequence','Launch sequence'),('affiliate_program','Affiliate prog.'),
        ('testimonial_system','Testimonials'),('lapsed_buyer_winback','Lapsed buyer')],
    4: [('seo_content_calendar','SEO calendar'),('lead_magnet_funnel','Lead magnet'),
        ('newsletter_monetization','Newsletter'),('saas_product_spec','SaaS spec'),
        ('ip_licensing','IP licensing'),('affiliate_partnerships','Affiliate part.'),
        ('youtube_podcast_strategy','YouTube/pod.'),('community_flywheel','Community'),
        ('programmatic_ads','Prog. ads'),('winback_campaign','Win-back')],
    5: [('income_allocation','Income alloc.'),('compound_growth_model','Projections'),
        ('fund_recommendations','Fund recs.'),('ips','IPS'),('real_estate','Real estate'),
        ('tax_optimization','Tax optim.'),('entity_structure','Entity struct.'),
        ('dca_schedule','DCA schedule'),('insurance','Insurance'),
        ('estate_planning','Estate plan.')],
}

_LAYER_BLOCKERS = {
    2: 'consulting_outreach',
    3: 'group_coaching_program',
    4: 'product_sales_page',
    5: 'lead_magnet_funnel',
}

_UNLOCK_NOTES = {
    2: 'Activates once your Layer 1 consulting outreach is complete.',
    3: 'Activates once your Layer 2 group coaching program is complete.',
    4: 'Activates once your Layer 3 course curriculum and sales page are in place.',
    5: 'Activates once your Layer 4 lead magnet funnel is in place.',
}


def build_journey_data(simulation_id: str) -> dict:
    """Build per-layer journey step data for the GCC Journey tab.
    Mirrors get_journey() in layer6/routes.py — used server-side for advisor view."""
    from app.models.layer6 import Layer6Config, Layer6Cycle, Layer6ActionQueue
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation
    from datetime import datetime as _dt, timedelta

    _sim = Simulation.query.get(simulation_id)
    _unlock_all = _sim.unlock_all_layers if _sim else False

    completed_by_type: dict = {}
    for a in AgentAction.query.filter_by(
        simulation_id=simulation_id, status=AgentAction.STATUS_COMPLETE,
    ).order_by(AgentAction.completed_at.asc()).all():
        completed_by_type[a.action_type] = a
    completed_types = set(completed_by_type)

    queued_by_type: dict = {}
    for q in Layer6ActionQueue.query.filter(
        Layer6ActionQueue.simulation_id == simulation_id,
        Layer6ActionQueue.status.in_([
            Layer6ActionQueue.STATUS_QUEUED,
            Layer6ActionQueue.STATUS_DISPATCHED,
        ])
    ).order_by(Layer6ActionQueue.created_at.asc()).all():
        queued_by_type[q.action_type] = q

    escalated_by_layer: dict = {}
    for e in Layer6ActionQueue.query.filter_by(
        simulation_id=simulation_id, status=Layer6ActionQueue.STATUS_ESCALATED,
    ).all():
        escalated_by_layer.setdefault(e.source_layer, []).append(e)

    config = Layer6Config.query.filter_by(simulation_id=simulation_id).first()
    last_cycle = Layer6Cycle.query.filter_by(simulation_id=simulation_id).order_by(
        Layer6Cycle.cycle_started_at.desc()
    ).first()
    cadence_map = {'daily': 1, 'every_3_days': 3, 'weekly': 7}
    eta_text = 'next cycle'
    if config and last_cycle:
        days = cadence_map.get(config.cadence, 1)
        next_run = last_cycle.cycle_started_at + timedelta(days=days)
        delta = next_run - _dt.utcnow()
        if delta.total_seconds() > 0:
            hours = delta.total_seconds() / 3600
            eta_text = f'in {int(hours)} hrs' if hours < 48 else f'in {int(hours / 24)} days'

    result = {}
    for layer_num, seq in _LAYER_STEPS.items():
        total = len(seq)
        blocker = _LAYER_BLOCKERS.get(layer_num)
        is_blocked = bool(blocker and blocker not in completed_types) and not _unlock_all
        steps = []
        for i, (atype, label) in enumerate(seq):
            artifact_fields = []
            artifact_version = None
            if atype in completed_types:
                status = 'complete'
                a = completed_by_type[atype]
                action_id = a.id
                raw = a.user_inputs or {}
                artifact_fields = [[k.replace('_', ' ').title(), str(v)[:80]]
                                   for k, v in list(raw.items())[:4] if v]
                artifact_version = 1
            elif atype in queued_by_type:
                q = queued_by_type[atype]
                status = 'running' if q.status == Layer6ActionQueue.STATUS_DISPATCHED else 'queued'
                action_id = q.agent_action_id
            else:
                status = 'pending'
                action_id = None
            steps.append({'seq': i + 1, 'type': atype, 'label': label,
                          'status': status, 'action_id': action_id,
                          'artifact_fields': artifact_fields,
                          'artifact_version': artifact_version})

        completed_count = sum(1 for s in steps if s['status'] == 'complete')
        layer_esc = escalated_by_layer.get(layer_num, [])
        suggested = None
        if not is_blocked:
            if layer_esc:
                suggested = {'state': 'escalated',
                             'label': f'{len(layer_esc)} actions need your approval',
                             'type': None, 'action_id': None}
            elif completed_count >= total:
                suggested = {'state': 'all_complete',
                             'label': 'All actions complete for this layer',
                             'type': None, 'action_id': None}
            else:
                for s in steps:
                    if s['status'] in ('queued', 'pending'):
                        prefix = '▶ Suggested first action' if completed_count == 0 else '▶ Suggested action'
                        suggested = {'state': 'queued',
                                     'label': f"{prefix}: {s['label']}",
                                     'type': s['type'], 'action_id': s['action_id']}
                        break

        latest_artifact = None
        for s in reversed(steps):
            if s['status'] == 'complete':
                a = completed_by_type[s['type']]
                raw = a.user_inputs or {}
                latest_artifact = {
                    'action_type': s['type'], 'label': s['label'], 'version': 1,
                    'fields': {k.replace('_', ' ').title(): str(v)[:80]
                               for k, v in list(raw.items())[:4] if v},
                }
                break

        result[str(layer_num)] = {
            'is_blocked': is_blocked,
            'unlock_note': _UNLOCK_NOTES.get(layer_num, '') if is_blocked else '',
            'total': total,
            'completed_count': completed_count,
            'steps': steps,
            'suggested': suggested,
            'latest_artifact': latest_artifact,
            'next_run_eta': eta_text,
            'escalated': len(layer_esc),
        }
    return result
