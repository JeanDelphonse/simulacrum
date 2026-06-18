from __future__ import annotations
import json
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class SimiConversation(db.Model):
    """Active Simi co-pilot conversation per simulation (SIM-PRD-CHAT-001 v1.2)."""
    __tablename__ = 'simi_conversations'

    id              = db.Column(db.String(9),  primary_key=True, default=generate_id)
    simulation_id   = db.Column(db.String(9),  nullable=False, index=True)
    user_id         = db.Column(db.String(9),  nullable=False, index=True)
    created_at      = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)
    last_message_at = db.Column(db.DateTime,   nullable=True)
    total_tokens    = db.Column(db.Integer,    nullable=False, default=0)

    messages = db.relationship('SimiMessage', backref='conversation',
                               lazy='dynamic', cascade='all, delete-orphan',
                               order_by='SimiMessage.created_at')

    def to_dict(self):
        return {
            'id':              self.id,
            'simulation_id':   self.simulation_id,
            'created_at':      self.created_at.isoformat(),
            'last_message_at': self.last_message_at.isoformat() if self.last_message_at else None,
            'total_tokens':    self.total_tokens,
        }


class SimiMessage(db.Model):
    """Individual message within a Simi conversation (SIM-PRD-CHAT-001 v1.2)."""
    __tablename__ = 'simi_messages'

    id              = db.Column(db.String(9),  primary_key=True, default=generate_id)
    conversation_id = db.Column(db.String(9),  db.ForeignKey('simi_conversations.id',
                                 ondelete='CASCADE'), nullable=False, index=True)
    role            = db.Column(db.String(10), nullable=False)   # 'user' | 'assistant'
    content         = db.Column(db.Text,       nullable=False)
    _tool_calls     = db.Column('tool_calls',  db.Text, nullable=True)
    tokens_used     = db.Column(db.Integer,    nullable=True)
    model           = db.Column(db.String(30), nullable=True)    # 'haiku' | 'sonnet'
    created_at      = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)

    @property
    def tool_calls(self):
        return json.loads(self._tool_calls) if self._tool_calls else None

    @tool_calls.setter
    def tool_calls(self, value):
        self._tool_calls = json.dumps(value) if value is not None else None

    def to_dict(self):
        return {
            'id':              self.id,
            'conversation_id': self.conversation_id,
            'role':            self.role,
            'content':         self.content,
            'tool_calls':      self.tool_calls,
            'tokens_used':     self.tokens_used,
            'model':           self.model,
            'created_at':      self.created_at.isoformat(),
        }


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
