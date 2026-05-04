from celery_worker import celery
import logging

logger = logging.getLogger(__name__)


@celery.task
def execute_agent_action_task(action_id: str):
    """Async Celery task: execute an AgentAction, create ArtifactVersion, propagate staleness."""
    from datetime import datetime
    from app.extensions import db
    from app.models.agent_action import AgentAction
    from app.models.simulation import Simulation
    from app.models.resume import Resume
    from app.services.claude import execute_agent_action
    from app.models.artifact import ArtifactVersion
    from utils.id_gen import generate_id

    action = AgentAction.query.get(action_id)
    if not action:
        logger.error('AgentAction %s not found', action_id)
        return

    action.status = AgentAction.STATUS_IN_PROGRESS
    db.session.commit()

    try:
        sim = Simulation.query.get(action.simulation_id)
        resume = Resume.query.get(sim.resume_id) if sim else None
        parsed_text = resume.parsed_text if resume else ''

        artifact = execute_agent_action(
            action_type=action.action_type,
            layer_number=action.layer_number,
            expertise_zone=sim.expertise_zone if sim else '',
            parsed_text=parsed_text,
            user_inputs=action.user_inputs,
            user_id=action.created_by,
            simulation_id=action.simulation_id,
            dispatch_source='user_rerun',
        )

        # Re-fetch to honour a stop request that arrived during execution
        db.session.expire(action)
        action = AgentAction.query.get(action_id)
        if action.status == AgentAction.STATUS_FAILED and action.error_message == 'Stopped by user':
            logger.info('AgentAction %s was stopped during execution — discarding result', action_id)
            return

        action.artifact = artifact
        action.status = AgentAction.STATUS_COMPLETE
        action.completed_at = datetime.utcnow()
        db.session.flush()

        # Determine version number (count existing versions + 1)
        prior_count = ArtifactVersion.query.filter_by(action_id=action_id).count()
        new_version_number = prior_count + 1

        # Demote any previous current version
        if prior_count:
            ArtifactVersion.query.filter_by(
                action_id=action_id, is_current=True,
            ).update({'is_current': False})

        # Generate change summary if this is a re-run
        change_summary = None
        if new_version_number > 1:
            change_summary = _generate_change_summary(action, new_version_number)

        version_label = f'v{new_version_number} — {datetime.utcnow().strftime("%b %d %Y")}'

        av = ArtifactVersion(
            id=generate_id(),
            action_id=action_id,
            simulation_id=action.simulation_id,
            layer_number=action.layer_number,
            action_type=action.action_type,
            version_number=new_version_number,
            version_label=version_label,
            content=artifact,
            file_type='text',
            change_summary=change_summary,
            is_current=True,
            created_by='user',
        )
        av.prefill_inputs = action.user_inputs
        db.session.add(av)
        db.session.commit()

        # Update upstream_action_id on matching ArtifactDependency rows so staleness works
        _link_upstream_dependencies(action)

        # Propagate staleness to downstream dependencies
        from app.services.prefill_engine import propagate_staleness
        propagate_staleness(action.simulation_id, action_id, new_version_number)

        logger.info('AgentAction %s completed — version %d created', action_id, new_version_number)

    except Exception as exc:
        logger.error('AgentAction %s failed: %s', action_id, exc)
        action.status = AgentAction.STATUS_FAILED
        action.error_message = str(exc)
        db.session.commit()


def _generate_change_summary(action, new_version_number: int) -> str:
    """Ask Claude to summarise what changed between the new and prior inputs."""
    try:
        import anthropic
        from flask import current_app
        from app.models.artifact import ArtifactVersion

        prior = ArtifactVersion.query.filter_by(
            action_id=action.id,
        ).order_by(ArtifactVersion.version_number.desc()).first()

        if not prior:
            return None

        prior_inputs = prior.prefill_inputs
        new_inputs = action.user_inputs

        if prior_inputs == new_inputs:
            return f'Re-run with identical inputs as v{prior.version_number}.'

        changes = []
        all_keys = set(prior_inputs) | set(new_inputs)
        for k in sorted(all_keys):
            old_val = prior_inputs.get(k, '(not set)')
            new_val = new_inputs.get(k, '(not set)')
            if old_val != new_val:
                changes.append(f'{k}: "{old_val}" → "{new_val}"')

        if not changes:
            return f'Re-run of v{prior.version_number}.'

        from utils.model_router import get_model
        model = get_model('artifact_change_summary')
        client = anthropic.Anthropic()
        changes_str = '; '.join(changes[:5])  # cap to avoid token waste
        message = client.messages.create(
            model=model,
            max_tokens=80,
            messages=[{
                'role': 'user',
                'content': (
                    f'Summarise this form input change in one concise sentence (max 20 words): {changes_str}'
                ),
            }],
        )
        return message.content[0].text.strip()
    except Exception as e:
        logger.debug('Change summary generation failed: %s', e)
        return None


def _link_upstream_dependencies(action):
    """Set upstream_action_id on ArtifactDependency rows that match this action type."""
    try:
        from app.extensions import db
        from app.models.artifact import ArtifactDependency

        deps = ArtifactDependency.query.filter_by(
            simulation_id=action.simulation_id,
            upstream_action_type=action.action_type,
            upstream_action_id=None,
        ).all()

        for dep in deps:
            dep.upstream_action_id = action.id
            dep.upstream_version_used = ArtifactVersion_count(action.id)

        if deps:
            db.session.commit()
    except Exception as e:
        logger.debug('_link_upstream_dependencies failed: %s', e)


def ArtifactVersion_count(action_id: str) -> int:
    from app.models.artifact import ArtifactVersion
    return ArtifactVersion.query.filter_by(action_id=action_id).count()
