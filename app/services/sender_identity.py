"""Who a commercial email is from, and the footer it carries (COMPLIANCE_PLAN C1, D7).

CAN-SPAM wants an honest From line, a working opt-out and the sender's valid
physical postal address. A member's outreach goes out under their name; their
postal address (encrypted on users.postal_address_enc, set in Settings) is
required before any campaign or agent email sends. SimiSI's own campaigns use
the Admin setting outreach_business_address. Footer wording comes from the
legal-text source.
"""
from __future__ import annotations

import logging
from html import escape

logger = logging.getLogger(__name__)

# Bumped whenever the footer or the unsubscribe link format changes; recorded
# with every send in the audit log (COMPLIANCE_PLAN C7).
UNSUB_VERSION = 'unsub-v2'
MIN_ADDRESS_LEN = 10


def postal_address(user) -> str | None:
    enc = getattr(user, 'postal_address_enc', None)
    if not enc:
        return None
    try:
        from app.services.token_crypto import decrypt_token
        return decrypt_token(enc) or None
    except Exception:
        logger.warning('postal address for %s could not be decrypted', getattr(user, 'id', '?'))
        return None


def set_postal_address(user, text: str) -> str:
    """Validate and store (encrypted). A PO box or private mailbox is accepted."""
    from app.extensions import db
    from app.services.token_crypto import encrypt_token
    value = ' '.join((text or '').split())
    if len(value) < MIN_ADDRESS_LEN or not any(ch.isdigit() for ch in value):
        raise ValueError('Enter a full mailing address: street or PO box, city, and postal code.')
    if len(value) > 300:
        raise ValueError('That address is too long.')
    user.postal_address_enc = encrypt_token(value)
    db.session.commit()
    return value


def business_address() -> str | None:
    """SimiSI's own postal address for its campaigns (Admin setting)."""
    try:
        from app.models.platform_settings import PlatformSetting
        v = (PlatformSetting.get('outreach_business_address', '') or '').strip()
        return v if len(v) >= MIN_ADDRESS_LEN else None
    except Exception:
        return None


def footer_html(sender_name: str, address: str, unsubscribe_url: str) -> str:
    from app.services import legal_text
    marker = '\x00UNSUB\x00'
    text = legal_text.get('email_footer', sender_name=sender_name or 'SimiSI',
                          sender_postal_address=address, unsubscribe_url=marker)
    html = escape(text).replace(escape(marker), '<a href="%s" style="color:#68738C;">Unsubscribe</a>'
                                % escape(unsubscribe_url, quote=True))
    return '<p style="font-size:12px;line-height:1.5;color:#68738C;margin-top:24px;">%s</p>' % html


def ask_for_address(user_id: str, simulation_id: str | None) -> None:
    """One GCC action item asking the member to add their mailing address."""
    if not simulation_id:
        return
    try:
        from app.models.layer6 import ActionItem
        from utils.action_items import create_action_item
        from app.services import legal_text
        if ActionItem.query.filter_by(simulation_id=simulation_id, item_type='postal_address_required',
                                      status=ActionItem.STATUS_ACTIVE).first():
            return
        create_action_item(simulation_id=simulation_id, user_id=user_id, item_type='postal_address_required',
                           title='Add your mailing address to send email',
                           action_url='/settings/notifications#sending',
                           description=legal_text.get('postal_address_required'))
    except Exception:
        logger.warning('could not raise the postal-address action item', exc_info=True)


def record_send(path: str, result: dict, trail: dict, user_id=None, resource_id=None) -> None:
    """One AuditLog 'outreach_send' row per send attempt, sent or not
    (COMPLIANCE_PLAN C7): which path, which unsubscribe version, and what the
    suppression, cap and postal-address checks said. Never breaks a send."""
    try:
        from app.extensions import db
        from app.models.audit_log import AuditLog
        AuditLog.log('outreach_send', user_id=user_id, resource_id=resource_id, metadata={
            'path': path,
            'status': (result or {}).get('status'),
            'reason': (result or {}).get('reason'),
            'unsub_version': UNSUB_VERSION if trail.get('unsubscribe', True) else None,
            'suppression': trail.get('suppression'),
            'cap_check': trail.get('cap_check'),
            'postal_address_set': trail.get('postal_address_set'),
        })
        db.session.commit()
    except Exception:
        logger.exception('outreach_send audit failed')
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass
