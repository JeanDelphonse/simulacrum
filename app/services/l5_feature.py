"""The global Layer 5 switch (COMPLIANCE_PLAN B5).

Same pattern as the Email-to-CRM switch (mail_feature): a feature_flags row,
key 'layer5_agents', mode 'on' | 'off'. On by default. Fails closed: if the
switch can't be read, Layer 5 is off. When off, no Layer 5 agent is run or
dispatched; existing Layer 5 documents stay viewable.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

FEATURE_KEY = 'layer5_agents'
MODES = ('on', 'off')


def state() -> dict:
    try:
        from app.models.mail_crm import FeatureFlag
        row = FeatureFlag.query.filter_by(feature_key=FEATURE_KEY).first()
    except Exception:
        logger.warning('Layer 5 switch unreadable; treating it as off', exc_info=True)
        return {'mode': 'off', 'reason': 'switch unreadable', 'updated_at': None}
    if not row:
        return {'mode': 'on', 'reason': None, 'updated_at': None}
    return {'mode': row.mode if row.mode in MODES else 'off', 'reason': row.reason, 'updated_at': row.updated_at}


def enabled() -> bool:
    return state()['mode'] == 'on'


def blocks(layer_number) -> bool:
    """True when this layer's agents may not run right now."""
    try:
        return int(layer_number or 0) == 5 and not enabled()
    except (TypeError, ValueError):
        return False


def set_mode(mode: str, admin_id: str, reason: str = '') -> None:
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mail_crm import FeatureFlag
    if mode not in MODES:
        raise ValueError('Mode must be on or off.')
    row = FeatureFlag.query.filter_by(feature_key=FEATURE_KEY).first()
    if not row:
        row = FeatureFlag(feature_key=FEATURE_KEY, mode=mode)
        db.session.add(row)
    row.mode, row.reason = mode, (reason or '').strip()[:300] or None
    row.updated_by, row.updated_at = admin_id, datetime.utcnow()
    AuditLog.log('layer5_switch_' + mode, user_id=admin_id, metadata={'reason': row.reason})
    db.session.commit()


def refusal():
    """The JSON response an agent-run endpoint returns while Layer 5 is off."""
    from flask import jsonify
    from app.services import legal_text
    return jsonify({'error': 'layer5_unavailable', 'message': legal_text.get('l5_unavailable')}), 403
