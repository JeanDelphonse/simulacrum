"""
Notification service — SIM-PRD-NOTIF-001.

Provides synchronous send_notification() that:
  1. Creates an in-app Notification record
  2. Sends email via SendGrid (if API key configured + user prefs allow)

Celery is not required; email is sent inline. Failures are logged and swallowed
so the calling operation is never blocked by an email error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def send_notification(
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    cta_url: Optional[str] = None,
    cta_label: Optional[str] = None,
    simulation_id: Optional[str] = None,
    priority: str = 'normal',
) -> Optional[str]:
    """
    Create in-app notification + send email if configured and user allows it.
    Returns the notification id, or None on failure.
    """
    from app.extensions import db
    from app.models.notification import Notification, NotificationPreference
    from utils.id_gen import generate_id

    try:
        notif = Notification(
            id=generate_id(),
            user_id=user_id,
            simulation_id=simulation_id,
            notification_type=notification_type,
            title=title,
            body=body,
            cta_url=cta_url,
            cta_label=cta_label,
            priority=priority,
        )
        db.session.add(notif)
        db.session.commit()
    except Exception as exc:
        logger.error('send_notification: DB insert failed user=%s type=%s: %s',
                     user_id, notification_type, exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None

    # retention_warning email is always sent regardless of prefs (FR-NOTIF-06)
    forced = notification_type == 'retention_warning'
    try:
        pref = NotificationPreference.get_for(user_id, notification_type)
        if forced or (pref.email_enabled and not pref.digest_mode):
            _send_email(notif)
    except Exception as exc:
        logger.warning('send_notification: email step failed user=%s type=%s: %s',
                       user_id, notification_type, exc)

    return notif.id


def _send_email(notif) -> None:
    """Send a single transactional email via SendGrid for the given notification."""
    from app.models.platform_settings import PlatformSetting
    from app.models.user import User
    from app.extensions import db

    api_key = PlatformSetting.get('sendgrid_api_key')
    if not api_key:
        return

    user = User.query.get(notif.user_id)
    if not user or not user.email:
        return

    try:
        import sendgrid as _sg
        from sendgrid.helpers.mail import Mail, To, From, Subject, PlainTextContent, HtmlContent
    except ImportError:
        # sendgrid package not installed — log once and move on
        logger.warning('sendgrid package not installed; skipping email notification')
        return

    first_name = (user.full_name or '').split()[0] if user.full_name else 'there'
    cta_html = ''
    if notif.cta_url and notif.cta_label:
        cta_html = (
            f'<p style="margin-top:20px">'
            f'<a href="{notif.cta_url}" style="background:#0f7b72;color:#fff;'
            f'padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600">'
            f'{notif.cta_label}</a></p>'
        )

    html_body = (
        f'<div style="font-family:sans-serif;max-width:560px;margin:auto">'
        f'<p>Hi {first_name},</p>'
        f'<p>{notif.body}</p>'
        f'{cta_html}'
        f'<hr style="margin-top:32px;border:none;border-top:1px solid #e5e7eb">'
        f'<p style="font-size:12px;color:#9ca3af">Simulacrum · '
        f'<a href="/settings/notifications" style="color:#9ca3af">Manage notification preferences</a></p>'
        f'</div>'
    )

    message = Mail(
        from_email=From('notifications@simulacrum.app', 'Simulacrum'),
        to_emails=To(user.email),
        subject=Subject(notif.title),
        plain_text_content=PlainTextContent(notif.body),
        html_content=HtmlContent(html_body),
    )

    try:
        client = _sg.SendGridAPIClient(api_key)
        client.send(message)
        from app.extensions import db as _db
        notif.email_sent = True
        notif.email_sent_at = datetime.utcnow()
        _db.session.commit()
        logger.info('Email notification sent: user=%s type=%s', notif.user_id, notif.notification_type)
    except Exception as exc:
        logger.warning('SendGrid send failed: %s', exc)


def get_unread_count(user_id: str) -> int:
    from app.models.notification import Notification
    return Notification.query.filter_by(user_id=user_id, read_at=None).count()


def get_notifications(user_id: str, limit: int = 30, offset: int = 0) -> list:
    from app.models.notification import Notification
    rows = (
        Notification.query
        .filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit).offset(offset).all()
    )
    return [r.to_dict() for r in rows]


def mark_read(notification_id: str, user_id: str) -> bool:
    from app.models.notification import Notification
    from app.extensions import db
    notif = Notification.query.filter_by(id=notification_id, user_id=user_id).first()
    if not notif or notif.read_at:
        return False
    notif.read_at = datetime.utcnow()
    db.session.commit()
    return True


def mark_all_read(user_id: str) -> int:
    from app.models.notification import Notification
    from app.extensions import db
    count = (
        Notification.query
        .filter_by(user_id=user_id)
        .filter(Notification.read_at.is_(None))
        .update({'read_at': datetime.utcnow()}, synchronize_session=False)
    )
    db.session.commit()
    return count


def save_preferences(user_id: str, prefs: list[dict]) -> None:
    """
    Upsert notification preferences.
    prefs: [{'notification_type': str, 'email_enabled': bool, 'digest_mode': bool}]
    """
    from app.models.notification import NotificationPreference, DIGEST_ELIGIBLE
    from app.extensions import db
    from utils.id_gen import generate_id

    for p in prefs:
        ntype = p.get('notification_type', '')
        email_enabled = bool(p.get('email_enabled', True))
        digest_mode = bool(p.get('digest_mode', False)) and ntype in DIGEST_ELIGIBLE

        existing = NotificationPreference.query.filter_by(
            user_id=user_id, notification_type=ntype
        ).first()
        if existing:
            existing.email_enabled = email_enabled
            existing.digest_mode = digest_mode
        else:
            db.session.add(NotificationPreference(
                id=generate_id(),
                user_id=user_id,
                notification_type=ntype,
                email_enabled=email_enabled,
                digest_mode=digest_mode,
            ))
    db.session.commit()


def get_preferences(user_id: str) -> dict:
    """Return a dict of notification_type → {email_enabled, digest_mode}."""
    from app.models.notification import NotificationPreference, EMAIL_DEFAULTS
    rows = NotificationPreference.query.filter_by(user_id=user_id).all()
    by_type = {r.notification_type: r for r in rows}
    result = {}
    for ntype, defaults in EMAIL_DEFAULTS.items():
        if ntype in by_type:
            r = by_type[ntype]
            result[ntype] = {'email_enabled': r.email_enabled, 'digest_mode': r.digest_mode}
        else:
            result[ntype] = dict(defaults)
    return result


def send_daily_digest(user_id: str) -> Optional[str]:
    """
    Aggregate yesterday's digest-eligible notifications and send a single summary email.
    Called by an external cron hitting POST /api/notifications/digest/<user_id>.
    """
    from app.models.notification import Notification, DIGEST_ELIGIBLE
    from app.extensions import db

    yesterday_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_end   = yesterday_start + timedelta(days=1)

    unsent = (
        Notification.query
        .filter_by(user_id=user_id, email_sent=False)
        .filter(Notification.notification_type.in_(DIGEST_ELIGIBLE))
        .filter(Notification.created_at >= yesterday_start)
        .filter(Notification.created_at < yesterday_end)
        .all()
    )
    if not unsent:
        return None

    lines = [f'• {n.title}' for n in unsent]
    body = 'Your Simulacrum digest for ' + yesterday_start.strftime('%b %-d') + ':\n\n' + '\n'.join(lines)

    notif_id = send_notification(
        user_id=user_id,
        notification_type='cycle_summary',
        title=f'Your Simulacrum digest — {yesterday_start.strftime("%b %-d")}',
        body=body,
        cta_url='/simulations',
        cta_label='Open GCC →',
        priority='low',
    )

    for n in unsent:
        n.email_sent = True
        n.email_sent_at = datetime.utcnow()
    db.session.commit()

    return notif_id


def purge_old_notifications(days: int = 90) -> int:
    """Remove notifications older than `days`. Called by maintenance cron."""
    from app.models.notification import Notification
    from app.extensions import db
    cutoff = datetime.utcnow() - timedelta(days=days)
    count = (
        Notification.query
        .filter(Notification.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return count
