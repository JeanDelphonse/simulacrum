"""PRD-SIM-PEER-001 — Peer Calibration.

Five tables behind the suggest-not-overwrite loop: which agents an owner shares,
who is in their circle, what peers proposed, the blend that resolved at a cycle,
and the reciprocity ledger that gates it.

Nothing here is authoritative over a live parameter. The value an agent actually
runs on stays in agent_context; a blend only becomes real when the owner accepts
it and peer_service writes it through that store (PRD §4, owner-authoritative).
"""
from __future__ import annotations

import json
from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


# ── Vocabulary, so nothing spells a status by hand ───────────────────────────

DIRECTION_INVITE_OUT = 'invite_out'
DIRECTION_REQUEST_IN = 'request_in'

INVITE_PENDING  = 'pending'
INVITE_ACCEPTED = 'accepted'
INVITE_DECLINED = 'declined'
INVITE_REVOKED  = 'revoked'

KIND_NUMERIC = 'numeric'
KIND_CHOICE  = 'choice'
KIND_TEXT    = 'text'
PARAM_KINDS  = (KIND_NUMERIC, KIND_CHOICE, KIND_TEXT)

SUGGESTION_PENDING   = 'pending'
SUGGESTION_BLENDED   = 'blended'
SUGGESTION_REJECTED  = 'rejected'
SUGGESTION_DISCARDED = 'discarded'

DECISION_PENDING  = 'pending'
DECISION_ACCEPTED = 'accepted'
DECISION_KEPT     = 'kept'

SIGNAL_TIGHT = 'tight'
SIGNAL_WIDE  = 'wide'


def _json_value(raw):
    """Read a {"value": ...} column that may be JSON or a JSON string.

    MySQL hands back a dict (native JSON column); SQLite hands back the text it
    stored. One reader for both so the tests and production agree.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw.get('value') if isinstance(raw, dict) else raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    return parsed.get('value') if isinstance(parsed, dict) else parsed


class AgentShare(db.Model):
    """One owner's decision to let their circle propose on one L1/L2 agent."""
    __tablename__ = 'agent_shares'

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    owner_user_id = db.Column(db.String(9), nullable=False, index=True)
    simulation_id = db.Column(db.String(9), nullable=False, index=True)
    agent_key     = db.Column(db.String(50), nullable=False)
    layer         = db.Column(db.String(2), nullable=False)
    is_shareable  = db.Column(db.Boolean, nullable=False, default=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('simulation_id', 'agent_key', name='uq_ash_sim_agent'),
        db.Index('idx_ash_owner', 'owner_user_id', 'is_shareable'),
    )

    @property
    def layer_number(self) -> int:
        try:
            return int(str(self.layer).lstrip('Ll'))
        except (TypeError, ValueError):
            return 0

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'agent_key': self.agent_key,
            'layer': self.layer,
            'is_shareable': bool(self.is_shareable),
        }


class PeerInvite(db.Model):
    """A membership in an owner's circle, in either direction."""
    __tablename__ = 'peer_invites'

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    owner_user_id = db.Column(db.String(9), nullable=False, index=True)
    peer_user_id  = db.Column(db.String(9), nullable=True, index=True)
    invite_email  = db.Column(db.String(160), nullable=True)
    peer_label    = db.Column(db.String(160), nullable=True)
    direction     = db.Column(db.String(12), nullable=False, default=DIRECTION_INVITE_OUT)
    status        = db.Column(db.String(10), nullable=False, default=INVITE_PENDING)
    message       = db.Column(db.String(500), nullable=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at   = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_pi_owner_peer', 'owner_user_id', 'peer_user_id', 'status'),
        db.Index('idx_pi_owner_status', 'owner_user_id', 'status'),
        db.Index('idx_pi_peer', 'peer_user_id', 'status'),
        db.Index('idx_pi_email', 'invite_email', 'status'),
    )

    @property
    def is_accepted(self) -> bool:
        return self.status == INVITE_ACCEPTED

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'peer_user_id': self.peer_user_id,
            'invite_email': self.invite_email,
            'peer_label': self.peer_label,
            'direction': self.direction,
            'status': self.status,
            'message': self.message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
        }


