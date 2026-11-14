"""Founder Track (SIM-PRD-FND-001, FOUNDER_PLAN.md section 3).

A founder owns one venture. Co-founders join it as members. The venture's
artifacts are evaluated against a nine-dimension rubric, the findings feed a
team of founder agents (config/founder_agents.json, never config/agents.json),
and the agents' work runs through a per-venture plan. The startup page and the
investor tables (Phase 3, behind the founder_investor switch) hang off the
venture too.

Every primary key is a CHAR(9) uppercase id from generate_pk(). Column names
avoid session_id / run_id / step_id (sql/tools/gen_delete_user.py maps those to
other tables): plan steps are plan_step_id and observability runs obs_run_id.

Migrations: sql/069_founder_core.sql ... sql/074_founder_investor.sql.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.mysql import LONGTEXT

from app.extensions import db
from utils.id_gen import generate_pk

_LongText = db.Text().with_variant(LONGTEXT(), 'mysql')


def _iso(value):
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# 069 founder_core
# ---------------------------------------------------------------------------

class FounderVenture(db.Model):
    """One venture per owner account."""
    __tablename__ = 'founder_venture'
    __table_args__ = (db.Index('ix_founder_venture_status', 'status'),)

    STAGES = ('idea', 'pre-seed', 'seed', 'series-a')
    STATUSES = ('active', 'paused', 'archived')
    TRACKS = ('standalone', 'addon')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    owner_user_id = db.Column(db.String(9), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    one_liner = db.Column(db.String(300), nullable=True)
    stage = db.Column(db.String(12), nullable=False, default='idea')
    status = db.Column(db.String(10), nullable=False, default='active')
    rung = db.Column(db.Integer, nullable=False, default=1)
    track = db.Column(db.String(12), nullable=False, default='standalone')
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_intent_id = db.Column(db.String(255), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=True)
    paused_at = db.Column(db.DateTime, nullable=True)
    support_access_until = db.Column(db.DateTime, nullable=True)
    checkin_cadence_days = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'owner_user_id': self.owner_user_id, 'name': self.name,
                'one_liner': self.one_liner, 'stage': self.stage, 'status': self.status, 'rung': self.rung,
                'track': self.track, 'paid': self.paid_at is not None, 'paid_at': _iso(self.paid_at),
                'paused_at': _iso(self.paused_at), 'checkin_cadence_days': self.checkin_cadence_days,
                'created_at': _iso(self.created_at), 'updated_at': _iso(self.updated_at)}


class VentureMember(db.Model):
    """The owner and co-founders. An invited co-founder has invite_email and
    invite_token until they join with their own account."""
    __tablename__ = 'venture_member'
    __table_args__ = (db.Index('ix_venture_member_venture', 'venture_id', 'status'),
                      db.Index('ix_venture_member_user', 'user_id', 'status'))

    ROLES = ('owner', 'cofounder')
    STATUSES = ('invited', 'active', 'left')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    user_id = db.Column(db.String(9), nullable=True)
    invite_email = db.Column(db.String(255), nullable=True)
    invite_token = db.Column(db.String(64), nullable=True, unique=True)
    role = db.Column(db.String(10), nullable=False, default='cofounder')
    title = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(10), nullable=False, default='invited')
    joined_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'user_id': self.user_id,
                'invite_email': self.invite_email, 'role': self.role, 'title': self.title,
                'status': self.status, 'joined_at': _iso(self.joined_at), 'created_at': _iso(self.created_at)}


class VentureArtifact(db.Model):
    """One uploaded or transcribed document, versioned per kind."""
    __tablename__ = 'venture_artifact'
    __table_args__ = (db.Index('ix_venture_artifact_kind', 'venture_id', 'kind', 'is_current'),)

    KINDS = ('startup_plan', 'exec_summary', 'ops_plan', 'pitch_deck', 'financial_model', 'pitch_transcript')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    is_current = db.Column(db.Boolean, nullable=False, default=True)
    filename = db.Column(db.String(255), nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    text_content = db.Column(_LongText, nullable=True)
    char_count = db.Column(db.Integer, nullable=False, default=0)
    uploaded_by = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self, with_text: bool = False):
        d = {'id': self.id, 'venture_id': self.venture_id, 'kind': self.kind, 'version': self.version,
             'is_current': bool(self.is_current), 'filename': self.filename, 'char_count': self.char_count,
             'uploaded_by': self.uploaded_by, 'created_at': _iso(self.created_at)}
        if with_text:
            d['text_content'] = self.text_content
        return d


class FounderRunMeta(db.Model):
    """Founder context for one observability run (agent_runs.id)."""
    __tablename__ = 'founder_run_meta'
    __table_args__ = (db.Index('ix_founder_run_meta_venture', 'venture_id', 'created_at'),)

    TRIGGERS = ('dispatch', 'founder', 'schedule', 'rerun')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    obs_run_id = db.Column(db.String(9), nullable=False, unique=True)
    venture_id = db.Column(db.String(9), nullable=False)
    plan_step_id = db.Column(db.String(9), nullable=True)
    agent_key = db.Column(db.String(60), nullable=False)
    trigger = db.Column(db.String(10), nullable=False, default='founder')
    autonomy_level = db.Column(db.Integer, nullable=True)
    approved_by = db.Column(db.String(9), nullable=True)
    prompt_version = db.Column(db.String(40), nullable=True)
    cost_cents = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 070 founder_eval
# ---------------------------------------------------------------------------

DIMENSIONS = ('problem', 'customer', 'market', 'solution', 'gtm', 'business_model', 'operations', 'team',
              'fundraising')


class EvaluationRun(db.Model):
    __tablename__ = 'evaluation_run'
    __table_args__ = (db.Index('ix_evaluation_run_venture', 'venture_id', 'started_at'),)

    STATUSES = ('running', 'done', 'failed')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    artifact_versions = db.Column(db.JSON, nullable=True)       # {kind: version}
    status = db.Column(db.String(10), nullable=False, default='running')
    scores = db.Column(db.JSON, nullable=True)                  # {dimension: 0-100}
    overall_score = db.Column(db.Float, nullable=True)
    rung_at_run = db.Column(db.Integer, nullable=True)
    obs_run_id = db.Column(db.String(9), nullable=True)
    cost_cents = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'artifact_versions': self.artifact_versions or {},
                'status': self.status, 'scores': self.scores or {}, 'overall_score': self.overall_score,
                'rung_at_run': self.rung_at_run, 'cost_cents': self.cost_cents,
                'started_at': _iso(self.started_at), 'finished_at': _iso(self.finished_at)}


class EvaluationFinding(db.Model):
    """A gap, contradiction, unsupported assumption or strength, with its citation."""
    __tablename__ = 'evaluation_finding'
    __table_args__ = (db.Index('ix_evaluation_finding_venture', 'venture_id', 'superseded_at'),
                      db.Index('ix_evaluation_finding_run', 'evaluation_run_id'))

    FTYPES = ('gap', 'contradiction', 'assumption', 'strength')
    SEVERITIES = ('low', 'medium', 'high')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    evaluation_run_id = db.Column(db.String(9), nullable=False)
    artifact_id = db.Column(db.String(9), nullable=True)
    dimension = db.Column(db.String(20), nullable=False)
    layer = db.Column(db.Integer, nullable=False, default=1)
    ftype = db.Column(db.String(14), nullable=False)
    severity = db.Column(db.String(8), nullable=False, default='medium')
    title = db.Column(db.String(300), nullable=False)
    detail = db.Column(db.Text, nullable=True)
    alpha = db.Column(db.Float, nullable=False, default=1.0)
    beta = db.Column(db.Float, nullable=False, default=1.0)
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    citation_text = db.Column(db.Text, nullable=True)
    citation_ref = db.Column(db.String(60), nullable=True)      # artifact kind and version, e.g. pitch_deck@v2
    recommended_agent = db.Column(db.String(60), nullable=True)
    superseded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'evaluation_run_id': self.evaluation_run_id,
                'artifact_id': self.artifact_id, 'dimension': self.dimension, 'layer': self.layer,
                'ftype': self.ftype, 'severity': self.severity, 'title': self.title, 'detail': self.detail,
                'confidence': self.confidence, 'citation_text': self.citation_text,
                'citation_ref': self.citation_ref, 'recommended_agent': self.recommended_agent,
                'created_at': _iso(self.created_at)}


class FindingRating(db.Model):
    __tablename__ = 'finding_rating'
    __table_args__ = (db.Index('ix_finding_rating_finding', 'finding_id'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    finding_id = db.Column(db.String(9), nullable=False)
    user_id = db.Column(db.String(9), nullable=False)
    fair = db.Column(db.Boolean, nullable=False, default=True)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class FounderEvalCache(db.Model):
    """One model answer per (artifact version, dimension), so an unchanged
    artifact isn't re-scored."""
    __tablename__ = 'founder_eval_cache'
    __table_args__ = (db.UniqueConstraint('artifact_id', 'dimension', name='uq_founder_eval_cache'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    artifact_id = db.Column(db.String(9), nullable=False)
    dimension = db.Column(db.String(20), nullable=False)
    payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 071 founder_plan
# ---------------------------------------------------------------------------

class AgentBlueprint(db.Model):
    __tablename__ = 'agent_blueprint'
    __table_args__ = (db.Index('ix_agent_blueprint_venture', 'venture_id', 'layer', 'status'),)

    STATUSES = ('recommended', 'accepted', 'declined')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    agent_key = db.Column(db.String(60), nullable=False)
    layer = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default='recommended')
    finding_id = db.Column(db.String(9), nullable=True)
    params = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'agent_key': self.agent_key, 'layer': self.layer,
                'status': self.status, 'finding_id': self.finding_id, 'params': self.params or {}}


