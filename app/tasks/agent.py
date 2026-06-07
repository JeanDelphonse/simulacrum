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

    # Cache scalar fields before the long execution. Long-running tasks trigger
    # _log_interaction retries that can invalidate the DB connection; if the
    # session-level connection is recycled mid-task, lazy-loading these from a
    # detached action object raises DetachedInstanceError.
    action_type      = action.action_type
    layer_number     = action.layer_number
    user_inputs      = action.user_inputs
    user_id          = action.created_by
    simulation_id    = action.simulation_id

    try:
        sim = Simulation.query.get(simulation_id)
        # Orchestrator-dispatched actions have created_by=None; fall back to
        # the simulation owner so record_agent_contacts gets a valid user_id.
        if not user_id and sim:
            user_id = sim.user_id
        resume = Resume.query.get(sim.resume_id) if sim else None
        parsed_text = resume.parsed_text if resume else ''

        artifact = execute_agent_action(
            action_type=action_type,
            layer_number=layer_number,
            expertise_zone=sim.expertise_zone if sim else '',
            parsed_text=parsed_text,
            user_inputs=user_inputs,
            user_id=user_id,
            simulation_id=simulation_id,
            dispatch_source='user_rerun',
            action_id=action_id,
        )

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
            simulation_id=simulation_id,
            layer_number=layer_number,
            action_type=action_type,
            version_number=new_version_number,
            version_label=version_label,
            content=artifact,
            file_type='text',
            change_summary=change_summary,
            is_current=True,
            created_by='user',
        )
        av.prefill_inputs = user_inputs
        db.session.add(av)
        db.session.commit()

        # Update upstream_action_id on matching ArtifactDependency rows so staleness works
        _link_upstream_dependencies(action)

        # Propagate staleness to downstream dependencies
        from app.services.prefill_engine import propagate_staleness
        propagate_staleness(simulation_id, action_id, new_version_number)

        logger.info('AgentAction %s completed — version %d created', action_id, new_version_number)

        # Post-completion: dispatch outreach emails based on trust level
        if action_type == 'outreach_email':
            try:
                _dispatch_outreach_emails(action_id, simulation_id, user_id)
            except Exception as exc:
                logger.warning('outreach_email post-send dispatch failed action=%s: %s', action_id, exc)

        if action_type == 'cold_email_campaign':
            try:
                _dispatch_cold_email_campaign(action_id, simulation_id, user_id)
            except Exception as exc:
                logger.warning('cold_email_campaign post-send dispatch failed action=%s: %s', action_id, exc)

    except Exception as exc:
        logger.error('AgentAction %s failed: %s', action_id, exc, exc_info=True)
        # Roll back any dirty/invalid transaction before writing the failure status.
        # Without this, a session left in REQUIRES_ROLLBACK state (e.g. by a failed
        # _log_run commit inside ProspectResearchEngine) will cause db.session.commit()
        # here to raise InvalidRequestError, leaving the action stuck in_progress forever.
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            action = AgentAction.query.get(action_id)
            if action:
                action.status = AgentAction.STATUS_FAILED
                action.error_message = str(exc)[:500]
                db.session.commit()
        except Exception as commit_err:
            logger.error('Could not persist FAILED status for action %s: %s', action_id, commit_err)


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


