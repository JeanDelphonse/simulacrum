from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class Layer6Config(db.Model):
    """Autonomy boundary configuration for the Layer 6 orchestrator, one per Simulation."""
    __tablename__ = 'layer6_configs'

    CADENCE_DAILY = 'daily'
    CADENCE_THREE_DAYS = 'every_3_days'
    CADENCE_WEEKLY = 'weekly'
    CADENCE_12H = 'every_12h'
    CADENCE_48H = 'every_48h'
    CADENCE_72H = 'every_72h'
    CADENCE_168H = 'every_168h'

    CONTACT_SCOPE_UPLOADED = 'uploaded_only'
    CONTACT_SCOPE_LINKEDIN = 'linkedin_connections'
    CONTACT_SCOPE_ANY = 'any_researched'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, unique=True, index=True,
    )
    _channel_approvals = db.Column('channel_approvals', db.Text, nullable=True)
    spend_ceiling = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    contact_scope = db.Column(db.String(30), nullable=False, default=CONTACT_SCOPE_UPLOADED)
    _blocked_actions = db.Column('blocked_actions', db.Text, nullable=True)
    cadence = db.Column(db.String(20), nullable=False, default=CADENCE_DAILY)
    actions_per_cycle = db.Column(db.Integer, nullable=False, default=3)
    _quiet_hours = db.Column('quiet_hours', db.Text, nullable=True)
    explore_phase_end_month = db.Column(db.Integer, nullable=False, default=3)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    trust_level = db.Column(db.String(20), nullable=False, default='balanced')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    cycles = db.relationship('Layer6Cycle', backref='config', lazy='dynamic',
                             foreign_keys='Layer6Cycle.simulation_id',
                             primaryjoin='Layer6Config.simulation_id == Layer6Cycle.simulation_id')

    @property
    def channel_approvals(self):
        defaults = {
            'email': True,
            'email_funnels': True,
            'linkedin': False,
            'calendar': False,
            'content_publishing': False,
        }
        if self._channel_approvals:
            defaults.update(json.loads(self._channel_approvals))
        return defaults

    @channel_approvals.setter
    def channel_approvals(self, value):
        self._channel_approvals = json.dumps(value) if value else None

    @property
    def blocked_actions(self):
        return json.loads(self._blocked_actions) if self._blocked_actions else []

    @blocked_actions.setter
    def blocked_actions(self, value):
        self._blocked_actions = json.dumps(value) if value else None

    @property
    def quiet_hours(self):
        return json.loads(self._quiet_hours) if self._quiet_hours else {'start': '22:00', 'end': '07:00'}

    @quiet_hours.setter
    def quiet_hours(self, value):
        self._quiet_hours = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'channel_approvals': self.channel_approvals,
            'spend_ceiling': float(self.spend_ceiling),
            'contact_scope': self.contact_scope,
            'blocked_actions': self.blocked_actions,
            'cadence': self.cadence,
            'actions_per_cycle': self.actions_per_cycle,
            'quiet_hours': self.quiet_hours,
            'explore_phase_end_month': self.explore_phase_end_month,
            'is_active': self.is_active,
            'trust_level': self.trust_level,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class Layer6Cycle(db.Model):
    """One orchestrator execution cycle for a Simulation."""
    __tablename__ = 'layer6_cycles'

    PHASE_EXPLORE = 'explore'
    PHASE_TRANSITION = 'transition'
    PHASE_EXPLOIT = 'exploit'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    cycle_number = db.Column(db.Integer, nullable=False, default=1)
    phase = db.Column(db.String(10), nullable=False, default=PHASE_EXPLORE)
    actions_scored = db.Column(db.Integer, nullable=False, default=0)
    actions_dispatched = db.Column(db.Integer, nullable=False, default=0)
    actions_escalated = db.Column(db.Integer, nullable=False, default=0)
    orchestrator_reasoning = db.Column(db.Text, nullable=True)
    user_insight = db.Column(db.Text, nullable=True)
    cycle_steps = db.Column(db.Text, nullable=True)  # JSON array of actionable to-do strings
    cycle_started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    cycle_completed_at = db.Column(db.DateTime, nullable=True)

    queued_actions = db.relationship('Layer6ActionQueue', backref='cycle', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'cycle_number': self.cycle_number,
            'phase': self.phase,
            'actions_scored': self.actions_scored,
            'actions_dispatched': self.actions_dispatched,
            'actions_escalated': self.actions_escalated,
            'orchestrator_reasoning': self.orchestrator_reasoning,
            'user_insight': self.user_insight,
            'cycle_steps': self.cycle_steps,
            'cycle_started_at': self.cycle_started_at.isoformat(),
            'cycle_completed_at': self.cycle_completed_at.isoformat() if self.cycle_completed_at else None,
        }

    def to_dict_summary(self):
        return {
            'id': self.id,
            'cycle_number': self.cycle_number,
            'phase': self.phase,
            'actions_dispatched': self.actions_dispatched,
            'actions_escalated': self.actions_escalated,
            'cycle_started_at': self.cycle_started_at.isoformat(),
            'cycle_completed_at': self.cycle_completed_at.isoformat() if self.cycle_completed_at else None,
        }


