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


class UserDiscount(db.Model):
    """A discount granted to one named user from the admin console.

    `SimulationDiscount` above is a global sale: a time window everyone shares.
    This is the other axis — a comp for one person, which nobody else gets and
    which the marketing copy never mentions.

    It is a voucher rather than a status. `max_uses` bounds it and `uses_count`
    records what has been spent, because "this user pays nothing, forever" is a
    standing commitment to unbounded generation spend and should never be the
    thing that happens when an admin picks the default. Unlimited exists
    (`max_uses = 0`) but has to be chosen.
    """
    __tablename__ = 'user_discounts'

    UNLIMITED = 0

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id             = db.Column(db.String(9), nullable=False, index=True)
    discount_percentage = db.Column(db.Integer, nullable=False)
    max_uses            = db.Column(db.Integer, nullable=False, default=1)
    uses_count          = db.Column(db.Integer, nullable=False, default=0)
    reason              = db.Column(db.String(200), nullable=True)
    expires_at          = db.Column(db.DateTime, nullable=True)
    granted_by          = db.Column(db.String(9), nullable=True)
    revoked_at          = db.Column(db.DateTime, nullable=True)
    revoked_by          = db.Column(db.String(9), nullable=True)
    created_at          = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_ud_user_live', 'user_id', 'revoked_at', 'expires_at'),
        db.Index('idx_ud_created', 'created_at'),
    )

    @property
    def is_unlimited(self) -> bool:
        return (self.max_uses or 0) == self.UNLIMITED

    @property
    def uses_remaining(self):
        """Uses left, or None when the grant is unlimited."""
        if self.is_unlimited:
            return None
        return max(0, (self.max_uses or 0) - (self.uses_count or 0))

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.utcnow()

    @property
    def is_exhausted(self) -> bool:
        return not self.is_unlimited and (self.uses_count or 0) >= (self.max_uses or 0)

    @property
    def is_live(self) -> bool:
        """Would this grant apply to a purchase right now?"""
        return (self.revoked_at is None
                and not self.is_expired
                and not self.is_exhausted)

    def status(self) -> str:
        if self.revoked_at is not None:
            return 'revoked'
        if self.is_expired:
            return 'expired'
        if self.is_exhausted:
            return 'used'
        return 'active'

    @classmethod
    def live_for_user(cls, user_id: str):
        """The best live grant for this user, or None.

        "Best" is the highest percentage, so a user holding both a 50% and a
        100% comp gets the 100%. Ties break toward the older grant, which is the
        one closest to expiring in practice.
        """
        if not user_id:
            return None
        now = datetime.utcnow()
        rows = cls.query.filter(
            cls.user_id == user_id,
            cls.revoked_at.is_(None),
        ).filter(
            db.or_(cls.expires_at.is_(None), cls.expires_at > now)
        ).order_by(cls.discount_percentage.desc(), cls.created_at.asc()).all()
        for row in rows:
            if not row.is_exhausted:
                return row
        return None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'discount_percentage': self.discount_percentage,
            'max_uses': self.max_uses,
            'uses_count': self.uses_count or 0,
            'uses_remaining': self.uses_remaining,
            'is_unlimited': self.is_unlimited,
            'reason': self.reason,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'granted_by': self.granted_by,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'status': self.status(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UserDiscountRedemption(db.Model):
    """One simulation a grant paid for.

    Kept separate from the grant so the history survives a revoke, and so the
    console can answer what a comp actually cost without inferring it from the
    simulation table after the base price has moved.
    """
    __tablename__ = 'user_discount_redemptions'

    id                = db.Column(db.String(9), primary_key=True, default=generate_id)
    discount_id       = db.Column(db.String(9), nullable=False, index=True)
    user_id           = db.Column(db.String(9), nullable=False, index=True)
    simulation_id     = db.Column(db.String(9), nullable=True, index=True)
    base_price_cents  = db.Column(db.Integer, nullable=False, default=0)
    charged_cents     = db.Column(db.Integer, nullable=False, default=0)
    created_at        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def comped_cents(self) -> int:
        return max(0, (self.base_price_cents or 0) - (self.charged_cents or 0))

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'discount_id': self.discount_id,
            'user_id': self.user_id,
            'simulation_id': self.simulation_id,
            'base_price_cents': self.base_price_cents or 0,
            'charged_cents': self.charged_cents or 0,
            'comped_cents': self.comped_cents,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
