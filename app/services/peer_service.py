"""PRD-SIM-PEER-001 — Peer Calibration.

An owner marks specific Layer 1 / Layer 2 agents shareable and invites a trusted
circle. Invited peers propose parameter values from the owner's bio page —
blind, without seeing the owner's current value. At the next cycle run the
owner's value (the prior) and the peer proposals (the likelihood) are combined
into a recommendation, shown with its range, spread and rationale. The owner
accepts it or keeps their own.

Three lines this module holds, because the whole feature depends on them:

  Owner-authoritative.  Nothing here writes a live parameter. `decide()` is the
      only path that touches agent_context, and only on an explicit accept. A
      blend is a proposal record, not a value.

  Blind.  Numeric and choice parameters are served to a peer as a name and a
      unit — never the owner's value. Text parameters are served as a brief
      (goal, persona, constraints) built from the agent definition and the
      owner's *public* positioning, never from their draft.

  Guardrail-bounded.  Every incoming proposal is evaluated against the owner's
      OBS-001 parameter bounds before it is accepted into the pool, so the blend
      can never recommend an out-of-policy value. Peer Calibration is what
      creates the external-input risk OBS-001 governs, so that check is a hard
      dependency, not a nicety.

Numbers use math; the LLM earns its place only on text and on explanation
(PRD §7). Every threshold, weight, cap and window is admin-editable and lives
in platform_settings under a `peer_` prefix — see SETTINGS_SPEC.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timedelta

from app.models.peer import (
    DECISION_ACCEPTED, DECISION_KEPT, DECISION_PENDING,
    DIRECTION_INVITE_OUT, DIRECTION_REQUEST_IN,
    INVITE_ACCEPTED, INVITE_DECLINED, INVITE_PENDING, INVITE_REVOKED,
    KIND_CHOICE, KIND_NUMERIC, KIND_TEXT,
    SIGNAL_TIGHT, SIGNAL_WIDE,
    SUGGESTION_BLENDED, SUGGESTION_DISCARDED, SUGGESTION_PENDING,
    SUGGESTION_REJECTED,
    AgentShare, ParameterSuggestion, PeerInvite, ReciprocityLedger,
    SuggestionBlend,
)
from app.models.platform_settings import PlatformSetting
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


class PeerError(Exception):
    """A refusal the caller should show the user verbatim."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Admin-configurable settings (standing convention: never hardcode a threshold)
# ---------------------------------------------------------------------------

# key -> (type, default, label, help). Editable through the existing
# PUT /api/admin/settings/<key> endpoint.
SETTINGS_SPEC = {
    'peer_enabled': (
        'bool', True, 'Peer Calibration enabled',
        'Master switch. When off, no agent can be shared, no proposal is accepted '
        'and the Peers overlay does not appear.'),
    'peer_max_layer': (
        'int', 2, 'Highest shareable layer',
        'Agents above this layer are never offered for sharing. 2 by design: '
        'income allocation, investments and other sensitive parameters are never '
        'crowd-tunable (PRD §14).'),
    'peer_min_suggestions': (
        'int', 3, 'Minimum proposals before a blend',
        'Below this, the owner sees the individual proposals but no blended '
        'recommendation — the stopping rule that keeps a blend from overfitting a '
        'thin, noisy sample.'),
    'peer_prior_weight': (
        'float', 2.0, 'Weight of the owner\'s own value',
        'How many peer proposals the owner\'s current value counts as in the '
        'numeric blend. Above 1 so a few cold proposals cannot swing the value.'),
    'peer_outlier_mad_k': (
        'float', 3.0, 'Outlier trim (MAD multiples)',
        'A proposal further than this many median-absolute-deviations from the '
        'median is trimmed before the blend. Lower trims harder.'),
    'peer_outlier_band_pct': (
        'int', 60, 'Outlier trim fallback band (%)',
        'Used when every proposal is identical (deviation of zero): a proposal '
        'further than this percentage from the median is still trimmed.'),
    'peer_tight_spread_pct': (
        'int', 15, 'Tight-cluster threshold (%)',
        'Spread at or below this share of the cluster reads as a confident '
        'cluster (exploit). Above it the blend is flagged wide — keep collecting.'),
    'peer_max_per_agent': (
        'int', 3, 'Proposals per peer per agent, per window',
        'Anti-farming cap. Counted over the rate-limit window below.'),
    'peer_cooldown_hours': (
        'int', 12, 'Cooldown per parameter (hours)',
        'How long a peer must wait before proposing again on the same parameter.'),
    'peer_max_rejected_per_window': (
        'int', 5, 'Out-of-bounds attempts per window',
        'A proposal a guardrail rejects does not burn the per-agent cap — a peer '
        'who is told their value is out of bounds should be able to correct it. '
        'This caps the attempts instead, so that allowance is not a spam hole. '
        'Counted across every engine the peer touches.'),
    'peer_rate_window_hours': (
        'int', 168, 'Rate-limit window (hours)',
        'The window the per-agent cap is counted over. 168 = one week.'),
    'peer_circle_max': (
        'int', 25, 'Maximum circle size',
        'Accepted peers per owner. A circle is a trusted few, not a crowd.'),
    'peer_reciprocity_gate_enabled': (
        'bool', True, 'Reciprocity gate enabled',
        'When on, an owner sees blended recommendations on their own shared '
        'agents only once they have contributed quality suggestions to their '
        'peers. An owner with nowhere to contribute is never gated.'),
    'peer_reciprocity_required': (
        'int', 2, 'Quality suggestions to unlock',
        'Substantive proposals — within guardrail bounds, not a duplicate, not '
        'trimmed as an outlier — needed in one window to unlock your own blends.'),
    'peer_reciprocity_window_days': (
        'int', 60, 'Reciprocity window (days)',
        'The rolling window the unlock count is measured over.'),
    'peer_note_max_chars': (
        'int', 400, 'Maximum characters in a proposal note',
        'The optional "why" a peer can attach to a proposal.'),
    'peer_text_max_chars': (
        'int', 1200, 'Maximum characters in a text proposal',
        'Caps what a context-blind text proposal can carry into the synthesis '
        'prompt.'),
    'peer_credibility_weighting': (
        'bool', False, 'Weight peers by proven outcome',
        'Off in v1 by deliberate choice (PRD §7): peers are equal-weighted until '
        'the outcome flywheel has enough data to earn a weight. Turning this on '
        'before then would manufacture confidence rather than measure it.'),
    'peer_signal_cache_ttl_seconds': (
        'int', 20, 'Peer-signal cache (seconds)',
        'How long the per-simulation peer rollup is memoized. Invalidated '
        'immediately whenever a proposal arrives, so this only bounds staleness '
        'for activity in other simulations.'),
    'peer_stream_seconds': (
        'int', 120, 'Peer stream lifetime (seconds)',
        'A live badge stream holds a web worker for its whole life, so it closes '
        'itself after this long and the client reconnects.'),
    'peer_stream_poll_seconds': (
        'int', 5, 'Peer stream tick (seconds)',
        'How often an open stream looks for new proposals.'),
}

_TRUE = ('1', 'true', 'yes', 'on', 'True')


def setting(key):
    """Read one peer setting, coerced to the type declared in SETTINGS_SPEC."""
    kind, default = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][1]
    try:
        raw = PlatformSetting.get(key)
    except Exception:
        return default
    if raw is None or raw == '':
        return default
    if kind == 'bool':
        return str(raw) in _TRUE
    if kind == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if kind == 'float':
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    return str(raw)


def all_settings() -> list:
    """Every setting with its value and metadata, for the admin panel."""
    return [
        {'key': key, 'type': spec[0], 'value': setting(key),
         'default': spec[1], 'label': spec[2], 'help': spec[3]}
        for key, spec in SETTINGS_SPEC.items()
    ]


def save_setting(key, value, updated_by=None):
    """Validate and persist one setting. Returns the coerced value."""
    if key not in SETTINGS_SPEC:
        raise ValueError('Unknown setting: %s' % key)
    kind, label = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][2]

    if kind == 'bool':
        coerced = '1' if (value is True or str(value) in _TRUE) else '0'
    elif kind == 'int':
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a whole number.' % label)
        if number < 0:
            raise ValueError('%s cannot be negative.' % label)
        if key == 'peer_max_layer' and not 1 <= number <= 2:
            raise ValueError('Sharing above Layer 2 is not available: income '
                             'allocation and investments are never crowd-tunable.')
        if key == 'peer_min_suggestions' and number < 1:
            raise ValueError('A blend needs at least one proposal.')
        coerced = str(number)
    elif kind == 'float':
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a number.' % label)
        if number < 0:
            raise ValueError('%s cannot be negative.' % label)
        coerced = str(number)
    else:
        coerced = str(value or '')

    PlatformSetting.set(key, coerced, updated_by=updated_by)
    invalidate()
    return setting(key)


def is_enabled() -> bool:
    return bool(setting('peer_enabled'))


