import logging
import re
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.models.integration import UserIntegration, EmailCampaign
from app.models.contact import Contact, ContactActivity
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_DECEPTIVE_SUBJECT_PATTERNS = [
    re.compile(r'^re:', re.IGNORECASE),
    re.compile(r'^fwd:', re.IGNORECASE),
    re.compile(r'(act now|urgent|last chance|limited time|expires today)', re.IGNORECASE),
]

OUTREACH_ACTION_TYPES = {
    'cold_email_campaign',
    'consulting_outreach',
    'speaking_proposals',
    'corporate_training_pitch',
    'alumni_reactivation',
    'lapsed_buyer_reactivation',
}


# ── CAN-SPAM validation ───────────────────────────────────────────────────────

def validate_can_spam(user_profile, email_steps: list) -> list:
    """Return list of compliance error strings. Empty list = pass."""
    errors = []
    if not user_profile or not getattr(user_profile, 'location', None):
        errors.append(
            'Add your business address in Settings → Profile before sending. '
            'CAN-SPAM requires a physical mailing address in every outbound email.'
        )
    for i, step in enumerate(email_steps, 1):
        subject = step.get('subject', '')
        for pat in _DECEPTIVE_SUBJECT_PATTERNS:
            if pat.search(subject):
                errors.append(
                    f'Step {i} subject line may be deceptive: "{subject}". '
                    f'Review before deploying.'
                )
                break
    return errors


# ── CAN-SPAM footer injection ─────────────────────────────────────────────────

def _build_footer(user_profile) -> str:
    address = getattr(user_profile, 'location', '') or 'Address not provided'
    return (
        f'\n\n---\n'
        f'{address}\n'
        f'You received this email because your professional background matches our '
        f'outreach criteria. To unsubscribe, click the link below.\n'
        f'{{{{unsubscribe_link}}}}'
    )


def inject_footer(body: str, user_profile) -> str:
    footer = _build_footer(user_profile)
    if '{{unsubscribe_link}}' not in body and '{unsubscribe_link}' not in body:
        return body + footer
    return body


# ── Deploy email campaign ─────────────────────────────────────────────────────

def deploy_email_campaign(action_id: str, simulation_id: str, artifact: dict) -> dict:
    """
    Deploy a generated email sequence artifact to Apollo.
    artifact must contain: prospects[], step1_subject, step1_body,
    step2_subject, step2_body, step3_subject, step3_body.
    Returns {'campaign_id': ..., 'apollo_sequence_id': ...} or raises.
    """
    from app.models.simulation import Simulation
    from app.models.profile import UserProfile

    sim = Simulation.query.get(simulation_id)
    if not sim:
        raise ValueError(f'Simulation {simulation_id} not found')

    integration = UserIntegration.query.filter_by(
        user_id=sim.user_id, provider='apollo'
    ).first()

    if not integration or not integration.is_connected:
        raise ApolloAuthRequired('apollo_auth_required')

    if integration.is_expired:
        _try_refresh_token(integration)

    user_profile = UserProfile.query.filter_by(user_id=sim.user_id).first()

    steps = [
        {'subject': artifact.get('step1_subject', ''), 'body': artifact.get('step1_body', '')},
        {'subject': artifact.get('step2_subject', ''), 'body': artifact.get('step2_body', '')},
        {'subject': artifact.get('step3_subject', ''), 'body': artifact.get('step3_body', '')},
    ]

    compliance_errors = validate_can_spam(user_profile, steps)
    if compliance_errors:
        raise ComplianceError(compliance_errors[0])

    access_token = integration.decrypt_access_token()
    from app.services.apollo_client import ApolloClient
    apollo = ApolloClient(access_token)

    apollo_contact_ids = []
    prospects = artifact.get('prospects', [])
    for prospect in prospects:
        contact = Contact.query.filter_by(
            user_id=sim.user_id, email=prospect.get('email')
        ).first()
        if contact and contact.do_not_contact:
            continue

        try:
            apollo_contact = apollo.upsert_contact(
                email=prospect.get('email', ''),
                first_name=prospect.get('first_name', ''),
                last_name=prospect.get('last_name', ''),
                title=prospect.get('job_title'),
                company=prospect.get('company'),
                custom_fields={'simulacrum_contact_id': prospect.get('contact_id')},
            )
            apollo_contact_ids.append(apollo_contact.get('id'))
        except Exception as exc:
            logger.warning('Apollo upsert failed for %s: %s', prospect.get('email'), exc)

        if contact and prospect.get('contact_id'):
            contact.advance_stage('active', created_by='agent',
                                  simulation_id=simulation_id, action_id=action_id)

    daily_limit = integration.apollo_daily_limit or 30

    apollo_steps = []
    delay_days = [0, 3, 4]
    for i, step in enumerate(steps):
        if step['subject'] or step['body']:
            apollo_steps.append({
                'type': 'email',
                'delay_days': delay_days[i],
                'subject': step['subject'],
                'body': inject_footer(step['body'], user_profile),
            })

    sequence = apollo.create_sequence(
        name=f'Simulacrum — {action_id} — {datetime.utcnow():%Y%m%d}',
        steps=apollo_steps,
        daily_limit=daily_limit,
    )
    sequence_id = sequence.get('id')

    if apollo_contact_ids:
        apollo.add_contacts_to_sequence(sequence_id, apollo_contact_ids)

    webhook_url = f'{current_app.config["BASE_URL"]}/webhooks/apollo/{sim.user_id}'
    try:
        apollo.register_webhook(
            url=webhook_url,
            events=['email_reply', 'email_bounced', 'email_opened', 'unsubscribed'],
            sequence_id=sequence_id,
        )
    except Exception as exc:
        logger.warning('Apollo webhook registration failed: %s', exc)

    campaign = EmailCampaign(
        id=generate_id(),
        simulation_id=simulation_id,
        action_id=action_id,
        apollo_sequence_id=sequence_id,
        contact_count=len(apollo_contact_ids),
        daily_limit=daily_limit,
        status='active',
    )
    db.session.add(campaign)
    db.session.commit()

    return {'campaign_id': campaign.id, 'apollo_sequence_id': sequence_id}


def _try_refresh_token(integration: UserIntegration):
    if not integration.refresh_token_enc:
        raise ApolloAuthRequired('apollo_token_expired')
    from app.services.apollo_client import refresh_access_token
    from app.services.token_crypto import encrypt_token, decrypt_token
    from datetime import timedelta
    try:
        refresh_token = decrypt_token(integration.refresh_token_enc)
        token_data = refresh_access_token(refresh_token)
        integration.access_token_enc = encrypt_token(token_data['access_token'])
        if token_data.get('refresh_token'):
            integration.refresh_token_enc = encrypt_token(token_data['refresh_token'])
        if token_data.get('expires_in'):
            integration.token_expires_at = datetime.utcnow() + timedelta(seconds=token_data['expires_in'])
        db.session.commit()
    except Exception as exc:
        logger.error('Apollo token refresh failed: %s', exc)
        raise ApolloAuthRequired('apollo_token_expired')


# ── Custom exceptions ─────────────────────────────────────────────────────────

class ApolloAuthRequired(Exception):
    pass


class ComplianceError(Exception):
    pass
