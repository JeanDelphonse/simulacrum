from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class Simulation(db.Model):
    __tablename__ = 'simulations'

    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_STREAMING = 'streaming'
    STATUS_COMPLETE = 'complete'
    STATUS_ERROR = 'error'
    STATUS_REFUNDED = 'refunded'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    resume_id = db.Column(db.String(9), db.ForeignKey('resumes.id', ondelete='SET NULL'), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    focus_hint = db.Column(db.Text, nullable=True)
    expertise_zone = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    stripe_payment_intent_id = db.Column(db.String(255), nullable=True)
    stripe_charge_id = db.Column(db.String(255), nullable=True)
    amount_charged_cents = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    unlock_all_layers = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    layers = db.relationship('SimulationLayer', backref='simulation', lazy='select',
                             cascade='all, delete-orphan', order_by='SimulationLayer.layer_number')
    collaborations = db.relationship('Collaboration', backref='simulation', lazy='dynamic',
                                     cascade='all, delete-orphan')
    activities = db.relationship('CollabActivity', backref='simulation', lazy='dynamic',
                                 cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'expertise_zone': self.expertise_zone,
            'focus_hint': self.focus_hint,
            'status': self.status,
            'layers': [layer.to_dict() for layer in self.layers],
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self):
        return f'<Simulation {self.name} ({self.status})>'


class SimulationLayer(db.Model):
    __tablename__ = 'simulation_layers'

    LAYER_NAMES = {
        1: 'Active Income — 1:1 Time-for-Money',
        2: 'Leveraged Income — One-to-Many',
        3: 'Productized Income — Sell Once, Deliver Many',
        4: 'Automated Residual Systems — Running Without You',
        5: 'Wealth Deployment — Money Working for You',
    }

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    layer_number = db.Column(db.Integer, nullable=False)
    layer_name = db.Column(db.String(255), nullable=False)
    income_type = db.Column(db.String(100), nullable=True)
    ai_narrative = db.Column(db.Text, nullable=True)
    priority_score = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    income_streams = db.relationship('IncomeStream', backref='layer', lazy='select',
                                     cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'layer_number': self.layer_number,
            'layer_name': self.layer_name,
            'income_type': self.income_type,
            'ai_narrative': self.ai_narrative,
            'priority_score': self.priority_score,
            'income_streams': [s.to_dict() for s in self.income_streams],
        }

    def __repr__(self):
        return f'<SimulationLayer L{self.layer_number}: {self.layer_name}>'


class IncomeStream(db.Model):
    __tablename__ = 'income_streams'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    layer_id = db.Column(db.String(9), db.ForeignKey('simulation_layers.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    platform = db.Column(db.String(255), nullable=True)
    est_monthly_low = db.Column(db.Integer, nullable=True)   # in dollars
    est_monthly_high = db.Column(db.Integer, nullable=True)  # in dollars
    ai_reasoning = db.Column(db.Text, nullable=False)
    _deliverable_refs = db.Column('deliverable_refs', db.Text, nullable=True)  # JSON array
    automation_level = db.Column(db.String(50), nullable=True)  # low | medium | high | full
    launch_timeline_weeks = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def deliverable_refs(self):
        if self._deliverable_refs:
            return json.loads(self._deliverable_refs)
        return []

    @deliverable_refs.setter
    def deliverable_refs(self, value):
        self._deliverable_refs = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'platform': self.platform,
            'est_monthly_low': self.est_monthly_low,
            'est_monthly_high': self.est_monthly_high,
            'ai_reasoning': self.ai_reasoning,
            'deliverable_refs': self.deliverable_refs,
            'automation_level': self.automation_level,
            'launch_timeline_weeks': self.launch_timeline_weeks,
        }

    def __repr__(self):
        return f'<IncomeStream {self.name}>'
