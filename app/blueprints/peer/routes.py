"""Peer Calibration routes — PRD-SIM-PEER-001 §11.

Sharing (owner):
  GET/PUT  /api/simulations/<sim_id>/shares                 which L1/L2 agents are shareable

The circle:
  GET      /api/peers/circle                                accepted, invited, requests
  POST     /api/peers/invite                                invite-out
  POST     /api/peers/request                               request-in (from a bio page)
  POST     /api/peers/<invite_id>/approve                   owner approves a request
  POST     /api/peers/<invite_id>/decline                   owner declines
  POST     /api/peers/<invite_id>/accept                    the invited peer accepts
  DELETE   /api/peers/<invite_id>                           owner removes a peer
  GET      /api/peers/my-invites                            circles inviting me

Proposing (invited peer):
  GET      /api/u/<handle>/shared-agents                    the blind payload
  POST     /api/peers/suggest                               submit by share_id
  POST     /api/simulations/<sim_id>/agents/<key>/suggest    the same, by agent

Reviewing (owner):
  GET      /api/simulations/<sim_id>/world/peer-signal      the World overlay read model
  GET      /api/simulations/<sim_id>/peer-stream            live badge counts (SSE)
  GET      /api/simulations/<sim_id>/shares/<id>/proposals  individual proposals
  POST     /api/blends/<blend_id>/decision                  Accept / Keep mine

Admin:
  GET/PUT  /api/admin/peer/settings                         every threshold
  GET      /api/admin/peer/metrics                          the §16 numbers

Two invariants the routes enforce rather than trust the client with: a peer is
authorised by circle membership and never by knowing an id, and no response to
a peer ever carries an owner's value.
"""
from __future__ import annotations

import logging
import time
import traceback
from functools import wraps

from flask import Response, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from app.blueprints.peer import peer_bp
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.peer import AgentShare
from app.models.profile import UserProfile
from app.models.simulation import Simulation
from app.services import peer_service as peers

logger = logging.getLogger(__name__)

_OWNED_TABLES = ('agent_shares', 'peer_invites', 'parameter_suggestions',
                 'suggestion_blends', 'reciprocity_ledger')
_MIGRATION = 'sql/051_peer_calibration.sql'


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def _missing_table(exc) -> str:
    """One of our tables the database says is absent, or ''."""
    text = str(getattr(exc, 'orig', exc)).lower()
    if "doesn't exist" not in text and 'no such table' not in text:
        return ''
    for table in _OWNED_TABLES:
        if table in text:
            return table
    return ''


@peer_bp.errorhandler(Exception)
def _peer_failed(exc):
    """A refusal the user can act on, and a setup error that names the fix."""
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, peers.PeerError):
        return jsonify({'error': exc.message, 'code': exc.code}), exc.status
    if isinstance(exc, HTTPException):
        return exc

    table = _missing_table(exc)
    if table:
        logger.error('Peer Calibration is missing table %s — %s has not been '
                     'run on this database.', table, _MIGRATION)
        return jsonify({
            'error': ('Peer Calibration is not set up on this database yet: the '
                      'table "%s" is missing. Run %s, then reload.'
                      % (table, _MIGRATION)),
            'setup_required': True,
            'missing_table': table,
            'migration': _MIGRATION,
        }), 503

    logger.error('Peer Calibration error:\n%s', traceback.format_exc())
    try:
        db.session.rollback()
    except Exception:
        pass
    return jsonify({'error': 'Internal server error'}), 500


def _own_sim(sim_id: str) -> Simulation:
    return Simulation.query.filter_by(
        id=sim_id, user_id=current_user.id).first_or_404()


# ---------------------------------------------------------------------------
# Sharing — Settings → Simulation Visibility (PRD §5)
# ---------------------------------------------------------------------------

@peer_bp.route('/api/simulations/<sim_id>/shares', methods=['GET'])
@login_required
def get_shares(sim_id):
    """Every agent eligible to be shared, and whether it currently is."""
    _own_sim(sim_id)
    return jsonify({
        'enabled': peers.is_enabled(),
        'max_layer': int(peers.setting('peer_max_layer')),
        'min_suggestions': int(peers.setting('peer_min_suggestions')),
        'agents': peers.shareable_agents(sim_id),
        'circle': peers.circle(current_user.id),
        'reciprocity': peers.gate_state(current_user.id),
    }), 200


@peer_bp.route('/api/simulations/<sim_id>/shares', methods=['PUT'])
@login_required
def put_shares(sim_id):
    """Set one agent's shareable flag, or several at once.

    Body: {"agent_key": "rate_card", "is_shareable": true}
       or {"shares": {"rate_card": true, "booking_page": false}}
    """
    _own_sim(sim_id)
    data = request.get_json(silent=True) or {}

    updates = data.get('shares')
    if not isinstance(updates, dict):
        agent_key = data.get('agent_key')
        if not agent_key:
            return jsonify({'error': 'agent_key is required'}), 400
        updates = {agent_key: data.get('is_shareable', True)}

    changed = []
    for agent_key, flag in updates.items():
        share = peers.set_share(sim_id, current_user.id, agent_key,
                                flag not in (False, 'false', '0', 0))
        changed.append(share.to_dict())

    return jsonify({'changed': changed,
                    'agents': peers.shareable_agents(sim_id)}), 200


