import logging
from flask import request, jsonify
from flask_login import login_required, current_user

from app.blueprints.notifications import notifications_bp

logger = logging.getLogger(__name__)


@notifications_bp.route('/api/notifications/unread-count')
@login_required
def unread_count():
    from app.services.notification_service import get_unread_count
    return jsonify({'count': get_unread_count(current_user.id)})


@notifications_bp.route('/api/notifications')
@login_required
def list_notifications():
    limit  = min(request.args.get('limit',  30, type=int), 100)
    offset = request.args.get('offset', 0, type=int)
    from app.services.notification_service import get_notifications
    return jsonify(get_notifications(current_user.id, limit=limit, offset=offset))


@notifications_bp.route('/api/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    from app.services.notification_service import mark_all_read as _mark
    count = _mark(current_user.id)
    return jsonify({'ok': True, 'marked': count})


@notifications_bp.route('/api/notifications/<notif_id>/read', methods=['POST'])
@login_required
def mark_one_read(notif_id):
    from app.services.notification_service import mark_read
    mark_read(notif_id, current_user.id)
    return jsonify({'ok': True})


@notifications_bp.route('/api/notifications/preferences', methods=['GET'])
@login_required
def get_prefs():
    from app.services.notification_service import get_preferences
    return jsonify(get_preferences(current_user.id))


@notifications_bp.route('/api/notifications/preferences', methods=['POST'])
@login_required
def save_prefs():
    data = request.get_json(silent=True) or {}
    prefs = data.get('preferences', [])
    if not isinstance(prefs, list):
        return jsonify({'error': 'preferences must be a list'}), 400
    from app.services.notification_service import save_preferences
    try:
        save_preferences(current_user.id, prefs)
        return jsonify({'ok': True})
    except Exception as exc:
        logger.error('save_prefs failed user=%s: %s', current_user.id, exc)
        return jsonify({'error': str(exc)}), 500


@notifications_bp.route('/api/notifications/digest/<user_id>', methods=['POST'])
def trigger_digest(user_id):
    """
    Cron endpoint — trigger daily digest for a user.
    Protected by a shared secret in the Authorization header.
    """
    from flask import current_app
    secret = current_app.config.get('DIGEST_CRON_SECRET')
    if secret:
        auth = request.headers.get('Authorization', '')
        if auth != f'Bearer {secret}':
            return jsonify({'error': 'Unauthorized'}), 401

    from app.services.notification_service import send_daily_digest
    notif_id = send_daily_digest(user_id)
    return jsonify({'ok': True, 'notif_id': notif_id})