class AgentParamOverride(db.Model):
    """A founder's change to a recommended parameter, kept with the old value."""
    __tablename__ = 'agent_param_override'
    __table_args__ = (db.Index('ix_agent_param_override_venture', 'venture_id', 'created_at'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    blueprint_id = db.Column(db.String(9), nullable=True)
    layer = db.Column(db.Integer, nullable=True)                 # set for a layer-wide override
    param_key = db.Column(db.String(30), nullable=False)
    previous_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.String(9), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class VenturePlanStep(db.Model):
    __tablename__ = 'venture_plan_step'
    __table_args__ = (db.Index('ix_venture_plan_step_venture', 'venture_id', 'state'),)

    KINDS = ('agent', 'manual', 'alignment', 'proof', 'legal')
    STATES = ('locked', 'ready', 'in_progress', 'awaiting_approval', 'done', 'skipped')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    layer = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(10), nullable=False, default='agent')
    agent_key = db.Column(db.String(60), nullable=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    state = db.Column(db.String(18), nullable=False, default='locked')
    prereq_keys = db.Column(db.JSON, nullable=True)
    effort = db.Column(db.Integer, nullable=False, default=3)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    owner_member_id = db.Column(db.String(9), nullable=True)
    output_id = db.Column(db.String(9), nullable=True)
    evidence_note = db.Column(db.Text, nullable=True)
    skip_reason = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'layer': self.layer, 'kind': self.kind,
                'agent_key': self.agent_key, 'title': self.title, 'description': self.description,
                'state': self.state, 'prereq_keys': self.prereq_keys or [], 'effort': self.effort,
                'sort_order': self.sort_order, 'owner_member_id': self.owner_member_id,
                'output_id': self.output_id, 'evidence_note': self.evidence_note, 'skip_reason': self.skip_reason,
                'completed_at': _iso(self.completed_at), 'updated_at': _iso(self.updated_at)}


class VentureStepEvent(db.Model):
    """Every state change of a plan step. founder_stream polls this table."""
    __tablename__ = 'venture_step_event'
    __table_args__ = (db.Index('ix_venture_step_event_venture', 'venture_id', 'created_at'),
                      db.Index('ix_venture_step_event_step', 'plan_step_id'))

    ACTOR_KINDS = ('founder', 'agent', 'system')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    plan_step_id = db.Column(db.String(9), nullable=True)       # NULL for venture-level events
    from_state = db.Column(db.String(18), nullable=True)
    to_state = db.Column(db.String(40), nullable=True)
    actor_user_id = db.Column(db.String(9), nullable=True)
    actor_kind = db.Column(db.String(8), nullable=False, default='system')
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'plan_step_id': self.plan_step_id,
                'from_state': self.from_state, 'to_state': self.to_state, 'actor_user_id': self.actor_user_id,
                'actor_kind': self.actor_kind, 'note': self.note, 'created_at': _iso(self.created_at)}