# ---------------------------------------------------------------------------
# Peer-signal cache (PRD §12, Caching)
# ---------------------------------------------------------------------------

_CACHE: dict = {}


def invalidate(simulation_id=None) -> None:
    """Drop the memoized rollup — for one simulation, or all of them."""
    if simulation_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(simulation_id, None)


# ---------------------------------------------------------------------------
# Parameter kinds and briefs (PRD §6 — blind vs context-blind)
# ---------------------------------------------------------------------------

# A label that names money, a percentage or a parenthesised unit is a number.
_MONEY_RE = re.compile(r'(usd|\$|%|\bpercent)', re.I)
_UNIT_PAREN_RE = re.compile(r'\((?:usd|weeks?|hours?|days?|months?|years?|%|count|number)\b',
                            re.I)
_QUANTITY_RE = re.compile(r'\b(how many|number of)\b', re.I)
# Key segments that name a quantity outright. Deliberately does not include
# `days`: "Available days" is Mon/Wed/Fri, not a count.
_NUMERIC_SEGMENTS = frozenset((
    'rate', 'fee', 'price', 'cost', 'budget', 'amount', 'limit', 'size',
    'capacity', 'count', 'quota', 'seats', 'pct', 'percent',
))
_NUMERIC_SUFFIXES = ('_weeks', '_hours', '_months', '_years', '_usd')


def classify_param(field: dict) -> str:
    """numeric | choice | text for one prompt_form field.

    An agent definition can override the guess with `peer_param_kind` on the
    field, which is how a compound label ("start date and price") avoids being
    served to a peer as a bare number.
    """
    override = str(field.get('peer_param_kind') or '').strip().lower()
    if override in (KIND_NUMERIC, KIND_CHOICE, KIND_TEXT):
        return override

    ftype = str(field.get('type') or 'text').lower()
    if ftype == 'select':
        return KIND_CHOICE
    if ftype == 'textarea':
        return KIND_TEXT

    label = str(field.get('label') or '')
    key = str(field.get('key') or '')
    if _MONEY_RE.search(label) or _UNIT_PAREN_RE.search(label) or _QUANTITY_RE.search(label):
        return KIND_NUMERIC
    if set(key.split('_')) & _NUMERIC_SEGMENTS:
        return KIND_NUMERIC
    if key.endswith(_NUMERIC_SUFFIXES):
        return KIND_NUMERIC
    return KIND_TEXT


def param_unit(field: dict) -> str:
    """The unit a blind numeric proposal is quoted in — all a peer needs (§6)."""
    label = str(field.get('label') or '')
    if re.search(r'usd|\$', label, re.I):
        return 'USD'
    if '%' in label or re.search(r'\bpercent', label, re.I):
        return '%'
    match = re.search(r'\((weeks?|hours?|days?|months?|years?)\b', label, re.I)
    if match:
        return match.group(1).lower()
    for suffix, unit in (('_weeks', 'weeks'), ('_hours', 'hours'),
                         ('_months', 'months'), ('_years', 'years')):
        if str(field.get('key') or '').endswith(suffix):
            return unit
    return ''


def _agent_meta(agent_key: str):
    from app.services.agent_registry import get_agent_meta, resolve_alias
    return get_agent_meta(resolve_alias(agent_key))


def shareable_agents(simulation_id: str) -> list:
    """Every agent in this simulation eligible to be shared, with its params.

    Eligibility is the layer ceiling and nothing else: an agent does not have to
    be active or configured to be shareable, because a cold parameter is exactly
    the one peer input helps most.
    """
    from app.models.simulation import Simulation
    from app.services.agent_registry import get_all_agents, resolve_alias
    from app.services.agent_selector import effective_selection

    sim = Simulation.query.get(simulation_id)
    if sim is None:
        return []

    ceiling = int(setting('peer_max_layer'))
    agents = [a for a in get_all_agents() if a.get('layer') in range(1, ceiling + 1)]
    try:
        active = effective_selection(sim, agents_flat=get_all_agents())
    except Exception:
        active = set()

    shares = {s.agent_key: s for s in AgentShare.query.filter_by(
        simulation_id=simulation_id).all()}

    out = []
    for agent in agents:
        key = resolve_alias(agent['action_type'])
        fields = agent.get('prompt_form') or []
        share = shares.get(key)
        out.append({
            'agent_key': key,
            'label': agent.get('label') or key,
            'description': agent.get('description') or '',
            'layer': agent['layer'],
            'is_shareable': bool(share and share.is_shareable),
            'share_id': share.id if share else None,
            'active': key in active,
            'params': [
                {'key': f['key'], 'label': f.get('label') or f['key'],
                 'kind': classify_param(f), 'unit': param_unit(f),
                 'options': f.get('options') or [],
                 'required': bool(f.get('required'))}
                for f in fields
            ],
        })
    return out


def _param_spec(agent_key: str, param_key: str):
    """The prompt_form field behind one param_key, or None if it is not one."""
    meta = _agent_meta(agent_key)
    for field in (meta or {}).get('prompt_form') or []:
        if field.get('key') == param_key:
            return field
    return None


def text_brief(agent_key: str, field: dict, owner_profile=None, zone=None) -> dict:
    """The context a *text* proposal needs to be coherent, and nothing more.

    Goal, persona and constraints — assembled from the agent definition and the
    owner's public positioning. The owner's own draft is never part of it, which
    is what makes the proposal context-blind rather than a rewrite (PRD §6).
    """
    meta = _agent_meta(agent_key) or {}
    persona_bits = []
    if zone:
        persona_bits.append(str(zone))
    if owner_profile is not None:
        if getattr(owner_profile, 'tagline', None):
            persona_bits.append(owner_profile.tagline)
        primary = getattr(owner_profile, 'primary_zone', None)
        if primary and str(primary) not in persona_bits:
            persona_bits.append(str(primary))

    constraints = []
    if field.get('required'):
        constraints.append('Required field.')
    if field.get('options'):
        constraints.append('One of: %s.' % ', '.join(str(o) for o in field['options']))
    constraints.append('Up to %d characters.' % int(setting('peer_text_max_chars')))

    return {
        'goal': meta.get('description') or meta.get('label') or '',
        'persona': ' · '.join(persona_bits) or 'An independent professional.',
        'constraints': ' '.join(constraints),
        'asks_for': field.get('label') or field.get('key') or '',
    }


# ---------------------------------------------------------------------------
# Sharing (PRD §5)
# ---------------------------------------------------------------------------

def set_share(simulation_id: str, owner_user_id: str, agent_key: str,
              is_shareable: bool) -> AgentShare:
    """Mark one L1/L2 agent shareable, or un-share it.

    Un-sharing discards the proposals still waiting on it: a peer's pending
    input on an agent the owner has withdrawn should not resurface at the next
    cycle (PRD §5, revocation).
    """
    from app.extensions import db
    from app.services.agent_registry import resolve_alias

    if not is_enabled():
        raise PeerError('Peer Calibration is switched off.', 403)

    key = resolve_alias(agent_key)
    meta = _agent_meta(key)
    if meta is None:
        raise PeerError('Unknown agent.', 404)

    ceiling = int(setting('peer_max_layer'))
    if meta.get('layer') not in range(1, ceiling + 1):
        raise PeerError(
            'Only Layer 1 and Layer 2 agents can be shared. Income allocation, '
            'investments and other sensitive parameters are never crowd-tunable.',
            400)

    share = AgentShare.query.filter_by(
        simulation_id=simulation_id, agent_key=key).first()
    if share is None:
        share = AgentShare(
            id=generate_id(), owner_user_id=owner_user_id,
            simulation_id=simulation_id, agent_key=key,
            layer='L%d' % meta['layer'], is_shareable=bool(is_shareable),
        )
        db.session.add(share)
    else:
        share.is_shareable = bool(is_shareable)
        share.updated_at = datetime.utcnow()
        if not is_shareable:
            _discard_pending(share_id=share.id)

    db.session.commit()
    invalidate(simulation_id)
    return share


def _discard_pending(share_id=None, peer_user_id=None, owner_user_id=None) -> int:
    """Take proposals out of the pool. Does not commit."""
    query = ParameterSuggestion.query.filter_by(status=SUGGESTION_PENDING)
    if share_id:
        query = query.filter_by(share_id=share_id)
    if peer_user_id:
        query = query.filter_by(peer_user_id=peer_user_id)
    if owner_user_id:
        share_ids = [s.id for s in AgentShare.query.filter_by(
            owner_user_id=owner_user_id).all()]
        if not share_ids:
            return 0
        query = query.filter(ParameterSuggestion.share_id.in_(share_ids))

    rows = query.all()
    for row in rows:
        row.status = SUGGESTION_DISCARDED
    return len(rows)


def shares_for_owner(owner_user_id: str, simulation_id=None) -> list:
    query = AgentShare.query.filter_by(owner_user_id=owner_user_id, is_shareable=True)
    if simulation_id:
        query = query.filter_by(simulation_id=simulation_id)
    return query.all()


