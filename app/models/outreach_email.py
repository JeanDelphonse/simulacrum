from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class EmailLog(db.Model):
    """Every outreach email sent by the Internal Email Engine (SIM-PRD-STEPS-001 B.6)."""
    __tablename__ = 'email_logs'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id       = db.Column(db.String(9), nullable=False, index=True)
    contact_id          = db.Column(
        db.String(9), db.ForeignKey('contacts.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    step_id             = db.Column(
        db.String(9), db.ForeignKey('action_steps.id', ondelete='SET NULL'),
        nullable=True, index=True,
    )
    action_id           = db.Column(db.String(9), nullable=True, index=True)
    subject             = db.Column(db.String(255), nullable=False)
    from_email          = db.Column(db.String(255), nullable=False)
    from_name           = db.Column(db.String(255), nullable=False)
    to_email            = db.Column(db.String(255), nullable=False)
    provider_message_id = db.Column(db.String(100), nullable=True, unique=True)
    status              = db.Column(db.String(20), nullable=False, default='sent')
    sent_at             = db.Column(db.DateTime, nullable=True)
    delivered_at        = db.Column(db.DateTime, nullable=True)
    opened_at           = db.Column(db.DateTime, nullable=True)
    open_count          = db.Column(db.Integer, nullable=False, default=0)
    clicked_at          = db.Column(db.DateTime, nullable=True)
    click_count         = db.Column(db.Integer, nullable=False, default=0)
    replied_at          = db.Column(db.DateTime, nullable=True)
    bounced_at          = db.Column(db.DateTime, nullable=True)
    bounce_reason       = db.Column(db.String(500), nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'contact_id': self.contact_id,
            'step_id': self.step_id,
            'action_id': self.action_id,
            'subject': self.subject,
            'from_email': self.from_email,
            'to_email': self.to_email,
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'delivered_at': self.delivered_at.isoformat() if self.delivered_at else None,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'open_count': self.open_count,
            'clicked_at': self.clicked_at.isoformat() if self.clicked_at else None,
            'click_count': self.click_count,
            'replied_at': self.replied_at.isoformat() if self.replied_at else None,
            'bounced_at': self.bounced_at.isoformat() if self.bounced_at else None,
            'bounce_reason': self.bounce_reason,
            'created_at': self.created_at.isoformat(),
        }


class EmailSuppression(db.Model):
    """Permanent email address suppressions — bounced or unsubscribed (SIM-PRD-STEPS-001 B.6)."""
    __tablename__ = 'email_suppressions'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    email      = db.Column(db.String(255), nullable=False, unique=True)
    reason     = db.Column(db.String(20), nullable=False)   # bounce, unsubscribe, complaint
    detail     = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @classmethod
    def is_suppressed(cls, email: str) -> bool:
        return bool(cls.query.filter_by(email=email.lower().strip()).first())
