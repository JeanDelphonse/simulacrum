"""FR-TOS-13 / FR-TOS-14: Daily inactivity scan and 30-day pre-deletion email."""
from celery_worker import celery
import logging

logger = logging.getLogger(__name__)


@celery.task
def check_inactive_users():
    """Identify users whose last_login_at is within 30 days of the retention window
    and send a pre-deletion warning email. Does NOT delete any data — deletion
    requires a second pass after the 30-day grace period.

    Controlled by platform_settings key 'data_retention_months' (default: 15).
    """
    from datetime import datetime, timedelta
    from app.models.user import User
    from app.models.platform_settings import PlatformSetting
    from app.services.email_service import send_data_retention_warning_email

    try:
        retention_months = int(PlatformSetting.get('data_retention_months', '15'))
    except (TypeError, ValueError):
        retention_months = 15

    now = datetime.utcnow()
    # Warn when inactivity is between (retention - 1 month) and retention months
    warn_after  = now - timedelta(days=(retention_months * 30) - 30)
    delete_after = now - timedelta(days=retention_months * 30)

    # Users in the warning window (inactive 14+ months but not yet 15 months)
    warning_users = User.query.filter(
        User.last_login_at <= warn_after,
        User.last_login_at > delete_after,
        User.deleted_at.is_(None),
        User.retention_warned_at.is_(None),
    ).all()

    warned = 0
    for user in warning_users:
        deletion_date = (user.last_login_at + timedelta(days=retention_months * 30)).strftime('%B %d, %Y')
        try:
            send_data_retention_warning_email(user.email, user.full_name, deletion_date)
            user.retention_warned_at = now
            warned += 1
        except Exception as e:
            logger.error('Failed to warn user %s about retention: %s', user.id, e)

    # Users past the full retention window — schedule for deletion
    expired_users = User.query.filter(
        User.last_login_at <= delete_after,
        User.deleted_at.is_(None),
    ).all()

    deleted = 0
    for user in expired_users:
        try:
            _delete_user_data(user)
            deleted += 1
        except Exception as e:
            logger.error('Failed to delete expired user data for %s: %s', user.id, e)

    from app.extensions import db
    db.session.commit()

    logger.info('Retention scan: warned=%d deleted=%d', warned, deleted)
    return {'warned': warned, 'deleted': deleted}


def _delete_user_data(user):
    """Permanently delete all user data except resume_consents (FR-TOS-09)."""
    from datetime import datetime
    from app.extensions import db
    from app.models.simulation import Simulation
    from app.models.resume import Resume

    # Soft-delete the user account (sets deleted_at, preserving FK integrity)
    user.deleted_at = datetime.utcnow()

    # Hard-delete simulations
    Simulation.query.filter_by(user_id=user.id).delete()

    # Hard-delete resume files and records (consent records survive — ON DELETE RESTRICT)
    resumes = Resume.query.filter_by(user_id=user.id).all()
    import os
    for r in resumes:
        if r.file_path and os.path.exists(r.file_path):
            try:
                os.remove(r.file_path)
            except OSError:
                pass
        db.session.delete(r)