# ---------------------------------------------------------------------------
# The circle (PRD §5)
# ---------------------------------------------------------------------------

@peer_bp.route('/api/peers/circle', methods=['GET'])
@login_required
def get_circle():
    return jsonify({
        'circle': peers.circle(current_user.id),
        'reciprocity': peers.gate_state(current_user.id),
    }), 200


@peer_bp.route('/api/peers/invite', methods=['POST'])
@login_required
def invite():
    data = request.get_json(silent=True) or {}
    handle = str(data.get('handle') or '').strip().lstrip('@')

    peer_user_id, label = None, str(data.get('name') or '').strip() or None
    if handle:
        profile = UserProfile.query.filter_by(username=handle.lower()).first()
        if profile is None:
            return jsonify({'error': 'No SimiSI member with that handle.'}), 404
        peer_user_id = profile.user_id
        label = label or profile.display_name

    row = peers.invite_peer(current_user.id, email=data.get('email'),
                            peer_user_id=peer_user_id, label=label)
    return jsonify({'invite': row.to_dict(),
                    'circle': peers.circle(current_user.id)}), 201


@peer_bp.route('/api/peers/request', methods=['POST'])
@login_required
def request_join():
    """A bio-page visitor asks to join an owner's circle. Grants nothing."""
    data = request.get_json(silent=True) or {}
    slug = str(data.get('handle') or data.get('slug') or '').strip().lower()
    if not slug:
        return jsonify({'error': 'handle is required'}), 400

    profile = UserProfile.query.filter_by(username=slug).first()
    if profile is None:
        return jsonify({'error': 'Page not found.'}), 404

    row = peers.request_to_join(profile.user_id, current_user.id,
                                message=data.get('message'))
    return jsonify({'request': row.to_dict()}), 201


@peer_bp.route('/api/peers/<invite_id>/approve', methods=['POST'])
@login_required
def approve(invite_id):
    row = peers.approve_request(invite_id, current_user.id)
    return jsonify({'invite': row.to_dict(),
                    'circle': peers.circle(current_user.id)}), 200


@peer_bp.route('/api/peers/<invite_id>/decline', methods=['POST'])
@login_required
def decline(invite_id):
    row = peers.decline_request(invite_id, current_user.id)
    return jsonify({'invite': row.to_dict(),
                    'circle': peers.circle(current_user.id)}), 200


@peer_bp.route('/api/peers/<invite_id>/accept', methods=['POST'])
@login_required
def accept(invite_id):
    """The invited peer accepts. Only they can — an owner cannot self-accept."""
    row = peers.accept_invite(invite_id, current_user.id)
    return jsonify({'invite': row.to_dict()}), 200


@peer_bp.route('/api/peers/<invite_id>', methods=['DELETE'])
@login_required
def remove(invite_id):
    row = peers.remove_peer(invite_id, current_user.id)
    return jsonify({'invite': row.to_dict(),
                    'circle': peers.circle(current_user.id)}), 200


@peer_bp.route('/api/peers/my-invites', methods=['GET'])
@login_required
def my_invites():
    return jsonify({'invites': peers.invites_for_peer(current_user.id)}), 200


# ---------------------------------------------------------------------------
# Proposing (PRD §4 step 2, §6)
# ---------------------------------------------------------------------------

@peer_bp.route('/api/u/<handle>/shared-agents', methods=['GET'])
def shared_agents(handle):
    """The bio-page entry point: what an invited peer may propose on.

    Open to an anonymous visitor on purpose — the answer for anyone who is not
    in the circle is only "a circle exists", which is what makes "request to
    join" a meaningful button rather than a shot in the dark. No owner value
    crosses this boundary in any branch.
    """
    profile = UserProfile.query.filter_by(username=handle.lower()).first()
    if profile is None:
        return jsonify({'error': 'Page not found.'}), 404

    viewer = current_user.id if current_user.is_authenticated else None
    payload = peers.peer_view(profile.user_id, viewer)
    payload['authenticated'] = bool(viewer)
    payload['is_owner'] = bool(viewer and viewer == profile.user_id)
    payload['handle'] = profile.username
    return jsonify(payload), 200


def _suggest(share_id):
    data = request.get_json(silent=True) or {}
    param_key = str(data.get('param_key') or '').strip()
    if not param_key:
        return jsonify({'error': 'param_key is required'}), 400
    result = peers.submit_suggestion(
        share_id, current_user.id, param_key,
        data.get('value'), note=data.get('note'),
    )
    return jsonify(result), 201


@peer_bp.route('/api/peers/suggest', methods=['POST'])
@login_required
def suggest_by_share():
    """Submit a blind proposal against an opaque share_id.

    The path the bio page uses: a peer is handed share ids, never simulation
    ids, so proposing never requires knowing anything about the owner's engine
    beyond what was shared with them.
    """
    data = request.get_json(silent=True) or {}
    share_id = str(data.get('share_id') or '').strip()
    if not share_id:
        return jsonify({'error': 'share_id is required'}), 400
    return _suggest(share_id)


