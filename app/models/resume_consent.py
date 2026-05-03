from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class ResumeConsent(db.Model):
    __tablename__ = 'resume_consents'

    id              = db.Column(db.String(9),   primary_key=True, default=generate_id)
    user_id         = db.Column(db.String(9),   nullable=False, index=True)  # no FK: charset mismatch; restrict enforced in app layer
    tos_version     = db.Column(db.String(20),  nullable=False)
    privacy_version = db.Column(db.String(20),  nullable=False)
    checkbox_1      = db.Column(db.Boolean,     nullable=False, default=False)
    checkbox_2      = db.Column(db.Boolean,     nullable=False, default=False)
    ip_address      = db.Column(db.String(45),  nullable=True)
    user_agent      = db.Column(db.String(500), nullable=True)
    consent_method  = db.Column(db.String(50),  nullable=False, default='modal_v1')
    withdrawn_at    = db.Column(db.DateTime,    nullable=True)
    created_at      = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':              self.id,
            'tos_version':     self.tos_version,
            'privacy_version': self.privacy_version,
            'created_at':      self.created_at.isoformat(),
            'withdrawn_at':    self.withdrawn_at.isoformat() if self.withdrawn_at else None,
        }

    def __repr__(self):
        return f'<ResumeConsent {self.id} user={self.user_id} tos={self.tos_version}>'
