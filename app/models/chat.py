from __future__ import annotations
import json
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class SimulationChatMessage(db.Model):
    __tablename__ = 'simulation_chat_messages'

    ROLE_USER      = 'user'
    ROLE_ASSISTANT = 'assistant'

    ACTION_PENDING   = 'pending'
    ACTION_CONFIRMED = 'confirmed'
    ACTION_CANCELLED = 'cancelled'
    ACTION_EXECUTED  = 'executed'

    id            = db.Column(db.String(9),  primary_key=True, default=generate_id)
    session_id    = db.Column(db.String(9),  nullable=True,  index=True)
    simulation_id = db.Column(db.String(9),  nullable=False, index=True)
    user_id       = db.Column(db.String(9),  nullable=False, index=True)
    role          = db.Column(db.String(20), nullable=False)
    content       = db.Column(db.Text,       nullable=False)
    intent        = db.Column(db.String(50), nullable=True)
    action_type   = db.Column(db.String(100), nullable=True)
    _action_params  = db.Column('action_params', db.Text, nullable=True)
    action_status = db.Column(db.String(20), nullable=True)
    _action_result  = db.Column('action_result', db.Text, nullable=True)
    model_used    = db.Column(db.String(50), nullable=True)
    tokens_input  = db.Column(db.Integer,   nullable=True)
    tokens_output = db.Column(db.Integer,   nullable=True)
    is_archived   = db.Column(db.Boolean,   nullable=False, default=False)
    created_at    = db.Column(db.DateTime,  nullable=False, default=datetime.utcnow)

    @property
    def action_params(self):
        return json.loads(self._action_params) if self._action_params else None

    @action_params.setter
    def action_params(self, value):
        self._action_params = json.dumps(value) if value is not None else None

    @property
    def action_result(self):
        return json.loads(self._action_result) if self._action_result else None

    @action_result.setter
    def action_result(self, value):
        self._action_result = json.dumps(value) if value is not None else None

    def to_dict(self) -> dict:
        return {
            'id':            self.id,
            'session_id':    self.session_id,
            'simulation_id': self.simulation_id,
            'role':          self.role,
            'content':       self.content,
            'intent':        self.intent,
            'action_type':   self.action_type,
            'action_params': self.action_params,
            'action_status': self.action_status,
            'action_result': self.action_result,
            'model_used':    self.model_used,
            'tokens_input':  self.tokens_input,
            'tokens_output': self.tokens_output,
            'created_at':    self.created_at.isoformat(),
        }
