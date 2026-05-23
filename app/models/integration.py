from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class UserIntegration(db.Model):
    __tablename__ = 'user_integrations'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id             = db.Column(db.String(9), nullable=False, index=True)
    provider            = db.Column(db.String(50), nullable=False)
    access_token_enc    = db.Column(db.Text, nullable=True)
    refresh_token_enc   = db.Column(db.Text, nullable=True)
    token_expires_at    = db.Column(db.DateTime, nullable=True)
    provider_account_id = db.Column(db.String(255), nullable=True)
    provider_scope      = db.Column(db.String(100), nullable=True)
    apollo_daily_limit  = db.Column(db.SmallInteger, nullable=False, default=30)
    meta_json           = db.Column(db.Text, nullable=True)
    warmup_started_at   = db.Column(db.DateTime, nullable=True)
    # SIM-PRD-SETTINGS-001: health monitoring + per-platform config
    config              = db.Column(db.Text, nullable=True)
    health_status       = db.Column(db.String(20), nullable=False, default='healthy')
    last_api_success_at = db.Column(db.DateTime, nullable=True)
    last_api_failure_at = db.Column(db.DateTime, nullable=True)
    consecutive_failures = db.Column(db.SmallInteger, nullable=False, default=0)
    connected_at        = db.Column(db.DateTime, nullable=True)
    disconnected_at     = db.Column(db.DateTime, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at          = db.Column(db.DateTime, default=datetime.utcnow,
                                    onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'provider', name='uq_ui_user_provider'),
    )

    @property
    def is_connected(self):
        return bool(self.access_token_enc)

    @property
    def is_expired(self):
        if not self.token_expires_at:
            return False
        return datetime.utcnow() >= self.token_expires_at

    @property
    def connection_status(self):
        if not self.is_connected:
            return 'not_connected'
        if self.is_expired:
            return 'expired'
        return 'connected'

    def get_config(self) -> dict:
        if not self.config:
            return {}
        import json as _json
        try:
            return _json.loads(self.config)
        except Exception:
            return {}

    def set_config(self, data: dict):
        import json as _json
        self.config = _json.dumps(data)

    def get_meta(self) -> dict:
        if not self.meta_json:
            return {}
        import json as _json
        try:
            return _json.loads(self.meta_json)
        except Exception:
            return {}

    def set_meta(self, data: dict):
        import json as _json
        self.meta_json = _json.dumps(data)

    def decrypt_access_token(self):
        if not self.access_token_enc:
            return None
        from app.services.token_crypto import decrypt_token
        return decrypt_token(self.access_token_enc)

    def decrypt_refresh_token(self):
        if not self.refresh_token_enc:
            return None
        from app.services.token_crypto import decrypt_token
        return decrypt_token(self.refresh_token_enc)

    def to_dict(self):
        d = {
            'provider': self.provider,
            'status': self.connection_status,
            'token_expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None,
        }
        if self.provider == 'apollo':
            d['apollo_daily_limit'] = self.apollo_daily_limit
        if self.provider == 'stripe':
            d['stripe_account_id'] = self.provider_account_id
            d['stripe_scope'] = self.provider_scope
        return d


class EmailCampaign(db.Model):
    __tablename__ = 'email_campaigns'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id       = db.Column(db.String(9), nullable=False, index=True)
    action_id           = db.Column(db.String(9), nullable=False, index=True)
    apollo_sequence_id  = db.Column(db.String(100), nullable=True)
    status              = db.Column(db.String(20), nullable=False, default='active')
    contact_count       = db.Column(db.SmallInteger, nullable=False, default=0)
    sent_count          = db.Column(db.SmallInteger, nullable=False, default=0)
    reply_count         = db.Column(db.SmallInteger, nullable=False, default=0)
    bounce_count        = db.Column(db.SmallInteger, nullable=False, default=0)
    unsubscribe_count   = db.Column(db.SmallInteger, nullable=False, default=0)
    click_count         = db.Column(db.SmallInteger, nullable=False, default=0)
    open_count          = db.Column(db.SmallInteger, nullable=False, default=0)
    daily_limit         = db.Column(db.SmallInteger, nullable=False, default=30)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'action_id': self.action_id,
            'apollo_sequence_id': self.apollo_sequence_id,
            'status': self.status,
            'contact_count': self.contact_count,
            'sent_count': self.sent_count,
            'reply_count': self.reply_count,
            'bounce_count': self.bounce_count,
            'unsubscribe_count': self.unsubscribe_count,
            'daily_limit': self.daily_limit,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class IntegrationAuditLog(db.Model):
    __tablename__ = 'integration_audit_log'

    id               = db.Column(db.String(9), primary_key=True, default=generate_id)
    admin_user_id    = db.Column(db.String(9), nullable=False, index=True)
    target_user_id   = db.Column(db.String(9), nullable=False, index=True)
    integration_type = db.Column(db.String(30), nullable=False)
    action           = db.Column(db.String(50), nullable=False)
    changes          = db.Column(db.Text, nullable=True)   # JSON {field: {from, to}}
    approved_by      = db.Column(db.String(9), nullable=True)
    ip_address       = db.Column(db.String(50), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'admin_user_id': self.admin_user_id,
            'target_user_id': self.target_user_id,
            'integration_type': self.integration_type,
            'action': self.action,
            'changes': self.changes,
            'approved_by': self.approved_by,
            'ip_address': self.ip_address,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class IntegrationActivityLog(db.Model):
    __tablename__ = 'integration_activity_log'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id    = db.Column(db.String(9), nullable=False, index=True)
    provider   = db.Column(db.String(50), nullable=False)
    event_type = db.Column(db.String(80), nullable=False)
    direction  = db.Column(db.String(10), nullable=False, default='outbound')
    status     = db.Column(db.String(20), nullable=False, default='success')
    detail     = db.Column(db.String(500), nullable=True)
    action_id  = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'event_type': self.event_type,
            'direction': self.direction,
            'status': self.status,
            'detail': self.detail,
            'action_id': self.action_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