class Layer6ActionQueue(db.Model):
    """Every action the orchestrator selects for a cycle, before and after dispatch."""
    __tablename__ = 'layer6_action_queue'

    STATUS_QUEUED = 'queued'
    STATUS_DISPATCHED = 'dispatched'
    STATUS_COMPLETE = 'complete'
    STATUS_ESCALATED = 'escalated'
    STATUS_REJECTED = 'rejected'
    STATUS_FAILED = 'failed'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    cycle_id = db.Column(
        db.String(9), db.ForeignKey('layer6_cycles.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    source_layer = db.Column(db.Integer, nullable=False)
    action_type = db.Column(db.String(100), nullable=False)
    priority_score = db.Column(db.Numeric(10, 6), nullable=False, default=0)
    _dependency_ids = db.Column('dependency_ids', db.Text, nullable=True)
    escalation_reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_QUEUED)
    agent_action_id = db.Column(
        db.String(9), db.ForeignKey('agent_actions.id', ondelete='SET NULL'), nullable=True,
    )
    dispatched_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    outcome_summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def dependency_ids(self):
        return json.loads(self._dependency_ids) if self._dependency_ids else []

    @dependency_ids.setter
    def dependency_ids(self, value):
        self._dependency_ids = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'cycle_id': self.cycle_id,
            'source_layer': self.source_layer,
            'action_type': self.action_type,
            'priority_score': float(self.priority_score),
            'dependency_ids': self.dependency_ids,
            'escalation_reason': self.escalation_reason,
            'status': self.status,
            'agent_action_id': self.agent_action_id,
            'dispatched_at': self.dispatched_at.isoformat() if self.dispatched_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'outcome_summary': self.outcome_summary,
            'created_at': self.created_at.isoformat(),
        }

    def to_pill_dict(self):
        return {
            'id': self.id,
            'action_type': self.action_type,
            'source_layer': self.source_layer,
            'status': self.status,
            'dispatched_at': self.dispatched_at.isoformat() if self.dispatched_at else None,
        }


class Layer6Outcome(db.Model):
    """Actual vs projected income per income stream per month — feeds Bayesian model."""
    __tablename__ = 'layer6_outcomes'

    REPORTED_BY_USER = 'user'
    REPORTED_BY_INTEGRATION = 'integration'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    layer_number = db.Column(db.Integer, nullable=False)
    income_stream_id = db.Column(
        db.String(9), db.ForeignKey('income_streams.id', ondelete='SET NULL'), nullable=True, index=True,
    )
    reporting_month = db.Column(db.String(7), nullable=False)  # YYYY-MM
    actual_income = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    projected_income = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    variance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    reported_by = db.Column(db.String(20), nullable=False, default=REPORTED_BY_USER)
    integration_source = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'layer_number': self.layer_number,
            'income_stream_id': self.income_stream_id,
            'reporting_month': self.reporting_month,
            'actual_income': float(self.actual_income),
            'projected_income': float(self.projected_income),
            'variance': float(self.variance),
            'reported_by': self.reported_by,
            'integration_source': self.integration_source,
            'created_at': self.created_at.isoformat(),
        }


