from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_pk


class ContactInvite(db.Model):
    """A member's invitation to one of their contacts to try SimiSI.

    One row per member per address. ``status`` is 'invited' once the email has
    gone, and 'joined' when that person creates an account (through the invite
    link or with the same email address).
    """
    __tablename__ = 'contact_invites'

    STATUS_INVITED = 'invited'
    STATUS_JOINED = 'joined'

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    user_id = db.Column(db.String(9), nullable=False, index=True)          # the member who invited
    contact_id = db.Column(db.String(9), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)          # lower-cased at send time
    token = db.Column(db.String(64), nullable=False, unique=True)
    status = db.Column(db.String(10), nullable=False, default=STATUS_INVITED)
    provider_message_id = db.Column(db.String(200), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    joined_at = db.Column(db.DateTime, nullable=True)
    joined_user_id = db.Column(db.String(9), nullable=True)                 # the account they created

    __table_args__ = (db.UniqueConstraint('user_id', 'email', name='uq_contact_invite_user_email'),)

    def to_dict(self):
        return {'status': self.status,
                'sent_at': self.sent_at.isoformat() if self.sent_at else None,
                'joined_at': self.joined_at.isoformat() if self.joined_at else None}
