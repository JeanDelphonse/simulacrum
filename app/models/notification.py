from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id

NOTIFICATION_TYPES = {
    'escalation', 'agent_complete', 'income', 'reply',
    'cycle_summary', 'retention_warning', 'milestone', 'chat_action',
}

DIGEST_ELIGIBLE = {'agent_complete', 'cycle_summary'}

EMAIL_DEFAULTS = {
    'escalation':        {'email_enabled': True,  'digest_mode': False},
    'agent_complete':    {'email_enabled': True,  'digest_mode': True},
    'income':            {'email_enabled': True,  'digest_mode': False},
    'reply':             {'email_enabled': True,  'digest_mode': False},
    'cycle_summary':     {'email_enabled': True,  'digest_mode': True},
    'retention_warning': {'email_enabled': True,  'digest_mode': False},
    'milestone':         {'email_enabled': True,  'digest_mode': False},
    'chat_action':       {'email_enabled': True,  'digest_mode': False},
}


class Notification(db.Model):
    __tablename__ = 'notifications'

    id                = db.Column(db.String(9),   primary_key=True, default=generate_id)
    user_id           = db.Column(db.String(9),   nullable=False, index=True)  # no FK — collation mismatch
    simulation_id     = db.Column(db.String(9),   nullable=True)
    notification_type = db.Column(db.String(50),  nullable=False)
    title             = db.Column(db.String(200),  nullable=False)
    body              = db.Column(db.Text,         nullable=False)
    cta_url           = db.Column(db.String(500),  nullable=True)
    cta_label         = db.Column(db.String(100),  nullable=True)
    priority          = db.Column(db.String(10),   nullable=False, default='normal')
    email_sent        = db.Column(db.Boolean,      nullable=False, default=False)
    email_sent_at     = db.Column(db.DateTime,     nullable=True)
    read_at           = db.Column(db.DateTime,     nullable=True)
    created_at        = db.Column(db.DateTime,     nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':                self.id,
            'user_id':           self.user_id,
            'simulation_id':     self.simulation_id,
            'notification_type': self.notification_type,
            'title':             self.title,
            'body':              self.body,
            'cta_url':           self.cta_url,
            'cta_label':         self.cta_label,
            'priority':          self.priority,
            'email_sent':        self.email_sent,
            'read_at':           self.read_at.isoformat() if self.read_at else None,
            'created_at':        self.created_at.isoformat() if self.created_at else None,
        }


class NotificationPreference(db.Model):
    __tablename__ = 'notification_preferences'

    id                = db.Column(db.String(9),  primary_key=True, default=generate_id)
    user_id           = db.Column(db.String(9),  nullable=False)  # no FK — collation mismatch
    notification_type = db.Column(db.String(50), nullable=False)
    email_enabled     = db.Column(db.Boolean,    nullable=False, default=True)
    digest_mode       = db.Column(db.Boolean,    nullable=False, default=False)
    updated_at        = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow,
                                  onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'notification_type', name='uk_np_user_type'),
    )

    @classmethod
    def get_for(cls, user_id: str, notification_type: str) -> 'NotificationPreference':
        pref = cls.query.filter_by(user_id=user_id, notification_type=notification_type).first()
        if pref:
            return pref
        defaults = EMAIL_DEFAULTS.get(notification_type, {'email_enabled': True, 'digest_mode': False})
        return cls(
            user_id=user_id,
            notification_type=notification_type,
            email_enabled=defaults['email_enabled'],
            digest_mode=defaults['digest_mode'],
        )
