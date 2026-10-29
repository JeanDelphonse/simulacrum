"""Plans, tools, autonomy and approvals (EXEC_PLAN P3 — L1: nothing executes)."""
from datetime import datetime
import json

from app.extensions import db
from utils.id_gen import generate_id


class ToolRegistry(db.Model):
    """A tool a plan step can name. Seeded from code (agent_plans.TOOL_SEED);
    admins may change ``enabled`` and ``cost_cents``."""
    __tablename__ = 'tool_registry'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    tool_key = db.Column(db.String(60), nullable=False, unique=True)
    integration = db.Column(db.String(40), nullable=True)
    side_effect_class = db.Column(db.String(20), nullable=False, default='none')
    blast_radius = db.Column(db.Integer, nullable=False, default=0)   # 0 none · 1 one person · 2 a list · 3 public
    cost_cents = db.Column(db.Integer, nullable=False, default=0)
    reversible = db.Column(db.Boolean, nullable=False, default=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)


class AgentPlan(db.Model):
    __tablename__ = 'agent_plans'
    __table_args__ = (db.Index('ix_agent_plans_action', 'action_id', 'status'),)

    STATUS_PROPOSED = 'proposed'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_SUPERSEDED = 'superseded'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    agent_key = db.Column(db.String(50), nullable=False)
    simulation_id = db.Column(db.String(9), nullable=False)
    action_id = db.Column(db.String(9), nullable=True)          # the artifact the plan acts on
    user_id = db.Column(db.String(9), nullable=True)
    goal = db.Column(db.Text, nullable=True)
    method = db.Column(db.String(60), nullable=True)
    autonomy_level = db.Column(db.String(4), nullable=False, default='L1')
    status = db.Column(db.String(20), nullable=False, default=STATUS_PROPOSED)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PlanStep(db.Model):
    __tablename__ = 'plan_steps'
    __table_args__ = (db.Index('ix_plan_steps_plan', 'plan_id', 'seq_index'),)

    APPROVAL_NOT_REQUIRED = 'not_required'
    APPROVAL_PENDING = 'pending'
    APPROVAL_APPROVED = 'approved'
    APPROVAL_REJECTED = 'rejected'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    plan_id = db.Column(db.String(9), nullable=False)
    seq_index = db.Column(db.Integer, nullable=False, default=0)
    tool_key = db.Column(db.String(60), nullable=True)            # None = a manual / review step
    method = db.Column(db.String(60), nullable=False)
    _inputs = db.Column('inputs_json', db.Text, nullable=True)
    expected = db.Column(db.String(300), nullable=True)
    reversibility = db.Column(db.String(20), nullable=False, default='reversible')
    blast = db.Column(db.Integer, nullable=False, default=0)
    est_cost_cents = db.Column(db.Integer, nullable=False, default=0)
    required_reason = db.Column(db.String(20), nullable=True)     # hard_gate | irreversible | blast
    approval_status = db.Column(db.String(20), nullable=False, default=APPROVAL_NOT_REQUIRED)

    @property
    def inputs(self) -> dict:
        return json.loads(self._inputs) if self._inputs else {}

    @inputs.setter
    def inputs(self, value):
        self._inputs = json.dumps(value) if value else None


class AgentAutonomyConfig(db.Model):
    """A user's autonomy level for one agent (L0–L3; L4 is never allowed)."""
    __tablename__ = 'agent_autonomy_config'
    __table_args__ = (db.UniqueConstraint('user_id', 'agent_key', name='uq_autonomy_user_agent'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    agent_key = db.Column(db.String(50), nullable=False)
    level = db.Column(db.String(4), nullable=False)
    _overrides = db.Column('overrides_json', db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ActionApproval(db.Model):
    """A human decision on something that needed approval (a plan step in P3)."""
    __tablename__ = 'action_approvals'
    __table_args__ = (db.Index('ix_action_approvals_target', 'target_type', 'target_id'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    target_type = db.Column(db.String(20), nullable=False)        # plan_step
    target_id = db.Column(db.String(9), nullable=False)
    required_reason = db.Column(db.String(20), nullable=True)
    decided_by = db.Column(db.String(9), nullable=True)
    decision = db.Column(db.String(10), nullable=False)           # approve | reject
    ts = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
