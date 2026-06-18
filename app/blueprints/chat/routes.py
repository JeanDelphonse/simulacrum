from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from app.blueprints.chat import chat_bp
from app.extensions import db
import logging as _log

_logger = _log.getLogger(__name__)


def _check_sim(sim_id: str):
    from app.models.simulation import Simulation
    return Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()


def _active_session_id(sim_id: str, user_id: str):
    """Return the most recent active session_id for this sim+user, or None."""
    from app.models.chat import SimulationChatMessage
    latest = (
        SimulationChatMessage.query
        .filter_by(simulation_id=sim_id, user_id=user_id, is_archived=False)
        .order_by(SimulationChatMessage.created_at.desc())
        .first()
    )
    return latest.session_id if latest else None


@chat_bp.route('/<sim_id>/chat', methods=['GET'])
@login_required
def get_history(sim_id: str):
    _check_sim(sim_id)
    from app.models.chat import SimulationChatMessage

    session_id = request.args.get('session_id') or _active_session_id(sim_id, current_user.id)

    if not session_id:
        return jsonify({'session_id': None, 'messages': []})

    msgs = (
        SimulationChatMessage.query
        .filter_by(simulation_id=sim_id, user_id=current_user.id,
                   session_id=session_id, is_archived=False)
        .order_by(SimulationChatMessage.created_at.asc())
        .limit(50)
        .all()
    )
    return jsonify({'session_id': session_id, 'messages': [m.to_dict() for m in msgs]})


