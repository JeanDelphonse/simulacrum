"""
Layer 6 Celery tasks.

  dispatch_layer6_action  — execute one orchestrator-queued agent action
  run_layer6_cycle_task   — periodic beat task; runs a cycle for each active L6 config
"""
import logging
from celery_worker import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, acks_late=True, reject_on_worker_lost=True,
             time_limit=300, max_retries=1, default_retry_delay=60)
def dispatch_layer6_action(self, queue_entry_id: str):
    """
    Execute a single action dispatched by the Layer 6 orchestrator.
    Reuses the existing agent action execution pipeline, linking the result
    back to the Layer6ActionQueue entry.
    """
    from app.extensions import db
    from app.models.layer6 import Layer6ActionQueue, Layer6ExecutionLog
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation
    from app.models.resume import Resume
    from app.services.claude import execute_agent_action
    from datetime import datetime

    entry = Layer6ActionQueue.query.get(queue_entry_id)
    if not entry:
        logger.warning('Layer6ActionQueue entry %s not found', queue_entry_id)
        return

    # Atomic lock: set agent_action_id placeholder to prevent concurrent runs
    try:
        res = db.session.execute(
            db.text("UPDATE layer6_action_queue SET agent_action_id = 'LOCK' WHERE id = :eid AND agent_action_id IS NULL"),
            {'eid': queue_entry_id}
        )
        db.session.commit()
        if res.rowcount == 0:
            logger.warning('Layer6ActionQueue entry %s already locked/processed — skipping', queue_entry_id)
            return
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to lock Layer6ActionQueue entry %s: %s', queue_entry_id, exc)
        return

    sim = Simulation.query.get(entry.simulation_id)
    if not sim:
        logger.warning('Simulation %s not found for L6 action', entry.simulation_id)
        return

    resume = Resume.query.get(sim.resume_id) if sim.resume_id else None
    parsed_text = resume.parsed_text if resume else ''

    # Create an AgentAction record so the result shows in the simulation view
    agent_action = AgentAction(
        simulation_id=entry.simulation_id,
        layer_number=entry.source_layer,
        action_type=entry.action_type,
        status=AgentAction.STATUS_IN_PROGRESS,
    )
    db.session.add(agent_action)
    db.session.flush()

    # Re-fetch entry and update real agent_action_id
    entry = Layer6ActionQueue.query.get(queue_entry_id)
    entry.agent_action_id = agent_action.id
    db.session.commit()

    from app.services.layer6 import _get_active_integrations, _build_integration_user_inputs
    _active_integrations = _get_active_integrations(sim.user_id)
    _injected_inputs = _build_integration_user_inputs(entry.action_type, _active_integrations, sim)

    try:
        result = execute_agent_action(
            action_type=entry.action_type,
            layer_number=entry.source_layer,
            expertise_zone=sim.expertise_zone,
            parsed_text=parsed_text,
            user_inputs=_injected_inputs,
            user_id=sim.user_id,
            simulation_id=entry.simulation_id,
            action_id=agent_action.id,
            prospect_count=sim.get_prospect_count(),
        )

        artifact = result if isinstance(result, str) else str(result)
        agent_action.artifact = artifact
        agent_action.status = AgentAction.STATUS_COMPLETE
        agent_action.completed_at = datetime.utcnow()

        entry.status = Layer6ActionQueue.STATUS_COMPLETE
        entry.completed_at = datetime.utcnow()
        entry.outcome_summary = artifact[:500] if artifact else ''

        log = Layer6ExecutionLog(
            simulation_id=entry.simulation_id,
            cycle_id=entry.cycle_id,
            action_id=entry.id,
            event_type=Layer6ExecutionLog.EVENT_COMPLETED,
            actor=Layer6ExecutionLog.ACTOR_ORCHESTRATOR,
            reasoning='Action completed successfully.',
        )
        db.session.add(log)
        db.session.commit()
        logger.info('Layer 6 action %s (%s) completed', entry.id, entry.action_type)

        # Post-completion: dispatch outreach emails based on trust level
        if entry.action_type == 'outreach_email':
            try:
                from app.tasks.agent import _dispatch_outreach_emails
                _dispatch_outreach_emails(agent_action.id, entry.simulation_id, sim.user_id)
            except Exception as _de:
                logger.warning('outreach_email post-send dispatch failed: %s', _de)

        if entry.action_type == 'cold_email_campaign':
            try:
                from app.tasks.agent import _dispatch_cold_email_campaign
                _dispatch_cold_email_campaign(agent_action.id, entry.simulation_id, sim.user_id)
            except Exception as _de:
                logger.warning('cold_email_campaign post-send dispatch failed: %s', _de)

        # Create scheduled action steps for multi-step agents (SIM-PRD-STEPS-001 A.3)
        try:
            from app.services.action_step_service import create_steps_from_artifact, AGENT_STEP_CONFIG
            if entry.action_type in AGENT_STEP_CONFIG:
                _n = create_steps_from_artifact(
                    agent_action_id=agent_action.id,
                    simulation_id=entry.simulation_id,
                    action_type=entry.action_type,
                    artifact_json=artifact,
                    parent_action_id=entry.id,
                )
                if _n:
                    logger.info('Created %d action steps for %s', _n, entry.action_type)
        except Exception as _ste:
            logger.warning('create_steps_from_artifact failed for %s: %s', entry.action_type, _ste)

        # Deploy artifact to integration chain (FR-WIRE-01)
        try:
            from app.services.wire_service import deploy_to_integration
            deploy_to_integration(
                user_id=sim.user_id,
                simulation_id=entry.simulation_id,
                action_id=agent_action.id,
                action_type=entry.action_type,
                artifact=artifact,
                layer_number=entry.source_layer,
            )
        except Exception as _we:
            logger.warning('wire deploy failed for %s: %s', entry.action_type, _we)

    except Exception as exc:
        logger.exception('Layer 6 action %s failed: %s', entry.id, exc)
        agent_action.status = AgentAction.STATUS_FAILED
        agent_action.error_message = str(exc)
        db.session.commit()
        # max_retries=1: attempt retry once; on second failure mark as failed directly
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        else:
            entry.status = Layer6ActionQueue.STATUS_FAILED
            db.session.commit()


