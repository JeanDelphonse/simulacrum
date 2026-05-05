from __future__ import annotations
from datetime import date, datetime
from app.extensions import db
from utils.id_gen import generate_id


class LayerIncomeRecord(db.Model):
    """Immutable record of income attributed to a specific layer/action. Corrections create new records."""
    __tablename__ = 'layer_income_records'

    SOURCE_MANUAL = 'manual_entry'
    SOURCE_STRIPE = 'stripe'

    id            = db.Column(db.String(9),   primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9),   nullable=False, index=True)
    layer_number  = db.Column(db.Integer,     nullable=False)
    action_id     = db.Column(db.String(9),   nullable=True)
    action_type   = db.Column(db.String(100), nullable=True)
    amount        = db.Column(db.Numeric(12, 2), nullable=False)
    currency      = db.Column(db.String(3),   nullable=False, default='USD')
    income_date   = db.Column(db.Date,        nullable=False, default=date.today)
    source        = db.Column(db.String(50),  nullable=False, default=SOURCE_MANUAL)
    source_ref    = db.Column(db.String(255), nullable=True)
    description   = db.Column(db.Text,        nullable=True)
    is_void       = db.Column(db.Boolean,     nullable=False, default=False)
    voided_by_id  = db.Column(db.String(9),   nullable=True)
    recorded_by   = db.Column(db.String(9),   nullable=False)
    created_at    = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'id':            self.id,
            'simulation_id': self.simulation_id,
            'layer_number':  self.layer_number,
            'action_id':     self.action_id,
            'action_type':   self.action_type,
            'amount':        float(self.amount),
            'currency':      self.currency,
            'income_date':   self.income_date.isoformat() if self.income_date else None,
            'source':        self.source,
            'source_ref':    self.source_ref,
            'description':   self.description,
            'is_void':       self.is_void,
            'recorded_by':   self.recorded_by,
            'created_at':    self.created_at.isoformat(),
        }
