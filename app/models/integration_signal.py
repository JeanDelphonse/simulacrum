from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class IntegrationSignal(db.Model):
    """Raw webhook/integration events captured for proactive alert processing (ENH-03)."""
    __tablename__ = 'integration_signals'

    SIGNAL_PROPOSAL_VIEWED = 'proposal_viewed'
    SIGNAL_BOOKING_CREATED = 'booking_created'
    SIGNAL_BOOKING_CANCELLED = 'booking_cancelled'
    SIGNAL_PAYMENT_RECEIVED = 'payment_received'
    SIGNAL_EMAIL_REPLY = 'email_reply'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), nullable=False, index=True)
    user_id = db.Column(db.String(9), nullable=False, index=True)
    signal_type = db.Column(db.String(50), nullable=False)
    payload = db.Column(db.Text, nullable=True)
    alert_created = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'user_id': self.user_id,
            'signal_type': self.signal_type,
            'alert_created': self.alert_created,
            'created_at': self.created_at.isoformat(),
        }