@celery.task
def cleanup_stale_actions():
    """Mark in_progress AgentActions older than 30 min as FAILED."""
    from datetime import datetime, timedelta
    from app.extensions import db
    from app.models.agent_action import AgentAction
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    stale = AgentAction.query.filter(
        AgentAction.status == AgentAction.STATUS_IN_PROGRESS,
        AgentAction.created_at < cutoff,
    ).all()
    for action in stale:
        action.status = AgentAction.STATUS_FAILED
        action.error_message = 'Timed out — marked failed by stale cleanup'
    if stale:
        db.session.commit()
        logger.info('Stale cleanup: marked %d actions as FAILED', len(stale))


@celery.task
def run_layer6_cycles():
    """
    Celery beat task — scheduled every 15 min; cadence and jitter checked inside task.
    Iterates all active Layer 6 configs and runs a cycle for those whose
    adaptive dispatch window has elapsed.
    """
    import hashlib as _hl
    from datetime import datetime, timedelta
    from app.extensions import db
    from app.models.layer6 import Layer6Config, Layer6Cycle
    from app.services.layer6 import run_orchestrator_cycle, _dispatch_window_minutes

    # Write Redis heartbeat (FR-ORCH-02)
    try:
        from flask import current_app
        redis_url = current_app.config.get('REDIS_URL')
        if redis_url:
            import redis as _redis
            r = _redis.from_url(redis_url)
            r.set('l6_beat_heartbeat', datetime.utcnow().isoformat(), ex=1800)
    except Exception as _he:
        logger.warning('Redis heartbeat write failed: %s', _he)

    CADENCE_MINUTES = {
        Layer6Config.CADENCE_DAILY: 1440,
        Layer6Config.CADENCE_THREE_DAYS: 4320,
        Layer6Config.CADENCE_WEEKLY: 10080,
        Layer6Config.CADENCE_12H: 720,
        Layer6Config.CADENCE_48H: 2880,
        Layer6Config.CADENCE_72H: 4320,
        Layer6Config.CADENCE_168H: 10080,
    }

    configs = Layer6Config.query.filter_by(is_active=True).all()
    logger.info('Layer 6 beat: checking %d active configs', len(configs))

    for cfg in configs:
        # Deterministic jitter 0-900s based on simulation_id hash (FR-ORCH-01)
        _hash_int = int(_hl.md5(cfg.simulation_id.encode()).hexdigest(), 16)
        jitter_seconds = _hash_int % 900

        dispatch_window = _dispatch_window_minutes(cfg.cadence) * 60  # convert to seconds

        last_cycle = Layer6Cycle.query.filter_by(
            simulation_id=cfg.simulation_id
        ).order_by(Layer6Cycle.cycle_started_at.desc()).first()

        if last_cycle:
            elapsed = (datetime.utcnow() - last_cycle.cycle_started_at).total_seconds()
            # Apply jitter: effectively delays the window by jitter_seconds
            if elapsed < dispatch_window + jitter_seconds:
                logger.debug('Simulation %s: dispatch window not elapsed, skipping', cfg.simulation_id)
                continue

        try:
            run_orchestrator_cycle(cfg.simulation_id)
            logger.info('Layer 6 cycle completed for simulation %s', cfg.simulation_id)
        except Exception as exc:
            logger.exception('Layer 6 cycle failed for simulation %s: %s', cfg.simulation_id, exc)