# ---------------------------------------------------------------------------
# The invited circle (PRD §5)
# ---------------------------------------------------------------------------

def _accepted_count(owner_user_id: str) -> int:
    return PeerInvite.query.filter_by(
        owner_user_id=owner_user_id, status=INVITE_ACCEPTED).count()


def is_peer(owner_user_id: str, viewer_user_id) -> bool:
    """Is this viewer in the owner's circle? The gate every propose path calls."""
    if not viewer_user_id or viewer_user_id == owner_user_id:
        return False
    return PeerInvite.query.filter_by(
        owner_user_id=owner_user_id, peer_user_id=viewer_user_id,
        status=INVITE_ACCEPTED).first() is not None


def membership(owner_user_id: str, viewer_user_id) -> PeerInvite | None:
    """This viewer's standing with the owner, whatever it is."""
    if not viewer_user_id:
        return None
    return PeerInvite.query.filter_by(
        owner_user_id=owner_user_id, peer_user_id=viewer_user_id,
    ).order_by(PeerInvite.created_at.desc()).first()


def invite_peer(owner_user_id: str, email=None, peer_user_id=None,
                label=None) -> PeerInvite:
    """Owner invites someone into their circle (invite-out, the v1 core)."""
    from app.extensions import db
    from app.models.user import User

    if not is_enabled():
        raise PeerError('Peer Calibration is switched off.', 403)
    if _accepted_count(owner_user_id) >= int(setting('peer_circle_max')):
        raise PeerError('Your circle is full (%d peers). Remove someone first.'
                        % int(setting('peer_circle_max')), 400)

    email = (email or '').strip().lower() or None
    if not email and not peer_user_id:
        raise PeerError('Give an email address or a handle to invite.', 400)

    # Resolve the email to an account when there is one, so accepting is one
    # click rather than a signup-then-link dance.
    if email and not peer_user_id:
        existing = User.query.filter(User.email == email).first()
        if existing and not existing.deleted_at:
            peer_user_id = existing.id
            label = label or existing.full_name

    if peer_user_id == owner_user_id:
        raise PeerError('You cannot invite yourself.', 400)

    prior = None
    if peer_user_id:
        prior = PeerInvite.query.filter_by(
            owner_user_id=owner_user_id, peer_user_id=peer_user_id).first()
    elif email:
        prior = PeerInvite.query.filter_by(
            owner_user_id=owner_user_id, invite_email=email).first()

    if prior is not None:
        if prior.status == INVITE_ACCEPTED:
            raise PeerError('They are already in your circle.', 409)
        # Re-inviting a declined or revoked peer reopens the same row rather
        # than stacking duplicates.
        prior.status = INVITE_PENDING
        prior.direction = DIRECTION_INVITE_OUT
        prior.resolved_at = None
        prior.created_at = datetime.utcnow()
        if label:
            prior.peer_label = label[:160]
        invite = prior
    else:
        invite = PeerInvite(
            id=generate_id(), owner_user_id=owner_user_id,
            peer_user_id=peer_user_id, invite_email=email,
            peer_label=(label or '')[:160] or None,
            direction=DIRECTION_INVITE_OUT, status=INVITE_PENDING,
        )
        db.session.add(invite)

    db.session.commit()
    _notify_invited(invite)
    return invite


def request_to_join(owner_user_id: str, peer_user_id: str, message=None) -> PeerInvite:
    """A bio-page visitor asks to join the circle. Grants nothing until approved."""
    from app.extensions import db

    if not is_enabled():
        raise PeerError('Peer Calibration is switched off.', 403)
    if peer_user_id == owner_user_id:
        raise PeerError('That is your own circle.', 400)
    if not shares_for_owner(owner_user_id):
        raise PeerError('This person is not accepting peer calibration yet.', 404)

    prior = PeerInvite.query.filter_by(
        owner_user_id=owner_user_id, peer_user_id=peer_user_id).first()
    if prior is not None:
        if prior.status == INVITE_ACCEPTED:
            raise PeerError('You are already in this circle.', 409)
        if prior.status == INVITE_PENDING:
            return prior
        if prior.status == INVITE_REVOKED:
            raise PeerError('This owner is not taking requests from you.', 403)
        prior.status = INVITE_PENDING
        prior.direction = DIRECTION_REQUEST_IN
        prior.message = (message or '')[:500] or None
        prior.resolved_at = None
        prior.created_at = datetime.utcnow()
        invite = prior
    else:
        invite = PeerInvite(
            id=generate_id(), owner_user_id=owner_user_id,
            peer_user_id=peer_user_id, direction=DIRECTION_REQUEST_IN,
            status=INVITE_PENDING, message=(message or '')[:500] or None,
        )
        db.session.add(invite)

    db.session.commit()
    _notify_owner_request(invite)
    return invite


def accept_invite(invite_id: str, peer_user_id: str) -> PeerInvite:
    """The invited peer accepts. Only they can — an owner cannot self-accept."""
    from app.extensions import db

    invite = PeerInvite.query.get(invite_id)
    if invite is None or invite.direction != DIRECTION_INVITE_OUT:
        raise PeerError('Invite not found.', 404)
    if invite.status != INVITE_PENDING:
        raise PeerError('That invite is no longer open.', 409)
    if invite.peer_user_id and invite.peer_user_id != peer_user_id:
        raise PeerError('That invite was addressed to someone else.', 403)

    invite.peer_user_id = peer_user_id
    invite.status = INVITE_ACCEPTED
    invite.resolved_at = datetime.utcnow()
    db.session.commit()
    return invite


def approve_request(invite_id: str, owner_user_id: str) -> PeerInvite:
    """The owner approves a request-in, or an invite the peer already claimed."""
    from app.extensions import db

    invite = PeerInvite.query.get(invite_id)
    if invite is None or invite.owner_user_id != owner_user_id:
        raise PeerError('Request not found.', 404)
    if invite.status == INVITE_ACCEPTED:
        return invite
    if _accepted_count(owner_user_id) >= int(setting('peer_circle_max')):
        raise PeerError('Your circle is full. Remove someone first.', 400)

    invite.status = INVITE_ACCEPTED
    invite.resolved_at = datetime.utcnow()
    db.session.commit()
    _notify_peer_approved(invite)
    return invite


def decline_request(invite_id: str, owner_user_id: str) -> PeerInvite:
    from app.extensions import db

    invite = PeerInvite.query.get(invite_id)
    if invite is None or invite.owner_user_id != owner_user_id:
        raise PeerError('Request not found.', 404)
    invite.status = INVITE_DECLINED
    invite.resolved_at = datetime.utcnow()
    db.session.commit()
    return invite


def remove_peer(invite_id: str, owner_user_id: str) -> PeerInvite:
    """Revoke a membership. The removed peer's pending proposals are discarded.

    Already-blended proposals stay: the blend the owner has in front of them is
    a record of a cycle that happened, and silently rewriting it would be worse
    than letting it stand (PRD §5).
    """
    from app.extensions import db

    invite = PeerInvite.query.get(invite_id)
    if invite is None or invite.owner_user_id != owner_user_id:
        raise PeerError('Peer not found.', 404)

    invite.status = INVITE_REVOKED
    invite.resolved_at = datetime.utcnow()
    if invite.peer_user_id:
        _discard_pending(peer_user_id=invite.peer_user_id, owner_user_id=owner_user_id)
    db.session.commit()
    invalidate()
    return invite


def circle(owner_user_id: str) -> dict:
    """The owner's circle for the settings panel: accepted, pending, requests."""
    from app.models.profile import UserProfile
    from app.models.user import User

    rows = PeerInvite.query.filter_by(owner_user_id=owner_user_id).order_by(
        PeerInvite.created_at.desc()).all()

    user_ids = [r.peer_user_id for r in rows if r.peer_user_id]
    names, avatars = {}, {}
    if user_ids:
        for user in User.query.filter(User.id.in_(user_ids)).all():
            names[user.id] = user.full_name or user.email
        for profile in UserProfile.query.filter(
                UserProfile.user_id.in_(user_ids)).all():
            avatars[profile.user_id] = profile.avatar_path or ''
            if profile.display_name:
                names[profile.user_id] = profile.display_name

    def shape(row):
        out = row.to_dict()
        out['display_name'] = (names.get(row.peer_user_id) or row.peer_label
                               or row.invite_email or 'Invited peer')
        out['avatar_path'] = avatars.get(row.peer_user_id, '')
        return out

    return {
        'accepted': [shape(r) for r in rows if r.status == INVITE_ACCEPTED],
        'invited': [shape(r) for r in rows
                    if r.status == INVITE_PENDING
                    and r.direction == DIRECTION_INVITE_OUT],
        'requests': [shape(r) for r in rows
                     if r.status == INVITE_PENDING
                     and r.direction == DIRECTION_REQUEST_IN],
        'max_size': int(setting('peer_circle_max')),
    }


