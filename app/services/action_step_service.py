"""
Scheduled Action Steps service (SIM-PRD-STEPS-001 Part A).

Handles creation, condition evaluation, and execution of multi-step agent artifacts.
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static step config for all 11 multi-step agents (A.6)
# Agents that produce a steps[] array in their artifact override these defaults.
# ---------------------------------------------------------------------------

AGENT_STEP_CONFIG = {
    'cold_email_campaign': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 72,  'per_contact': True,  'condition': 'no_reply'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 168, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'consulting_outreach': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 72,  'per_contact': True,  'condition': 'no_reply'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 120, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'consulting_proposal': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',         'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'proposal_followup',  'delay_hours': 120, 'per_contact': True,  'condition': 'no_signature'},
        ],
    },
    'speaking_proposals': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 168, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'corporate_training': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 120, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'launch_sequence': {
        'steps': [
            {'step_number': 1, 'step_type': 'convertkit_broadcast', 'delay_hours': 0,   'per_contact': False, 'condition': None},
            {'step_number': 2, 'step_type': 'convertkit_broadcast', 'delay_hours': 72,  'per_contact': False, 'condition': None},
            {'step_number': 3, 'step_type': 'convertkit_broadcast', 'delay_hours': 168, 'per_contact': False, 'condition': None},
        ],
    },
    'alumni_reactivation': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 96,  'per_contact': True,  'condition': 'no_reply'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 168, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'sponsorship_outreach': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 120, 'per_contact': True,  'condition': 'no_reply'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 240, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'partnership_proposal': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 120, 'per_contact': True,  'condition': 'no_reply'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 240, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
    'lapsed_buyer': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 72,  'per_contact': True,  'condition': 'no_purchase'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 168, 'per_contact': True,  'condition': 'no_purchase'},
        ],
    },
    'client_winback': {
        'steps': [
            {'step_number': 1, 'step_type': 'email_send',     'delay_hours': 0,   'per_contact': True,  'condition': None},
            {'step_number': 2, 'step_type': 'email_followup', 'delay_hours': 72,  'per_contact': True,  'condition': 'no_reply'},
            {'step_number': 3, 'step_type': 'email_followup', 'delay_hours': 168, 'per_contact': True,  'condition': 'no_reply'},
        ],
    },
}


def create_steps_from_artifact(
    agent_action_id: str,
    simulation_id: str,
    action_type: str,
    artifact_json: str,
    parent_action_id: str = None,
) -> int:
    """
    Parse artifact and create ActionStep records for each contact × step.

    Returns the number of steps created.
    """
    import json as _json
    from app.extensions import db
    from app.models.action_step import ActionStep
    from utils.id_gen import generate_id

    step_config = AGENT_STEP_CONFIG.get(action_type)
    if not step_config:
        return 0

    # Parse artifact
    try:
        data = _json.loads(artifact_json) if isinstance(artifact_json, str) else artifact_json
    except Exception:
        logger.warning('create_steps_from_artifact: could not parse artifact for %s', agent_action_id)
        return 0

    # Prefer steps[] from artifact; fall back to static config
    artifact_steps = data.get('steps') or step_config['steps']

    # Extract contacts
    contacts = _extract_contacts(data, action_type)
    total_steps = len(artifact_steps)
    now = datetime.utcnow()
    created = 0

    for step_def in artifact_steps:
        step_num = step_def.get('step_number', 1)
        step_type = step_def.get('step_type', 'email_send')
        delay_hours = step_def.get('delay_hours', 0)
        per_contact = step_def.get('per_contact', True)
        condition_str = step_def.get('condition')
        condition_type = condition_str if isinstance(condition_str, str) else (
            condition_str.get('type') if isinstance(condition_str, dict) else None
        )
        scheduled_for = now + timedelta(hours=delay_hours)

        if per_contact and contacts:
            for contact_id, contact_data in contacts.items():
                subject = _build_subject(step_def, step_num, data, contact_data)
                payload = _build_payload(step_def, data, contact_data)

                # condition_ref semantics by condition_type:
                #   no_reply/no_purchase/no_booking → contact_id
                #   no_signature → agent_action_id (to look up the proposal document)
                if condition_type in ('no_reply', 'no_purchase', 'no_booking'):
                    condition_ref = contact_id
                elif condition_type == 'no_signature':
                    condition_ref = agent_action_id
                else:
                    condition_ref = None

                step = ActionStep(
                    id=generate_id(),
                    agent_action_id=agent_action_id,
                    parent_action_id=parent_action_id,
                    simulation_id=simulation_id,
                    step_number=step_num,
                    total_steps=total_steps,
                    action_type=action_type,
                    step_type=step_type,
                    subject=subject,
                    scheduled_for=scheduled_for,
                    condition_type=condition_type,
                    condition_ref=condition_ref,
                )
                step.payload = payload
                db.session.add(step)
                created += 1
        else:
            # Global step (launch_sequence broadcasts, etc.)
            subject = _build_subject(step_def, step_num, data, {})
            payload = _build_payload(step_def, data, {})
            step = ActionStep(
                id=generate_id(),
                agent_action_id=agent_action_id,
                parent_action_id=parent_action_id,
                simulation_id=simulation_id,
                step_number=step_num,
                total_steps=total_steps,
                action_type=action_type,
                step_type=step_type,
                subject=subject,
                scheduled_for=scheduled_for,
                condition_type=condition_type,
                condition_ref=None,
            )
            step.payload = payload
            db.session.add(step)
            created += 1

    try:
        db.session.commit()
        logger.info('Created %d action steps for %s (action=%s)', created, action_type, agent_action_id)
    except Exception as exc:
        logger.error('create_steps_from_artifact commit failed: %s', exc)
        db.session.rollback()
        return 0

    return created


def _extract_contacts(data: dict, action_type: str) -> dict:
    """Return {contact_id: prospect_dict} from artifact data."""
    contacts = {}

    # Standard contact arrays across agent types
    for key in ('prospects', 'contacts', 'alumni', 'sponsors', 'partners', 'clients'):
        items = data.get(key) or []
        for item in items:
            cid = item.get('crm_contact_id')
            if cid:
                contacts[cid] = item

    return contacts


def _build_subject(step_def: dict, step_num: int, data: dict, contact_data: dict) -> str:
    """Derive a subject for the step record."""
    if step_num == 1:
        # Pull from the matching email draft in the artifact
        emails = data.get('emails') or []
        contact_idx = contact_data.get('contact_index', 0)
        for em in emails:
            if em.get('contact_index') == contact_idx:
                return em.get('subject', '')
        # Fallback: first email
        if emails:
            return emails[0].get('subject', '')
    # Follow-up: prefix the original subject
    payload = step_def.get('payload') or {}
    prefix = payload.get('subject_prefix', 'Re: ')
    original = data.get('emails', [{}])[0].get('subject', '') if data.get('emails') else ''
    return f'{prefix}{original}' if original else ''


def _build_payload(step_def: dict, data: dict, contact_data: dict) -> dict:
    """Build the execution payload for the step."""
    payload = dict(step_def.get('payload') or {})
    contact_idx = contact_data.get('contact_index', 0)

    # For step 1, embed the actual email body
    if step_def.get('step_number') == 1:
        emails = data.get('emails') or []
        for em in emails:
            if em.get('contact_index') == contact_idx:
                payload['html_body'] = em.get('html_body', '')
                payload['subject'] = em.get('subject', '')
                break
        if not payload.get('html_body') and emails:
            payload['html_body'] = emails[0].get('html_body', '')
            payload['subject'] = emails[0].get('subject', '')

    # Store contact metadata for template rendering in follow-ups
    if contact_data:
        payload['first_name'] = contact_data.get('first_name', '')
        payload['last_name'] = contact_data.get('last_name', '')
        payload['contact_id'] = contact_data.get('crm_contact_id', '')

    return payload


# ---------------------------------------------------------------------------
# Condition evaluation (A.4, FR-STEP-05)
# ---------------------------------------------------------------------------

def evaluate_condition(step) -> bool:
    """
    Return True if the step should execute, False if it should be skipped.
    Sets step.skip_reason when returning False.
    """
    if step.condition_type is None:
        return True

    if step.condition_type == 'no_reply':
        from app.models.outreach_email import EmailLog
        replied = EmailLog.query.filter(
            EmailLog.contact_id == step.condition_ref,
            EmailLog.replied_at.isnot(None),
            EmailLog.replied_at > step.created_at,
        ).first()
        if replied:
            step.skip_reason = f'Prospect replied on {replied.replied_at.date()}'
            return False
        return True

    if step.condition_type == 'no_signature':
        # condition_ref = agent_action_id of the proposal agent
        from app.models.signing import SigningDocument
        query = SigningDocument.query.filter_by(simulation_id=step.simulation_id)
        if step.condition_ref:
            query = query.filter_by(action_id=step.condition_ref)
        doc = query.order_by(SigningDocument.created_at.desc()).first()
        if doc and doc.signed_at:
            step.skip_reason = f'Document signed on {doc.signed_at.date()}'
            return False
        return True

    if step.condition_type == 'no_purchase':
        # condition_ref = contact CRM id; LayerIncomeRecord doesn't store contact_id directly,
        # so check for any income for this simulation recorded after this step was created.
        from app.models.income import LayerIncomeRecord
        from datetime import timezone
        income = LayerIncomeRecord.query.filter(
            LayerIncomeRecord.simulation_id == step.simulation_id,
            LayerIncomeRecord.is_void.is_(False),
            LayerIncomeRecord.created_at > step.created_at,
        ).first()
        if income:
            step.skip_reason = f'Purchase recorded on {income.created_at.date()}'
            return False
        return True

    if step.condition_type == 'no_booking':
        from app.models.integration_signal import IntegrationSignal
        booking = IntegrationSignal.query.filter(
            IntegrationSignal.simulation_id == step.simulation_id,
            IntegrationSignal.signal_type == IntegrationSignal.SIGNAL_BOOKING_CREATED,
        ).first()
        if booking:
            step.skip_reason = f'Call booked on {booking.created_at.date()}'
            return False
        return True

    return True  # unknown condition type → execute


# ---------------------------------------------------------------------------
# Step execution (A.5)
# ---------------------------------------------------------------------------

def execute_step(step, user_id: str, from_email: str, from_name: str) -> bool:
    """
    Execute a due ActionStep. Returns True on success, False on skip/error.
    Does NOT commit — caller must commit after updating step.status.
    """
    step_type = step.step_type
    payload = step.payload

    if step_type in ('email_send', 'email_followup'):
        return _execute_email_step(step, payload, from_email, from_name)

    if step_type == 'proposal_followup':
        return _execute_email_step(step, payload, from_email, from_name)

    if step_type == 'linkedin_post':
        logger.info('linkedin_post step %s — LinkedIn API dispatch not yet implemented', step.id)
        return True  # Mark executed; actual LinkedIn wiring is future work

    if step_type == 'convertkit_broadcast':
        return _execute_convertkit_step(step, payload, user_id)

    if step_type == 'drip_release':
        logger.info('drip_release step %s — Kajabi/ConvertKit drip not yet implemented', step.id)
        return True

    logger.warning('Unknown step_type %s for step %s', step_type, step.id)
    return False


def _execute_email_step(step, payload: dict, from_email: str, from_name: str) -> bool:
    contact_id = payload.get('contact_id') or step.condition_ref
    if not contact_id:
        logger.warning('email step %s has no contact_id in payload', step.id)
        return False

    subject = step.subject or payload.get('subject', '(no subject)')
    html_body = payload.get('html_body', '')

    # Render template variables
    first_name = payload.get('first_name', '')
    if first_name and '{{first_name}}' in html_body:
        html_body = html_body.replace('{{first_name}}', first_name)

    from app.services.outreach_email_service import send_outreach_email
    result = send_outreach_email(
        simulation_id=step.simulation_id,
        contact_id=contact_id,
        subject=subject,
        html_body=html_body,
        from_email=from_email,
        from_name=from_name,
        step_id=step.id,
        action_id=step.agent_action_id,
    )
    return result.get('status') == 'sent'


def _execute_convertkit_step(step, payload: dict, user_id: str) -> bool:
    try:
        from app.models.integration import UserIntegration
        ck = UserIntegration.query.filter_by(user_id=user_id, provider='convertkit').first()
        if not ck or not ck.is_connected:
            logger.info('convertkit_broadcast step %s — ConvertKit not connected', step.id)
            return True  # Not an error; mark executed

        # ConvertKit broadcast API call — wire_service integration pending
        logger.info(
            'convertkit_broadcast step %s subject="%s" — ConvertKit API wiring is pending',
            step.id, payload.get('subject', ''),
        )
        return True
    except Exception as exc:
        logger.error('convertkit_broadcast step %s failed: %s', step.id, exc)
        return False


# ---------------------------------------------------------------------------
# Cycle integration — called from run_orchestrator_cycle
# ---------------------------------------------------------------------------

def process_due_steps(simulation_id: str, user_id: str) -> dict:
    """
    Evaluate and execute all due scheduled steps for this simulation.
    Called at the start of each orchestrator cycle, before new agent dispatch.
    """
    from app.extensions import db
    from app.models.action_step import ActionStep
    from app.models.user import User

    user = User.query.get(user_id)
    from_email = user.email if user else 'noreply@simulacrumai.io'
    from_name = user.full_name if user else 'Simulacrum'

    due_steps = ActionStep.query.filter(
        ActionStep.simulation_id == simulation_id,
        ActionStep.status == ActionStep.STATUS_SCHEDULED,
        ActionStep.scheduled_for <= datetime.utcnow(),
    ).order_by(ActionStep.scheduled_for).all()

    executed = skipped = errors = 0
    for step in due_steps:
        try:
            if evaluate_condition(step):
                success = execute_step(step, user_id, from_email, from_name)
                if success:
                    step.status = ActionStep.STATUS_EXECUTED
                    step.executed_at = datetime.utcnow()
                    executed += 1
                else:
                    # send_outreach_email already logged the error; keep scheduled so it doesn't retry
                    step.status = ActionStep.STATUS_SKIPPED
                    step.skipped_at = datetime.utcnow()
                    step.skip_reason = 'Execution failed — see email logs'
                    errors += 1
            else:
                step.status = ActionStep.STATUS_SKIPPED
                step.skipped_at = datetime.utcnow()
                skipped += 1
        except Exception as exc:
            logger.error('process_due_steps: step %s failed: %s', step.id, exc)
            errors += 1

    if due_steps:
        try:
            db.session.commit()
        except Exception as exc:
            logger.error('process_due_steps commit failed: %s', exc)
            db.session.rollback()

    if due_steps:
        logger.info(
            'process_due_steps sim=%s: %d executed, %d skipped, %d errors',
            simulation_id, executed, skipped, errors,
        )
    return {'executed': executed, 'skipped': skipped, 'errors': errors}


def cancel_pending_steps(simulation_id: str) -> int:
    """Cancel all scheduled steps for a simulation (used on pause/cancel)."""
    from app.extensions import db
    from app.models.action_step import ActionStep

    count = ActionStep.query.filter_by(
        simulation_id=simulation_id,
        status=ActionStep.STATUS_SCHEDULED,
    ).update({
        'status': ActionStep.STATUS_CANCELLED,
    })
    try:
        db.session.commit()
    except Exception as exc:
        logger.error('cancel_pending_steps failed: %s', exc)
        db.session.rollback()
        return 0
    return count
