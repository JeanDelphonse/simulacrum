import logging
from flask import current_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal send helpers
# ---------------------------------------------------------------------------

def _send_via_smtp(subject: str, recipients: list[str], body: str):
    from flask_mail import Message
    from app.extensions import mail
    sender_email = current_app.config['MAIL_DEFAULT_SENDER']
    sender_name  = current_app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')
    msg = Message(subject, recipients=recipients, body=body, sender=(sender_name, sender_email))
    mail.send(msg)


def _send_via_sendgrid(subject: str, recipients: list[str], body: str):
    import sendgrid
    from sendgrid.helpers.mail import Mail, To, From
    sg = sendgrid.SendGridAPIClient(api_key=current_app.config['SENDGRID_API_KEY'])
    sender_email = current_app.config['MAIL_DEFAULT_SENDER']
    sender_name  = current_app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')
    message = Mail(
        from_email=From(sender_email, sender_name),
        to_emails=[To(r) for r in recipients],
        subject=subject,
        plain_text_content=body,
    )
    response = sg.send(message)
    if response.status_code >= 400:
        raise RuntimeError(f'SendGrid returned {response.status_code}: {response.body}')


def _send(subject: str, recipients: list[str], body: str):
    provider = current_app.config.get('EMAIL_PROVIDER', 'smtp')
    if provider == 'sendgrid':
        _send_via_sendgrid(subject, recipients, body)
    else:
        _send_via_smtp(subject, recipients, body)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_verification_email(user_email: str, user_name: str, token: str):
    from flask import url_for
    try:
        verify_url = url_for('auth.verify_email', token=token, _external=True)
        _send(
            subject='Verify your Simulacrum account',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Verify your email: {verify_url}\n\n'
                f'Expires in 24 hours.\n\n— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error('Failed to send verification email to %s: %s', user_email, e, exc_info=True)
        raise


def send_password_reset_email(user_email: str, user_name: str, token: str):
    from flask import url_for
    try:
        reset_url = url_for('auth.reset_password_confirm', token=token, _external=True)
        _send(
            subject='Reset your Simulacrum password',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Reset your password: {reset_url}\n\n'
                f'Expires in 24 hours.\n\n— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send reset email to {user_email}: {e}')


def send_invoice_email(user_email: str, user_name: str, sim_name: str, sim_id: str, amount_cents: int = 69500):
    from datetime import datetime
    amount_str = f'${amount_cents / 100:.2f}'
    try:
        _send(
            subject=f'Your Simulacrum Invoice — {sim_name}',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Payment of {amount_str} processed for Simulation: {sim_name}\n'
                f'Simulation ID: {sim_id}\n'
                f'Amount: {amount_str}\n'
                f'Date: {datetime.utcnow().strftime("%Y-%m-%d")}\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send invoice email to {user_email}: {e}')


def send_simulation_failed_email(user_email: str, user_name: str, sim_name: str, sim_id: str, amount_cents: int = 1000):
    """Notify user that simulation generation failed and a refund has been issued."""
    amount_str = f'${amount_cents / 100:.2f}'
    try:
        _send(
            subject=f'Simulacrum: Generation failed for "{sim_name}" — refund issued',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'We\'re sorry — the generation of your simulation "{sim_name}" failed after multiple attempts.\n\n'
                f'A full refund of {amount_str} has been automatically issued to your original payment method. '
                f'It may take 5–10 business days to appear on your statement.\n\n'
                f'Simulation ID: {sim_id}\n\n'
                f'You can try creating a new simulation at any time.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send failure email to {user_email}: {e}')


def send_partner_application_received_email(partner_email: str, partner_name: str):
    try:
        _send(
            subject='Simulacrum Partner Program — Application Received',
            recipients=[partner_email],
            body=(
                f'Hi {partner_name},\n\n'
                f'Thank you for applying to the Simulacrum Referral Partner Program.\n\n'
                f'Our team will review your application and get back to you within 2 business days.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send partner application email to {partner_email}: {e}')


def send_partner_approved_email(partner_email: str, partner_name: str, referral_code: str):
    try:
        _send(
            subject='Simulacrum Partner Program — Application Approved!',
            recipients=[partner_email],
            body=(
                f'Hi {partner_name},\n\n'
                f'Congratulations! Your application to the Simulacrum Referral Partner Program has been approved.\n\n'
                f'Your referral code: {referral_code}\n\n'
                f'Log in to your partner dashboard to view your referral link, earnings, and advisor clients.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send partner approval email to {partner_email}: {e}')


def send_partner_rejected_email(partner_email: str, partner_name: str, reason: str = None):
    reason_line = f'\n\nReason: {reason}' if reason else ''
    try:
        _send(
            subject='Simulacrum Partner Program — Application Update',
            recipients=[partner_email],
            body=(
                f'Hi {partner_name},\n\n'
                f'Thank you for your interest in the Simulacrum Referral Partner Program.\n\n'
                f'After careful review, we are unable to approve your application at this time.{reason_line}\n\n'
                f'You are welcome to reapply in the future.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send partner rejection email to {partner_email}: {e}')


def send_referral_invitation_email(partner, recipient_email: str, recipient_first_name: str,
                                   personal_message: str, invitation_id: str):
    from flask import url_for
    try:
        ref_url = partner.referral_link()
        pixel_url = url_for('partners.referral_open_pixel', invitation_id=invitation_id, _external=True)
        greeting = f'Hi {recipient_first_name},' if recipient_first_name else 'Hi,'
        msg_block = f'\n\n{personal_message}' if personal_message else ''
        _send(
            subject=f'{partner.full_name} invited you to try Simulacrum',
            recipients=[recipient_email],
            body=(
                f'{greeting}{msg_block}\n\n'
                f'{partner.full_name} ({partner.partner_type}) thinks you could benefit from '
                f'Simulacrum — an AI platform that maps your professional background into a '
                f'personalised 5-layer wealth blueprint.\n\n'
                f'Get started here: {ref_url}\n\n'
                f'— Simulacrum\n\n'
                f'<img src="{pixel_url}" width="1" height="1" />'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send referral invitation to {recipient_email}: {e}')


def send_password_changed_email(user_email: str, user_name: str):
    from datetime import datetime
    try:
        _send(
            subject='Your Simulacrum password was changed',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Your Simulacrum password was changed on {datetime.utcnow().strftime("%Y-%m-%d at %H:%M UTC")}.\n\n'
                f'If this was not you, contact support immediately by replying to this email.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send password change email to {user_email}: {e}')


def send_email_change_verification(new_email: str, user_name: str, token: str):
    from flask import url_for
    try:
        confirm_url = url_for('profile.confirm_email_change', _external=True) + f'?token={token}'
        _send(
            subject='Confirm your new Simulacrum email address',
            recipients=[new_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Click the link below to confirm your new email address for Simulacrum:\n\n'
                f'{confirm_url}\n\n'
                f'This link expires in 24 hours. If you did not request this change, ignore this email.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send email change verification to {new_email}: {e}')


def send_email_change_notification(old_email: str, user_name: str, new_email: str):
    try:
        _send(
            subject='Your Simulacrum email address was changed',
            recipients=[old_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Your Simulacrum email address was changed to {new_email}.\n\n'
                f'If you did not make this change, contact support immediately.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send email change notification to {old_email}: {e}')


def send_account_deletion_email(user_email: str, user_name: str, recovery_token: str):
    from flask import url_for
    try:
        recovery_url = url_for('profile.recover_account', _external=True)
        _send(
            subject='Your Simulacrum account has been scheduled for deletion',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Your Simulacrum account has been scheduled for permanent deletion in 30 days.\n\n'
                f'All your simulations, artifacts, and profile data will be permanently removed after this period.\n\n'
                f'To cancel the deletion and recover your account within 30 days, use this link:\n\n'
                f'{recovery_url}?token={recovery_token}\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send deletion email to {user_email}: {e}')


def send_profile_inquiry_email(owner_email: str, owner_name: str, visitor_name: str,
                                visitor_email: str, subject: str, message: str):
    try:
        _send(
            subject=f'New inquiry from {visitor_name} via your Simulacrum profile',
            recipients=[owner_email],
            body=(
                f'Hi {owner_name},\n\n'
                f'You have a new inquiry via your Simulacrum public profile.\n\n'
                f'From: {visitor_name} <{visitor_email}>\n'
                f'Subject: {subject}\n\n'
                f'Message:\n{message}\n\n'
                f'Reply directly to this email to respond to {visitor_name}.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send profile inquiry email to {owner_email}: {e}')


def send_feedback_received_email(user_email: str, user_name: str):
    try:
        _send(
            subject='Your Simulacrum story has been received.',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Your Simulacrum feedback has been received. Our team will review it within 2 business days.\n\n'
                f'If approved, it will appear on our home page — we will notify you.\n\n'
                f'Thank you for sharing your experience.\n\n— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send feedback received email to {user_email}: {e}')


def send_admin_new_feedback_email(admin_email: str, submitter_name: str, star_rating: int,
                                   quote_text: str, outcome_text: str, layers: list):
    layer_str = ', '.join(l['label'] for l in layers) if layers else 'None'
    stars = '★' * star_rating + '☆' * (5 - star_rating)
    try:
        _send(
            subject=f'New Simulacrum testimonial — pending review.',
            recipients=[admin_email],
            body=(
                f'New feedback submitted by {submitter_name} — {star_rating} stars ({stars}).\n\n'
                f'Layers attributed: {layer_str}\n\n'
                f'Outcome:\n{outcome_text}\n\n'
                f'Testimonial quote:\n"{quote_text}"\n\n'
                f'Review in the admin panel.\n\n— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send admin feedback notification to {admin_email}: {e}')


def send_feedback_approved_email(user_email: str, user_name: str, is_featured: bool):
    if is_featured:
        body = (
            f'Hi {user_name},\n\n'
            f'Your Simulacrum story has been featured on our home page. '
            f'We selected it to highlight the real results our users achieve. Thank you.\n\n'
            f'View it live at simulacrum.io\n\n— Simulacrum'
        )
    else:
        body = (
            f'Hi {user_name},\n\n'
            f'Your Simulacrum story has been approved and is now live on our home page. '
            f'Thank you for sharing your experience.\n\n'
            f'View it live at simulacrum.io\n\n— Simulacrum'
        )
    try:
        _send(subject='Your Simulacrum story is live.', recipients=[user_email], body=body)
    except Exception as e:
        logger.error(f'Failed to send feedback approved email to {user_email}: {e}')


def send_feedback_rejected_email(user_email: str, user_name: str, admin_note: str = None):
    note_block = f'\n\nFeedback from our team: {admin_note}' if admin_note else ''
    try:
        _send(
            subject='An update on your Simulacrum story.',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Thank you for sharing your Simulacrum story. After review, we are unable to feature '
                f'this submission on our home page at this time.{note_block}\n\n'
                f'You are welcome to submit new feedback at any time from your dashboard.\n\n— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send feedback rejected email to {user_email}: {e}')


def send_feedback_withdrawal_request_email(admin_email: str, submitter_name: str, feedback_id: str):
    try:
        _send(
            subject=f'Testimonial withdrawal request — {submitter_name}',
            recipients=[admin_email],
            body=(
                f'{submitter_name} has requested withdrawal of their approved testimonial.\n\n'
                f'Feedback ID: {feedback_id}\n\n'
                f'Review and unpublish in the admin feedback panel if appropriate.\n\n— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send withdrawal request email to {admin_email}: {e}')


def send_data_retention_warning_email(user_email: str, user_name: str, deletion_date: str):
    try:
        _send(
            subject='Your Simulacrum data is scheduled for deletion in 30 days',
            recipients=[user_email],
            body=(
                f'Hi {user_name},\n\n'
                f'Your Simulacrum account has been inactive for an extended period.\n\n'
                f'Your data — including all simulations, resume files, and Growth Command Center records — '
                f'is scheduled for permanent deletion on {deletion_date}.\n\n'
                f'To keep your account active, simply sign in before {deletion_date}:\n'
                f'https://simulacrum.io/login\n\n'
                f'If you would prefer to delete your data now rather than wait, you can request '
                f'immediate deletion by emailing privacy@simulacrum.io.\n\n'
                f'— Simulacrum\n\n'
                f'Questions? Reply to this email or contact privacy@simulacrum.io'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send retention warning email to {user_email}: {e}')


def send_collab_invite_email(invitee_email: str, inviter_name: str, sim_name: str, share_token: str):
    from flask import url_for
    try:
        accept_url = url_for('collaboration.accept_invite', token=share_token, _external=True)
        _send(
            subject=f'{inviter_name} invited you to collaborate on "{sim_name}"',
            recipients=[invitee_email],
            body=(
                f'Hi,\n\n'
                f'{inviter_name} has invited you to collaborate on their Simulacrum wealth simulation: "{sim_name}"\n\n'
                f'Accept the invitation: {accept_url}\n\n'
                f'This link expires in 30 days.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        logger.error(f'Failed to send collab invite to {invitee_email}: {e}')


def send_alert_digest_email(user, alerts: list):
    """Daily digest of active proactive alerts (ENH-04)."""
    if not alerts:
        return
    try:
        lines = [f'Hi {(user.full_name or "").split()[0] or "there"},\n',
                 'Here is your daily Simulacrum activity digest:\n']
        for a in alerts:
            lines.append(f'  • [{a.item_type.replace("_", " ").title()}] {a.title}')
        lines.append('\nLog in to take action: https://simulacrum.app/simulations')
        lines.append('\n— Simulacrum')
        _send(
            subject=f'Your Simulacrum digest — {len(alerts)} alert{"s" if len(alerts) != 1 else ""}',
            recipients=[user.email],
            body='\n'.join(lines),
        )
    except Exception as e:
        logger.error(f'Failed to send alert digest to {user.email}: {e}')
