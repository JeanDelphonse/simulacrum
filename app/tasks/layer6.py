"""
Layer 6 Celery tasks.

  dispatch_layer6_action  — execute one orchestrator-queued agent action
  run_layer6_cycle_task   — periodic beat task; runs a cycle for each active L6 config
"""
import logging
from celery_worker import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=2, default_retry_delay=30)
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
    from utils.id_gen import generate_id

    entry = Layer6ActionQueue.query.get(queue_entry_id)
    if not entry:
        logger.warning('Layer6ActionQueue entry %s not found', queue_entry_id)
        return

    sim = Simulation.query.get(entry.simulation_id)
    if not sim:
        logger.warning('Simulation %s not found for L6 action', entry.simulation_id)
        return

    resume = Resume.query.get(sim.resume_id)
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

    entry.agent_action_id = agent_action.id
    db.session.commit()

    try:
        result = execute_agent_action(
            action_type=entry.action_type,
            layer_number=entry.source_layer,
            expertise_zone=sim.expertise_zone,
            parsed_text=parsed_text,
            user_inputs={},
            user_id=sim.user_id,
            simulation_id=entry.simulation_id,
        )

        artifact = result.get('content') or result.get('artifact') or str(result)
        agent_action.artifact = artifact
        agent_action.status = AgentAction.STATUS_COMPLETE
        agent_action.completed_at = __import__('datetime').datetime.utcnow()

        from datetime import datetime
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

    except Exception as exc:
        logger.exception('Layer 6 action %s failed: %s', entry.id, exc)
        agent_action.status = AgentAction.STATUS_FAILED
        agent_action.error_message = str(exc)
        db.session.commit()
        raise self.retry(exc=exc)


@celery.task
def run_layer6_cycles():
    """
    Celery beat task — scheduled periodically (recommended: every hour).
    Iterates all active Layer 6 configs and runs a cycle for those whose
    cadence timer has elapsed.
    """
    from datetime import datetime, timedelta
    from app.extensions import db
    from app.models.layer6 import Layer6Config, Layer6Cycle
    from app.services.layer6 import run_orchestrator_cycle

    CADENCE_MINUTES = {
        Layer6Config.CADENCE_DAILY: 1440,
        Layer6Config.CADENCE_THREE_DAYS: 4320,
        Layer6Config.CADENCE_WEEKLY: 10080,
    }

    configs = Layer6Config.query.filter_by(is_active=True).all()
    logger.info('Layer 6 beat: checking %d active configs', len(configs))

    for cfg in configs:
        cadence_minutes = CADENCE_MINUTES.get(cfg.cadence, 1440)
        last_cycle = Layer6Cycle.query.filter_by(
            simulation_id=cfg.simulation_id
        ).order_by(Layer6Cycle.cycle_started_at.desc()).first()

        if last_cycle:
            elapsed = datetime.utcnow() - last_cycle.cycle_started_at
            if elapsed.total_seconds() < cadence_minutes * 60:
                logger.debug('Simulation %s: cadence not elapsed, skipping', cfg.simulation_id)
                continue

        try:
            run_orchestrator_cycle(cfg.simulation_id)
            logger.info('Layer 6 cycle completed for simulation %s', cfg.simulation_id)
        except Exception as exc:
            logger.exception('Layer 6 cycle failed for simulation %s: %s', cfg.simulation_id, exc)
