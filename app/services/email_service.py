import logging
from typing import Optional
from flask import current_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML email wrapper
# ---------------------------------------------------------------------------

def _html_wrap(body_html: str, preheader: str = '') -> str:
    """Wrap a body snippet in a full, deliverable HTML email shell."""
    pre = f'<span style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheader}&nbsp;</span>' if preheader else ''
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SimulacrumAI.io</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:'Helvetica Neue',Arial,sans-serif;">
{pre}
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f6f8;padding:40px 16px;">
  <tr><td align="center">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">

      <!-- Logo -->
      <tr><td align="center" style="padding-bottom:24px;">
        <a href="https://simulacrumai.io" style="text-decoration:none;display:inline-flex;align-items:center;gap:8px;">
          <span style="display:inline-block;width:32px;height:32px;background:#14b8a6;border-radius:7px;text-align:center;line-height:32px;font-size:16px;color:#fff;">✦</span>
          <span style="font-size:15px;font-weight:700;color:#111827;letter-spacing:-0.2px;">SimulacrumAI.io</span>
        </a>
      </td></tr>

      <!-- Card -->
      <tr><td style="background:#ffffff;border-radius:12px;padding:40px 40px 32px;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
        {body_html}
      </td></tr>

      <!-- Footer -->
      <tr><td align="center" style="padding-top:24px;font-size:12px;color:#9ca3af;line-height:1.6;">
        SimulacrumAI.io · Bay Area Experiences Ventures<br>
        You received this email because you have an account with Simulacrum.
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


def _btn(url: str, label: str) -> str:
    """Teal CTA button."""
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" style="margin:28px 0 8px;">'
        f'<tr><td style="background:#14b8a6;border-radius:8px;">'
        f'<a href="{url}" style="display:inline-block;padding:13px 28px;font-size:15px;'
        f'font-weight:600;color:#0a0e1a;text-decoration:none;letter-spacing:-0.1px;">{label}</a>'
        f'</td></tr></table>'
    )


def _fallback_link(url: str) -> str:
    """Small grey fallback link line below the button."""
    return (
        f'<p style="font-size:12px;color:#9ca3af;margin:12px 0 0;word-break:break-all;">'
        f'Or copy this link: <a href="{url}" style="color:#14b8a6;">{url}</a></p>'
    )


def _h1(text: str) -> str:
    return f'<h1 style="font-size:22px;font-weight:700;color:#111827;margin:0 0 12px;line-height:1.3;">{text}</h1>'


def _p(text: str, muted: bool = False) -> str:
    color = '#6b7280' if muted else '#374151'
    return f'<p style="font-size:15px;color:{color};line-height:1.65;margin:0 0 16px;">{text}</p>'


def _divider() -> str:
    return '<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">'


# ---------------------------------------------------------------------------
# Internal send helpers
# ---------------------------------------------------------------------------

def _send_via_smtp(subject: str, recipients: list, body: str, html: Optional[str] = None):
    from flask_mail import Message
    from app.extensions import mail
    sender_email = current_app.config['MAIL_DEFAULT_SENDER']
    sender_name  = current_app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')
    msg = Message(subject, recipients=recipients, body=body, sender=(sender_name, sender_email))
    if html:
        msg.html = html
    mail.send(msg)


def _send_via_sendgrid(subject: str, recipients: list, body: str, html: Optional[str] = None):
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
        html_content=html,
    )
    response = sg.send(message)
    if response.status_code >= 400:
        raise RuntimeError(f'SendGrid returned {response.status_code}: {response.body}')


def _send(subject: str, recipients: list, body: str, html: Optional[str] = None):
    provider = current_app.config.get('EMAIL_PROVIDER', 'smtp')
    if provider == 'sendgrid':
        _send_via_sendgrid(subject, recipients, body, html)
    else:
        _send_via_smtp(subject, recipients, body, html)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_verification_email(user_email: str, user_name: str, verify_url: str):
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Confirm your SimulacrumAI.io account by clicking the link below.\n\n'
            f'{verify_url}\n\n'
            f'This link expires in 24 hours. If you did not create an account, ignore this email.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Confirm your email address') +
            _p(f'Hi {first}, welcome to SimulacrumAI.io.') +
            _p('Click the button below to verify your email address and activate your account.') +
            _btn(verify_url, 'Confirm email address') +
            _fallback_link(verify_url) +
            _divider() +
            _p('This link expires in 24 hours. If you did not create an account, you can safely ignore this email.', muted=True),
            preheader='Confirm your SimulacrumAI.io account — one click to get started.',
        )
        _send(
            subject='Confirm your SimulacrumAI.io account',
            recipients=[user_email],
            body=plain,
            html=html,
        )
    except Exception as e:
        logger.error('Failed to send verification email to %s: %s', user_email, e, exc_info=True)
        raise


