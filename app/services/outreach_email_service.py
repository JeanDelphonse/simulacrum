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
    api_key: str = None,
    unsubscribe: bool = False,
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

    # Final gate: Do Not Contact is checked at send time, whatever queued the
    # send (PRD-SIM-MAIL-001 — the Email-to-CRM switch marks contacts DNC).
    if contact.do_not_contact:
        return {'status': 'skipped', 'reason': 'do_not_contact'}

    # Never email a template row an agent saved as a contact
    # ("[First Name 1] [Last Name 1]"), even one already scheduled.
    from app.services.contact_lookup import is_placeholder_contact
    if is_placeholder_contact(contact.first_name, contact.last_name,
                              contact.company_name, contact.email):
        logger.warning('Outreach to placeholder contact %s skipped', contact_id)
        return {'status': 'skipped', 'reason': 'placeholder_contact'}

    # Recipient uniqueness (recipient_ledger): one agent run per person per
    # simulation, and never the same email twice from one run. Every agent send
    # — post-completion hooks and scheduled steps — passes through here.
    try:
        from app.services.recipient_ledger import check_send
        allowed, reason = check_send(simulation_id, action_id, contact, subject)
    except Exception as _ge:
        logger.error('recipient guard failed open for contact %s: %s', contact_id, _ge)
        db.session.rollback()
        allowed, reason = True, ''
    if not allowed:
        logger.info('Outreach to contact %s skipped (%s) action=%s', contact_id, reason, action_id)
        return {'status': 'skipped', 'reason': reason}

    to_email = contact.email.lower().strip()

    # Sandbox routing: redirect to the simulation owner's email instead of the real contact
    try:
        from app.models.layer6 import Layer6Config as _L6Cfg
        from app.models.simulation import Simulation as _Sim
        from app.models.user import User as _User
        _cfg = _L6Cfg.query.filter_by(simulation_id=simulation_id).first()
        if _cfg and _cfg.sandbox_email_routing:
            _sim = _Sim.query.get(simulation_id)
            _owner = _User.query.get(_sim.user_id) if _sim else None
            _sandbox_addr = (_owner.email if _owner and _owner.email else 'simi@simulacrumai.io')
            logger.info('Sandbox routing: redirecting outreach for contact %s to %s', contact_id, _sandbox_addr)
            to_email = _sandbox_addr
    except Exception as _se:
        logger.warning('Sandbox routing check failed: %s', _se)

    if EmailSuppression.is_suppressed(to_email):
        return {'status': 'skipped', 'reason': 'suppressed'}

    # api_key: the user's own SendGrid key when the platform send provider is
    # 'own_sendgrid' (EXEC_PLAN P5); otherwise SimiSI's account.
    api_key = api_key or current_app.config.get('SENDGRID_API_KEY')
    if not api_key:
        logger.warning('SENDGRID_API_KEY not set — outreach email skipped for contact %s', contact_id)
        return {'status': 'skipped', 'reason': 'no_api_key'}

    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import (
            Mail, From, TrackingSettings, OpenTracking, ClickTracking,
            Header, CustomArg,
        )

        unsub_url = None
        if unsubscribe:
            # One-click unsubscribe (CAN-SPAM / RFC 8058): footer link plus
            # List-Unsubscribe headers; the link adds the address to the
            # suppression list every send checks.
            from app.services.outreach_campaign_service import (
                unsubscribe_url_for, BUSINESS_NAME, BUSINESS_ADDRESS)
            unsub_url = unsubscribe_url_for(to_email)
            html_body = (html_body or '') + (
                '<p style="font-size:12px;color:#888;margin-top:24px;">'
                'Sent by %s via SimiSI · %s, %s · '
                '<a href="%s" style="color:#888;">Unsubscribe</a></p>'
                % (from_name or 'SimiSI', BUSINESS_NAME, BUSINESS_ADDRESS, unsub_url))

        message = Mail(
            from_email=From(from_email, from_name),
            to_emails=to_email,
            subject=subject,
            html_content=html_body,
        )

        # Custom headers for webhook attribution
        headers = [
            Header('X-Simulacrum-Simulation', simulation_id),
            Header('X-Simulacrum-Contact', contact_id),
            Header('X-Simulacrum-Step', step_id or 'direct'),
        ]
        if unsub_url:
            headers += [
                Header('List-Unsubscribe', '<%s>' % unsub_url),
                Header('List-Unsubscribe-Post', 'List-Unsubscribe=One-Click'),
            ]
        message.header = headers

        # Unique args so webhook events can be attributed
        message.custom_arg = [
            CustomArg('simulation_id', simulation_id),
            CustomArg('contact_id', contact_id),
            CustomArg('step_id', step_id or ''),
            CustomArg('action_id', action_id or ''),
        ]

        # Open and click tracking
        message.tracking_settings = TrackingSettings(
            open_tracking=OpenTracking(enable=True),
            click_tracking=ClickTracking(enable=True, enable_text=True),
        )

        # Reply-to routes through SimiSI for inbound parse tracking,
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
        return {'status': 'sent', 'message_id': provider_message_id, 'email_log_id': log.id}

    except Exception as exc:
        logger.error('send_outreach_email failed contact=%s: %s', contact_id, exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'status': 'error', 'reason': str(exc)}
