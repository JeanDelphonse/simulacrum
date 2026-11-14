"""Founder Track switches (FOUNDER_PLAN.md section 2).

The gate copies guide_service.mode()/is_enabled(): a FeatureFlag row holds the
global mode (off | allowlist | on) and UserFeatureOverride rows allow or block
single members. Admins pass unless the mode is off. Every read fails closed.

Keys:
  founder_track     the whole track. Default off: every founder route 404s.
  founder_pitch     the spoken pitch. Default on (matters only when the track is on).
  founder_investor  Phase 3. Default off. Refuses to leave off until an admin has
                    recorded the counsel sign-off date (fnd_counsel_signoff_at).
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

TRACK = 'founder_track'
PITCH = 'founder_pitch'
INVESTOR = 'founder_investor'

DEFAULT_MODES = {TRACK: 'off', PITCH: 'on', INVESTOR: 'off'}
MODES = ('off', 'allowlist', 'on')
STATES = ('follow_global', 'allowed', 'blocked')


class FounderError(Exception):
    pass


def _check_key(key: str) -> None:
    if key not in DEFAULT_MODES:
        raise FounderError('Unknown Founder Track switch: %s' % key)


def mode(key: str = TRACK) -> str:
    """off | allowlist | on. Fails closed (off) when the flag can't be read."""
    _check_key(key)
    try:
        from app.models.mail_crm import FeatureFlag
        row = FeatureFlag.query.filter_by(feature_key=key).first()
        return row.mode if row and row.mode in MODES else DEFAULT_MODES[key]
    except Exception as exc:
        logger.error('founder gate %s unreadable, treating as off: %s', key, exc)
        return 'off'


def is_enabled(user, key: str = TRACK) -> bool:
    """True when this user may use the feature. Sub-switches (pitch, investor)
    also need the track itself to be enabled for the user."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    try:
        if key != TRACK and not is_enabled(user, TRACK):
            return False
        m = mode(key)
        if m == 'off':
            return False
        if key == INVESTOR:
            from app.services import founder_config
            if not founder_config.counsel_signed_off():
                return False
        if getattr(user, 'is_admin', False):
            return True
        from app.models.mail_crm import UserFeatureOverride
        row = UserFeatureOverride.query.filter_by(user_id=user.id, feature_key=key).first()
        state = row.state if row else 'follow_global'
        if state == 'blocked':
            return False
        return m == 'on' or state == 'allowed'
    except Exception as exc:
        logger.error('founder gate %s failed closed for %s: %s', key, getattr(user, 'id', '?'), exc)
        return False


def public_pages_enabled() -> bool:
    """Public /startup/<slug> pages render only while the track is not off."""
    return mode(TRACK) != 'off'


def set_mode(key: str, mode: str = None, admin_id: str = None, *, new_mode: str = None) -> None:
    """set_mode(key, mode, admin_id) per FOUNDER_PLAN section 5; new_mode= is
    accepted as an alias."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mail_crm import FeatureFlag
    _check_key(key)
    new_mode = mode if mode is not None else new_mode
    if new_mode not in MODES:
        raise FounderError('Unknown mode.')
    if key == INVESTOR and new_mode != 'off':
        from app.services import founder_config
        if not founder_config.counsel_signed_off():
            raise FounderError('Investor connection stays off until the counsel sign-off date is recorded.')
    row = FeatureFlag.query.filter_by(feature_key=key).first()
    if row is None:
        row = FeatureFlag(feature_key=key, mode=new_mode)
        db.session.add(row)
    row.mode, row.updated_by, row.updated_at = new_mode, admin_id, datetime.utcnow()
    AuditLog.log('founder_mode', user_id=admin_id, metadata={'key': key, 'mode': new_mode})
    db.session.commit()


def set_user_access(user_ids: list, state: str, admin_id: str, key: str = TRACK) -> int:
    from app.extensions import db
    from app.models.mail_crm import UserFeatureOverride
    _check_key(key)
    if state not in STATES:
        raise FounderError('Unknown state.')
    n = 0
    for uid in dict.fromkeys(user_ids or []):
        row = UserFeatureOverride.query.filter_by(user_id=uid, feature_key=key).first()
        if row is None:
            row = UserFeatureOverride(user_id=uid, feature_key=key)
            db.session.add(row)
        row.state, row.set_by, row.set_at, row.reason = state, admin_id, datetime.utcnow(), 'Founder Track'
        n += 1
    db.session.commit()
    return n
