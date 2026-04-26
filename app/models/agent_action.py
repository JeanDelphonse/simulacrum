from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class AgentAction(db.Model):
    __tablename__ = 'agent_actions'

    STATUS_PENDING = 'pending'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETE = 'complete'
    STATUS_FAILED = 'failed'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    layer_number = db.Column(db.Integer, nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    _user_inputs = db.Column('user_inputs', db.Text, nullable=True)       # JSON dict
    artifact = db.Column(db.Text, nullable=True)                           # Generated content
    _archived_artifact = db.Column('archived_artifact', db.Text, nullable=True)  # Prior run content
    archived_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    error_message = db.Column(db.Text, nullable=True)
    created_by = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    @property
    def user_inputs(self):
        return json.loads(self._user_inputs) if self._user_inputs else {}

    @user_inputs.setter
    def user_inputs(self, value):
        self._user_inputs = json.dumps(value) if value else None

    @property
    def archived_artifact(self):
        return self._archived_artifact

    @archived_artifact.setter
    def archived_artifact(self, value):
        self._archived_artifact = value

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'layer_number': self.layer_number,
            'action_type': self.action_type,
            'user_inputs': self.user_inputs,
            'artifact': self.artifact,
            'has_archived_artifact': self._archived_artifact is not None,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }

    def __repr__(self):
        return f'<AgentAction {self.action_type} L{self.layer_number} ({self.status})>'