def invites_for_peer(peer_user_id: str) -> list:
    """Circles this user has been invited into and not yet answered."""
    from app.models.profile import UserProfile
    from app.models.user import User

    rows = PeerInvite.query.filter_by(
        peer_user_id=peer_user_id, status=INVITE_PENDING,
        direction=DIRECTION_INVITE_OUT).all()
    out = []
    for row in rows:
        owner = User.query.get(row.owner_user_id)
        profile = UserProfile.query.filter_by(user_id=row.owner_user_id).first()
        out.append({
            'id': row.id,
            'owner_name': (profile.display_name if profile and profile.display_name
                           else (owner.full_name if owner else 'A Simulacrum member')),
            'owner_slug': profile.username if profile else None,
            'created_at': row.created_at.isoformat() if row.created_at else None,
        })
    return out


# ---------------------------------------------------------------------------
# What a peer is allowed to see (PRD §6, §14)
# ---------------------------------------------------------------------------

def peer_view(owner_user_id: str, viewer_user_id) -> dict:
    """The blind payload behind GET /api/u/<handle>/shared-agents.

    Everything an invited peer needs to propose, and nothing the owner has not
    shared: which agents are open, each parameter's name, kind and unit, or —
    for text — its brief. No owner values, no results, no contacts, no other
    agents.
    """
    from app.models.profile import UserProfile
    from app.models.simulation import Simulation

    if not is_enabled():
        return {'enabled': False, 'shared_agents': [], 'is_peer': False}

    shares = shares_for_owner(owner_user_id)
    if not shares:
        return {'enabled': True, 'shares_anything': False,
                'shared_agents': [], 'is_peer': False}

    invited = is_peer(owner_user_id, viewer_user_id)
    standing = membership(owner_user_id, viewer_user_id)
    profile = UserProfile.query.filter_by(user_id=owner_user_id).first()

    payload = {
        'enabled': True,
        'shares_anything': True,
        'is_peer': invited,
        'status': standing.status if standing else None,
        'invite_id': standing.id if standing and standing.status == INVITE_PENDING
                     and standing.direction == DIRECTION_INVITE_OUT else None,
        'circle_size': _accepted_count(owner_user_id),
        'agent_count': len(shares),
        'shared_agents': [],
    }
    if not invited:
        # A non-invited visitor learns only that a circle exists, which is what
        # makes "request to join" meaningful without leaking the engine.
        return payload

    zones = {}
    for share in shares:
        sim = Simulation.query.get(share.simulation_id)
        zones[share.id] = sim.expertise_zone if sim else None

    already = _peer_proposal_counts(viewer_user_id, [s.id for s in shares])
    cooldown = timedelta(hours=int(setting('peer_cooldown_hours')))
    cap = int(setting('peer_max_per_agent'))
    now = datetime.utcnow()

    for share in shares:
        meta = _agent_meta(share.agent_key) or {}
        fields = meta.get('prompt_form') or []
        if not fields:
            continue
        used = already.get(share.id, {})
        params = []
        for field in fields:
            kind = classify_param(field)
            entry = {
                'param_key': field['key'],
                'label': field.get('label') or field['key'],
                'kind': kind,
                'required': bool(field.get('required')),
                'proposed': bool(used.get(field['key'])),
            }
            last = used.get(field['key'])
            if last and (now - last) < cooldown:
                entry['cooldown_until'] = (last + cooldown).isoformat()
            if kind == KIND_NUMERIC:
                entry['unit'] = param_unit(field)
            elif kind == KIND_CHOICE:
                entry['options'] = [str(o) for o in (field.get('options') or [])]
            else:
                entry['brief'] = text_brief(share.agent_key, field, profile,
                                            zones.get(share.id))
            params.append(entry)

        payload['shared_agents'].append({
            'share_id': share.id,
            'agent_key': share.agent_key,
            'label': meta.get('label') or share.agent_key,
            'description': meta.get('description') or '',
            'layer': share.layer_number,
            'params': params,
            'proposals_used': len(used),
            'proposals_allowed': cap,
        })
    return payload


def _peer_proposal_counts(peer_user_id, share_ids) -> dict:
    """{share_id: {param_key: last_created_at}} inside the rate-limit window."""
    if not peer_user_id or not share_ids:
        return {}
    window = datetime.utcnow() - timedelta(hours=int(setting('peer_rate_window_hours')))
    rows = ParameterSuggestion.query.filter(
        ParameterSuggestion.peer_user_id == peer_user_id,
        ParameterSuggestion.share_id.in_(list(share_ids)),
        ParameterSuggestion.created_at >= window,
        ParameterSuggestion.status != SUGGESTION_DISCARDED,
        ParameterSuggestion.guardrail_ok == True,  # noqa: E712
    ).all()
    out: dict = {}
    for row in rows:
        bucket = out.setdefault(row.share_id, {})
        if row.param_key not in bucket or row.created_at > bucket[row.param_key]:
            bucket[row.param_key] = row.created_at
    return out


# ---------------------------------------------------------------------------
# Submitting a proposal (PRD §4 step 2, §8)
# ---------------------------------------------------------------------------

