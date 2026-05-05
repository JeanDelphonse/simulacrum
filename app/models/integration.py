from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class UserIntegration(db.Model):
    __tablename__ = 'user_integrations'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id             = db.Column(db.String(9), nullable=False, index=True)
    provider            = db.Column(db.String(50), nullable=False)   # 'apollo' | 'stripe'
    access_token_enc    = db.Column(db.Text, nullable=True)
    refresh_token_enc   = db.Column(db.Text, nullable=True)
    token_expires_at    = db.Column(db.DateTime, nullable=True)
    provider_account_id = db.Column(db.String(255), nullable=True)   # Stripe: acct_xxxx
    provider_scope      = db.Column(db.String(100), nullable=True)   # OAuth scope string
    apollo_daily_limit  = db.Column(db.SmallInteger, nullable=False, default=30)
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