class VentureAgentOutput(db.Model):
    __tablename__ = 'venture_agent_output'
    __table_args__ = (db.Index('ix_venture_agent_output_step', 'plan_step_id', 'version'),
                      db.Index('ix_venture_agent_output_venture', 'venture_id'))

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    plan_step_id = db.Column(db.String(9), nullable=False)
    agent_key = db.Column(db.String(60), nullable=False)
    content = db.Column(_LongText, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    obs_run_id = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'plan_step_id': self.plan_step_id,
                'agent_key': self.agent_key, 'content': self.content, 'version': self.version,
                'obs_run_id': self.obs_run_id, 'created_at': _iso(self.created_at)}


class ProofPlan(db.Model):
    __tablename__ = 'proof_plan'
    __table_args__ = (db.Index('ix_proof_plan_venture', 'venture_id', 'status'),)

    STATUSES = ('draft', 'agreed', 'complete')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    customer_name = db.Column(db.String(200), nullable=True)
    pain = db.Column(db.Text, nullable=True)
    baseline = db.Column(db.Text, nullable=True)
    smallest_build = db.Column(db.Text, nullable=True)
    success_criteria = db.Column(db.JSON, nullable=True)
    duration = db.Column(db.String(100), nullable=True)
    provides = db.Column(db.JSON, nullable=True)                 # {founder: [...], customer: [...]}
    on_success = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(10), nullable=False, default='draft')
    agreed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ProofResult(db.Model):
    __tablename__ = 'proof_result'
    __table_args__ = (db.Index('ix_proof_result_plan', 'proof_plan_id'),
                      db.Index('ix_proof_result_venture', 'venture_id'))

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    proof_plan_id = db.Column(db.String(9), nullable=False)
    results = db.Column(db.JSON, nullable=True)
    measured = db.Column(db.Boolean, nullable=False, default=False)
    benefits_text = db.Column(db.Text, nullable=True)
    quote_text = db.Column(db.Text, nullable=True)
    quote_permission = db.Column(db.Boolean, nullable=False, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 072 founder_team
# ---------------------------------------------------------------------------

class AlignmentResponse(db.Model):
    __tablename__ = 'alignment_response'
    __table_args__ = (db.UniqueConstraint('member_id', 'question_key', name='uq_alignment_member_question'),
                      db.Index('ix_alignment_response_venture', 'venture_id'))

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    member_id = db.Column(db.String(9), nullable=False)
    question_key = db.Column(db.String(40), nullable=False)
    answer = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class DecisionRight(db.Model):
    """Who has the final say on one decision type (optionally for one layer)."""
    __tablename__ = 'decision_right'
    __table_args__ = (db.Index('ix_decision_right_venture', 'venture_id', 'decision_type'),)

    DECISION_TYPES = ('publish_page', 'investor_contact', 'customer_contact', 'spend', 'hiring', 'product',
                      'legal')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    decision_type = db.Column(db.String(20), nullable=False)
    layer = db.Column(db.Integer, nullable=True)
    holder_member_id = db.Column(db.String(9), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'decision_type': self.decision_type,
                'layer': self.layer, 'holder_member_id': self.holder_member_id}


# ---------------------------------------------------------------------------
# 073 startup_page
# ---------------------------------------------------------------------------

class StartupPage(db.Model):
    __tablename__ = 'startup_page'
    __table_args__ = (db.Index('ix_startup_page_status', 'status'),)

    STATUSES = ('draft', 'held', 'published', 'unpublished')
    NAME_CHECKS = ('pending', 'clear', 'held')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False, unique=True)
    slug = db.Column(db.String(80), nullable=False, unique=True)
    status = db.Column(db.String(12), nullable=False, default='draft')
    name = db.Column(db.String(200), nullable=False)
    one_liner = db.Column(db.String(300), nullable=True)
    logo_path = db.Column(db.String(500), nullable=True)
    stage = db.Column(db.String(12), nullable=True)
    problem = db.Column(db.Text, nullable=True)
    customer = db.Column(db.Text, nullable=True)
    solution = db.Column(db.Text, nullable=True)
    demo_url = db.Column(db.String(500), nullable=True)
    market = db.Column(db.Text, nullable=True)
    market_method = db.Column(db.Text, nullable=True)
    business_model = db.Column(db.Text, nullable=True)
    traction = db.Column(db.JSON, nullable=True)
    ask_round = db.Column(db.String(60), nullable=True)
    ask_amount = db.Column(db.BigInteger, nullable=True)        # whole USD
    use_of_funds = db.Column(db.Text, nullable=True)
    founder_stated = db.Column(db.JSON, nullable=True)          # fields shown as founder-stated (no evidence)
    name_check = db.Column(db.String(8), nullable=False, default='pending')
    published_at = db.Column(db.DateTime, nullable=True)
    publish_approved_by = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'slug': self.slug, 'status': self.status,
                'name': self.name, 'one_liner': self.one_liner, 'logo_path': self.logo_path, 'stage': self.stage,
                'problem': self.problem, 'customer': self.customer, 'solution': self.solution,
                'demo_url': self.demo_url, 'market': self.market, 'market_method': self.market_method,
                'business_model': self.business_model, 'traction': self.traction or [],
                'ask_round': self.ask_round, 'ask_amount': self.ask_amount, 'use_of_funds': self.use_of_funds,
                'founder_stated': self.founder_stated or [], 'name_check': self.name_check,
                'published_at': _iso(self.published_at), 'updated_at': _iso(self.updated_at)}