class Layer6Momentum(db.Model):
    """Daily snapshot of leading indicators across all layers — powers Momentum zone."""
    __tablename__ = 'layer6_momentum'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    snapshot_date = db.Column(db.Date, nullable=False)
    email_list_size = db.Column(db.Integer, nullable=False, default=0)
    linkedin_connections = db.Column(db.Integer, nullable=False, default=0)
    course_enrollments = db.Column(db.Integer, nullable=False, default=0)
    funnel_opt_in_rate = db.Column(db.Numeric(5, 4), nullable=False, default=0)
    seo_organic_sessions = db.Column(db.Integer, nullable=False, default=0)
    newsletter_subscribers = db.Column(db.Integer, nullable=False, default=0)
    pipeline_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    investment_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    consulting_bookings_mo = db.Column(db.Integer, nullable=False, default=0)
    last_milestone_reached_cents = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'snapshot_date': self.snapshot_date.isoformat(),
            'email_list_size': self.email_list_size,
            'linkedin_connections': self.linkedin_connections,
            'course_enrollments': self.course_enrollments,
            'funnel_opt_in_rate': float(self.funnel_opt_in_rate),
            'seo_organic_sessions': self.seo_organic_sessions,
            'newsletter_subscribers': self.newsletter_subscribers,
            'pipeline_value': float(self.pipeline_value),
            'investment_balance': float(self.investment_balance),
            'consulting_bookings_mo': self.consulting_bookings_mo,
            'last_milestone_reached_cents': self.last_milestone_reached_cents,
            'created_at': self.created_at.isoformat(),
        }


class Layer6ExecutionLog(db.Model):
    """Permanent immutable audit log of every orchestrator decision and user override."""
    __tablename__ = 'layer6_execution_log'

    EVENT_DISPATCHED = 'dispatched'
    EVENT_COMPLETED = 'completed'
    EVENT_ESCALATED = 'escalated'
    EVENT_APPROVED = 'approved'
    EVENT_REJECTED = 'rejected'
    EVENT_OVERRIDDEN = 'overridden'
    EVENT_PAUSED = 'paused'
    EVENT_RESUMED = 'resumed'
    EVENT_RECALIBRATED = 're_calibrated'

    ACTOR_ORCHESTRATOR = 'orchestrator'
    ACTOR_USER = 'user'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    cycle_id = db.Column(
        db.String(9), db.ForeignKey('layer6_cycles.id', ondelete='SET NULL'), nullable=True,
    )
    action_id = db.Column(
        db.String(9), db.ForeignKey('layer6_action_queue.id', ondelete='SET NULL'), nullable=True,
    )
    event_type = db.Column(db.String(30), nullable=False)
    actor = db.Column(db.String(20), nullable=False, default=ACTOR_ORCHESTRATOR)
    reasoning = db.Column(db.Text, nullable=True)
    model_tier = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'cycle_id': self.cycle_id,
            'action_id': self.action_id,
            'event_type': self.event_type,
            'actor': self.actor,
            'reasoning': self.reasoning,
            'model_tier': self.model_tier,
            'created_at': self.created_at.isoformat(),
        }