def _dispatch_outreach_emails(action_id: str, simulation_id: str, user_id: str):
    """
    Post-completion hook for outreach_email.
    Phase 1 (always): upsert every prospect with a real email into CRM as 'prospect'.
    Phase 2: if email approved → auto-send; else → create escalation ActionItem.
    """
    import json
    from app.extensions import db
    from app.models.layer6 import Layer6Config
    from app.models.artifact import ArtifactVersion
    from app.models.contact import Contact
    from utils.id_gen import generate_id

    config = Layer6Config.query.filter_by(simulation_id=simulation_id).first()
    email_approved = (
        config.channel_approvals.get('email', False) if config else False
    )

    av = ArtifactVersion.query.filter_by(
        action_id=action_id, is_current=True,
    ).first()
    if not av or not av.content:
        return

    try:
        data = json.loads(av.content)
    except Exception:
        return

    prospects = data.get('prospects', [])
    if not prospects:
        return

    _FALLBACK_EMAIL = 'valuemanager.management@gmail.com'

    # ── Phase 1: upsert ALL prospects with real emails into CRM as 'prospect' ──
    artifact_changed = False
    crm_created = 0
    for p in prospects:
        email = (p.get('email') or '').strip().lower()
        if not email or email == _FALLBACK_EMAIL:
            continue
        crm_id = p.get('crm_contact_id')
        contact = Contact.query.get(crm_id) if crm_id else None
        if not contact:
            contact = Contact.query.filter_by(user_id=user_id, email=email).first()
        if not contact:
            contact = Contact(
                id=generate_id(),
                user_id=user_id,
                first_name=p.get('first_name', ''),
                last_name=p.get('last_name', ''),
                email=email,
                job_title=p.get('job_title', ''),
                company_name=p.get('company_name', ''),
                pipeline_stage='prospect',
                source='agent_action',
                source_action_id=action_id,
            )
            db.session.add(contact)
            db.session.flush()
            crm_created += 1
        if not p.get('crm_contact_id'):
            p['crm_contact_id'] = contact.id
            artifact_changed = True

    if artifact_changed:
        av.content = json.dumps(data, ensure_ascii=False)

    try:
        db.session.commit()
        logger.info('outreach_email: upserted %d new CRM contacts for action %s', crm_created, action_id)
    except Exception as exc:
        logger.error('outreach_email CRM upsert failed for action %s: %s', action_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass

    # ── Phase 2: send or escalate ──
    drafts = [p for p in prospects if p.get('send_status') == 'draft']
    if not drafts:
        return

    if email_approved:
        from app.services.consulting_outreach_service import send_prospect_email
        sent = 0
        for idx, p in enumerate(prospects):
            if p.get('send_status') != 'draft':
                continue
            try:
                send_prospect_email(action_id, idx, user_id, simulation_id)
                sent += 1
            except Exception as exc:
                logger.warning(
                    'Auto-send failed for prospect %d action %s: %s', idx, action_id, exc,
                )
        logger.info('Auto-sent %d outreach emails for action %s', sent, action_id)
    else:
        n = len(drafts)
        from utils.action_items import create_action_item
        create_action_item(
            simulation_id=simulation_id,
            user_id=user_id,
            item_type='escalation',
            title=f'Review & send {n} outreach email{"s" if n != 1 else ""} — approval required',
            description=(
                'Your outreach emails are drafted and ready. '
                'Open the artifact to review each one, then click Send.'
            ),
            action_url=f'/artifacts/{action_id}',
            source_action_id=action_id,
            emit_sse=True,
        )


def _dispatch_cold_email_campaign(action_id: str, simulation_id: str, user_id: str):
    """
    Post-completion hook for cold_email_campaign.
    Phase 1 (always): upsert every prospect with a real email into CRM as 'prospect'.
    Phase 2: send Step 1 emails if channel approved, else create escalation ActionItem.
    Creates follow-up escalation ActionItems for Step 2/3 when Step 1 is sent.
    """
    import json
    from app.extensions import db
    from app.models.layer6 import Layer6Config
    from app.models.artifact import ArtifactVersion
    from app.models.contact import Contact, ContactActivity
    from utils.id_gen import generate_id
    from utils.action_items import create_action_item
    import datetime

    config = Layer6Config.query.filter_by(simulation_id=simulation_id).first()
    email_approved = config.channel_approvals.get('email', False) if config else False

    av = ArtifactVersion.query.filter_by(action_id=action_id, is_current=True).first()
    if not av or not av.content:
        return

    try:
        data = json.loads(av.content)
    except Exception:
        return

    prospects = data.get('prospects', [])
    if not prospects:
        return

    _FALLBACK_EMAIL = 'valuemanager.management@gmail.com'

    # ── Phase 1: upsert ALL prospects with real emails into CRM as 'prospect' ──
    artifact_changed = False
    crm_created = 0
    for p in prospects:
        email = (p.get('email') or '').strip().lower()
        if not email or email == _FALLBACK_EMAIL:
            continue  # skip fallback-email placeholders

        crm_id = p.get('crm_contact_id')
        contact = Contact.query.get(crm_id) if crm_id else None
        if not contact:
            contact = Contact.query.filter_by(user_id=user_id, email=email).first()
        if not contact:
            contact = Contact(
                id=generate_id(),
                user_id=user_id,
                first_name=p.get('first_name', ''),
                last_name=p.get('last_name', ''),
                email=email,
                job_title=p.get('job_title', ''),
                company_name=p.get('company_name', ''),
                pipeline_stage='prospect',
                source='agent_action',
                source_action_id=action_id,
            )
            db.session.add(contact)
            db.session.flush()
            crm_created += 1

        if not p.get('crm_contact_id'):
            p['crm_contact_id'] = contact.id
            artifact_changed = True

    if artifact_changed:
        av.content = json.dumps(data, ensure_ascii=False)

    try:
        db.session.commit()
        logger.info('cold_email_campaign: upserted %d new CRM contacts for action %s', crm_created, action_id)
    except Exception as exc:
        logger.error('cold_email_campaign CRM upsert failed for action %s: %s', action_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass

    # ── Phase 2: send Step 1 emails (or escalate for review) ──
    step1_prospects = [
        (idx, p) for idx, p in enumerate(prospects)
        if p.get('sequence') and p['sequence'][0].get('send_status') == 'draft'
    ]
    if not step1_prospects:
        return

    if email_approved:
        sent = 0
        from app.models.agent_action import AgentAction
        from app.services.consulting_outreach_service import _try_apollo_send
        action_obj = AgentAction.query.get(action_id)

        for idx, p in step1_prospects:
            email = p.get('email') or _FALLBACK_EMAIL
            step1 = p['sequence'][0]
            try:
                _try_apollo_send(
                    {'email': email, 'email_draft': {'subject': step1.get('subject', ''), 'body': step1.get('body', '')}},
                    user_id, action_obj,
                )
                step1['send_status'] = 'sent'
                step1['sent_at'] = datetime.datetime.utcnow().isoformat()

                # Contact already exists from Phase 1; look up to advance stage
                crm_id = p.get('crm_contact_id')
                contact = Contact.query.get(crm_id) if crm_id else None
                if not contact and email != _FALLBACK_EMAIL:
                    contact = Contact.query.filter_by(user_id=user_id, email=email.lower().strip()).first()

                if contact:
                    contact.advance_stage('active', created_by='orchestrator',
                                          simulation_id=simulation_id, action_id=action_id)
                    contact.last_contacted_at = datetime.datetime.utcnow()
                    db.session.add(ContactActivity(
                        id=generate_id(),
                        contact_id=contact.id,
                        simulation_id=simulation_id,
                        action_id=action_id,
                        activity_type='outreach_sent',
                        notes='Cold email campaign step 1 sent',
                        created_by='orchestrator',
                    ))
                sent += 1
            except Exception as exc:
                logger.warning('cold_email step1 send failed prospect %d: %s', idx, exc)

        av.content = json.dumps(data, ensure_ascii=False)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        logger.info('Cold email campaign step 1: sent %d/%d for action %s', sent, len(step1_prospects), action_id)

        n = len(step1_prospects)
        for step_num, delay_days in ((2, 7), (3, 14)):
            create_action_item(
                simulation_id=simulation_id,
                user_id=user_id,
                item_type='escalation',
                title=f'Send cold email Step {step_num} to {n} prospect{"s" if n != 1 else ""} (day {delay_days})',
                description=f'Step {step_num} follow-ups are ready. Send them {delay_days} days after your Step 1 outreach.',
                action_url=f'/artifacts/{action_id}',
                source_action_id=action_id,
                emit_sse=False,
            )
    else:
        n = len(step1_prospects)
        create_action_item(
            simulation_id=simulation_id,
            user_id=user_id,
            item_type='escalation',
            title=f'Review & send cold email Step 1 to {n} prospect{"s" if n != 1 else ""} — approval required',
            description='Your cold email campaign is drafted. Review each email and send Step 1 to start the sequence.',
            action_url=f'/artifacts/{action_id}',
            source_action_id=action_id,
            emit_sse=True,
        )
