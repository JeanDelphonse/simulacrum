from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class SimulationDiscount(db.Model):
    __tablename__ = 'simulation_discounts'

    VALID_PERCENTAGES = [10, 15, 20, 50, 100]

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    discount_percentage = db.Column(db.Integer, nullable=False)
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime, nullable=False)
    label = db.Column(db.String(30), nullable=True)
    created_by = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @classmethod
    def get_active(cls):
        """Return the currently active discount, or None."""
        now = datetime.utcnow()
        return (
            cls.query
            .filter(cls.start_at <= now, cls.end_at > now)
            .order_by(cls.created_at.desc())
            .first()
        )

    def get_status(self):
        now = datetime.utcnow()
        if now < self.start_at:
            return 'scheduled'
        if now >= self.end_at:
            return 'expired'
        return 'active'

    def purchases_during(self):
        """Count completed simulations purchased while this discount was active."""
        from app.models.simulation import Simulation
        return Simulation.query.filter(
            Simulation.discount_applied_percentage == self.discount_percentage,
            Simulation.created_at >= self.start_at,
            Simulation.created_at <= self.end_at,
            Simulation.status == Simulation.STATUS_COMPLETE,
        ).count()

    def to_dict(self):
        return {
            'id': self.id,
            'discount_percentage': self.discount_percentage,
            'start_at': self.start_at.isoformat(),
            'end_at': self.end_at.isoformat(),
            'label': self.label,
            'status': self.get_status(),
            'simulations_purchased': self.purchases_during(),
            'created_at': self.created_at.isoformat(),
        }