class StartupSlugHistory(db.Model):
    """Old slugs, so links keep working (301 to the current slug)."""
    __tablename__ = 'startup_slug_history'
    __table_args__ = (db.Index('ix_startup_slug_history_page', 'startup_page_id'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    startup_page_id = db.Column(db.String(9), nullable=False)
    old_slug = db.Column(db.String(80), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class StartupPageVisit(db.Model):
    """Startup page views (distinct from the site-wide page_views)."""
    __tablename__ = 'startup_page_visit'
    __table_args__ = (db.Index('ix_startup_page_visit_page', 'startup_page_id', 'created_at'),)

    VIEWS = ('public', 'investor')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    startup_page_id = db.Column(db.String(9), nullable=False)
    visitor_hash = db.Column(db.String(64), nullable=True)
    investor_link_id = db.Column(db.String(9), nullable=True)
    view = db.Column(db.String(10), nullable=False, default='public')
    referrer = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 074 founder_investor (Phase 3)
# ---------------------------------------------------------------------------

class ComplianceProfile(db.Model):
    """The offering exemption the founder's lawyer chose, and data consents."""
    __tablename__ = 'compliance_profile'

    EXEMPTIONS = ('506b', '506c', 'undecided')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False, unique=True)
    exemption = db.Column(db.String(10), nullable=False, default='undecided')
    recorded_by = db.Column(db.String(9), nullable=True)
    recorded_at = db.Column(db.DateTime, nullable=True)
    mailbox_consent_at = db.Column(db.DateTime, nullable=True)
    contacts_consent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InvestorLink(db.Model):
    """A private link to the investor view, one per recipient or channel."""
    __tablename__ = 'investor_link'
    __table_args__ = (db.Index('ix_investor_link_venture', 'venture_id'),
                      db.Index('ix_investor_link_page', 'startup_page_id'))

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    startup_page_id = db.Column(db.String(9), nullable=False)
    label = db.Column(db.String(200), nullable=True)
    token = db.Column(db.String(64), nullable=False, unique=True)
    investor_email = db.Column(db.String(255), nullable=True)
    created_by = db.Column(db.String(9), nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    opened_at = db.Column(db.DateTime, nullable=True)
    open_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InvestorAccess(db.Model):
    __tablename__ = 'investor_access'
    __table_args__ = (db.Index('ix_investor_access_page_email', 'startup_page_id', 'email'),
                      db.Index('ix_investor_access_stripe', 'stripe_session_id'))

    KINDS = ('invited', 'paid', 'requested')
    STATUSES = ('pending', 'active', 'requested', 'denied', 'refunded')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    startup_page_id = db.Column(db.String(9), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default='requested')
    investor_link_id = db.Column(db.String(9), nullable=True)
    stripe_session_id = db.Column(db.String(255), nullable=True)
    payment_intent_id = db.Column(db.String(255), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(10), nullable=False, default='pending')
    approved_by = db.Column(db.String(9), nullable=True)
    opted_in_updates = db.Column(db.Boolean, nullable=False, default=False)
    refunded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InvestorUpdate(db.Model):
    __tablename__ = 'investor_update'
    __table_args__ = (db.Index('ix_investor_update_venture', 'venture_id', 'status'),)

    STATUSES = ('draft', 'approved', 'sent')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    subject = db.Column(db.String(300), nullable=True)
    body = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(10), nullable=False, default='draft')
    approved_by = db.Column(db.String(9), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    recipient_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InvestorProfile(db.Model):
    """Admin-curated directory of investors, accelerators and grant programs
    (plus ones founders add). Global: not owned by any one user."""
    __tablename__ = 'investor_profile'
    __table_args__ = (db.Index('ix_investor_profile_kind', 'kind'),)

    KINDS = ('investor', 'accelerator', 'grant')
    SOURCES = ('admin', 'founder')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    name = db.Column(db.String(200), nullable=False)
    firm = db.Column(db.String(200), nullable=True)
    kind = db.Column(db.String(12), nullable=False, default='investor')
    stages = db.Column(db.JSON, nullable=True)
    check_min = db.Column(db.BigInteger, nullable=True)         # whole USD
    check_max = db.Column(db.BigInteger, nullable=True)
    sectors = db.Column(db.JSON, nullable=True)
    geographies = db.Column(db.JSON, nullable=True)
    apply_url = db.Column(db.String(500), nullable=True)
    accepts_applications = db.Column(db.Boolean, nullable=False, default=False)
    auto_submit_allowed = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(8), nullable=False, default='admin')
    added_by = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InvestorTarget(db.Model):
    __tablename__ = 'investor_target'
    __table_args__ = (db.Index('ix_investor_target_venture', 'venture_id', 'status'),)

    STATUSES = ('candidate', 'calibration', 'pursuing', 'passed')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    investor_profile_id = db.Column(db.String(9), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    firm = db.Column(db.String(200), nullable=True)
    kind = db.Column(db.String(12), nullable=False, default='investor')
    fit_reasons = db.Column(db.JSON, nullable=True)
    fit_score = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(12), nullable=False, default='candidate')
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class IntroPath(db.Model):
    """A connector contact who could introduce the founder to a target.
    Found by deterministic code only (no LLM sees email-imported contacts)."""
    __tablename__ = 'intro_path'
    __table_args__ = (db.Index('ix_intro_path_venture', 'venture_id'),
                      db.Index('ix_intro_path_target', 'investor_target_id'))

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    investor_target_id = db.Column(db.String(9), nullable=False)
    connector_contact_id = db.Column(db.String(9), nullable=False)
    strength = db.Column(db.Float, nullable=False, default=0.0)
    evidence = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(12), nullable=False, default='candidate')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class VentureOutreachAction(db.Model):
    """Anything an agent wants to send outside the venture: drafted, approved
    under the autonomy matrix, then (Phase 3) sent. Agents never send."""
    __tablename__ = 'venture_outreach_action'
    __table_args__ = (db.Index('ix_venture_outreach_action_venture', 'venture_id', 'status'),)

    ACTION_TYPES = ('research', 'intro_request', 'followup', 'first_contact', 'financial_message',
                    'customer_message', 'application')
    STATUSES = ('drafted', 'awaiting_approval', 'approved', 'sent', 'blocked')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    venture_id = db.Column(db.String(9), nullable=False)
    plan_step_id = db.Column(db.String(9), nullable=True)
    action_type = db.Column(db.String(20), nullable=False)
    level_required = db.Column(db.Integer, nullable=True)
    level_applied = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(18), nullable=False, default='drafted')
    approved_by = db.Column(db.String(9), nullable=True)
    block_reason = db.Column(db.String(300), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    target_ref = db.Column(db.String(200), nullable=True)
    subject = db.Column(db.String(300), nullable=True)
    body = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'venture_id': self.venture_id, 'plan_step_id': self.plan_step_id,
                'action_type': self.action_type, 'level_required': self.level_required,
                'level_applied': self.level_applied, 'status': self.status, 'approved_by': self.approved_by,
                'block_reason': self.block_reason, 'sent_at': _iso(self.sent_at), 'target_ref': self.target_ref,
                'subject': self.subject, 'body': self.body, 'created_at': _iso(self.created_at)}


class FounderAlertRule(db.Model):
    """Admin alert thresholds for the Founder Track fleet. Global."""
    __tablename__ = 'founder_alert_rule'
    __table_args__ = (db.UniqueConstraint('metric', name='uq_founder_alert_rule_metric'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    metric = db.Column(db.String(60), nullable=False)
    threshold = db.Column(db.Float, nullable=False, default=0.0)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