@chat_bp.route('/<sim_id>/chat', methods=['POST'])
@login_required
def send_message(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    user_message = (body.get('message') or '').strip()
    session_id   = (body.get('session_id') or '').strip() or None

    if not user_message:
        return jsonify({'error': 'message required'}), 400

    from app.models.chat import SimulationChatMessage
    from utils.id_gen import generate_id
    from app.services.chat_service import chat_response
    import logging as _log

    # Resolve or create session
    if not session_id:
        session_id = _active_session_id(sim_id, current_user.id) or generate_id()

    try:
        user_msg = SimulationChatMessage(
            id=generate_id(), session_id=session_id,
            simulation_id=sim_id, user_id=current_user.id,
            role='user', content=user_message,
        )
        db.session.add(user_msg)
        db.session.commit()
    except Exception as _e:
        db.session.rollback()
        _log.getLogger(__name__).error('Chat user-msg save failed: %s', _e)
        return jsonify({'error': f'DB error (migration needed?): {_e}'}), 500

    full_name = current_user.full_name or ''
    display_name = (full_name.strip().split()[0]
                    if full_name.strip()
                    else (current_user.email or 'there').split('@')[0])

    try:
        result = chat_response(sim_id, current_user.id, user_message, display_name,
                               session_id=session_id)
        return jsonify(result)
    except Exception as _e:
        _log.getLogger(__name__).error('Chat response failed: %s', _e, exc_info=True)
        return jsonify({'error': str(_e)}), 500


@chat_bp.route('/<sim_id>/chat/confirm', methods=['POST'])
@login_required
def confirm_action(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    message_id  = body.get('message_id')
    action_type = body.get('action_type')
    parameters  = body.get('parameters', {})

    if not message_id or not action_type:
        return jsonify({'error': 'message_id and action_type required'}), 400

    from app.services.chat_service import execute_chat_action
    try:
        result = execute_chat_action(sim_id, current_user.id, message_id, action_type, parameters)
        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@chat_bp.route('/<sim_id>/chat/cancel', methods=['POST'])
@login_required
def cancel_action(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    message_id = body.get('message_id')
    if not message_id:
        return jsonify({'error': 'message_id required'}), 400

    from app.models.chat import SimulationChatMessage
    msg = SimulationChatMessage.query.filter_by(
        id=message_id, simulation_id=sim_id, user_id=current_user.id
    ).first_or_404()
    msg.action_status = SimulationChatMessage.ACTION_CANCELLED
    db.session.commit()
    return jsonify({'ok': True})


@chat_bp.route('/<sim_id>/chat', methods=['DELETE'])
@login_required
def clear_history(sim_id: str):
    _check_sim(sim_id)
    body = request.get_json(silent=True) or {}
    session_id = (body.get('session_id') or '').strip() or None

    from app.models.chat import SimulationChatMessage
    q = SimulationChatMessage.query.filter_by(
        simulation_id=sim_id, user_id=current_user.id, is_archived=False
    )
    if session_id:
        q = q.filter_by(session_id=session_id)
    q.update({'is_archived': True})
    db.session.commit()
    return jsonify({'ok': True})


# ── Simi v1.2 routes (SIM-PRD-CHAT-001 v1.2) ─────────────────────────────────

def _get_or_create_conversation(sim_id: str, user_id: str):
    """Return the active SimiConversation for sim+user, creating one if none exists."""
    from app.models.chat import SimiConversation
    from utils.id_gen import generate_id
    from datetime import datetime
    conv = (
        SimiConversation.query
        .filter_by(simulation_id=sim_id, user_id=user_id)
        .order_by(SimiConversation.created_at.desc())
        .first()
    )
    if not conv:
        conv = SimiConversation(
            id=generate_id(), simulation_id=sim_id, user_id=user_id,
            created_at=datetime.utcnow(), total_tokens=0,
        )
        db.session.add(conv)
        db.session.commit()
    return conv


def _user_first_name() -> str:
    full = (current_user.full_name or '').strip()
    if full:
        return full.split()[0]
    return (current_user.email or 'there').split('@')[0]


@chat_bp.route('/<sim_id>/simi/open', methods=['GET'])
@login_required
def simi_open(sim_id: str):
    """
    Return the active conversation, opening message, and suggested question pills.
    Creates a conversation if none exists.
    GET /api/simulations/<sim_id>/simi/open?tab=journey
    """
    _check_sim(sim_id)
    current_tab = request.args.get('tab', 'journey')

    from app.models.chat import SimiConversation, SimiMessage
    from app.models.agent_action import AgentAction
    from app.services.simi_service import (
        build_simi_context, build_opening_message, get_suggestions
    )

    # Count prior conversations to detect first-time user
    prior_conv_count = SimiConversation.query.filter_by(
        simulation_id=sim_id, user_id=current_user.id
    ).count()

    conv = _get_or_create_conversation(sim_id, current_user.id)

    # Load last 30 messages for history
    msgs = SimiMessage.query.filter_by(conversation_id=conv.id).order_by(
        SimiMessage.created_at.asc()
    ).limit(30).all()

    # Detect first-time (no prior conversations existed before this one)
    # prior_conv_count was measured before _get_or_create, so 0 means this is the first ever
    is_first_time = (prior_conv_count == 0)

    # Detect empty state: no agents dispatched yet
    try:
        is_empty_state = AgentAction.query.filter_by(simulation_id=sim_id).count() == 0
    except Exception:
        is_empty_state = False

    # Build opening message if this is a fresh conversation
    opening = None
    suggestions = get_suggestions(current_tab)
    if not msgs:
        try:
            ctx     = build_simi_context(sim_id, current_user.id)
            opening = build_opening_message(ctx, current_tab, _user_first_name())
        except Exception as e:
            _logger.error('Simi opening message error: %s', e)
            opening = f"Hi {_user_first_name()}! I'm Simi, your simulation co-pilot. What can I help you with?"

    return jsonify({
        'conversation_id': conv.id,
        'total_tokens':    conv.total_tokens or 0,
        'opening':         opening,
        'suggestions':     suggestions,
        'history':         [m.to_dict() for m in msgs],
        'is_first_time':   is_first_time,
        'is_empty_state':  is_empty_state,
    })


@chat_bp.route('/<sim_id>/simi/message', methods=['POST'])
@login_required
def simi_message(sim_id: str):
    """
    Send a message to Simi and get a response.
    POST /api/simulations/<sim_id>/simi/message
    Body: {conversation_id, message, current_tab}
    """
    _check_sim(sim_id)
    body       = request.get_json(silent=True) or {}
    user_msg   = (body.get('message') or '').strip()
    conv_id    = (body.get('conversation_id') or '').strip()
    current_tab = (body.get('current_tab') or 'journey').strip()

    if not user_msg:
        return jsonify({'error': 'message required'}), 400

    # Resolve conversation
    from app.models.chat import SimiConversation, SimiMessage
    from utils.id_gen import generate_id
    from datetime import datetime

    if conv_id:
        conv = SimiConversation.query.filter_by(
            id=conv_id, simulation_id=sim_id, user_id=current_user.id
        ).first()
        if not conv:
            conv = _get_or_create_conversation(sim_id, current_user.id)
    else:
        conv = _get_or_create_conversation(sim_id, current_user.id)

    # Persist user message
    try:
        user_row = SimiMessage(
            id=generate_id(), conversation_id=conv.id,
            role='user', content=user_msg, created_at=datetime.utcnow(),
        )
        db.session.add(user_row)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        _logger.error('Simi user msg save failed: %s', e)
        return jsonify({'error': f'DB error: {e}'}), 500

    # Call service
    from app.services.simi_service import simi_chat
    try:
        result = simi_chat(
            sim_id=sim_id,
            user_id=current_user.id,
            conv_id=conv.id,
            user_message=user_msg,
            current_tab=current_tab,
            user_first_name=_user_first_name(),
        )
        return jsonify({**result, 'conversation_id': conv.id})
    except Exception as e:
        _logger.error('Simi chat failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/<sim_id>/simi/conversation', methods=['DELETE'])
@login_required
def simi_new_conversation(sim_id: str):
    """
    Start a new conversation (archive the old one and create fresh).
    DELETE /api/simulations/<sim_id>/simi/conversation
    """
    _check_sim(sim_id)
    from app.models.chat import SimiConversation
    from utils.id_gen import generate_id
    from datetime import datetime

    # Create a new conversation — old one is simply abandoned (not deleted)
    new_conv = SimiConversation(
        id=generate_id(), simulation_id=sim_id, user_id=current_user.id,
        created_at=datetime.utcnow(), total_tokens=0,
    )
    db.session.add(new_conv)
    db.session.commit()
    return jsonify({'ok': True, 'conversation_id': new_conv.id})


@chat_bp.route('/<sim_id>/simi/refresh', methods=['POST'])
@login_required
def simi_refresh_context(sim_id: str):
    """
    Refresh simulation context and return a new opening message.
    POST /api/simulations/<sim_id>/simi/refresh
    Body: {current_tab}
    """
    _check_sim(sim_id)
    body        = request.get_json(silent=True) or {}
    current_tab = (body.get('current_tab') or 'journey').strip()

    from app.services.simi_service import build_simi_context, build_opening_message, get_suggestions
    try:
        ctx      = build_simi_context(sim_id, current_user.id)
        opening  = build_opening_message(ctx, current_tab, _user_first_name())
        suggestions = get_suggestions(current_tab)
        return jsonify({'ok': True, 'opening': opening, 'suggestions': suggestions})
    except Exception as e:
        _logger.error('Simi refresh failed: %s', e)
        return jsonify({'error': str(e)}), 500
