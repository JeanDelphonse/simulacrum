"""SME Console conversational guide (PRD "SME Console Conversational Guide").

Not the human-SME console (/sme, SME-001/002): this is the user-facing AI
guide that helps a user set one goal per income layer. Table names follow the
PRD (sme_guides, sme_sessions, ...); code uses the guide_ prefix.

Transcripts, session summaries and briefing snapshots are encrypted at rest;
no staff or employer route reads them. Approved goal cards and accepted skills
are permanent profile data and survive conversation-history deletion.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


def _j(v):
    return json.dumps(v) if v not in (None, '', [], {}) else None


def _l(v):
    try:
        return json.loads(v) if v else None
    except (TypeError, ValueError):
        return None


class SmeGuide(db.Model):
    """One on-screen guide: Simi or an illustrated person. Same AI underneath;
    only the look and the voice differ."""
    __tablename__ = 'sme_guides'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    slug = db.Column(db.String(30), nullable=False, unique=True)
    display_name = db.Column(db.String(60), nullable=False)
    kind = db.Column(db.String(12), nullable=False, default='illustrated')   # mascot | illustrated | video
    style_label = db.Column(db.String(80), nullable=True)                    # "Calm · analytical"
    voice_label = db.Column(db.String(80), nullable=True)                    # "Measured baritone"
    voice_ref = db.Column(db.String(120), nullable=True)                     # neural voice id (provider TBD)
    gender_hint = db.Column(db.String(2), nullable=True)                     # browser-voice stand-in: f | m
    voice_index = db.Column(db.Integer, nullable=False, default=0)
    pitch = db.Column(db.Float, nullable=False, default=1.0)
    rate = db.Column(db.Float, nullable=False, default=1.0)
    vendor_avatar_ref = db.Column(db.String(120), nullable=True)             # photoreal pilot (off)
    _appearance = db.Column('appearance_json', db.Text, nullable=True)       # illustrated portrait params
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    @property
    def appearance(self) -> dict:
        return _l(self._appearance) or {}

    @appearance.setter
    def appearance(self, v):
        self._appearance = _j(v)

    def to_dict(self):
        return dict(self.appearance, id=self.id, slug=self.slug, name=self.display_name, kind=self.kind,
                    tone=self.style_label, voice=self.voice_label, gender=self.gender_hint,
                    vi=self.voice_index, pitch=self.pitch, rate=self.rate, enabled=self.enabled,
                    sort_order=self.sort_order)


class UserGuidePref(db.Model):
    __tablename__ = 'user_guide_prefs'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False, unique=True)
    guide_id = db.Column(db.String(9), nullable=True)
    voice_enabled = db.Column(db.Boolean, nullable=False, default=True)
    video_enabled = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SmeSession(db.Model):
    __tablename__ = 'sme_sessions'
    __table_args__ = (db.Index('ix_sme_sessions_user', 'user_id', 'started_at'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    guide_id = db.Column(db.String(9), nullable=True)
    mode = db.Column(db.String(10), nullable=False, default='animated')      # animated | video | text
    layer = db.Column(db.Integer, nullable=False, default=1)                 # layer in focus now
    grow_step = db.Column(db.String(1), nullable=True)                       # G | R | O | W
    status = db.Column(db.String(10), nullable=False, default='active')      # active | ended
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_turn_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    turn_count = db.Column(db.Integer, nullable=False, default=0)
    _layers = db.Column('layers_covered', db.Text, nullable=True)
    model_cost_cents = db.Column(db.Float, nullable=False, default=0.0)
    media_cost_cents = db.Column(db.Float, nullable=False, default=0.0)
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    summary_enc = db.Column(db.Text, nullable=True)
    crisis_flag = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def layers_covered(self) -> list:
        return _l(self._layers) or []

    @layers_covered.setter
    def layers_covered(self, v):
        self._layers = _j(sorted(set(v or [])))


class SmeTurn(db.Model):
    __tablename__ = 'sme_turns'
    __table_args__ = (db.Index('ix_sme_turns_session', 'session_id', 'created_at'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    session_id = db.Column(db.String(9), nullable=False)
    speaker = db.Column(db.String(6), nullable=False)                        # user | guide
    text_enc = db.Column(db.Text, nullable=False)
    input_mode = db.Column(db.String(6), nullable=True)                      # voice | typed
    _cited = db.Column('cited_source_ids', db.Text, nullable=True)
    _ui = db.Column('ui_actions_json', db.Text, nullable=True)
    layer = db.Column(db.Integer, nullable=True)
    grow_step = db.Column(db.String(1), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def cited_source_ids(self) -> list:
        return _l(self._cited) or []

    @cited_source_ids.setter
    def cited_source_ids(self, v):
        self._cited = _j(v)

    @property
    def ui_actions(self) -> dict:
        return _l(self._ui) or {}

    @ui_actions.setter
    def ui_actions(self, v):
        self._ui = _j(v)


class SmeBriefing(db.Model):
    """The facts the guide was handed for a session (encrypted snapshot)."""
    __tablename__ = 'sme_briefings'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    session_id = db.Column(db.String(9), nullable=False, index=True)
    source_snapshot_enc = db.Column('source_snapshot_json', db.Text, nullable=False)
    built_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class SkillSuggestion(db.Model):
    __tablename__ = 'skill_suggestions'
    __table_args__ = (db.Index('ix_skill_suggestions_user', 'user_id', 'status'),)

    PROPOSED, ACCEPTED, DECLINED = 'proposed', 'accepted', 'declined'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    session_id = db.Column(db.String(9), nullable=True)
    skill_name = db.Column(db.String(120), nullable=False)
    evidence_ref = db.Column(db.String(60), nullable=True)                   # briefing source id
    evidence_text = db.Column(db.String(400), nullable=True)                 # the achievement it rests on
    status = db.Column(db.String(10), nullable=False, default=PROPOSED)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {'id': self.id, 'skill': self.skill_name, 'evidence_ref': self.evidence_ref,
                'evidence': self.evidence_text, 'status': self.status}


class GoalCard(db.Model):
    __tablename__ = 'goal_cards'
    __table_args__ = (db.Index('ix_goal_cards_user', 'user_id', 'layer', 'status'),)

    DRAFT, APPROVED, EDITED, RETIRED = 'draft', 'approved', 'edited', 'retired'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    session_id = db.Column(db.String(9), nullable=True)
    layer = db.Column(db.Integer, nullable=False)
    goal_text = db.Column(db.String(400), nullable=True)
    measure = db.Column(db.String(300), nullable=True)
    target = db.Column(db.String(120), nullable=True)
    deadline = db.Column(db.String(40), nullable=True)
    _evidence = db.Column('evidence_refs', db.Text, nullable=True)           # [{source_id, label}]
    simulation_ref = db.Column(db.String(60), nullable=True)
    status = db.Column(db.String(10), nullable=False, default=DRAFT)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)
    review_at = db.Column(db.DateTime, nullable=True)

    @property
    def evidence_refs(self) -> list:
        return _l(self._evidence) or []

    @evidence_refs.setter
    def evidence_refs(self, v):
        self._evidence = _j(v)

    def to_dict(self):
        return {'id': self.id, 'layer': self.layer, 'goal': self.goal_text, 'measure': self.measure,
                'target': self.target, 'deadline': self.deadline, 'evidence': self.evidence_refs,
                'simulation_ref': self.simulation_ref, 'status': self.status,
                'approved_at': self.approved_at.isoformat() if self.approved_at else None,
                'review_at': self.review_at.date().isoformat() if self.review_at else None,
                'agent_changes': [c.to_dict() for c in GoalCardAgentChange.query.filter_by(goal_card_id=self.id).all()]}


class GoalCardAgentChange(db.Model):
    """A proposed change to an agent's settings. applied_at stays empty until
    goal-driven agent configuration (EXEC configuration path) is live."""
    __tablename__ = 'goal_card_agent_changes'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    goal_card_id = db.Column(db.String(9), nullable=False, index=True)
    agent_id = db.Column(db.String(50), nullable=False)                      # agents.json action_type
    setting_key = db.Column(db.String(80), nullable=True)
    old_value = db.Column(db.String(300), nullable=True)
    new_value = db.Column(db.String(300), nullable=True)
    applied_at = db.Column(db.DateTime, nullable=True)
    exec_ref = db.Column(db.String(60), nullable=True)

    def to_dict(self):
        return {'agent': self.agent_id, 'setting': self.setting_key, 'old': self.old_value,
                'new': self.new_value, 'applied': self.applied_at is not None}
