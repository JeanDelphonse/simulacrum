from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class ActionStep(db.Model):
    """Sub-action within an agent artifact that executes on a future cycle (SIM-PRD-STEPS-001 A.2)."""
    __tablename__ = 'action_steps'

    STATUS_SCHEDULED = 'scheduled'
    STATUS_EXECUTED  = 'executed'
    STATUS_SKIPPED   = 'skipped'
    STATUS_CANCELLED = 'cancelled'

    # Condition types (A.4)
    CONDITION_NO_REPLY    = 'no_reply'
    CONDITION_NO_SIGNATURE = 'no_signature'
    CONDITION_NO_PURCHASE = 'no_purchase'
    CONDITION_NO_BOOKING  = 'no_booking'

    id               = db.Column(db.String(9), primary_key=True, default=generate_id)
    # agent_action_id is the primary reference (works for both orchestrator and user-run actions)
    agent_action_id  = db.Column(
        db.String(9), db.ForeignKey('agent_actions.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    # parent_action_id is set only when dispatched via the orchestrator queue
    parent_action_id = db.Column(
        db.String(9), db.ForeignKey('layer6_action_queue.id', ondelete='SET NULL'),
        nullable=True, index=True,
    )
    simulation_id    = db.Column(db.String(9), nullable=False, index=True)
    step_number      = db.Column(db.Integer, nullable=False)
    total_steps      = db.Column(db.Integer, nullable=False)
    action_type      = db.Column(db.String(50), nullable=False)
    step_type        = db.Column(db.String(50), nullable=False)
    subject          = db.Column(db.String(255), nullable=True)
    _payload         = db.Column('payload', db.Text, nullable=False, default='{}')
    scheduled_for    = db.Column(db.DateTime, nullable=False, index=True)
    condition_type   = db.Column(db.String(30), nullable=True)
    condition_ref    = db.Column(db.String(9), nullable=True)  # contact_id or document_id
    status           = db.Column(db.String(20), nullable=False, default=STATUS_SCHEDULED, index=True)
    executed_at      = db.Column(db.DateTime, nullable=True)
    skipped_at       = db.Column(db.DateTime, nullable=True)
    skip_reason      = db.Column(db.String(200), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('agent_action_id', 'step_number', name='uq_step_action_num'),
    )

    @property
    def payload(self):
        return json.loads(self._payload) if self._payload else {}

    @payload.setter
    def payload(self, value):
        self._payload = json.dumps(value) if value is not None else '{}'

    def to_dict(self):
        return {
            'id': self.id,
            'agent_action_id': self.agent_action_id,
            'parent_action_id': self.parent_action_id,
            'simulation_id': self.simulation_id,
            'step_number': self.step_number,
            'total_steps': self.total_steps,
            'action_type': self.action_type,
            'step_type': self.step_type,
            'subject': self.subject,
            'scheduled_for': self.scheduled_for.isoformat(),
            'condition_type': self.condition_type,
            'condition_ref': self.condition_ref,
            'status': self.status,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'skipped_at': self.skipped_at.isoformat() if self.skipped_at else None,
            'skip_reason': self.skip_reason,
            'created_at': self.created_at.isoformat(),
        }