@peer_bp.route('/api/simulations/<sim_id>/agents/<agent_key>/suggest',
               methods=['POST'])
@login_required
def suggest_by_agent(sim_id, agent_key):
    """The §11 path. Authorised by circle membership, not by ownership."""
    from app.services.agent_registry import resolve_alias

    share = AgentShare.query.filter_by(
        simulation_id=sim_id, agent_key=resolve_alias(agent_key)).first()
    if share is None or not share.is_shareable:
        return jsonify({'error': 'That agent is not shared.'}), 404
    return _suggest(share.id)


# ---------------------------------------------------------------------------
# Reviewing (PRD §9)
# ---------------------------------------------------------------------------

@peer_bp.route('/api/simulations/<sim_id>/world/peer-signal', methods=['GET'])
@login_required
def world_peer_signal(sim_id):
    """Per-agent pending counts, open blends, the circle and the nudge."""
    _own_sim(sim_id)
    refresh = request.args.get('refresh') in ('1', 'true', 'yes')
    return jsonify(peers.peer_signal(sim_id, current_user.id,
                                     use_cache=not refresh)), 200


@peer_bp.route('/api/simulations/<sim_id>/shares/<share_id>/proposals',
               methods=['GET'])
@login_required
def share_proposals(sim_id, share_id):
    """The individual proposals on one parameter — what the owner sees below
    the minimum threshold, when there is no blend yet."""
    _own_sim(sim_id)
    param_key = str(request.args.get('param_key') or '').strip()
    if not param_key:
        return jsonify({'error': 'param_key is required'}), 400
    return jsonify({
        'proposals': peers.proposals_for_param(share_id, param_key,
                                               current_user.id),
        'min_suggestions': int(peers.setting('peer_min_suggestions')),
    }), 200


@peer_bp.route('/api/simulations/<sim_id>/peer-stream', methods=['GET'])
@login_required
def peer_stream(sim_id):
    """Live badge and count increments as proposals arrive (PRD §9.1).

    Bounded on purpose, the same trade the observability console and the
    simulation poller make: a long-lived stream holds a Passenger worker for its
    whole life, so this one closes itself and the client reconnects. The
    presence is real-time; the blend still resolves only at the next cycle.
    """
    _own_sim(sim_id)
    from app.blueprints.simulations.sse import sse_event, sse_keepalive

    lifetime = int(peers.setting('peer_stream_seconds'))
    tick = max(1, int(peers.setting('peer_stream_poll_seconds')))
    deadline = time.monotonic() + lifetime
    owner_id = current_user.id

    def generate():
        last = None
        while time.monotonic() < deadline:
            try:
                total = peers.pending_count(sim_id)
                if total != last:
                    last = total
                    model = peers.peer_signal(sim_id, owner_id, use_cache=False)
                    yield sse_event('peer_signal', {
                        'pending_total': total,
                        'agents': model['agents'],
                        'nudge': model['nudge'],
                        'circle': model['circle'],
                    })
            except Exception as exc:
                logger.debug('peer stream tick failed: %s', exc)
            try:
                yield sse_keepalive()
            except GeneratorExit:
                return
            time.sleep(tick)
        yield sse_event('reconnect', {})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                 'Connection': 'keep-alive'},
    )


@peer_bp.route('/api/blends/<blend_id>/decision', methods=['POST'])
@login_required
def blend_decision(blend_id):
    """Accept the recommendation, or keep your own. The owner decides (PRD §4)."""
    data = request.get_json(silent=True) or {}
    decision = str(data.get('decision') or '').strip().lower()
    result = peers.decide(blend_id, current_user.id, decision)
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Admin — thresholds and metrics
# ---------------------------------------------------------------------------

@peer_bp.route('/api/admin/peer/settings', methods=['GET'])
@login_required
@admin_required
def get_peer_settings():
    return jsonify({'settings': peers.all_settings()}), 200


@peer_bp.route('/api/admin/peer/settings', methods=['PUT'])
@login_required
@admin_required
def put_peer_settings():
    data = request.get_json(silent=True) or {}
    updates = data.get('settings')
    if not isinstance(updates, dict):
        if 'key' not in data:
            return jsonify({'error': 'settings object or key/value is required'}), 400
        updates = {data['key']: data.get('value')}

    saved = {}
    for key, value in updates.items():
        try:
            saved[key] = peers.save_setting(key, value, updated_by=current_user.id)
        except ValueError as exc:
            db.session.rollback()
            return jsonify({'error': str(exc), 'key': key}), 400

    AuditLog.log('peer_settings_updated', user_id=current_user.id,
                 metadata={'keys': sorted(saved.keys())})
    db.session.commit()
    return jsonify({'saved': saved, 'settings': peers.all_settings()}), 200


@peer_bp.route('/api/admin/peer/metrics', methods=['GET'])
@login_required
@admin_required
def get_peer_metrics():
    return jsonify(peers.metrics()), 200
