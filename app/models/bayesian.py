from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class BayesianPosterior(db.Model):
    """Per-simulation Bayesian posterior store for the orchestrator's EMA update loop."""
    __tablename__ = 'bayesian_posteriors'

    id              = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id   = db.Column(db.String(9), nullable=False, index=True)
    posterior_key   = db.Column(db.String(200), nullable=False)
    # e.g. 'reply_rate:cold_email_campaign', 'booking_rate:discovery_call'
    value           = db.Column(db.Numeric(10, 6), nullable=False, default=0.5)
    last_direction  = db.Column(db.String(1), nullable=True)   # '+' or '-'
    last_weight     = db.Column(db.Numeric(4, 3), nullable=True)
    update_count    = db.Column(db.Integer, nullable=False, default=0)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow,
                                onupdate=datetime.utcnow, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('simulation_id', 'posterior_key', name='uq_bp_sim_key'),
    )

    def to_dict(self):
        return {
            'posterior_key': self.posterior_key,
            'value': float(self.value),
            'last_direction': self.last_direction,
            'update_count': self.update_count,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
