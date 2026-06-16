"""
Internal Email Engine (SIM-PRD-STEPS-001 Part B).

All outreach emails are sent from this module via SendGrid.
Apollo is no longer used for email delivery — discovery only.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def send_outreach_email(
    simulation_id: str,
    contact_id: str,
    subject: str,
    html_body: str,
    from_email: str,
    from_name: str,
    step_id: str = None,
    action_id: str = None,
) -> dict:
    """
    Send a single outreach email via SendGrid.

    Checks suppression list, logs the send, and updates CRM contact fields.
    Returns {'status': 'sent'|'skipped', ...}.
    """
    from flask import current_app
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.outreach_email import EmailLog, EmailSuppression
    from utils.id_gen import generate_id

    contact = Contact.query.get(contact_id)
    if not contact or not contact.email:
        return {'status': 'skipped', 'reason': 'no_email'}

    to_email = contact.email.lower().strip()

    if EmailSuppression.is_suppressed(to_email):
        return {'status': 'skipped', 'reason': 'suppressed'}

    api_key = current_app.config.get('SENDGRID_API_KEY')
    if not api_key:
        logger.warning('SENDGRID_API_KEY not set — outreach email skipped for contact %s', contact_id)
        return {'status': 'skipped', 'reason': 'no_api_key'}

    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import (
            Mail, TrackingSettings, OpenTracking, ClickTracking,
        )

        message = Mail(
            from_email=(from_email, from_name),
            to_emails=to_email,
            subject=subject,
            html_content=html_body,
        )

        # Custom headers for webhook attribution
        message.header = [
            ('X-Simulacrum-Simulation', simulation_id),
            ('X-Simulacrum-Contact', contact_id),
            ('X-Simulacrum-Step', step_id or 'direct'),
        ]

        # Unique args so webhook events can be attributed
        message.custom_arg = [
            ('simulation_id', simulation_id),
            ('contact_id', contact_id),
            ('step_id', step_id or ''),
            ('action_id', action_id or ''),
        ]

        # Open and click tracking
        message.tracking_settings = TrackingSettings(
            open_tracking=OpenTracking(enable=True),
            click_tracking=ClickTracking(enable=True, enable_text=True),
        )

        # Reply-to routes through Simulacrum for inbound parse tracking,
        # but points back to the user so replies also reach their inbox.
        base_domain = current_app.config.get('INBOUND_PARSE_DOMAIN', 'mail.simulacrumai.io')
        message.reply_to = f'reply-{contact_id}@{base_domain}'

        client = sg_module.SendGridAPIClient(api_key)
        response = client.send(message)
        provider_message_id = response.headers.get('X-Message-Id')

        log = EmailLog(
            id=generate_id(),
            simulation_id=simulation_id,
            contact_id=contact_id,
            step_id=step_id,
            action_id=action_id,
            subject=subject,
            from_email=from_email,
            from_name=from_name,
            to_email=to_email,
            provider_message_id=provider_message_id,
            status='sent',
            sent_at=datetime.utcnow(),
        )
        db.session.add(log)

        # Update CRM contact
        contact.last_contacted_at = datetime.utcnow()
        contact.outreach_count = (contact.outreach_count or 0) + 1
        if contact.pipeline_stage == 'prospect':
            contact.pipeline_stage = 'active'

        db.session.commit()
        logger.info('Outreach email sent to %s (contact=%s, step=%s)', to_email, contact_id, step_id)
        return {'status': 'sent', 'message_id': provider_message_id}

    except Exception as exc:
        logger.error('send_outreach_email failed contact=%s: %s', contact_id, exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'status': 'error', 'reason': str(exc)}
