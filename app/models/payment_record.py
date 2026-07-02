from datetime import datetime
from app.extensions import db


class PaymentRecord(db.Model):
    __tablename__ = 'payment_records'

    TYPE_SIMULATION   = 'simulation'
    TYPE_VOICE        = 'voice'
    TYPE_TIER_UPGRADE = 'tier_upgrade'
    TYPE_ENTERPRISE   = 'enterprise'

    STATUS_SUCCEEDED = 'succeeded'
    STATUS_REFUNDED  = 'refunded'
    STATUS_FAILED    = 'failed'

    id                = db.Column(db.BigInteger,  primary_key=True, autoincrement=True)
    user_id           = db.Column(db.String(9),   nullable=False, index=True)
    simulation_id     = db.Column(db.String(9),   nullable=True)
    stripe_payment_id = db.Column(db.String(100), nullable=False)
    amount_cents      = db.Column(db.Integer,     nullable=False)
    currency          = db.Column(db.String(3),   nullable=False, default='usd')
    payment_type      = db.Column(db.String(20),  nullable=False, index=True)
    discount_code     = db.Column(db.String(50),  nullable=True)
    discount_pct      = db.Column(db.Integer,     nullable=True)
    status            = db.Column(db.String(20),  nullable=False, default='succeeded')
    created_at        = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow, index=True)