def _coerce_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or '').strip()
    if not text:
        return None
    text = text.replace(',', '').replace('$', '').replace('%', '').strip()
    # "150/hr", "150 usd", "2 weeks" — take the leading quantity.
    match = re.match(r'^-?\d+(?:\.\d+)?', text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def submit_suggestion(share_id: str, peer_user_id: str, param_key: str,
                      value, note=None) -> dict:
    """Record one blind proposal.

    The order of the checks is the point. Membership and shareability first
    (authorisation), then rate limits and dedupe (anti-farming), then the
    guardrail bound (policy). Only a proposal that clears all three enters the
    pool, and only one that clears all three *and* says something new advances
    the reciprocity ledger.
    """
    from app.extensions import db
    from app.services import guardrail_service as gr

    if not is_enabled():
        raise PeerError('Peer Calibration is switched off.', 403)

    share = AgentShare.query.get(share_id)
    if share is None or not share.is_shareable:
        raise PeerError('That agent is not shared.', 404)
    if share.owner_user_id == peer_user_id:
        raise PeerError('You cannot propose on your own agent.', 400)
    if not is_peer(share.owner_user_id, peer_user_id):
        raise PeerError('Only invited peers can propose on this engine.', 403)

    field = _param_spec(share.agent_key, param_key)
    if field is None:
        raise PeerError('That agent has no such parameter.', 404)
    kind = classify_param(field)

    # ── Shape ────────────────────────────────────────────────────────────────
    numeric_value = None
    if kind == KIND_NUMERIC:
        numeric_value = _coerce_number(value)
        if numeric_value is None:
            raise PeerError('Propose a number for "%s".'
                            % (field.get('label') or param_key), 400)
        stored = numeric_value
    elif kind == KIND_CHOICE:
        options = [str(o) for o in (field.get('options') or [])]
        stored = str(value or '').strip()
        if options and stored not in options:
            raise PeerError('Choose one of: %s.' % ', '.join(options), 400)
        if not stored:
            raise PeerError('Choose an option.', 400)
    else:
        stored = str(value or '').strip()
        if not stored:
            raise PeerError('Write a proposal first.', 400)
        limit = int(setting('peer_text_max_chars'))
        if len(stored) > limit:
            raise PeerError('Keep it under %d characters.' % limit, 400)

    note = (str(note or '').strip() or None)
    if note:
        note = note[:int(setting('peer_note_max_chars'))]

    # ── Rate limits and dedupe (PRD §8) ──────────────────────────────────────
    # The caps count what actually reached the pool. A proposal the owner's
    # guardrails rejected never got there, so it does not burn the peer's slot
    # or start their cooldown — being told "that is outside their bounds" and
    # then locked out for twelve hours would teach nothing. Out-of-bounds
    # attempts are bounded separately, below, so this is not a spam hole.
    window = datetime.utcnow() - timedelta(hours=int(setting('peer_rate_window_hours')))
    recent = ParameterSuggestion.query.filter(
        ParameterSuggestion.peer_user_id == peer_user_id,
        ParameterSuggestion.share_id == share_id,
        ParameterSuggestion.created_at >= window,
    ).all()
    mine = [s for s in recent
            if s.guardrail_ok and s.status != SUGGESTION_DISCARDED]

    same_param = [s for s in mine if s.param_key == param_key]
    cooldown_hours = int(setting('peer_cooldown_hours'))
    if same_param:
        latest = max(same_param, key=lambda s: s.created_at)
        wait_until = latest.created_at + timedelta(hours=cooldown_hours)
        if datetime.utcnow() < wait_until:
            raise PeerError(
                'You proposed on this parameter recently. You can revise it after %s.'
                % wait_until.strftime('%d %b %H:%M UTC'), 429, code='cooldown')

    if len({s.param_key for s in mine}) >= int(setting('peer_max_per_agent')) \
            and param_key not in {s.param_key for s in mine}:
        raise PeerError(
            'You have used your %d proposals on this agent for now.'
            % int(setting('peer_max_per_agent')), 429, code='cap')

    rejected_cap = int(setting('peer_max_rejected_per_window'))
    rejected_recent = ParameterSuggestion.query.filter(
        ParameterSuggestion.peer_user_id == peer_user_id,
        ParameterSuggestion.created_at >= window,
        ParameterSuggestion.guardrail_ok == False,  # noqa: E712
    ).count()
    if rejected_cap and rejected_recent >= rejected_cap:
        raise PeerError(
            'Too many of your recent proposals fell outside the bounds these '
            'engines are governed by. Try again later.', 429, code='rejected_cap')

    # Dedupe: the same peer restating the same value on the same parameter is
    # one proposal, not two. It is also not new signal, so it does not advance
    # the gate.
    duplicate = False
    for prior in same_param:
        if prior.status == SUGGESTION_PENDING and str(prior.value) == str(stored):
            duplicate = True
            break

    # ── Guardrails (OBS-001 — a hard dependency, PRD §13) ────────────────────
    guardrail_ok, reason = True, None
    try:
        decision = gr.check_param_value(
            share.agent_key, share.layer, param_key, stored,
            user_id=share.owner_user_id)
        if decision.blocked:
            guardrail_ok, reason = False, decision.reason or 'outside the owner\'s bounds'
    except Exception as exc:
        # Evaluation itself failing is not the peer's fault, and this is not the
        # enforcement path for an agent's own output — let the proposal in and
        # log it rather than silently swallowing peer input.
        logger.warning('peer proposal guardrail check failed: %s', exc)

    suggestion = ParameterSuggestion(
        id=generate_id(), share_id=share_id, peer_user_id=peer_user_id,
        agent_key=share.agent_key, param_key=param_key, param_kind=kind,
        value_json={'value': stored},
        numeric_value=numeric_value,
        note=note,
        status=SUGGESTION_PENDING if guardrail_ok else SUGGESTION_REJECTED,
        guardrail_ok=guardrail_ok,
        reject_reason=(reason[:500] if reason else None),
        is_quality=bool(guardrail_ok and not duplicate),
    )
    db.session.add(suggestion)

    if suggestion.is_quality:
        _bump_ledger(peer_user_id, quality=True)
    else:
        _bump_ledger(peer_user_id, quality=False)

    db.session.commit()
    invalidate(share.simulation_id)

    if not guardrail_ok:
        raise PeerError(
            'That value is outside the bounds this engine is governed by (%s), '
            'so it was not added to the pool.' % reason, 422, code='guardrail')

    return {
        'suggestion': suggestion.to_dict(),
        'counted_toward_unlock': bool(suggestion.is_quality),
        'duplicate': duplicate,
        'reciprocity': gate_state(peer_user_id),
    }


# ---------------------------------------------------------------------------
# The reciprocity gate (PRD §8)
# ---------------------------------------------------------------------------

def _ledger(peer_user_id: str, create=True):
    from app.extensions import db

    row = ReciprocityLedger.query.filter_by(peer_user_id=peer_user_id).first()
    if row is None:
        if not create:
            return None
        row = ReciprocityLedger(
            id=generate_id(), peer_user_id=peer_user_id,
            window_start=datetime.utcnow(),
        )
        db.session.add(row)
        db.session.flush()
        return row

    window = timedelta(days=int(setting('peer_reciprocity_window_days')))
    if row.window_start and (datetime.utcnow() - row.window_start) > window:
        row.quality_suggestions_count = 0
        row.total_suggestions_count = 0
        row.unlocked = False
        row.window_start = datetime.utcnow()
    return row


def _bump_ledger(peer_user_id: str, quality: bool) -> None:
    """Advance the ledger. Does not commit — the caller owns the transaction."""
    row = _ledger(peer_user_id)
    row.total_suggestions_count = (row.total_suggestions_count or 0) + 1
    if quality:
        row.quality_suggestions_count = (row.quality_suggestions_count or 0) + 1
    row.unlocked = (row.quality_suggestions_count or 0) >= int(
        setting('peer_reciprocity_required'))
    row.updated_at = datetime.utcnow()


def has_contribution_opportunity(user_id: str) -> bool:
    """Is this user in anyone's circle, on a circle that shares something?

    The gate only bites when there is somewhere to contribute. An owner who has
    not been invited anywhere cannot earn an unlock, so gating them would be a
    dead end rather than an incentive.
    """
    rows = PeerInvite.query.filter_by(
        peer_user_id=user_id, status=INVITE_ACCEPTED).all()
    for row in rows:
        if shares_for_owner(row.owner_user_id):
            return True
    return False


def gate_state(user_id: str) -> dict:
    """Whether this owner's own blended recommendations are unlocked, and why."""
    required = int(setting('peer_reciprocity_required'))
    enabled = bool(setting('peer_reciprocity_gate_enabled'))
    row = _ledger(user_id, create=False)
    count = (row.quality_suggestions_count if row else 0) or 0

    if not enabled:
        return {'enabled': False, 'unlocked': True, 'required': required,
                'count': count, 'reason': None}

    if not has_contribution_opportunity(user_id):
        return {'enabled': True, 'unlocked': True, 'required': required,
                'count': count, 'has_opportunity': False,
                'reason': 'You have no peer engines to contribute to yet, so '
                          'your recommendations are not gated.'}

    unlocked = count >= required
    return {
        'enabled': True,
        'unlocked': unlocked,
        'required': required,
        'count': count,
        'has_opportunity': True,
        'reason': None if unlocked else (
            'Suggest on a peer\'s engine to unlock suggestions on yours — %d of '
            '%d quality suggestions so far.' % (count, required)),
    }


# ---------------------------------------------------------------------------
# The aggregation model (PRD §7)
# ---------------------------------------------------------------------------

def _median(values: list) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def trim_outliers(values: list) -> tuple:
    """(survivors, trimmed) by median absolute deviation.

    MAD rather than standard deviation because the thing being defended against
    is exactly the case that inflates a standard deviation: one or two extreme
    proposals. When every proposal is identical the MAD is zero and no spread
    measure can help, so a percentage band around the median takes over.
    """
    if len(values) < 3:
        return list(values), []

    median = _median(values)
    deviations = [abs(v - median) for v in values]
    mad = _median(deviations)

    if mad > 0:
        limit = float(setting('peer_outlier_mad_k')) * mad
    else:
        limit = (float(setting('peer_outlier_band_pct')) / 100.0) * abs(median)
        if limit <= 0:
            return list(values), []

    survivors = [v for v in values if abs(v - median) <= limit]
    trimmed = [v for v in values if abs(v - median) > limit]
    # Never trim the pool down below the minimum a blend needs: if the trim
    # would leave too little to blend, the honest answer is a wide range over
    # everything, not a narrow one over a hand-picked two.
    if len(survivors) < int(setting('peer_min_suggestions')):
        return list(values), []
    return survivors, trimmed


def blend_numeric(prior, values: list) -> dict | None:
    """The weighted blend: owner value as prior, peer proposals as likelihood.

    The prior carries a fixed weight in peer-proposal units, so three cold
    proposals move a value but cannot capture it. Peers are equal-weighted in
    v1 — simpler and honest until outcome data can earn a weight (PRD §7).
    """
    minimum = int(setting('peer_min_suggestions'))
    numbers = [v for v in (_coerce_number(v) for v in values) if v is not None]
    if len(numbers) < minimum:
        return None

    survivors, trimmed = trim_outliers(numbers)
    n = len(survivors)
    if n < minimum:
        return None

    mean = sum(survivors) / n
    cluster = _median(survivors)
    low, high = min(survivors), max(survivors)
    variance = sum((v - mean) ** 2 for v in survivors) / n
    spread = math.sqrt(variance)

    prior_value = _coerce_number(prior)
    weight = float(setting('peer_prior_weight'))
    if prior_value is None or weight <= 0:
        posterior = mean
        method = 'peer_mean'
    else:
        posterior = (weight * prior_value + sum(survivors)) / (weight + n)
        method = 'weighted_blend'

    denominator = abs(cluster) if cluster else (abs(mean) or 1.0)
    spread_pct = (spread / denominator) * 100.0
    tight = spread_pct <= float(setting('peer_tight_spread_pct'))

    return {
        'kind': KIND_NUMERIC,
        'posterior': {
            'value': round(posterior, 4),
            'cluster': round(cluster, 4),
            'mean': round(mean, 4),
            'method': method,
            'prior_weight': weight,
            'n': n,
        },
        'range_low': round(low, 4),
        'range_high': round(high, 4),
        'spread': round(spread, 4),
        'spread_pct': round(spread_pct, 4),
        'spread_signal': SIGNAL_TIGHT if tight else SIGNAL_WIDE,
        'n_suggestions': n,
        'n_trimmed': len(trimmed),
        'rationale': _numeric_rationale(prior_value, posterior, cluster, low, high,
                                        spread_pct, n, len(trimmed), tight),
    }


def blend_choice(prior, values: list) -> dict | None:
    """A choice parameter blends by plurality, and reports the disagreement."""
    minimum = int(setting('peer_min_suggestions'))
    picks = [str(v).strip() for v in values if str(v or '').strip()]
    if len(picks) < minimum:
        return None

    tally: dict = {}
    for pick in picks:
        tally[pick] = tally.get(pick, 0) + 1
    winner, top = max(tally.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(picks)
    disagreement = (1.0 - top / float(n)) * 100.0
    tight = disagreement <= float(setting('peer_tight_spread_pct'))

    prior_text = str(prior or '').strip()
    if prior_text and prior_text == winner:
        rationale = ('%d of %d peers picked "%s" — the same as your current '
                     'choice.' % (top, n, winner))
    else:
        rationale = ('%d of %d peers picked "%s"%s.'
                     % (top, n, winner,
                        ' rather than your "%s"' % prior_text if prior_text else ''))
    if len(tally) > 1:
        rest = ', '.join('%s ×%d' % (k, v) for k, v in
                         sorted(tally.items(), key=lambda kv: -kv[1]) if k != winner)
        rationale += ' Also proposed: %s.' % rest

    return {
        'kind': KIND_CHOICE,
        'posterior': {'value': winner, 'cluster': winner, 'method': 'plurality',
                      'tally': tally, 'n': n},
        'range_low': None, 'range_high': None,
        'spread': None,
        'spread_pct': round(disagreement, 4),
        'spread_signal': SIGNAL_TIGHT if tight else SIGNAL_WIDE,
        'n_suggestions': n,
        'n_trimmed': 0,
        'rationale': rationale,
    }


def _numeric_rationale(prior, posterior, cluster, low, high, spread_pct,
                       n, trimmed, tight) -> str:
    """Written by arithmetic, not by tokens (PRD §7)."""
    def money(value):
        return ('%g' % round(value, 2))

    parts = ['%d peer%s landed between %s and %s, clustered at %s'
             % (n, '' if n == 1 else 's', money(low), money(high), money(cluster))]
    parts.append('%s spread (±%.0f%%)' % ('tight' if tight else 'wide', spread_pct))

    if prior is None:
        parts.append('you had not set a value, so the recommendation is the peer '
                     'average, %s' % money(posterior))
    else:
        parts.append('blended with your %s at a prior weight of %g, the '
                     'recommendation is %s'
                     % (money(prior), float(setting('peer_prior_weight')),
                        money(posterior)))
    if trimmed:
        parts.append('%d outlier%s trimmed'
                     % (trimmed, '' if trimmed == 1 else 's'))
    if not tight:
        parts.append('the spread is wide, so treat this as still collecting '
                     'rather than settled')
    return ('. '.join(p[0].upper() + p[1:] for p in parts) + '.')[:1000]


def blend_text(prior, proposals: list, agent_key: str, param_key: str,
               brief: dict) -> dict | None:
    """Synthesize peer text proposals and the owner's original into one version.

    This is the one place the LLM earns its keep: numbers use math, text needs
    judgement. If the model is unreachable the blend is skipped rather than
    faked — the owner keeps seeing the individual proposals.
    """
    minimum = int(setting('peer_min_suggestions'))
    texts = [str(p or '').strip() for p in proposals if str(p or '').strip()]
    if len(texts) < minimum:
        return None

    from utils.model_router import get_model

    numbered = '\n\n'.join('Peer %d: %s' % (i + 1, t) for i, t in enumerate(texts))
    owner_block = (('The owner\'s current version:\n%s\n\n' % prior)
                   if str(prior or '').strip() else
                   'The owner has not written a version yet.\n\n')

    prompt = (
        'You are synthesizing peer suggestions for one field of a professional\'s '
        'income-engine configuration. Produce ONE blended version and a one-line '
        'rationale.\n\n'
        'Field: %s\n'
        'Goal of this agent: %s\n'
        'Who it is for: %s\n'
        'Constraints: %s\n\n'
        '%s'
        'Peer suggestions (written without seeing the owner\'s version):\n%s\n\n'
        'Rules:\n'
        '- Keep the owner\'s voice and any specifics only they could know.\n'
        '- Adopt what the peers genuinely improve; ignore what merely restates.\n'
        '- Stay inside the constraints above.\n'
        '- The rationale names what changed and how many peers drove it, e.g. '
        '"2 peers tightened your opener to lead with the client\'s problem".\n\n'
        'Reply with JSON only: {"blended": "...", "rationale": "..."}'
        % (brief.get('asks_for') or param_key, brief.get('goal') or '',
           brief.get('persona') or '', brief.get('constraints') or '',
           owner_block, numbered)
    )

    try:
        from app.services.claude import _client
        model = get_model('peer_text_synthesis')
        response = _client().messages.create(
            model=model,
            max_tokens=1600,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        logger.warning('peer text synthesis failed for %s.%s: %s',
                       agent_key, param_key, exc)
        return None

    parsed = _parse_json_object(raw)
    blended = str((parsed or {}).get('blended') or '').strip()
    rationale = str((parsed or {}).get('rationale') or '').strip()
    if not blended:
        logger.warning('peer text synthesis returned no blended version for %s.%s',
                       agent_key, param_key)
        return None

    try:
        from app.services.claude import _log_interaction
        from app.models.ai_interaction import AIInteraction
        _log_interaction(AIInteraction.TYPE_AGENT_ACTION, None, None,
                         getattr(response, 'usage', None), model=model)
    except Exception:
        pass

    limit = int(setting('peer_text_max_chars'))
    return {
        'kind': KIND_TEXT,
        'posterior': {'value': blended[:limit], 'method': 'llm_synthesis',
                      'n': len(texts)},
        'range_low': None, 'range_high': None, 'spread': None, 'spread_pct': None,
        'spread_signal': SIGNAL_TIGHT,
        'n_suggestions': len(texts),
        'n_trimmed': 0,
        'rationale': (rationale or '%d peers contributed to this version.'
                      % len(texts))[:1000],
    }


def _parse_json_object(raw: str):
    text = str(raw or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Resolving blends at the cycle boundary (PRD §4 step 3, §12 Optimal Stopping)
# ---------------------------------------------------------------------------

def _owner_values(simulation_id: str, layer_number: int) -> dict:
    from app.models.agent_context import AgentContext
    try:
        return AgentContext.get_for_layer(simulation_id, layer_number) or {}
    except Exception as exc:
        logger.warning('peer blend could not read owner values: %s', exc)
        return {}


def resolve_blends_for_cycle(simulation_id: str, cycle_id=None) -> dict:
    """Blend every shared parameter that has met the minimum, once per cycle.

    Called from the orchestrator cycle. Best-effort by construction: a failure
    on one parameter must not cost the cycle, and a cycle that runs twice must
    not blend twice — the unique index on (share, param, cycle) is what makes
    the second call a no-op.
    """
    from app.extensions import db
    from app.models.profile import UserProfile
    from app.models.simulation import Simulation

    summary = {'blends': 0, 'skipped': 0, 'params': []}
    if not is_enabled():
        return summary

    shares = AgentShare.query.filter_by(
        simulation_id=simulation_id, is_shareable=True).all()
    if not shares:
        return summary

    sim = Simulation.query.get(simulation_id)
    profile = UserProfile.query.filter_by(
        user_id=shares[0].owner_user_id).first() if shares else None

    for share in shares:
        pending = ParameterSuggestion.query.filter_by(
            share_id=share.id, status=SUGGESTION_PENDING, guardrail_ok=True,
        ).all()
        if not pending:
            continue

        by_param: dict = {}
        for row in pending:
            by_param.setdefault(row.param_key, []).append(row)

        owner_values = _owner_values(simulation_id, share.layer_number)

        for param_key, rows in by_param.items():
            try:
                existing = SuggestionBlend.query.filter_by(
                    share_id=share.id, param_key=param_key, cycle_id=cycle_id,
                ).first()
                if existing is not None:
                    continue

                field = _param_spec(share.agent_key, param_key)
                if field is None:
                    continue
                kind = classify_param(field)
                prior = owner_values.get(param_key)
                values = [r.value for r in rows]

                if kind == KIND_NUMERIC:
                    result = blend_numeric(prior, values)
                elif kind == KIND_CHOICE:
                    result = blend_choice(prior, values)
                else:
                    result = blend_text(
                        prior, values, share.agent_key, param_key,
                        text_brief(share.agent_key, field, profile,
                                   sim.expertise_zone if sim else None))

                if result is None:
                    # Below the minimum, or synthesis was unavailable. The
                    # proposals stay pending so the next cycle can try again —
                    # that is the stopping rule, not a failure.
                    summary['skipped'] += 1
                    continue

                blend = SuggestionBlend(
                    id=generate_id(), share_id=share.id,
                    agent_key=share.agent_key, param_key=param_key,
                    param_kind=kind,
                    owner_prior_json={'value': prior},
                    posterior_json=result['posterior'],
                    range_low=result['range_low'],
                    range_high=result['range_high'],
                    spread=result['spread'],
                    spread_pct=result['spread_pct'],
                    spread_signal=result['spread_signal'],
                    rationale=result['rationale'],
                    n_suggestions=result['n_suggestions'],
                    n_trimmed=result['n_trimmed'],
                    cycle_id=cycle_id,
                    owner_decision=DECISION_PENDING,
                )
                db.session.add(blend)
                for row in rows:
                    row.status = SUGGESTION_BLENDED
                db.session.commit()

                summary['blends'] += 1
                summary['params'].append({'agent_key': share.agent_key,
                                          'param_key': param_key})
            except Exception as exc:
                db.session.rollback()
                logger.warning('peer blend failed for %s.%s: %s',
                               share.agent_key, param_key, exc)

    invalidate(simulation_id)
    if summary['blends']:
        _notify_owner_blends(shares[0].owner_user_id, simulation_id, summary)
    return summary


# ---------------------------------------------------------------------------
# The owner's decision (PRD §4 step 4)
# ---------------------------------------------------------------------------

def decide(blend_id: str, owner_user_id: str, decision: str) -> dict:
    """Accept the recommendation, or keep your own. The only write path to a
    live parameter in this whole module.

    Accepting writes through AgentContext — the same store the Layer Actions
    form and the orchestrator read — so there is never a second source of truth
    for what an agent runs on.
    """
    from app.extensions import db
    from app.models.agent_context import AgentContext

    blend = SuggestionBlend.query.get(blend_id)
    if blend is None:
        raise PeerError('Recommendation not found.', 404)
    share = AgentShare.query.get(blend.share_id)
    if share is None or share.owner_user_id != owner_user_id:
        raise PeerError('Recommendation not found.', 404)
    if decision not in (DECISION_ACCEPTED, DECISION_KEPT):
        raise PeerError('Decide to accept it or keep yours.', 400)
    if not blend.is_pending:
        raise PeerError('You already decided on this one.', 409)

    gate = gate_state(owner_user_id)
    if not gate['unlocked']:
        raise PeerError(gate.get('reason') or 'Your recommendations are locked.',
                        403, code='reciprocity')

    applied = None
    if decision == DECISION_ACCEPTED:
        value = blend.recommendation
        if value is None:
            raise PeerError('This recommendation has no value to apply.', 409)
        if blend.param_kind == KIND_NUMERIC:
            # Store what a human would type, not a float artefact: the field is
            # a free-text input and the orchestrator reads it as a string.
            number = float(value)
            value = int(round(number)) if abs(number - round(number)) < 1e-9 \
                else round(number, 2)
        AgentContext.upsert(share.simulation_id, share.layer_number,
                            blend.param_key, value)
        applied = value

    blend.owner_decision = decision
    blend.decided_at = datetime.utcnow()
    db.session.commit()

    if applied is not None:
        try:
            from app.services import world_service
            world_service.invalidate(share.simulation_id)
        except Exception:
            pass
    invalidate(share.simulation_id)

    return {'blend': blend.to_dict(), 'applied': applied,
            'simulation_id': share.simulation_id}


# ---------------------------------------------------------------------------
# Peer signal for the World overlay (PRD §9)
# ---------------------------------------------------------------------------

def peer_signal(simulation_id: str, owner_user_id: str, use_cache=True) -> dict:
    """Per-agent pending counts and open blends — what the Peers mode draws.

    Off-by-default in the UI, so this endpoint is only hit when the owner asks
    for it; the base World never pays for it.
    """
    ttl = int(setting('peer_signal_cache_ttl_seconds'))
    if use_cache and ttl > 0:
        hit = _CACHE.get(simulation_id)
        if hit and (time.time() - hit['at']) < ttl:
            return hit['model']

    model = _build_signal(simulation_id, owner_user_id)
    if ttl > 0:
        _CACHE[simulation_id] = {'at': time.time(), 'model': model}
    return model


def _build_signal(simulation_id: str, owner_user_id: str) -> dict:
    from app.models.profile import UserProfile
    from app.models.user import User

    if not is_enabled():
        return {'enabled': False, 'agents': {}, 'blends': [], 'circle': [],
                'nudge': None}

    shares = AgentShare.query.filter_by(simulation_id=simulation_id).all()
    live = [s for s in shares if s.is_shareable]
    share_ids = [s.id for s in live]

    gate = gate_state(owner_user_id)
    agents: dict = {}
    for share in live:
        agents[share.agent_key] = {
            'share_id': share.id, 'layer': share.layer_number,
            'shared': True, 'pending': 0, 'blends': 0, 'contributors': 0,
        }

    contributors: set = set()
    if share_ids:
        pending = ParameterSuggestion.query.filter(
            ParameterSuggestion.share_id.in_(share_ids),
            ParameterSuggestion.status == SUGGESTION_PENDING,
            ParameterSuggestion.guardrail_ok == True,  # noqa: E712
        ).all()
        per_agent_peers: dict = {}
        for row in pending:
            entry = agents.get(row.agent_key)
            if entry is None:
                continue
            entry['pending'] += 1
            per_agent_peers.setdefault(row.agent_key, set()).add(row.peer_user_id)
            contributors.add(row.peer_user_id)
        for key, peer_set in per_agent_peers.items():
            agents[key]['contributors'] = len(peer_set)

    blends = []
    if share_ids:
        rows = SuggestionBlend.query.filter(
            SuggestionBlend.share_id.in_(share_ids),
            SuggestionBlend.owner_decision == DECISION_PENDING,
        ).order_by(SuggestionBlend.created_at.desc()).all()

        # "Who contributed this cycle" (PRD §9.2) is not only who has something
        # still waiting: a peer whose proposal has already been folded into a
        # recommendation the owner has not answered yet contributed most of all.
        # They drop off the list once the owner decides, which is what makes it
        # read as this cycle rather than all time.
        if rows:
            open_params = {(r.share_id, r.param_key) for r in rows}
            for row in ParameterSuggestion.query.filter(
                ParameterSuggestion.share_id.in_({r.share_id for r in rows}),
                ParameterSuggestion.status == SUGGESTION_BLENDED,
                ParameterSuggestion.guardrail_ok == True,  # noqa: E712
            ).all():
                if (row.share_id, row.param_key) in open_params:
                    contributors.add(row.peer_user_id)

        for row in rows:
            if row.agent_key in agents:
                agents[row.agent_key]['blends'] += 1
            payload = row.to_dict()
            payload['param_label'] = _param_label(row.agent_key, row.param_key)
            payload['agent_label'] = _agent_label(row.agent_key)
            payload['unit'] = param_unit(_param_spec(row.agent_key,
                                                     row.param_key) or {})
            if not gate['unlocked']:
                # Gated: the owner sees that a recommendation exists and what it
                # would take to unlock it, not the number itself.
                for hidden in ('recommendation', 'posterior', 'range_low',
                               'range_high', 'rationale', 'owner_prior'):
                    payload[hidden] = None
                payload['locked'] = True
            blends.append(payload)

    circle_rows = PeerInvite.query.filter_by(
        owner_user_id=owner_user_id, status=INVITE_ACCEPTED).all()
    peer_ids = [r.peer_user_id for r in circle_rows if r.peer_user_id]
    names, avatars = {}, {}
    if peer_ids:
        for user in User.query.filter(User.id.in_(peer_ids)).all():
            names[user.id] = user.full_name or user.email
        for profile in UserProfile.query.filter(
                UserProfile.user_id.in_(peer_ids)).all():
            avatars[profile.user_id] = profile.avatar_path or ''
            if profile.display_name:
                names[profile.user_id] = profile.display_name

    circle_payload = [
        {'display_name': names.get(pid) or 'Peer',
         'avatar_path': avatars.get(pid, ''),
         'contributed': pid in contributors}
        for pid in peer_ids
    ]

    return {
        'enabled': True,
        'simulation_id': simulation_id,
        'max_layer': int(setting('peer_max_layer')),
        'min_suggestions': int(setting('peer_min_suggestions')),
        'agents': agents,
        'blends': blends,
        'circle': circle_payload,
        'circle_size': len(peer_ids),
        'reciprocity': gate,
        'pending_total': sum(a['pending'] for a in agents.values()),
        'nudge': _nudge(agents, blends, gate),
        'stream': {
            'lifetime_seconds': int(setting('peer_stream_seconds')),
            'poll_seconds': int(setting('peer_stream_poll_seconds')),
        },
    }


def _agent_label(agent_key: str) -> str:
    meta = _agent_meta(agent_key) or {}
    return meta.get('label') or agent_key


def _param_label(agent_key: str, param_key: str) -> str:
    field = _param_spec(agent_key, param_key)
    return (field or {}).get('label') or param_key


def _nudge(agents: dict, blends: list, gate: dict) -> dict | None:
    """The Layer 6 orchestrator ranking peer signal as an input (PRD §9.2).

    An open recommendation that would move a value outranks a pile of proposals
    that has not resolved yet, because only the first is something the owner can
    act on right now.
    """
    for blend in blends:
        if blend.get('locked'):
            continue
        prior = blend.get('owner_prior')
        value = blend.get('recommendation')
        try:
            prior_n, value_n = float(prior), float(value)
        except (TypeError, ValueError):
            continue
        if prior_n <= 0:
            continue
        delta = (value_n - prior_n) / abs(prior_n)
        if abs(delta) < 0.05:
            continue
        return {
            'agent_key': blend['agent_key'],
            'kind': 'blend',
            'text': '%d peers think your %s is %s — review?'
                    % (blend.get('n_suggestions') or 0,
                       blend.get('param_label') or blend['param_key'],
                       'low' if delta > 0 else 'high'),
        }

    if gate.get('enabled') and not gate.get('unlocked'):
        return {'agent_key': None, 'kind': 'reciprocity', 'text': gate.get('reason')}

    best_key, best = None, 0
    for key, entry in agents.items():
        if entry['pending'] > best:
            best_key, best = key, entry['pending']
    if best_key and best:
        return {
            'agent_key': best_key, 'kind': 'pending',
            'text': '%d proposal%s waiting on %s — they blend at the next cycle.'
                    % (best, '' if best == 1 else 's', _agent_label(best_key)),
        }
    return None


def pending_count(simulation_id: str) -> int:
    """Cheap read for the live stream: how many proposals are in the pool."""
    share_ids = [s.id for s in AgentShare.query.filter_by(
        simulation_id=simulation_id, is_shareable=True).all()]
    if not share_ids:
        return 0
    return ParameterSuggestion.query.filter(
        ParameterSuggestion.share_id.in_(share_ids),
        ParameterSuggestion.status == SUGGESTION_PENDING,
        ParameterSuggestion.guardrail_ok == True,  # noqa: E712
    ).count()


def proposals_for_param(share_id: str, param_key: str, owner_user_id: str) -> list:
    """The individual proposals on one parameter.

    What the owner sees below the minimum threshold, when there is no blended
    recommendation yet (PRD §7). Peer identity is withheld here: the owner sees
    their circle's aggregate contribution, not who guessed what — the record of
    that lives in the table for audit, not in the review UI.
    """
    share = AgentShare.query.get(share_id)
    if share is None or share.owner_user_id != owner_user_id:
        raise PeerError('Not found.', 404)
    rows = ParameterSuggestion.query.filter_by(
        share_id=share_id, param_key=param_key, guardrail_ok=True,
    ).filter(ParameterSuggestion.status.in_(
        [SUGGESTION_PENDING, SUGGESTION_BLENDED])
    ).order_by(ParameterSuggestion.created_at.asc()).all()
    return [r.to_dict() for r in rows]


# ---------------------------------------------------------------------------
# Notifications (best-effort: never break a write for a message)
# ---------------------------------------------------------------------------

def _base_url() -> str:
    from flask import current_app
    return (current_app.config.get('BASE_URL')
            or 'https://simulacrumai.io').rstrip('/')


def _owner_name(user_id: str) -> str:
    from app.models.profile import UserProfile
    from app.models.user import User
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if profile and profile.display_name:
        return profile.display_name
    user = User.query.get(user_id)
    return (user.full_name if user else None) or 'A Simulacrum member'


def _owner_slug(user_id: str):
    from app.models.profile import UserProfile
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    return profile.username if profile else None


def _notify_invited(invite: PeerInvite) -> None:
    """Tell the invited peer. In-app when they have an account, email otherwise."""
    from app.services import notification_service

    owner_name = _owner_name(invite.owner_user_id)
    slug = _owner_slug(invite.owner_user_id)
    url = '%s/u/%s#peer-calibration' % (_base_url(), slug) if slug else _base_url()

    try:
        if invite.peer_user_id:
            notification_service.send_notification(
                user_id=invite.peer_user_id,
                notification_type='peer_invite',
                title='%s invited you to calibrate their engine' % owner_name,
                body=('They have shared some of their Layer 1 and Layer 2 agents '
                      'and would like your view on the numbers. You will propose '
                      'blind — you never see their current value.'),
                cta_url=url, cta_label='See what they shared →',
            )
            return
        if invite.invite_email:
            from app.services.email_service import send_peer_invite_email
            send_peer_invite_email(invite.invite_email, owner_name, url)
    except Exception as exc:
        logger.warning('peer invite notification failed: %s', exc)


def _notify_owner_request(invite: PeerInvite) -> None:
    from app.services import notification_service
    try:
        notification_service.send_notification(
            user_id=invite.owner_user_id,
            notification_type='peer_request',
            title='%s asked to join your calibration circle'
                  % (_owner_name(invite.peer_user_id) if invite.peer_user_id
                     else 'Someone'),
            body=(invite.message or 'They would like to propose values on the '
                                    'agents you have shared.'),
            cta_url='%s/settings/visibility#peer-calibration' % _base_url(),
            cta_label='Approve or decline →',
        )
    except Exception as exc:
        logger.warning('peer request notification failed: %s', exc)


def _notify_peer_approved(invite: PeerInvite) -> None:
    from app.services import notification_service
    if not invite.peer_user_id:
        return
    slug = _owner_slug(invite.owner_user_id)
    try:
        notification_service.send_notification(
            user_id=invite.peer_user_id,
            notification_type='peer_approved',
            title='You are in %s\'s calibration circle' % _owner_name(invite.owner_user_id),
            body='You can now propose values on the agents they have shared.',
            cta_url=('%s/u/%s#peer-calibration' % (_base_url(), slug)
                     if slug else _base_url()),
            cta_label='Propose a value →',
        )
    except Exception as exc:
        logger.warning('peer approval notification failed: %s', exc)


def _notify_owner_blends(owner_user_id: str, simulation_id: str,
                         summary: dict) -> None:
    from app.services import notification_service
    count = summary['blends']
    first = summary['params'][0] if summary['params'] else {}
    label = _param_label(first.get('agent_key', ''), first.get('param_key', ''))
    try:
        notification_service.send_notification(
            user_id=owner_user_id,
            notification_type='peer_blend',
            title=('Your peers have a recommendation on %s' % label if count == 1
                   else '%d peer recommendations are ready' % count),
            body=('Your value, the peer range and the blended recommendation are '
                  'side by side in the Peers view. Nothing changes until you '
                  'accept it.'),
            cta_url='%s/simulations/%s/gcc?tab=network&peers=1'
                    % (_base_url(), simulation_id),
            cta_label='Review in the World →',
            simulation_id=simulation_id,
        )
    except Exception as exc:
        logger.warning('peer blend notification failed: %s', exc)


# ---------------------------------------------------------------------------
# Success metrics (PRD §16)
# ---------------------------------------------------------------------------

def metrics() -> dict:
    """The §16 dashboard numbers, computed from the tables that own them."""
    from sqlalchemy import func

    from app.extensions import db

    owners_sharing = db.session.query(
        func.count(func.distinct(AgentShare.owner_user_id))
    ).filter(AgentShare.is_shareable == True).scalar() or 0  # noqa: E712
    shared_agents = AgentShare.query.filter_by(is_shareable=True).count()
    suggestions = ParameterSuggestion.query.filter_by(guardrail_ok=True).count()
    circles = db.session.query(
        func.count(func.distinct(PeerInvite.owner_user_id))
    ).filter(PeerInvite.status == INVITE_ACCEPTED).scalar() or 0
    memberships = PeerInvite.query.filter_by(status=INVITE_ACCEPTED).count()

    decided = SuggestionBlend.query.filter(
        SuggestionBlend.owner_decision != DECISION_PENDING).count()
    accepted = SuggestionBlend.query.filter_by(
        owner_decision=DECISION_ACCEPTED).count()

    unlocked = ReciprocityLedger.query.filter_by(unlocked=True).count()
    contributors = db.session.query(
        func.count(func.distinct(ParameterSuggestion.peer_user_id))
    ).scalar() or 0

    return {
        'owners_sharing': int(owners_sharing),
        'shared_agents': shared_agents,
        'suggestions': suggestions,
        'suggestions_per_shared_agent': round(suggestions / shared_agents, 2)
                                        if shared_agents else 0.0,
        'blends': SuggestionBlend.query.count(),
        'accept_rate': round(accepted / decided * 100.0, 1) if decided else None,
        'circles': int(circles),
        'avg_circle_size': round(memberships / circles, 2) if circles else 0.0,
        'contributors': int(contributors),
        'reciprocity_unlocked': unlocked,
        'reciprocity_conversion': round(unlocked / contributors * 100.0, 1)
                                  if contributors else None,
    }
