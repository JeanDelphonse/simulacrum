from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class AgentRecipient(db.Model):
    """One person an agent has targeted in a simulation — the uniqueness ledger.

    A recipient is recorded when the agent's artifact names them, not when an
    email goes out: sends can be held for channel approval, and a contact who was
    drafted but not yet sent must not be drafted again by the next cycle.

    recipient_key is the lower-cased email, or ``name:<first last>|<company>``
    for contacts the agent produced without an address.
    """
    __tablename__ = 'agent_recipients'
    __table_args__ = (
        db.UniqueConstraint('simulation_id', 'action_type', 'recipient_key',
                            name='uq_agent_recipient'),
        db.Index('ix_agent_recipient_sim_agent', 'simulation_id', 'action_type'),
    )

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)       # canonical agent name
    recipient_key = db.Column(db.String(255), nullable=False)
    contact_id = db.Column(db.String(9), nullable=True)
    action_id = db.Column(db.String(9), nullable=True)           # the run that claimed them
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<AgentRecipient {self.action_type} {self.recipient_key}>'