def send_password_reset_email(user_email: str, user_name: str, token: str):
    from flask import url_for
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        reset_url = url_for('auth.reset_password_confirm', token=token, _external=True)
        plain = (
            f'Hi {first},\n\n'
            f'We received a request to reset your SimulacrumAI.io password.\n\n'
            f'{reset_url}\n\n'
            f'This link expires in 24 hours. If you did not request a reset, ignore this email — your password has not changed.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Reset your password') +
            _p(f'Hi {first},') +
            _p('We received a request to reset your SimulacrumAI.io password. Click the button below to choose a new one.') +
            _btn(reset_url, 'Reset password') +
            _fallback_link(reset_url) +
            _divider() +
            _p('This link expires in 24 hours. If you did not request a password reset, you can safely ignore this email — your password has not changed.', muted=True),
            preheader='Reset your SimulacrumAI.io password.',
        )
        _send(
            subject='Reset your SimulacrumAI.io password',
            recipients=[user_email],
            body=plain,
            html=html,
        )
    except Exception as e:
        logger.error('Failed to send reset email to %s: %s', user_email, e, exc_info=True)


def send_invoice_email(user_email: str, user_name: str, sim_name: str, sim_id: str, amount_cents: int = 69500):
    from datetime import datetime
    first = (user_name or '').strip().split()[0] or 'there'
    amount_str = f'${amount_cents / 100:.2f}'
    date_str = datetime.utcnow().strftime('%B %d, %Y')
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Payment of {amount_str} processed for: {sim_name}\n'
            f'Simulation ID: {sim_id}\n'
            f'Date: {date_str}\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Payment confirmed') +
            _p(f'Hi {first}, your payment has been processed.') +
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:20px 0;">'
            f'<tr><td style="padding:10px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#6b7280;">Simulation</td>'
            f'<td style="padding:10px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#111827;text-align:right;font-weight:600;">{sim_name}</td></tr>'
            f'<tr><td style="padding:10px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#6b7280;">Amount</td>'
            f'<td style="padding:10px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#111827;text-align:right;font-weight:700;">{amount_str}</td></tr>'
            f'<tr><td style="padding:10px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#6b7280;">Date</td>'
            f'<td style="padding:10px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#111827;text-align:right;">{date_str}</td></tr>'
            f'</table>' +
            _p(f'Simulation ID: {sim_id}', muted=True),
            preheader=f'Payment of {amount_str} confirmed for {sim_name}.',
        )
        _send(subject=f'Your Simulacrum invoice — {sim_name}', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send invoice email to %s: %s', user_email, e, exc_info=True)


def send_simulation_failed_email(user_email: str, user_name: str, sim_name: str, sim_id: str, amount_cents: int = 1000):
    first = (user_name or '').strip().split()[0] or 'there'
    amount_str = f'${amount_cents / 100:.2f}'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'We\'re sorry — the generation of your simulation "{sim_name}" failed after multiple attempts.\n\n'
            f'A full refund of {amount_str} has been automatically issued. It may take 5–10 business days to appear.\n\n'
            f'Simulation ID: {sim_id}\n\n'
            f'You can try creating a new simulation at any time.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Simulation generation failed') +
            _p(f'Hi {first},') +
            _p(f'We\'re sorry — the generation of your simulation <strong>"{sim_name}"</strong> failed after multiple attempts.') +
            _p(f'A full refund of <strong>{amount_str}</strong> has been automatically issued to your original payment method. It may take 5–10 business days to appear on your statement.') +
            _divider() +
            _p(f'Simulation ID: {sim_id}', muted=True) +
            _p('You can try creating a new simulation at any time from your dashboard.', muted=True),
            preheader=f'Refund of {amount_str} issued for {sim_name}.',
        )
        _send(subject=f'Simulacrum: generation failed for "{sim_name}" — refund issued', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send failure email to %s: %s', user_email, e, exc_info=True)


def send_partner_application_received_email(partner_email: str, partner_name: str):
    first = (partner_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Thank you for applying to the Simulacrum Referral Partner Program.\n\n'
            f'Our team will review your application and get back to you within 2 business days.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Application received') +
            _p(f'Hi {first},') +
            _p('Thank you for applying to the Simulacrum Referral Partner Program.') +
            _p('Our team will review your application and get back to you within 2 business days.'),
            preheader='Your partner application has been received.',
        )
        _send(subject='Simulacrum Partner Program — application received', recipients=[partner_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send partner application email to %s: %s', partner_email, e, exc_info=True)


def send_partner_approved_email(partner_email: str, partner_name: str, referral_code: str):
    first = (partner_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Your application to the Simulacrum Referral Partner Program has been approved.\n\n'
            f'Your referral code: {referral_code}\n\n'
            f'Log in to your partner dashboard to view your referral link, earnings, and advisor clients.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('You\'re approved! 🎉') +
            _p(f'Hi {first}, congratulations — your Simulacrum partner application has been approved.') +
            f'<p style="font-size:14px;color:#6b7280;margin:0 0 8px;">Your referral code</p>'
            f'<p style="font-size:22px;font-weight:700;color:#14b8a6;letter-spacing:2px;margin:0 0 24px;">{referral_code}</p>' +
            _p('Log in to your partner dashboard to find your referral link, track earnings, and manage advisor clients.'),
            preheader='Your Simulacrum partner application has been approved.',
        )
        _send(subject='Simulacrum Partner Program — you\'re approved!', recipients=[partner_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send partner approval email to %s: %s', partner_email, e, exc_info=True)


def send_partner_rejected_email(partner_email: str, partner_name: str, reason: str = None):
    first = (partner_name or '').strip().split()[0] or 'there'
    reason_block = f'<br><br>Feedback from our team: {reason}' if reason else ''
    reason_plain = f'\n\nReason: {reason}' if reason else ''
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Thank you for your interest in the Simulacrum Referral Partner Program.\n\n'
            f'After careful review, we are unable to approve your application at this time.{reason_plain}\n\n'
            f'You are welcome to reapply in the future.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Partner application update') +
            _p(f'Hi {first},') +
            _p(f'Thank you for your interest in the Simulacrum Referral Partner Program. After careful review, we are unable to approve your application at this time.{reason_block}') +
            _p('You are welcome to reapply in the future.'),
            preheader='An update on your Simulacrum partner application.',
        )
        _send(subject='Simulacrum Partner Program — application update', recipients=[partner_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send partner rejection email to %s: %s', partner_email, e, exc_info=True)


def send_referral_invitation_email(partner, recipient_email: str, recipient_first_name: str,
                                   personal_message: str, invitation_id: str):
    from flask import url_for
    try:
        ref_url    = partner.referral_link()
        pixel_url  = url_for('partners.referral_open_pixel', invitation_id=invitation_id, _external=True)
        greeting   = recipient_first_name or 'there'
        msg_block_plain = f'\n\n{personal_message}' if personal_message else ''
        msg_block_html  = f'<blockquote style="border-left:3px solid #14b8a6;margin:16px 0;padding:8px 16px;color:#374151;font-style:italic;">{personal_message}</blockquote>' if personal_message else ''
        plain = (
            f'Hi {greeting},{msg_block_plain}\n\n'
            f'{partner.full_name} ({partner.partner_type}) thinks you could benefit from '
            f'Simulacrum — an AI platform that maps your professional background into a '
            f'personalised 5-layer wealth blueprint.\n\n'
            f'Get started here: {ref_url}\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1(f'{partner.full_name} invited you to try Simulacrum') +
            _p(f'Hi {greeting},') +
            msg_block_html +
            _p(f'{partner.full_name} thinks you could benefit from Simulacrum — an AI platform that maps your professional background into a personalised 5-layer wealth blueprint.') +
            _btn(ref_url, 'Get started free') +
            _fallback_link(ref_url) +
            f'<img src="{pixel_url}" width="1" height="1" style="display:none;" />',
            preheader=f'{partner.full_name} invited you to try SimulacrumAI.io.',
        )
        _send(subject=f'{partner.full_name} invited you to try Simulacrum', recipients=[recipient_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send referral invitation to %s: %s', recipient_email, e, exc_info=True)


def send_password_changed_email(user_email: str, user_name: str):
    from datetime import datetime
    first = (user_name or '').strip().split()[0] or 'there'
    date_str = datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Your SimulacrumAI.io password was changed on {date_str}.\n\n'
            f'If this was not you, contact support immediately by replying to this email.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Your password was changed') +
            _p(f'Hi {first},') +
            _p(f'Your SimulacrumAI.io password was changed on {date_str}.') +
            _divider() +
            _p('If this was not you, contact support immediately by replying to this email.', muted=True),
            preheader='Your SimulacrumAI.io password was changed.',
        )
        _send(subject='Your SimulacrumAI.io password was changed', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send password change email to %s: %s', user_email, e, exc_info=True)


def send_email_change_verification(new_email: str, user_name: str, token: str):
    from flask import url_for
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        confirm_url = url_for('profile.confirm_email_change', _external=True) + f'?token={token}'
        plain = (
            f'Hi {first},\n\n'
            f'Click the link below to confirm your new email address for SimulacrumAI.io:\n\n'
            f'{confirm_url}\n\n'
            f'This link expires in 24 hours. If you did not request this change, ignore this email.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Confirm your new email address') +
            _p(f'Hi {first},') +
            _p('Click the button below to confirm your new email address for SimulacrumAI.io.') +
            _btn(confirm_url, 'Confirm new email address') +
            _fallback_link(confirm_url) +
            _divider() +
            _p('This link expires in 24 hours. If you did not request this change, you can safely ignore this email.', muted=True),
            preheader='Confirm your new SimulacrumAI.io email address.',
        )
        _send(subject='Confirm your new SimulacrumAI.io email address', recipients=[new_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send email change verification to %s: %s', new_email, e, exc_info=True)


def send_email_change_notification(old_email: str, user_name: str, new_email: str):
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Your SimulacrumAI.io email address was changed to {new_email}.\n\n'
            f'If you did not make this change, contact support immediately.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Email address changed') +
            _p(f'Hi {first},') +
            _p(f'Your SimulacrumAI.io email address was changed to <strong>{new_email}</strong>.') +
            _divider() +
            _p('If you did not make this change, contact support immediately by replying to this email.', muted=True),
            preheader='Your SimulacrumAI.io email address was changed.',
        )
        _send(subject='Your SimulacrumAI.io email address was changed', recipients=[old_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send email change notification to %s: %s', old_email, e, exc_info=True)


def send_account_deletion_email(user_email: str, user_name: str, recovery_token: str):
    from flask import url_for
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        recovery_url = url_for('profile.recover_account', _external=True) + f'?token={recovery_token}'
        plain = (
            f'Hi {first},\n\n'
            f'Your SimulacrumAI.io account has been scheduled for permanent deletion in 30 days.\n\n'
            f'All your simulations, artifacts, and profile data will be permanently removed after this period.\n\n'
            f'To cancel the deletion and recover your account, use this link within 30 days:\n\n'
            f'{recovery_url}\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Your account is scheduled for deletion') +
            _p(f'Hi {first},') +
            _p('Your SimulacrumAI.io account has been scheduled for permanent deletion in <strong>30 days</strong>. All simulations, artifacts, and profile data will be permanently removed.') +
            _p('If you changed your mind, click the button below to cancel the deletion and recover your account.') +
            _btn(recovery_url, 'Cancel deletion and recover account') +
            _fallback_link(recovery_url) +
            _divider() +
            _p('This recovery link expires in 30 days. After that, your data cannot be recovered.', muted=True),
            preheader='Action required: your SimulacrumAI.io account deletion is scheduled.',
        )
        _send(subject='Your SimulacrumAI.io account is scheduled for deletion', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send deletion email to %s: %s', user_email, e, exc_info=True)


def send_profile_inquiry_email(owner_email: str, owner_name: str, visitor_name: str,
                                visitor_email: str, subject: str, message: str):
    first = (owner_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'You have a new inquiry via your Simulacrum public profile.\n\n'
            f'From: {visitor_name} <{visitor_email}>\n'
            f'Subject: {subject}\n\n'
            f'Message:\n{message}\n\n'
            f'Reply directly to this email to respond to {visitor_name}.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('New inquiry from your profile') +
            _p(f'Hi {first}, you have a new inquiry via your SimulacrumAI.io public profile.') +
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:16px 0;">'
            f'<tr><td style="padding:8px 0;border-top:1px solid #e5e7eb;font-size:13px;color:#6b7280;width:80px;">From</td>'
            f'<td style="padding:8px 0;border-top:1px solid #e5e7eb;font-size:13px;color:#111827;">{visitor_name} &lt;{visitor_email}&gt;</td></tr>'
            f'<tr><td style="padding:8px 0;border-top:1px solid #e5e7eb;font-size:13px;color:#6b7280;">Subject</td>'
            f'<td style="padding:8px 0;border-top:1px solid #e5e7eb;font-size:13px;color:#111827;">{subject}</td></tr>'
            f'</table>'
            f'<div style="background:#f9fafb;border-radius:8px;padding:16px;margin:16px 0;font-size:14px;color:#374151;line-height:1.65;">{message}</div>' +
            _p(f'Reply directly to this email to respond to {visitor_name}.', muted=True),
            preheader=f'New inquiry from {visitor_name} via your Simulacrum profile.',
        )
        _send(subject=f'New inquiry from {visitor_name} — SimulacrumAI.io', recipients=[owner_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send profile inquiry email to %s: %s', owner_email, e, exc_info=True)


def send_feedback_received_email(user_email: str, user_name: str):
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Your Simulacrum story has been received. Our team will review it within 2 business days.\n\n'
            f'If approved, it will appear on our home page — we will notify you.\n\n'
            f'Thank you for sharing your experience.\n\n— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Story received') +
            _p(f'Hi {first},') +
            _p('Your Simulacrum story has been received. Our team will review it within 2 business days.') +
            _p('If approved, it will appear on our home page and we\'ll notify you. Thank you for sharing your experience.'),
            preheader='Your Simulacrum story has been received.',
        )
        _send(subject='Your Simulacrum story has been received', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send feedback received email to %s: %s', user_email, e, exc_info=True)


def send_escalation_email(
    to_email: str,
    name: str,
    title: str,
    description: Optional[str],
    action_url: str,
    sim_name: str,
):
    """Notify the user by email that an item has been escalated and needs their approval."""
    first = (name or '').strip().split()[0] or 'there'
    desc_text = (description or '').strip()
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Your Simulacrum orchestrator has escalated an item that needs your review.\n\n'
            f'Simulation: {sim_name}\n'
            f'Action required: {title}\n'
            + (f'\n{desc_text}\n' if desc_text else '')
            + f'\nReview it here:\n{action_url}\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Action required')
            + _p(f'Hi {first}, your orchestrator has escalated an item that needs your approval.')
            + f'<div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;'
              f'padding:1rem 1.25rem;margin:0 0 20px;">'
              f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
              f'letter-spacing:.06em;color:#92400e;margin-bottom:6px;">Simulation · {sim_name}</div>'
              f'<div style="font-size:15px;font-weight:700;color:#111827;margin-bottom:6px;">{title}</div>'
              + (f'<div style="font-size:14px;color:#374151;line-height:1.6;">{desc_text}</div>' if desc_text else '')
              + '</div>'
            + _btn(action_url, 'Review in GCC →')
            + _fallback_link(action_url)
            + _divider()
            + _p('The orchestrator paused this action until you approve or reject it.', muted=True),
            preheader=f'Action required: {title}',
        )
        _send(
            subject=f'Action required: {title} — SimulacrumAI.io',
            recipients=[to_email],
            body=plain,
            html=html,
        )
    except Exception as e:
        logger.error('Failed to send escalation email to %s: %s', to_email, e, exc_info=True)


def send_admin_new_feedback_email(admin_email: str, submitter_name: str, star_rating: int,
                                   quote_text: str, outcome_text: str, layers: list):
    layer_str = ', '.join(l['label'] for l in layers) if layers else 'None'
    stars = '★' * star_rating + '☆' * (5 - star_rating)
    try:
        plain = (
            f'New feedback submitted by {submitter_name} — {star_rating} stars ({stars}).\n\n'
            f'Layers attributed: {layer_str}\n\n'
            f'Outcome:\n{outcome_text}\n\n'
            f'Testimonial quote:\n"{quote_text}"\n\n'
            f'Review in the admin panel.\n\n— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('New testimonial — pending review') +
            _p(f'<strong>{submitter_name}</strong> submitted a {star_rating}-star testimonial.') +
            f'<p style="font-size:24px;margin:0 0 16px;color:#f59e0b;">{stars}</p>'
            f'<p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Layers</p>'
            f'<p style="font-size:14px;color:#111827;margin:0 0 16px;">{layer_str}</p>'
            f'<p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Outcome</p>'
            f'<p style="font-size:14px;color:#374151;margin:0 0 16px;">{outcome_text}</p>'
            f'<p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Quote</p>'
            f'<p style="font-size:14px;color:#374151;font-style:italic;margin:0 0 0;">"{quote_text}"</p>',
            preheader=f'New {star_rating}-star testimonial from {submitter_name}.',
        )
        _send(subject=f'New Simulacrum testimonial — pending review', recipients=[admin_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send admin feedback notification to %s: %s', admin_email, e, exc_info=True)


def send_feedback_approved_email(user_email: str, user_name: str, is_featured: bool):
    first = (user_name or '').strip().split()[0] or 'there'
    if is_featured:
        headline = 'Your story is featured! 🌟'
        body_text = 'Your Simulacrum story has been selected to be featured on our home page. We chose it to highlight the real results our users achieve. Thank you for sharing.'
        preheader = 'Your Simulacrum story is featured on our home page.'
    else:
        headline = 'Your story is live'
        body_text = 'Your Simulacrum story has been approved and is now live on our home page. Thank you for sharing your experience.'
        preheader = 'Your Simulacrum story is now live on our home page.'
    try:
        plain = f'Hi {first},\n\n{body_text}\n\nView it live at simulacrumai.io\n\n— SimulacrumAI.io'
        html = _html_wrap(
            _h1(headline) +
            _p(f'Hi {first},') +
            _p(body_text) +
            _btn('https://simulacrumai.io', 'View your story live'),
            preheader=preheader,
        )
        _send(subject=headline, recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send feedback approved email to %s: %s', user_email, e, exc_info=True)


def send_feedback_rejected_email(user_email: str, user_name: str, admin_note: str = None):
    first = (user_name or '').strip().split()[0] or 'there'
    note_plain = f'\n\nFeedback from our team: {admin_note}' if admin_note else ''
    note_html  = f'<p style="font-size:14px;color:#6b7280;font-style:italic;margin:12px 0 0;">Feedback from our team: {admin_note}</p>' if admin_note else ''
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Thank you for sharing your Simulacrum story. After review, we are unable to feature this submission on our home page at this time.{note_plain}\n\n'
            f'You are welcome to submit new feedback at any time from your dashboard.\n\n— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('An update on your story') +
            _p(f'Hi {first},') +
            _p('Thank you for sharing your Simulacrum story. After review, we are unable to feature this submission on our home page at this time.') +
            note_html +
            _divider() +
            _p('You are welcome to submit new feedback at any time from your dashboard.', muted=True),
            preheader='An update on your Simulacrum story submission.',
        )
        _send(subject='An update on your Simulacrum story', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send feedback rejected email to %s: %s', user_email, e, exc_info=True)


def send_feedback_withdrawal_request_email(admin_email: str, submitter_name: str, feedback_id: str):
    try:
        plain = (
            f'{submitter_name} has requested withdrawal of their approved testimonial.\n\n'
            f'Feedback ID: {feedback_id}\n\n'
            f'Review and unpublish in the admin feedback panel if appropriate.\n\n— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Testimonial withdrawal request') +
            _p(f'<strong>{submitter_name}</strong> has requested withdrawal of their approved testimonial.') +
            _p(f'Feedback ID: {feedback_id}', muted=True) +
            _p('Review and unpublish in the admin feedback panel if appropriate.', muted=True),
            preheader=f'Withdrawal request from {submitter_name}.',
        )
        _send(subject=f'Testimonial withdrawal request — {submitter_name}', recipients=[admin_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send withdrawal request email to %s: %s', admin_email, e, exc_info=True)


def send_data_retention_warning_email(user_email: str, user_name: str, deletion_date: str):
    first = (user_name or '').strip().split()[0] or 'there'
    try:
        login_url = 'https://simulacrumai.io/login'
        plain = (
            f'Hi {first},\n\n'
            f'Your Simulacrum account has been inactive for an extended period.\n\n'
            f'Your data — including all simulations, resume files, and Growth Command Center records — '
            f'is scheduled for permanent deletion on {deletion_date}.\n\n'
            f'To keep your account active, simply sign in before {deletion_date}:\n'
            f'{login_url}\n\n'
            f'If you would prefer to delete your data now, email simi@simulacrumai.io.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Your data is scheduled for deletion') +
            _p(f'Hi {first},') +
            _p(f'Your SimulacrumAI.io account has been inactive for an extended period. Your data — including all simulations, resume files, and Growth Command Center records — is scheduled for permanent deletion on <strong>{deletion_date}</strong>.') +
            _p('To keep your account active, sign in before that date.') +
            _btn(login_url, 'Sign in to keep my account') +
            _divider() +
            _p('If you would prefer to delete your data now, email simi@simulacrumai.io.', muted=True),
            preheader=f'Action required: your data is scheduled for deletion on {deletion_date}.',
        )
        _send(subject='Your SimulacrumAI.io data is scheduled for deletion', recipients=[user_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send retention warning email to %s: %s', user_email, e, exc_info=True)


def send_collab_invite_email(invitee_email: str, inviter_name: str, sim_name: str, share_token: str):
    from flask import url_for
    try:
        accept_url = url_for('collaboration.accept_invite', token=share_token, _external=True)
        plain = (
            f'Hi,\n\n'
            f'{inviter_name} has invited you to collaborate on their Simulacrum wealth simulation: "{sim_name}"\n\n'
            f'Accept the invitation: {accept_url}\n\n'
            f'This link expires in 30 days.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1(f'{inviter_name} invited you to collaborate') +
            _p(f'{inviter_name} has invited you to collaborate on their Simulacrum wealth simulation:') +
            f'<p style="font-size:17px;font-weight:700;color:#111827;margin:0 0 24px;">"{sim_name}"</p>' +
            _btn(accept_url, 'Accept invitation') +
            _fallback_link(accept_url) +
            _divider() +
            _p('This invitation expires in 30 days.', muted=True),
            preheader=f'{inviter_name} invited you to collaborate on a Simulacrum simulation.',
        )
        _send(subject=f'{inviter_name} invited you to collaborate on "{sim_name}"', recipients=[invitee_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send collab invite to %s: %s', invitee_email, e, exc_info=True)


def send_alert_digest_email(user, alerts: list):
    """Daily digest of active proactive alerts (ENH-04)."""
    if not alerts:
        return
    first = (user.full_name or '').strip().split()[0] or 'there'
    try:
        lines = [f'Hi {first},\n', 'Here is your daily Simulacrum activity digest:\n']
        for a in alerts:
            lines.append(f'  • [{a.item_type.replace("_", " ").title()}] {a.title}')
        lines.append('\nLog in to take action: https://simulacrumai.io/simulations')
        lines.append('\n— SimulacrumAI.io')

        alert_rows = ''.join(
            f'<tr><td style="padding:10px 0;border-top:1px solid #e5e7eb;">'
            f'<span style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;">'
            f'{a.item_type.replace("_", " ")}</span><br>'
            f'<span style="font-size:14px;color:#111827;">{a.title}</span>'
            f'</td></tr>'
            for a in alerts
        )
        html = _html_wrap(
            _h1(f'Your daily digest — {len(alerts)} alert{"s" if len(alerts) != 1 else ""}') +
            _p(f'Hi {first}, here is your Simulacrum activity digest.') +
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;">{alert_rows}</table>' +
            _btn('https://simulacrumai.io/simulations', 'View in Simulacrum'),
            preheader=f'{len(alerts)} alert{"s" if len(alerts) != 1 else ""} in your Simulacrum digest.',
        )
        _send(
            subject=f'Your Simulacrum digest — {len(alerts)} alert{"s" if len(alerts) != 1 else ""}',
            recipients=[user.email],
            body='\n'.join(lines),
            html=html,
        )
    except Exception as e:
        logger.error('Failed to send alert digest to %s: %s', user.email, e, exc_info=True)


def send_cycle_report_email(
    to_email: str,
    name: str,
    sim_name: str,
    sim_id: str,
    cycle_number: int,
    phase: str,
    actions_scored: int,
    dispatched: list,
    escalated: list,
    user_insight: str,
    cycle_steps,
    next_cycle_at: str,
    gcc_url: str,
):
    """Send a full cycle report to the user after each Layer 6 orchestrator cycle."""
    first = (name or '').strip().split()[0] or 'there'
    phase_label = {'explore': 'Exploration', 'transition': 'Transition', 'exploit': 'Exploit'}.get(phase, phase.title())
    dispatched_count = len(dispatched)
    escalated_count = len(escalated)
    subject = (
        f'Cycle #{cycle_number} complete — {dispatched_count} agent{"s" if dispatched_count != 1 else ""} dispatched'
        + (f', {escalated_count} awaiting approval' if escalated_count else '')
        + f' — SimulacrumAI.io'
    )

    # ── Plain text ──
    plain_lines = [
        f'Hi {first},',
        f'',
        f'Cycle #{cycle_number} ({phase_label} phase) has completed for your simulation "{sim_name}".',
        f'',
        f'Summary',
        f'  Agents scored:    {actions_scored}',
        f'  Agents dispatched: {dispatched_count}',
        f'  Awaiting approval: {escalated_count}',
        f'',
    ]
    if dispatched:
        plain_lines.append('Agents dispatched this cycle:')
        for d in dispatched:
            plain_lines.append(f'  • {d["label"]}')
        plain_lines.append('')
    if escalated:
        plain_lines.append('Actions awaiting your approval:')
        for e in escalated:
            plain_lines.append(f'  • {e["label"]}')
            if e.get('reason'):
                plain_lines.append(f'    Reason: {e["reason"]}')
        plain_lines.append('')
    if user_insight:
        plain_lines += ['Orchestrator insight:', user_insight, '']
    if cycle_steps:
        steps_list = _parse_cycle_steps(cycle_steps)
        if steps_list:
            plain_lines.append('Your to-do items:')
            for s in steps_list:
                plain_lines.append(f'  • {s}')
            plain_lines.append('')
    plain_lines += [
        f'Next cycle: {next_cycle_at}',
        f'',
        f'View your GCC: {gcc_url}',
        f'',
        f'— SimulacrumAI.io',
    ]
    plain = '\n'.join(plain_lines)

    # ── HTML ──
    phase_color = {'explore': '#6366f1', 'transition': '#f59e0b', 'exploit': '#14b8a6'}.get(phase, '#6b7280')

    stat_row = (
        f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:20px 0;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">'
        f'<tr>'
        f'<td style="padding:16px;text-align:center;border-right:1px solid #e5e7eb;">'
        f'<div style="font-size:22px;font-weight:700;color:#111827;">{actions_scored}</div>'
        f'<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-top:4px;">Scored</div>'
        f'</td>'
        f'<td style="padding:16px;text-align:center;border-right:1px solid #e5e7eb;">'
        f'<div style="font-size:22px;font-weight:700;color:#14b8a6;">{dispatched_count}</div>'
        f'<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-top:4px;">Dispatched</div>'
        f'</td>'
        f'<td style="padding:16px;text-align:center;">'
        f'<div style="font-size:22px;font-weight:700;color:{"#f59e0b" if escalated_count else "#6b7280"};">{escalated_count}</div>'
        f'<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-top:4px;">Need approval</div>'
        f'</td>'
        f'</tr></table>'
    )

    dispatched_html = ''
    if dispatched:
        rows = ''.join(
            f'<tr><td style="padding:9px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#111827;">'
            f'<span style="display:inline-block;width:8px;height:8px;background:#14b8a6;border-radius:50%;margin-right:8px;"></span>'
            f'{d["label"]}</td></tr>'
            for d in dispatched
        )
        dispatched_html = (
            f'<p style="font-size:13px;font-weight:700;color:#374151;text-transform:uppercase;letter-spacing:.05em;margin:24px 0 0;">Agents dispatched</p>'
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;">{rows}</table>'
        )

    escalated_html = ''
    if escalated:
        esc_items = ''.join(
            f'<div style="margin-bottom:10px;">'
            f'<div style="font-size:14px;font-weight:700;color:#92400e;">{e["label"]}</div>'
            + (f'<div style="font-size:13px;color:#78350f;margin-top:2px;">{e["reason"]}</div>' if e.get('reason') else '')
            + '</div>'
            for e in escalated
        )
        escalated_html = (
            f'<div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:16px 20px;margin:20px 0;">'
            f'<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#92400e;margin-bottom:10px;">Awaiting your approval</div>'
            + esc_items
            + '</div>'
        )

    insight_html = ''
    if user_insight:
        insight_html = (
            f'<div style="background:#f0fdf4;border-left:3px solid #14b8a6;border-radius:0 8px 8px 0;'
            f'padding:14px 16px;margin:20px 0;">'
            f'<div style="font-size:12px;font-weight:700;color:#065f46;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">Orchestrator insight</div>'
            f'<div style="font-size:14px;color:#064e3b;line-height:1.65;">{user_insight}</div>'
            f'</div>'
        )

    steps_html = ''
    steps_list = _parse_cycle_steps(cycle_steps) if cycle_steps else []
    if steps_list:
        step_rows = ''.join(
            f'<tr><td style="padding:8px 0;border-top:1px solid #e5e7eb;font-size:14px;color:#374151;vertical-align:top;">'
            f'<span style="font-weight:600;color:#14b8a6;margin-right:8px;">{i + 1}.</span>{s}</td></tr>'
            for i, s in enumerate(steps_list)
        )
        steps_html = (
            f'<p style="font-size:13px;font-weight:700;color:#374151;text-transform:uppercase;letter-spacing:.05em;margin:24px 0 0;">Your to-do items</p>'
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;">{step_rows}</table>'
        )

    next_cycle_html = (
        f'<div style="background:#f9fafb;border-radius:8px;padding:14px 16px;margin:24px 0 0;font-size:13px;color:#6b7280;">'
        f'Next cycle scheduled: <strong style="color:#374151;">{next_cycle_at}</strong>'
        f'</div>'
    )

    body_html = (
        _h1(f'Cycle #{cycle_number} complete')
        + f'<p style="margin:0 0 4px;font-size:13px;color:#6b7280;">{sim_name}</p>'
        + f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;'
          f'text-transform:uppercase;letter-spacing:.06em;color:#fff;background:{phase_color};margin-bottom:16px;">'
          f'{phase_label} phase</span>'
        + stat_row
        + dispatched_html
        + escalated_html
        + insight_html
        + steps_html
        + next_cycle_html
        + _btn(gcc_url, 'View in GCC →')
        + _fallback_link(gcc_url)
    )
    html = _html_wrap(body_html, preheader=f'Cycle #{cycle_number}: {dispatched_count} agents dispatched. Next: {next_cycle_at}.')

    try:
        _send(subject=subject, recipients=[to_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send cycle report email to %s: %s', to_email, e, exc_info=True)


def _parse_cycle_steps(cycle_steps) -> list:
    """Return a list of step strings from whatever format cycle_steps is stored as."""
    import json as _json
    if not cycle_steps:
        return []
    if isinstance(cycle_steps, list):
        return [str(s) for s in cycle_steps if s]
    if isinstance(cycle_steps, str):
        try:
            parsed = _json.loads(cycle_steps)
            if isinstance(parsed, list):
                return [str(s) for s in parsed if s]
        except Exception:
            pass
        return [s.strip() for s in cycle_steps.split('\n') if s.strip()]
    return []


def send_bcc_signup_notification(user_email: str, user_name: str):
    try:
        _send(
            subject=f'New sign-up: {user_name} <{user_email}>',
            recipients=['simi@simulacrumai.io'],
            body=f'New user registered.\n\nName: {user_name}\nEmail: {user_email}',
        )
    except Exception as e:
        logger.error('BCC signup notification failed: %s', e)


def send_bcc_simulation_notification(user_email: str, user_name: str, sim_name: str, sim_id):
    try:
        _send(
            subject=f'New simulation: {sim_name} — {user_name}',
            recipients=['simi@simulacrumai.io'],
            body=f'New simulation created.\n\nUser: {user_name} <{user_email}>\nSimulation: {sim_name}\nID: {sim_id}',
        )
    except Exception as e:
        logger.error('BCC simulation notification failed: %s', e)


def send_job_application_received_email(applicant_email: str, applicant_name: str, job_title: str):
    """Careers — confirmation to the applicant."""
    first = (applicant_name or '').strip().split()[0] or 'there'
    try:
        plain = (
            f'Hi {first},\n\n'
            f'Thank you for applying for the {job_title} role at Simulacrum.\n\n'
            f'We have your application and your resume. We read every one — if there is a fit, '
            f'you will hear from us by email.\n\n'
            f'— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('Application received') +
            _p(f'Hi {first},') +
            _p(f'Thank you for applying for the <strong>{job_title}</strong> role at Simulacrum.') +
            _p('We have your application and your resume. We read every one — if there is a fit, you will hear from us by email.'),
            preheader=f'Your application for {job_title} has been received.',
        )
        _send(subject=f'Simulacrum — application received ({job_title})',
              recipients=[applicant_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send job application email to %s: %s', applicant_email, e, exc_info=True)


def send_admin_new_job_application_email(admin_email: str, applicant_name: str, applicant_email: str,
                                         phone: str, job_title: str, resume_filename: str):
    """Careers — notify the admin that an application is waiting in /admin/careers."""
    try:
        plain = (
            f'New application for {job_title}.\n\n'
            f'Name: {applicant_name}\n'
            f'Email: {applicant_email}\n'
            f'Phone: {phone}\n'
            f'Resume: {resume_filename}\n\n'
            f'Review it in the admin panel under Careers.\n\n— SimulacrumAI.io'
        )
        html = _html_wrap(
            _h1('New job application') +
            _p(f'<strong>{applicant_name}</strong> applied for <strong>{job_title}</strong>.') +
            f'<p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Email</p>'
            f'<p style="font-size:14px;color:#111827;margin:0 0 12px;">{applicant_email}</p>'
            f'<p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Phone</p>'
            f'<p style="font-size:14px;color:#111827;margin:0 0 12px;">{phone}</p>'
            f'<p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Resume</p>'
            f'<p style="font-size:14px;color:#111827;margin:0 0 0;">{resume_filename}</p>',
            preheader=f'{applicant_name} applied for {job_title}.',
        )
        _send(subject=f'New application — {job_title}', recipients=[admin_email], body=plain, html=html)
    except Exception as e:
        logger.error('Failed to send admin job application notification to %s: %s', admin_email, e, exc_info=True)