class ParameterSuggestion(db.Model):
    """One blind proposal from one peer on one shared parameter."""
    __tablename__ = 'parameter_suggestions'

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    share_id      = db.Column(db.String(9), nullable=False, index=True)
    peer_user_id  = db.Column(db.String(9), nullable=False, index=True)
    agent_key     = db.Column(db.String(50), nullable=False)
    param_key     = db.Column(db.String(100), nullable=False)
    param_kind    = db.Column(db.String(10), nullable=False, default=KIND_NUMERIC)
    value_json    = db.Column(db.JSON, nullable=True)
    numeric_value = db.Column(db.Numeric(18, 4), nullable=True)
    note          = db.Column(db.String(500), nullable=True)
    status        = db.Column(db.String(10), nullable=False, default=SUGGESTION_PENDING)
    guardrail_ok  = db.Column(db.Boolean, nullable=False, default=True)
    reject_reason = db.Column(db.String(500), nullable=True)
    is_quality    = db.Column(db.Boolean, nullable=False, default=False)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_ps_share_param', 'share_id', 'param_key', 'status'),
        db.Index('idx_ps_peer_param', 'peer_user_id', 'share_id', 'param_key', 'created_at'),
        db.Index('idx_ps_peer_created', 'peer_user_id', 'created_at'),
    )

    @property
    def value(self):
        return _json_value(self.value_json)

    def to_dict(self, include_peer: bool = False) -> dict:
        out = {
            'id': self.id,
            'agent_key': self.agent_key,
            'param_key': self.param_key,
            'param_kind': self.param_kind,
            'value': self.value,
            'note': self.note,
            'status': self.status,
            'guardrail_ok': bool(self.guardrail_ok),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_peer:
            out['peer_user_id'] = self.peer_user_id
        return out


class SuggestionBlend(db.Model):
    """The recommendation one cycle produced for one shared parameter."""
    __tablename__ = 'suggestion_blends'

    id               = db.Column(db.String(9), primary_key=True, default=generate_id)
    share_id         = db.Column(db.String(9), nullable=False, index=True)
    agent_key        = db.Column(db.String(50), nullable=False)
    param_key        = db.Column(db.String(100), nullable=False)
    param_kind       = db.Column(db.String(10), nullable=False, default=KIND_NUMERIC)
    owner_prior_json = db.Column(db.JSON, nullable=True)
    posterior_json   = db.Column(db.JSON, nullable=True)
    range_low        = db.Column(db.Numeric(18, 4), nullable=True)
    range_high       = db.Column(db.Numeric(18, 4), nullable=True)
    spread           = db.Column(db.Numeric(18, 4), nullable=True)
    spread_pct       = db.Column(db.Numeric(10, 4), nullable=True)
    # Not `signal`: reserved in MySQL (the SIGNAL statement).
    spread_signal    = db.Column(db.String(10), nullable=True)
    rationale        = db.Column(db.String(1000), nullable=True)
    n_suggestions    = db.Column(db.Integer, nullable=False, default=0)
    n_trimmed        = db.Column(db.Integer, nullable=False, default=0)
    cycle_id         = db.Column(db.String(9), nullable=True)
    owner_decision   = db.Column(db.String(10), nullable=False, default=DECISION_PENDING)
    decided_at       = db.Column(db.DateTime, nullable=True)
    created_at       = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('share_id', 'param_key', 'cycle_id',
                            name='uq_sb_share_param_cycle'),
        db.Index('idx_sb_share_decision', 'share_id', 'owner_decision'),
        db.Index('idx_sb_created', 'created_at'),
    )

    @property
    def owner_prior(self):
        return _json_value(self.owner_prior_json)

    @property
    def posterior(self) -> dict:
        raw = self.posterior_json
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @property
    def recommendation(self):
        return self.posterior.get('value')

    @property
    def is_pending(self) -> bool:
        return self.owner_decision == DECISION_PENDING

    def to_dict(self) -> dict:
        def num(value):
            return float(value) if value is not None else None

        return {
            'id': self.id,
            'share_id': self.share_id,
            'agent_key': self.agent_key,
            'param_key': self.param_key,
            'param_kind': self.param_kind,
            'owner_prior': self.owner_prior,
            'recommendation': self.recommendation,
            'posterior': self.posterior,
            'range_low': num(self.range_low),
            'range_high': num(self.range_high),
            'spread': num(self.spread),
            'spread_pct': num(self.spread_pct),
            'spread_signal': self.spread_signal,
            'rationale': self.rationale,
            'n_suggestions': self.n_suggestions or 0,
            'n_trimmed': self.n_trimmed or 0,
            'cycle_id': self.cycle_id,
            'owner_decision': self.owner_decision,
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ReciprocityLedger(db.Model):
    """How much a peer has contributed this window, and whether that unlocks
    blended recommendations on their own shared agents (PRD §8)."""
    __tablename__ = 'reciprocity_ledger'

    id                        = db.Column(db.String(9), primary_key=True, default=generate_id)
    peer_user_id              = db.Column(db.String(9), nullable=False, unique=True)
    quality_suggestions_count = db.Column(db.Integer, nullable=False, default=0)
    total_suggestions_count   = db.Column(db.Integer, nullable=False, default=0)
    unlocked                  = db.Column(db.Boolean, nullable=False, default=False)
    window_start              = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at                = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                                          onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'quality_suggestions_count': self.quality_suggestions_count or 0,
            'total_suggestions_count': self.total_suggestions_count or 0,
            'unlocked': bool(self.unlocked),
            'window_start': self.window_start.isoformat() if self.window_start else None,
        }