class ActionItem(db.Model):
    """Priority-sorted action items surfaced in the GCC Action Queue tab."""
    __tablename__ = 'action_items'

    TIER_CRITICAL = 1  # blocking the system
    TIER_ACTION   = 2  # needs user decision
    TIER_REVIEW   = 3  # something happened
    TIER_INFORM   = 4  # good to know

    STATUS_ACTIVE    = 'active'
    STATUS_RESOLVED  = 'resolved'
    STATUS_DISMISSED = 'dismissed'

    ITEM_TYPES = [
        'agent_complete', 'agent_approval_required', 'escalation_tool_missing',
        'escalation_tool_expired', 'agent_failure', 'webhook_reply',
        'webhook_booking', 'webhook_signed', 'webhook_payment',
        'webhook_purchase', 'income_missing', 'blog_review',
        'blog_published', 'stale_artifact', 'cycle_ready',
        'first_cycle', 'orchestrator_recommendation', 'milestone',
        'waitlist_threshold', 'contact_promote',
        'bio_chat_started', 'bio_page_review', 'social_post_approval',
        'proactive_alert', 'layer_unlocked',
    ]

    id               = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id    = db.Column(db.String(9), nullable=False, index=True)
    user_id          = db.Column(db.String(9), nullable=False, index=True)
    item_type        = db.Column(db.String(50), nullable=False)
    urgency_tier     = db.Column(db.SmallInteger, nullable=False)
    title            = db.Column(db.String(200), nullable=False)
    description      = db.Column(db.String(500), nullable=True)
    layer_number     = db.Column(db.SmallInteger, nullable=True)
    action_label     = db.Column(db.String(50), nullable=False)
    action_url       = db.Column(db.String(500), nullable=False)
    source_action_id = db.Column(db.String(9), nullable=True)
    source_artifact_id = db.Column(db.String(9), nullable=True)
    source_contact_id  = db.Column(db.String(9), nullable=True)
    source_income_id   = db.Column(db.String(9), nullable=True)
    status           = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)
    resolved_at      = db.Column(db.DateTime, nullable=True)
    dismissed_at     = db.Column(db.DateTime, nullable=True)
    is_dismissable   = db.Column(db.Boolean, nullable=False, default=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('idx_ai_user_active', 'user_id', 'simulation_id', 'status', 'urgency_tier', 'created_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'user_id': self.user_id,
            'item_type': self.item_type,
            'urgency_tier': self.urgency_tier,
            'title': self.title,
            'description': self.description,
            'layer_number': self.layer_number,
            'action_label': self.action_label,
            'action_url': self.action_url,
            'source_action_id': self.source_action_id,
            'source_artifact_id': self.source_artifact_id,
            'source_contact_id': self.source_contact_id,
            'source_income_id': self.source_income_id,
            'status': self.status,
            'is_dismissable': self.is_dismissable,
            'created_at': self.created_at.isoformat(),
        }


class Layer6ShareToken(db.Model):
    """Read-only share link token for the orchestrator diagram (30-day expiry)."""
    __tablename__ = 'layer6_share_tokens'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    cycle_id = db.Column(
        db.String(9), db.ForeignKey('layer6_cycles.id', ondelete='CASCADE'),
        nullable=True,
    )
    token = db.Column(db.String(9), nullable=False, unique=True, default=generate_id)
    created_by = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'token': self.token,
            'simulation_id': self.simulation_id,
            'cycle_id': self.cycle_id,
            'expires_at': self.expires_at.isoformat(),
            'created_at': self.created_at.isoformat(),
        }


class CyclePosteriorSnapshot(db.Model):
    """Snapshot of all Bayesian posteriors at the Score step of each cycle (FR-DIFF-10)."""
    __tablename__ = 'cycle_posterior_snapshots'

    id             = db.Column(db.String(9), primary_key=True, default=generate_id)
    cycle_id       = db.Column(
        db.String(9), db.ForeignKey('layer6_cycles.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    simulation_id  = db.Column(db.String(9), nullable=False, index=True)
    action_type    = db.Column(db.String(100), nullable=False)
    posterior_value = db.Column(db.Numeric(10, 6), nullable=False)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('cycle_id', 'action_type', name='uq_cps_cycle_action'),
    )

    def to_dict(self):
        return {
            'cycle_id': self.cycle_id,
            'action_type': self.action_type,
            'posterior_value': float(self.posterior_value),
        }
