"""PRD-SIM-OBS-001 — Agent Observability & Governance models.

Three bands, deliberately separate so they can be retained and purged on
different clocks (PRD §6, §12):

  AgentRun                          the shared run header, product lifecycle
  AgentRunTool / AgentEvent /       the run detail, short window (30d default)
  AgentArtifact
  GuardrailRule / GuardrailPolicy / the governance record, kept long
  ObservabilityFlag / AdminAccessAudit

AgentRun is the source of truth: one emit hook writes it, and the console,
calibration and the World view all read it. Nothing writes it directly except
app/services/observability_service.py.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


# ── Vocabularies ─────────────────────────────────────────────────────────────

STATUS_RUNNING = 'running'
STATUS_SUCCESS = 'success'
STATUS_ERROR = 'error'
STATUS_BLOCKED = 'blocked'

ACTION_LOG = 'log'
ACTION_WARN = 'warn'
ACTION_BLOCK = 'block'
# Ordered weakest → strongest. The console promotes a rule along this ladder
# rather than jumping straight to block (PRD §10, Overfitting).
ACTION_ORDER = (ACTION_LOG, ACTION_WARN, ACTION_BLOCK)

SEVERITIES = ('low', 'medium', 'high', 'critical')

RULE_TOOL_ALLOWLIST = 'tool_allowlist'
RULE_PARAM_BOUND = 'param_bound'
RULE_OUTPUT_FILTER = 'output_filter'
RULE_CONTENT_POLICY = 'content_policy'
RULE_RATE_LIMIT = 'rate_limit'
RULE_MODEL_CONSTRAINT = 'model_constraint'

RULE_TYPES = (
    RULE_TOOL_ALLOWLIST, RULE_PARAM_BOUND, RULE_OUTPUT_FILTER,
    RULE_CONTENT_POLICY, RULE_RATE_LIMIT, RULE_MODEL_CONSTRAINT,
)

RULE_TYPE_LABELS = {
    RULE_TOOL_ALLOWLIST:   'Tool allow / deny-list',
    RULE_PARAM_BOUND:      'Parameter bound',
    RULE_OUTPUT_FILTER:    'Output filter',
    RULE_CONTENT_POLICY:   'Content policy',
    RULE_RATE_LIMIT:       'Rate limit',
    RULE_MODEL_CONSTRAINT: 'Model constraint',
}

# The console's axis. L1-L5 are the wealth layers; L6 is the orchestrator,
# which has no layer in LAYER_DEFINITIONS but is a peer here; L0 is everything
# a user does before a simulation exists — resume parse, expertise-zone
# extraction, LinkedIn normalisation. The free tier is where most people meet
# the product, so it gets the same card, filters and guardrail scoping as the
# paid layers rather than a bolted-on panel.
#
# L0 is ordered first and needs no migration: agent_runs.layer is VARCHAR(2).
LAYER_FREE = 'L0'

LAYER_NAMES = {
    'L0': 'Free Tier',
    'L1': 'Active Income',
    'L2': 'Leveraged Income',
    'L3': 'Productized Income',
    'L4': 'Automated Residual',
    'L5': 'Wealth Deployment',
    'L6': 'Orchestration',
}
LAYER_CODES = tuple(LAYER_NAMES)

# Shown under the layer name on the overview card.
LAYER_SUBTITLES = {
    'L0': 'Before a simulation exists',
    'L6': 'Orchestration',
}


class AgentRun(db.Model):
    """One agent invocation. The shared header every consumer reads."""

    __tablename__ = 'agent_runs'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=True, index=True)
    simulation_id = db.Column(db.String(9), nullable=True, index=True)
    action_id = db.Column(db.String(9), nullable=True, index=True)
    agent_key = db.Column(db.String(50), nullable=False, index=True)
    layer = db.Column(db.String(2), nullable=False, index=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_RUNNING)
    model = db.Column(db.String(80), nullable=True)
    prompt_tokens = db.Column(db.Integer, nullable=True)
    completion_tokens = db.Column(db.Integer, nullable=True)
    latency_ms = db.Column(db.Integer, nullable=True)

    params_json = db.Column(db.JSON, nullable=True)
    projected_value = db.Column(db.Numeric(16, 4), nullable=True)

    error_code = db.Column(db.String(80), nullable=True)
    dispatch_source = db.Column(db.String(30), nullable=True)
    is_sampled = db.Column(db.Boolean, nullable=False, default=True)
    flag_count = db.Column(db.Integer, nullable=False, default=0)

    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def to_dict(self, user_label: str = None, agent_label: str = None) -> dict:
        """Row shape for the runs table. Cheap — no joins, no detail reads."""
        return {
            'run_id': self.id,
            'agent_key': self.agent_key,
            'agent_label': agent_label or self.agent_key,
            'layer': self.layer,
            'layer_name': LAYER_NAMES.get(self.layer, self.layer),
            'user_id': self.user_id,
            'user_label': user_label,
            'simulation_id': self.simulation_id,
            'action_id': self.action_id,
            'status': self.status,
            'flagged': bool(self.flag_count),
            'flag_count': self.flag_count or 0,
            'model': self.model,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'total_tokens': self.total_tokens,
            'latency_ms': self.latency_ms,
            'error_code': self.error_code,
            'dispatch_source': self.dispatch_source,
            'is_sampled': bool(self.is_sampled),
            'projected_value': float(self.projected_value) if self.projected_value is not None else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
        }


class AgentRunTool(db.Model):
    """One tool call inside a run, in the order it happened."""

    __tablename__ = 'agent_run_tools'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    run_id = db.Column(db.String(9), nullable=False, index=True)
    tool_name = db.Column(db.String(60), nullable=False, index=True)
    sequence_index = db.Column(db.Integer, nullable=False, default=0)
    input_summary = db.Column(db.String(500), nullable=True)
    output_summary = db.Column(db.String(500), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_SUCCESS)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'tool_name': self.tool_name,
            'sequence_index': self.sequence_index,
            'input_summary': self.input_summary,
            'output_summary': self.output_summary,
            'duration_ms': self.duration_ms,
            'status': self.status,
        }


class AgentEvent(db.Model):
    """One step of the behaviour trace — what the agent did, and why."""

    __tablename__ = 'agent_events'

    TYPE_DECISION = 'decision'
    TYPE_STEP = 'step'
    TYPE_WARNING = 'warning'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    run_id = db.Column(db.String(9), nullable=False, index=True)
    type = db.Column(db.String(20), nullable=False, default=TYPE_STEP)
    payload_json = db.Column(db.JSON, nullable=True)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self, run_started_at: datetime = None) -> dict:
        payload = self.payload_json or {}
        if isinstance(payload, str):          # SQLite round-trip safety
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {'message': payload}
        offset = None
        if run_started_at and self.ts:
            offset = max(0.0, (self.ts - run_started_at).total_seconds())
        return {
            'id': self.id,
            'type': self.type,
            'message': payload.get('message', ''),
            'detail': {k: v for k, v in payload.items() if k != 'message'},
            'ts': self.ts.isoformat() if self.ts else None,
            'offset_s': offset,
        }


class AgentArtifact(db.Model):
    """A deliverable a run produced: summary + reference, not the content.

    is_quarantined marks an output a blocking guardrail withheld. That output
    never reached the product store, so withheld_content holds the only copy
    until an admin releases it (which writes it through) or retention purges it.
    """

    __tablename__ = 'agent_artifacts'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    run_id = db.Column(db.String(9), nullable=False, index=True)
    user_id = db.Column(db.String(9), nullable=True)
    agent_key = db.Column(db.String(50), nullable=False)
    layer = db.Column(db.String(2), nullable=False, index=True)
    artifact_type = db.Column(db.String(80), nullable=False, index=True)
    summary_json = db.Column(db.JSON, nullable=True)
    content_ref = db.Column(db.String(120), nullable=True)
    size = db.Column(db.Integer, nullable=True)

    is_quarantined = db.Column(db.Boolean, nullable=False, default=False)
    withheld_content = db.Column(db.Text, nullable=True)
    released_at = db.Column(db.DateTime, nullable=True)
    released_by = db.Column(db.String(9), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self, include_content: bool = False) -> dict:
        """Summary view by default. include_content is the audited fetch."""
        summary = self.summary_json or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except ValueError:
                summary = {}
        out = {
            'id': self.id,
            'run_id': self.run_id,
            'agent_key': self.agent_key,
            'layer': self.layer,
            'artifact_type': self.artifact_type,
            'summary': summary,
            'content_ref': self.content_ref,
            'size': self.size,
            'is_quarantined': bool(self.is_quarantined),
            'released_at': self.released_at.isoformat() if self.released_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_content:
            out['content'] = self.withheld_content
        return out


class GuardrailRule(db.Model):
    """One admin-authored constraint the agents run under."""

    __tablename__ = 'guardrail_rules'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    name = db.Column(db.String(80), nullable=False, unique=True)
    scope = db.Column(db.String(20), nullable=False, default='global')
    scope_ref = db.Column(db.String(50), nullable=True)
    rule_type = db.Column(db.String(30), nullable=False)
    definition_json = db.Column(db.JSON, nullable=True)
    action = db.Column(db.String(10), nullable=False, default=ACTION_LOG)
    severity = db.Column(db.String(10), nullable=False, default='medium')
    notes = db.Column(db.String(500), nullable=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)

    eval_count = db.Column(db.Integer, nullable=False, default=0)
    fire_count = db.Column(db.Integer, nullable=False, default=0)
    block_count = db.Column(db.Integer, nullable=False, default=0)

    created_by = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @property
    def definition(self) -> dict:
        d = self.definition_json
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except ValueError:
                return {}
        return d or {}

    @property
    def scope_label(self) -> str:
        if self.scope == 'global':
            return 'Global'
        if self.scope == 'layer':
            return '%s · %s' % (self.scope_ref, LAYER_NAMES.get(self.scope_ref, ''))
        return 'Agent · %s' % self.scope_ref

    @property
    def fire_rate(self) -> float:
        """Share of evaluations that fired. The over-fire signal (PRD §9)."""
        if not self.eval_count:
            return 0.0
        return round(100.0 * self.fire_count / self.eval_count, 1)

    def applies_to(self, layer: str, agent_key: str) -> bool:
        if not self.is_enabled:
            return False
        if self.scope == 'global':
            return True
        if self.scope == 'layer':
            return self.scope_ref == layer
        return self.scope_ref == agent_key

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'scope': self.scope,
            'scope_ref': self.scope_ref,
            'scope_label': self.scope_label,
            'rule_type': self.rule_type,
            'rule_type_label': RULE_TYPE_LABELS.get(self.rule_type, self.rule_type),
            'definition': self.definition,
            'action': self.action,
            'severity': self.severity,
            'notes': self.notes,
            'is_enabled': bool(self.is_enabled),
            'eval_count': self.eval_count or 0,
            'fire_count': self.fire_count or 0,
            'block_count': self.block_count or 0,
            'fire_rate': self.fire_rate,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class GuardrailPolicy(db.Model):
    """A named bundle of rules, so a posture can be switched on as a unit."""

    __tablename__ = 'guardrail_policies'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    name = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(500), nullable=True)
    scope = db.Column(db.String(20), nullable=False, default='global')
    scope_ref = db.Column(db.String(50), nullable=True)
    rule_ids_json = db.Column(db.JSON, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @property
    def rule_ids(self) -> list:
        ids = self.rule_ids_json
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except ValueError:
                return []
        return list(ids or [])

    def to_dict(self) -> dict:
        rules = (GuardrailRule.query.filter(GuardrailRule.id.in_(self.rule_ids)).all()
                 if self.rule_ids else [])
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'scope': self.scope,
            'scope_ref': self.scope_ref,
            'rule_ids': self.rule_ids,
            'rules': [{'id': r.id, 'name': r.name, 'action': r.action} for r in rules],
            'is_active': bool(self.is_active),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ObservabilityFlag(db.Model):
    """One firing of one rule against one run, tool call or artifact."""

    __tablename__ = 'observability_flags'

    TARGET_RUN = 'run'
    TARGET_TOOL = 'tool'
    TARGET_ARTIFACT = 'artifact'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    target_type = db.Column(db.String(12), nullable=False)
    target_id = db.Column(db.String(9), nullable=False)
    run_id = db.Column(db.String(9), nullable=True, index=True)
    rule_id = db.Column(db.String(9), nullable=True, index=True)
    rule_name = db.Column(db.String(80), nullable=True)
    layer = db.Column(db.String(2), nullable=True)
    agent_key = db.Column(db.String(50), nullable=True)
    user_id = db.Column(db.String(9), nullable=True)
    severity = db.Column(db.String(10), nullable=False, default='medium')
    action = db.Column(db.String(10), nullable=False, default=ACTION_LOG)
    reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self, user_label: str = None, agent_label: str = None) -> dict:
        return {
            'id': self.id,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'run_id': self.run_id,
            'rule_id': self.rule_id,
            'rule_name': self.rule_name,
            'layer': self.layer,
            'layer_name': LAYER_NAMES.get(self.layer, self.layer),
            'agent_key': self.agent_key,
            'agent_label': agent_label or self.agent_key,
            'user_id': self.user_id,
            'user_label': user_label,
            'severity': self.severity,
            'action': self.action,
            'reason': self.reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AdminAccessAudit(db.Model):
    """Who looked at whose agent activity. Written on every cross-user read."""

    __tablename__ = 'admin_access_audit'

    VIEW_RUN = 'view_run'
    VIEW_ARTIFACT = 'view_artifact'
    EXPORT = 'export'
    RELEASE_ARTIFACT = 'release_artifact'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    admin_user_id = db.Column(db.String(9), nullable=True, index=True)
    action = db.Column(db.String(30), nullable=False)
    target_id = db.Column(db.String(120), nullable=True)
    subject_user_id = db.Column(db.String(9), nullable=True)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self, admin_label: str = None, subject_label: str = None) -> dict:
        return {
            'id': self.id,
            'admin_user_id': self.admin_user_id,
            'admin_label': admin_label,
            'action': self.action,
            'target_id': self.target_id,
            'subject_user_id': self.subject_user_id,
            'subject_label': subject_label,
            'ts': self.ts.isoformat() if self.ts else None,
        }
